"""三路并发图片领域任务；每张调用既有 Pydantic Direct 入口。"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore, Event
from uuid import uuid4


from erp_web.release_guard import release_guard
from erp_web.schemas.image_batches import ImageBatch, ImageBatchItem
from erp_web.schemas.image_sets import ImageSetPlan
from erp_web.stores.image_batch_store import utc_now
from .ai_model_errors import safe_model_error_text
from .ai_run_cancellation import check_cancellation
from .capability_errors import BusinessCapabilityError
from . import image_service


# 进程内所有图片批次共享上限，不能因同时提交多个批次而放大并发。
image_request_slots = BoundedSemaphore(3)


def run_image_batch(*, store, app_dir, product_id: str, prompt: str,
                    sources: list[dict], process: Callable[[dict], dict],
                    persist: Callable[[dict], None], draft_id: str = "",
                    request_id: str = "", background: bool = False,
                    image_set: ImageSetPlan | None = None,
                    retained_items: dict[str, ImageBatchItem] | None = None,
                    request_fingerprint: str = "", before_create: Callable[[], None] | None = None,
                    preparation_fingerprint: str = "", prepare_workers: Callable[[], int] | None = None,
                    max_workers: int = 3) -> dict:
    conversation_id = request_id or f"image_batch_{uuid4().hex}"
    batch = ImageBatch(
        conversation_id=conversation_id, product_id=product_id, draft_id=draft_id, prompt=prompt,
        created_at=utc_now(), updated_at=utc_now(),
        image_set=image_set, request_fingerprint=request_fingerprint, preparation_fingerprint=preparation_fingerprint,
        items=[ImageBatchItem(index=index, source_id=str(item["id"]),
                             source_url=image_service.preview_for_path(str(item.get("path") or item.get("local_path") or ""), app_dir)
                             or str(item.get("preview_url") or item.get("url") or ""))
               for index, item in enumerate(sources)],
    )
    for item in batch.items:
        retained = (retained_items or {}).get(item.source_id)
        if retained is not None:
            batch.items[item.index] = retained.model_copy(update={"index": item.index})
    # 注册与幂等检查必须先完成；异步执行仍使用同一领域批次与三路执行上限。
    with store.lock:
        existing = store.get(conversation_id)
        if existing:
            if (existing.product_id != product_id or existing.draft_id != draft_id
                    or [item.source_id for item in existing.items] != [item.source_id for item in batch.items]
                    or existing.prompt != prompt or existing.image_set != image_set
                    or existing.request_fingerprint != request_fingerprint):
                raise ValueError("图片请求标识已用于其他操作。")
            snapshot = existing.snapshot()
            return {"ok": bool(snapshot["success"] or not snapshot["terminal"]), "imagePoolItems": [], "image_batch": snapshot,
                    "conversation_id": conversation_id, "message": "已恢复原图片任务。"}
        if before_create is not None:
            before_create()
        store.create(batch)

    stopped = Event()

    def stop_queued(reason):
        stopped.set()
        store.fail_queued(conversation_id, f"此图片尚未发送。{reason}")

    def work(index, source):
        with image_request_slots:
            if stopped.is_set():
                return []
            try:
                check_cancellation()
                store.update_item(conversation_id, index, status="running", started_at=utc_now())
                result = process(source)
                if not result.get("ok") or not result.get("imagePoolItems"):
                    raise BusinessCapabilityError(result.get("error_code") or "IMAGE_GENERATE_FAILED",
                        result.get("error") or result.get("message") or "图片服务没有返回结果。")
                urls = [str(item.get("preview_url") or "") for item in result["imagePoolItems"]]
                store.update_item(conversation_id, index, result_urls=urls)
                # persist 必须完成真实商品写入后返回；失败不能标记为成功。
                check_cancellation()
                persist(result)
                store.update_item(conversation_id, index, status="success", finished_at=utc_now(),
                                  result_urls=urls, result_asset_ids=[str(item["id"]) for item in result["imagePoolItems"] if item.get("id")])
                return result["imagePoolItems"]
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                error = f"图片服务返回 HTTP {status}。" if status else safe_model_error_text(exc)
                store.update_item(conversation_id, index, status="failed", finished_at=utc_now(), error=error or "图片处理失败。",
                                  error_code=str(getattr(exc, "code", "") or ""),
                                  outcome_unknown=bool(getattr(exc, "outcome_unknown", False)))
                if getattr(exc, "code", "") in {"CANVAS_REQUEST_NOT_SENT", "DESKTOP_UNAVAILABLE"}:
                    stop_queued(error)
                return []

    def execute():
        workers = max_workers
        if prepare_workers and any(item.status == "queued" for item in batch.items):
            try:
                with image_request_slots:
                    check_cancellation()
                    workers = prepare_workers()
            except Exception as exc:
                # 预检只读取连接状态，即使回执丢失也可以确定图片未发送。
                stop_queued(safe_model_error_text(exc))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="image-batch") as pool:
            futures = [pool.submit(copy_context().run, work, index, source) for index, source in enumerate(sources)
                       if not stopped.is_set() and batch.items[index].status == "queued"]
            # 保存发生在每个 worker 内；这里只按原图顺序汇总，不阻挡其他结果落库。
            results = [item for future in futures for item in future.result()]
        snapshot = store.get(conversation_id).snapshot()
        unknown = sum(item["outcome_unknown"] for item in snapshot["items"])
        message = f"处理结束：成功 {snapshot['success']} 张，失败 {snapshot['failed'] - unknown} 张"
        message += f"，结果待核对 {unknown} 张。" if unknown else "。"
        if not results and snapshot["items"]:
            message += " " + snapshot["items"][0]["error"]
        return {"ok": bool(results), "imagePoolItems": results, "generated_count": len(results),
                "provider": next((str(item["provider"]) for item in results if item.get("provider")), ""),
                "image_batch": snapshot, "conversation_id": conversation_id,
                "message": message}

    if background:
        release_guard.start_thread("图片批次", "draft-image-batch", execute)
        return {"ok": True, "imagePoolItems": [], "image_batch": batch.snapshot(),
                "conversation_id": conversation_id, "message": "图片处理已开始。"}
    return execute()

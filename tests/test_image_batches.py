"""图片批次并发、持久化和原生历史回归。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from dataclasses import replace
from types import SimpleNamespace
import base64

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.providers import Provider

from erp_web.context import get_context
from erp_web.schemas.image_batches import ImageBatch, ImageBatchItem
from erp_web.services.ai_direct_history import current_history_sink
from erp_web.services.image_batch_service import run_image_batch
from erp_web.stores.image_batch_store import ImageBatchStore, utc_now
from erp_web.services import ai_direct_request_service, ai_model_probe_service
from erp_web.services.ai_pydantic_image_model import OpenAIImagesModel
from tests.test_ai_direct_request_service import _binding


def arguments(process, persist, count=6):
    context = get_context()
    return dict(store=context.image_batches, messages=context.pydantic_messages,
                app_dir=context.paths.app_dir, product_id="p", prompt="翻译图片",
                sources=[{"id": str(i), "url": f"https://example.com/{i}.png"} for i in range(count)],
                process=process, persist=persist)


def test_three_parallel_items_queue_and_save_before_slowest_finishes():
    lock = Lock()
    entered = [Event() for _ in range(6)]
    release = [Event() for _ in range(6)]
    saved = [Event() for _ in range(6)]
    active = peak = 0

    def process(source):
        nonlocal active, peak
        index = int(source["id"])
        with lock:
            active += 1
            peak = max(peak, active)
        entered[index].set()
        assert release[index].wait(10)
        with lock:
            active -= 1
        sink = current_history_sink()
        sink([ModelRequest(parts=[UserPromptPart(f"原图 {index}")]), ModelResponse(parts=[TextPart(f"结果 {index}")])])
        if index == 2:
            raise RuntimeError("图片服务返回 502")
        return {"ok": True, "imagePoolItems": [{"id": str(index), "preview_url": f"/file?path={index}.png"}]}

    def persist(result):
        saved[int(result["imagePoolItems"][0]["id"])].set()

    args = arguments(process, persist)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run_image_batch, **args)
        try:
            assert all(event.wait(5) for event in entered[:3])
            history = get_context().pydantic_messages.list()[0]
            live = args["store"].get(history.conversation_id).snapshot()
            assert (live["running"], live["queued"], live["completed"]) == (3, 3, 0)
            release[1].set()
            assert saved[1].wait(5)
            assert entered[3].wait(5)
            assert not saved[0].is_set()
            live = args["store"].get(history.conversation_id).snapshot()
            assert live["items"][1]["status"] == "success"
            assert live["items"][0]["status"] == "running"
        finally:
            for event in release:
                event.set()
        result = future.result(timeout=10)
    assert peak == 3
    snapshot = result["image_batch"]
    assert (snapshot["success"], snapshot["failed"], snapshot["completed"]) == (5, 1, 6)
    assert snapshot["terminal"]
    assert [row["id"] for row in result["imagePoolItems"]] == ["0", "1", "3", "4", "5"]
    reopened = ImageBatchStore(args["store"].path).get(result["conversation_id"]).snapshot()
    assert reopened == snapshot
    history = get_context().pydantic_messages.get(result["conversation_id"]).model_messages()
    assert len(history) == 13
    assert [m.metadata["image_batch_item"] for m in history[1::2]] == list(range(6))


def test_save_failure_keeps_result_but_never_reports_success():
    def fail_save(result):
        raise RuntimeError("草稿保存冲突")
    result = run_image_batch(**arguments(
        lambda source: {"ok": True, "imagePoolItems": [{"preview_url": "/file?path=result.png"}]},
        fail_save, count=1,
    ))
    assert not result["ok"]
    item = result["image_batch"]["items"][0]
    assert item["status"] == "failed"
    assert item["result_urls"] == ["/file?path=result.png"]
    assert "保存冲突" in item["error"]


def test_parallel_batches_share_three_slots(monkeypatch):
    context = get_context()
    release = Event()
    first_started = Event()
    second_registered = Event()
    lock = Lock()
    active = peak = started = 0
    batch_ids = []
    create = context.image_batches.create

    def register(batch):
        create(batch)
        batch_ids.append(batch.conversation_id)
        if len(batch_ids) == 2:
            second_registered.set()

    monkeypatch.setattr(context.image_batches, "create", register)

    def process(source):
        nonlocal active, peak, started
        with lock:
            active += 1
            started += 1
            peak = max(peak, active)
            if started == 3:
                first_started.set()
        assert release.wait(10)
        with lock:
            active -= 1
        return {"ok": True, "imagePoolItems": [{"preview_url": "/file?path=result.png"}]}

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(run_image_batch, **arguments(process, lambda result: None, count=3))
        second = None
        try:
            assert first_started.wait(5)
            second = pool.submit(run_image_batch, **arguments(process, lambda result: None, count=3))
            assert second_registered.wait(5)
            assert context.image_batches.get(batch_ids[1]).snapshot()["queued"] == 3
        finally:
            release.set()
        assert first.result(timeout=10)["image_batch"]["success"] == 3
        assert second.result(timeout=10)["image_batch"]["success"] == 3
    assert peak == 3


def test_restart_preserves_success_and_marks_unfinished_without_replay(tmp_path):
    store = ImageBatchStore(tmp_path / "batches.sqlite3")
    store.create(ImageBatch(conversation_id="c", product_id="p", prompt="编辑",
        created_at=utc_now(), updated_at=utc_now(), items=[
            ImageBatchItem(index=0, source_id="a", status="success", result_urls=["/file?path=a.png"]),
            ImageBatchItem(index=1, source_id="b", status="running"),
            ImageBatchItem(index=2, source_id="c"),
        ]))
    batch = ImageBatchStore(store.path).get("c").snapshot()
    assert batch["terminal"]
    assert batch["success"] == 1
    assert batch["items"][1]["outcome_unknown"]
    assert not batch["items"][2]["outcome_unknown"]


def test_terminal_unknown_releases_draft_and_preserves_uncertain_receipts(tmp_path):
    store = ImageBatchStore(tmp_path / "batches.sqlite3")
    first = ImageBatch(conversation_id="old", product_id="p", draft_id="d", prompt="翻译",
                       created_at=utc_now(), updated_at=utc_now(), items=[
                           ImageBatchItem(index=0, source_id="a"), ImageBatchItem(index=1, source_id="b")])
    store.create(first)
    store.update_item("old", 0, status="failed", outcome_unknown=True, error="已发送，回执丢失")
    with pytest.raises(ValueError, match="正在处理"):
        store.assert_draft_idle("d")
    store.update_item("old", 1, status="failed", outcome_unknown=True,
                      error="等待 Google 登录超时，本次尚未发送修图请求；登录后可重新执行。")
    snapshot = store.get("old").snapshot()
    assert snapshot["terminal"] and snapshot["failed"] == 2
    assert [item["outcome_unknown"] for item in snapshot["items"]] == [True, False]
    store.assert_draft_idle("d")
    store.create(first.model_copy(update={"conversation_id": "manual", "created_at": utc_now()}))
    assert store.latest_for_draft("d").conversation_id == "manual"
    assert store.get("old").snapshot() == snapshot


def test_batch_uses_real_pydantic_direct_and_preserves_all_image_messages(monkeypatch, app_dir):
    raw = ai_model_probe_service._image_edit_probe_bytes()
    source = app_dir / "source.png"
    source.write_bytes(raw)
    calls = []

    class Images:
        async def edit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(raw).decode(), url=None)])

    class FakeProvider(Provider):
        @property
        def name(self): return "test"
        @property
        def base_url(self): return "https://example.com/v1"
        @property
        def client(self): return SimpleNamespace(images=Images())

    monkeypatch.setattr(ai_direct_request_service, "create_pydantic_model_binding",
                        lambda *a, **k: replace(_binding(required=("image_edit",)), model=OpenAIImagesModel("test-image", provider=FakeProvider())))

    def process(item):
        rows = ai_direct_request_service.edit_images(app_dir=app_dir, use_case_id="image.translate",
            model={}, required_capabilities=("image_edit",), prompt="翻译", images=[{**item, "path": str(source)}],
            size="1024x1024", quality="medium", count=1, timeout_seconds=30)
        return {"ok": True, "imagePoolItems": [{"id": item["id"], "preview_url": "/file?path=result.png"}], "native": rows}

    result = run_image_batch(**arguments(process, lambda result: None, count=4))
    assert result["image_batch"]["success"] == 4
    assert len(calls) == 4
    history = get_context().pydantic_messages.get(result["conversation_id"]).model_messages()
    assert len(history) == 9
    assert all(message.files[0].data == raw for message in history[2::2])
    assert all(call["n"] == 1 for call in calls)


@pytest.mark.parametrize("strategy", ["append", "replace_all", "replace_selected"])
def test_facade_saves_each_result_without_losing_other_images(monkeypatch, app_dir, strategy):
    from erp_web.facades import image_facade
    from erp_web.services import image_translate_service
    from tests.test_domain_write_capabilities import _seed_product

    context = get_context()
    product = _seed_product("batch-product")
    sources = [{"id": str(index), "url": f"https://example.com/{index}.png",
                "origin": "source", "selected": True, "order": index} for index in range(5)]
    product["source"]["image_pool"] = sources
    product["drafts"]["mercadolibre"]["images"] = [
        {"asset_id": str(index), "role": "main" if index == 0 else "detail", "order": index}
        for index in range(5)
    ]
    product = context.products.save_product(product)
    draft_id = product["drafts"]["mercadolibre"]["draft_id"]
    raw = ai_model_probe_service._image_edit_probe_bytes()

    real_edit = image_translate_service.edit_images
    def fake_edit(app, product, config, *, image_ids, **kwargs):
        if image_ids == ["2"]:
            raise RuntimeError("图片服务返回 502")
        return real_edit(app, product, config, image_ids=image_ids,
                         provider=lambda cfg, req: [{"bytes": raw, "source_id": image_ids[0]}], **kwargs)

    monkeypatch.setattr(image_translate_service, "edit_images", fake_edit)
    response, status = image_facade.edit_images_payload({
        "product_id": product["product_id"], "source_image_ids": [str(i) for i in range(5)],
        "prompt": "翻译成俄语", "platform": "mercadolibre", "draft_id": draft_id,
        "apply_to_draft": True, "draft_image_strategy": strategy,
    })
    assert status == 200
    assert response["image_batch"]["success"] == 4
    assert response["image_batch"]["failed"] == 1
    saved = context.products.load_product_from_index(product["product_id"], "")
    assert len(saved["source"]["image_pool"]) == 9
    draft = context.products.draft_record(draft_id)
    assert len(draft["images"]) == {"append": 9, "replace_all": 4, "replace_selected": 5}[strategy]
    for item in response["imagePoolItems"]:
        assert (app_dir / item["path"]).exists()
        assert item["derived_from_id"] in {"0", "1", "3", "4"}
        assert any(ref["asset_id"] == item["id"] for ref in draft["images"])


def test_batch_read_route_and_ui_projection_preserve_canonical_history():
    from erp_web.http_route_units import ai_work_routes
    from erp_web.facades.ai_chat_facade import ui_messages_payload
    from urllib.parse import urlparse

    def process(source):
        current_history_sink()([ModelRequest(parts=[UserPromptPart("原图")]), ModelResponse(parts=[TextPart("结果")])])
        return {"ok": True, "imagePoolItems": [{"preview_url": "/file?path=result.png"}]}
    result = run_image_batch(**arguments(process, lambda result: None, count=1))
    conversation_id = result["conversation_id"]
    responses = []
    handler = SimpleNamespace(send_json=lambda payload, *args: responses.append(payload))
    assert ai_work_routes.handle_get(handler, urlparse(f"/api/v1/ai-work/conversations/{conversation_id}/image-batch"))
    assert responses[0]["image_batch"]["success"] == 1
    ui, status = ui_messages_payload(conversation_id)
    assert status == 200
    assert len(ui["messages"]) == 1
    assert len(get_context().pydantic_messages.get(conversation_id).model_messages()) == 3


def test_superseded_image_keeps_provider_result_without_applying_old_request():
    from pydantic_ai import CancellationToken
    from erp_web.services.ai_run_cancellation import bind_cancellation_token

    token = CancellationToken()
    saved = []

    def process(source):
        token.cancel()
        return {"ok": True, "imagePoolItems": [{"preview_url": "/file?path=old.png"}]}

    with bind_cancellation_token(token):
        result = run_image_batch(**arguments(process, saved.append, count=1))
    assert not saved
    item = result["image_batch"]["items"][0]
    assert item["status"] == "failed"
    assert item["result_urls"] == ["/file?path=old.png"]


def test_canvas_stops_remaining_images_and_preserves_completed_receipts():
    from erp_web.services.capability_errors import BusinessCapabilityError

    entered = []
    def process(source):
        entered.append(source["id"])
        if len(entered) == 2:
            raise BusinessCapabilityError("CANVAS_REQUEST_NOT_SENT", "已检测到 Canvas 预览，但翻译连接未就绪。")
        return {"ok": True, "imagePoolItems": [{"id": "translated", "preview_url": "/translated.png"}]}

    snapshot = run_image_batch(**arguments(process, lambda _: None), prepare_workers=lambda: 1)["image_batch"]
    assert entered == ["0", "1"] and snapshot["terminal"]
    assert snapshot["items"][0]["result_asset_ids"] == ["translated"]
    assert snapshot["items"][1]["started_at"]
    assert all(row["status"] == "failed" and not row["outcome_unknown"] for row in snapshot["items"][1:])
    assert all(not row["started_at"] and "尚未发送" in row["error"] for row in snapshot["items"][2:])

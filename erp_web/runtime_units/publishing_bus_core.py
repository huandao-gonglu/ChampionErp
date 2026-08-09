# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Protocol



class PublishingAdapter(Protocol):
    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        ...

    def required_attributes_missing(self, product: dict[str, Any], config: dict[str, Any]) -> list[str]:
        ...

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        ...


class PublishingJobStore(Protocol):
    """Persistence contract implemented by ``erp_web.db.ErpDatabase``."""

    def save_publish_job(self, state: dict[str, Any]) -> None:
        ...

    def load_publish_job(self, job_id: str) -> dict[str, Any]:
        ...

    def list_pending_publish_jobs(self) -> list[dict[str, Any]]:
        ...

    def list_publish_jobs(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        platform: str = "",
        product_id: str = "",
    ) -> tuple[list[dict[str, Any]], str]:
        ...


ACTIVE_JOB_STATUSES = frozenset({"pending", "queued", "running", "retrying"})
SUCCESS_JOB_STATUSES = frozenset(
    {"success", "finished", "published", "real_publish_success"}
)
FAILED_JOB_STATUSES = frozenset(
    {"failed", "error", "blocked", "real_publish_failed"}
)


def _publish_job_display_status(state: dict[str, Any]) -> str:
    raw_platforms = (
        state.get("platforms") if isinstance(state.get("platforms"), dict) else {}
    )
    platform_states = [
        item
        for item in raw_platforms.values()
        if isinstance(item, dict)
    ]
    statuses = {
        str(item.get("status") or item.get("stage") or "").strip().lower()
        for item in platform_states
    }
    if statuses & ACTIVE_JOB_STATUSES:
        if statuses <= {"pending", "queued"}:
            return "queued"
        return "running"

    has_success = bool(statuses & SUCCESS_JOB_STATUSES)
    has_failure = bool(statuses & FAILED_JOB_STATUSES)
    if has_success and has_failure:
        return "partial"
    if has_failure:
        return "failed"
    if has_success and statuses <= SUCCESS_JOB_STATUSES:
        return "success"

    root_status = str(state.get("status") or "").strip().lower()
    if root_status in ACTIVE_JOB_STATUSES:
        return "queued" if root_status in {"pending", "queued"} else "running"
    if root_status in FAILED_JOB_STATUSES:
        return "failed"
    if root_status in SUCCESS_JOB_STATUSES:
        return "success"
    return root_status or "queued"


def _publish_job_summary(state: dict[str, Any]) -> dict[str, Any]:
    product = state.get("product") if isinstance(state.get("product"), dict) else {}
    raw_platforms = (
        state.get("platforms") if isinstance(state.get("platforms"), dict) else {}
    )
    platforms: list[dict[str, Any]] = []
    for platform, raw in sorted(raw_platforms.items()):
        item = raw if isinstance(raw, dict) else {}
        platforms.append(
            {
                "platform": str(platform),
                "draft_id": str(item.get("draft_id") or ""),
                "site": str(item.get("site") or ""),
                "status": str(item.get("status") or ""),
                "stage": str(item.get("stage") or ""),
                "attempts": int(item.get("attempts") or 0),
                "error": str(item.get("error") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )

    error = next((item["error"] for item in platforms if item["error"]), "")
    attempts = max((item["attempts"] for item in platforms), default=0)
    stage = next(
        (item["stage"] for item in reversed(platforms) if item["stage"]),
        "",
    )
    draft_id = str(
        state.get("draft_id")
        or product.get("current_draft_id")
        or product.get("draft_id")
        or ""
    )
    if not draft_id:
        draft_id = next(
            (item["draft_id"] for item in platforms if item["draft_id"]),
            "",
        )
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    if not draft_id:
        for platform in platforms:
            draft = drafts.get(platform["platform"])
            if isinstance(draft, dict) and draft.get("draft_id"):
                draft_id = str(draft["draft_id"])
                break
    return {
        "job_id": str(state.get("job_id") or ""),
        "product_id": str(product.get("product_id") or state.get("product_id") or ""),
        "product_name": str(state.get("product_name") or product.get("name") or ""),
        "draft_id": draft_id,
        "status": _publish_job_display_status(state),
        "raw_status": str(state.get("status") or ""),
        "stage": stage,
        "attempts": attempts,
        "error": error,
        "platforms": platforms,
        "created_at": str(state.get("created_at") or ""),
        "updated_at": str(state.get("updated_at") or ""),
    }


class PublishingBus:
    """Publish queue with SQLite-backed job state.

    Job state never contains store credentials/config: the platform config is
    fetched from ``config_provider`` (store_auth-backed) at execution time.
    """

    def __init__(
        self,
        store: PublishingJobStore,
        adapters: dict[str, PublishingAdapter],
        config_provider: Callable[[], dict[str, Any]] | None = None,
        terminal_callback: (
            Callable[[dict[str, Any]], dict[str, Any] | None] | None
        ) = None,
        max_workers: int = 6,
        max_retries: int = 1,
        retry_delay_seconds: float = 0.25,
        auto_resume_pending: bool = True,
    ) -> None:
        self.store = store
        self.adapters = adapters
        self.config_provider = config_provider or (lambda: {})
        self.terminal_callback = terminal_callback
        self.max_retries = max(0, int(max_retries))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="PublishingBus")
        self._lock = threading.RLock()
        self._futures: dict[str, list[Future[Any]]] = {}
        if auto_resume_pending:
            self.recover_pending_jobs()

    def enqueue(
        self,
        product: dict[str, Any],
        platforms: list[str],
        *,
        targets: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        selected = [platform for platform in platforms if platform in self.adapters]
        if not selected:
            raise ValueError("请选择至少一个可发布平台。")

        product_id = str(product.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("发布任务缺少 product_id。")
        bindings: dict[str, dict[str, str]] = {}
        for platform in selected:
            raw = targets.get(platform) if isinstance(targets.get(platform), dict) else {}
            draft_id = str(raw.get("draft_id") or "").strip()
            site = str(raw.get("site") or "").strip()
            target_product_id = str(raw.get("product_id") or product_id).strip()
            if not draft_id or not site:
                raise ValueError(f"{platform} 发布任务缺少 draft_id 或 site。")
            if target_product_id != product_id:
                raise ValueError(
                    f"{platform} 草稿绑定商品 {target_product_id} 与发布商品 {product_id} 不一致。"
                )
            bindings[platform] = {
                "draft_id": draft_id,
                "site": site,
                "product_id": product_id,
            }

        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        now = current_time()
        state = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "draft_id": next(iter(bindings.values()))["draft_id"] if len(bindings) == 1 else "",
            "product_name": str(product.get("name") or ""),
            "product": copy.deepcopy(product),
            "platforms": {
                platform: self._new_platform_state(
                    platform,
                    now,
                    bindings[platform],
                )
                for platform in selected
            },
        }
        self._write_state(job_id, state)
        self._submit_job(job_id, product, selected)
        self._update_job_status(job_id)
        return {
            "ok": True,
            "job_id": job_id,
            "platforms": selected,
            "targets": bindings,
            "status": "queued",
        }

    def recover_pending_jobs(self) -> list[str]:
        recovered: list[str] = []
        for state in self.store.list_pending_publish_jobs():
            if not isinstance(state, dict):
                continue
            job_id = str(state.get("job_id") or "")
            if not job_id:
                continue
            if (
                str(state.get("status") or "").strip().lower()
                == "completed"
                and not state.get("terminal_results_persisted")
            ):
                # Job status is committed before the terminal callback runs.
                # A process crash in that narrow window must be recoverable on
                # the next startup, just like an interrupted worker.
                if self.terminal_callback is None:
                    continue
                self._update_job_status(job_id)
                if self._read_state(job_id).get(
                    "terminal_results_persisted"
                ):
                    recovered.append(job_id)
                continue
            if self._resume_state(job_id, state):
                recovered.append(job_id)
        return recovered

    def wait(self, job_id: str, timeout: float | None = None) -> None:
        deadline = time.time() + timeout if timeout else None
        for future in list(self._futures.get(job_id, [])):
            remaining = None if deadline is None else max(0.0, deadline - time.time())
            future.result(timeout=remaining)
        self._update_job_status(job_id)

    def get_status(self, job_id: str) -> dict[str, Any]:
        return self._read_state(job_id)

    def get_public_status(self, job_id: str) -> dict[str, Any]:
        state = copy.deepcopy(self._read_state(job_id))
        summary = _publish_job_summary(state)
        state.pop("product", None)
        state["product_id"] = summary["product_id"]
        state["product_name"] = summary["product_name"]
        state["draft_id"] = summary["draft_id"]
        state["display_status"] = summary["status"]
        platforms = state.get("platforms")
        if isinstance(platforms, dict):
            for item in platforms.values():
                if not isinstance(item, dict):
                    continue
                result = item.get("result")
                if isinstance(result, dict):
                    result.pop("product", None)
        return state

    def list_jobs(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        status: str = "",
        platform: str = "",
        product_id: str = "",
    ) -> dict[str, Any]:
        resolved_limit = max(1, min(int(limit or 50), 100))
        status = str(status or "").strip().lower()
        scan_cursor = str(cursor or "").strip()
        summaries: list[dict[str, Any]] = []

        while len(summaries) <= resolved_limit:
            states, next_scan_cursor = self.store.list_publish_jobs(
                limit=100,
                cursor=scan_cursor,
                platform=str(platform or "").strip().lower(),
                product_id=str(product_id or "").strip(),
            )
            if not states:
                break
            for state in states:
                summary = _publish_job_summary(state)
                if status and summary["status"] != status:
                    continue
                summaries.append(summary)
                if len(summaries) > resolved_limit:
                    return {
                        "items": summaries[:resolved_limit],
                        "next_cursor": summaries[resolved_limit - 1]["job_id"],
                    }
            if not next_scan_cursor:
                break
            scan_cursor = next_scan_cursor

        return {"items": summaries[:resolved_limit], "next_cursor": ""}

    def _submit_job(self, job_id: str, product: dict[str, Any], platforms: list[str]) -> None:
        futures = [
            self.executor.submit(self._run_platform, job_id, copy.deepcopy(product), platform)
            for platform in platforms
        ]
        self._futures[job_id] = futures

    def _resume_state(self, job_id: str, state: dict[str, Any]) -> bool:
        product = state.get("product") if isinstance(state.get("product"), dict) else {}
        pending = []
        platforms = state.get("platforms") if isinstance(state.get("platforms"), dict) else {}
        for platform, item in platforms.items():
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            if status in {"pending", "queued", "running", "retrying"} and platform in self.adapters:
                item["status"] = "queued"
                item["stage"] = "resuming"
                item["error"] = str(item.get("error") or "")
                item["updated_at"] = current_time()
                pending.append(platform)
        if not pending:
            return False
        state["status"] = "queued"
        state["updated_at"] = current_time()
        # 旧 job 状态里若残留 config/凭据，一律丢弃（发布执行时从 store_auth 现取）。
        state.pop("config", None)
        self._write_state(job_id, state)
        self._submit_job(job_id, product, pending)
        self._update_job_status(job_id)
        return True

    def _new_platform_state(
        self,
        platform: str,
        now: str,
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = target if isinstance(target, dict) else {}
        return {
            "platform": platform,
            "draft_id": str(target.get("draft_id") or ""),
            "site": str(target.get("site") or ""),
            "product_id": str(target.get("product_id") or ""),
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "stage": "queued",
            "error": "",
            "result": None,
            "attempts": 0,
        }

    def _run_platform(self, job_id: str, product: dict[str, Any], platform: str) -> None:
        adapter = self.adapters[platform]
        attempts = 0
        max_attempts = self.max_retries + 1

        while attempts < max_attempts:
            attempts += 1
            try:
                # 每次执行时现取店铺配置（凭据来自 store_auth 表），不落任何 job 持久化。
                config = self.config_provider()
                config = config if isinstance(config, dict) else {}
                state = self._read_state(job_id)
                platform_state = (
                    state.get("platforms", {}).get(platform)
                    if isinstance(state.get("platforms"), dict)
                    else {}
                )
                stored_result = (
                    platform_state.get("result")
                    if isinstance(platform_state, dict)
                    and isinstance(platform_state.get("result"), dict)
                    else None
                )
                if self._is_pending_publish_result(stored_result):
                    result: Any = stored_result
                    self._set_platform(
                        job_id,
                        platform,
                        status="running",
                        stage="waiting_platform_confirmation",
                        error="",
                        attempts=attempts,
                    )
                else:
                    self._set_platform(job_id, platform, status="running", stage="resolving_category", attempts=attempts)
                    resolved = adapter.resolve_category(product, config)
                    product = resolved if isinstance(resolved, dict) else product
                    drafts = (
                        product.get("drafts")
                        if isinstance(product.get("drafts"), dict)
                        else {}
                    )
                    draft = (
                        drafts.get(platform)
                        if isinstance(drafts.get(platform), dict)
                        else {}
                    )
                    self._set_platform(
                        job_id,
                        platform,
                        stage="validating_required_attributes",
                        category_id=str(
                            draft.get("category_id")
                            or ""
                        ),
                        attempts=attempts,
                    )
                    missing = adapter.required_attributes_missing(product, config)
                    if missing:
                        self._set_platform(
                            job_id,
                            platform,
                            status="failed",
                            stage="failed",
                            error="缺失必填属性：" + "，".join(str(item) for item in missing),
                            attempts=attempts,
                        )
                        return
                    self._set_platform(job_id, platform, stage="publishing", attempts=attempts)
                    result = adapter.publish(product, platform, config)

                while self._is_pending_publish_result(result):
                    poller = getattr(adapter, "poll_publish_status", None)
                    if not callable(poller):
                        raise RuntimeError(
                            f"{platform} 返回待确认状态，但发布适配器未实现状态轮询"
                        )
                    persisted_pending = self._persisted_platform_result(result)
                    self._set_platform(
                        job_id,
                        platform,
                        status="running",
                        stage="waiting_platform_confirmation",
                        error="",
                        result=persisted_pending,
                        attempts=attempts,
                    )
                    interval_provider = getattr(
                        adapter,
                        "publish_poll_interval_seconds",
                        None,
                    )
                    interval = (
                        interval_provider(config)
                        if callable(interval_provider)
                        else self.retry_delay_seconds
                    )
                    time.sleep(max(0.05, float(interval)))
                    result = poller(result, config)

                persisted_result = (
                    self._persisted_platform_result(result)
                )
                result_status = (
                    str(result.get("status") or "").strip().lower()
                    if isinstance(result, dict)
                    else ""
                )
                success_evidence = (
                    isinstance(result, dict)
                    and result.get("ok") is True
                    and (
                        result_status
                        in {
                            "published",
                            "success",
                            "real_publish_success",
                        }
                        or bool(
                            result.get("id")
                            or result.get("item_id")
                            or result.get("external_id")
                        )
                    )
                )
                if not success_evidence:
                    failure_status = (
                        result_status
                        if result_status
                        in {
                            "failed",
                            "not_ready",
                            "ready_for_real_publish",
                            "skipped",
                        }
                        else "failed"
                    )
                    error = (
                        str(result.get("error") or "")
                        if isinstance(result, dict)
                        else ""
                    )
                    self._set_platform(
                        job_id,
                        platform,
                        status=failure_status,
                        stage=failure_status,
                        error=error
                        or "发布适配器未返回可验证的成功结果",
                        result=persisted_result,
                        attempts=attempts,
                    )
                    return
                self._set_platform(
                    job_id,
                    platform,
                    status="success",
                    stage="finished",
                    result=persisted_result,
                    attempts=attempts,
                )
                return
            except Exception as exc:
                retryable = attempts < max_attempts
                self._set_platform(
                    job_id,
                    platform,
                    status="retrying" if retryable else "failed",
                    stage="retrying" if retryable else "failed",
                    error=str(exc),
                    attempts=attempts,
                )
                if not retryable:
                    return
                time.sleep(self.retry_delay_seconds)
        self._update_job_status(job_id)

    @staticmethod
    def _is_pending_publish_result(result: Any) -> bool:
        return bool(
            isinstance(result, dict)
            and str(result.get("status") or "").strip().lower()
            in {"pending_confirmation", "publish_pending_confirmation"}
        )

    @staticmethod
    def _persisted_platform_result(result: Any) -> dict[str, Any] | None:
        persisted = copy.deepcopy(result) if isinstance(result, dict) else None
        if isinstance(persisted, dict):
            # job 根节点已经保存恢复执行所需的 product；平台 result
            # 再存一次只会让 SQLite 与发布日志成倍膨胀。
            persisted.pop("product", None)
        return persisted

    def _set_platform(self, job_id: str, platform: str, **updates: Any) -> None:
        with self._lock:
            state = self._read_state(job_id)
            item = state["platforms"].setdefault(platform, self._new_platform_state(platform, current_time()))
            item.update(updates)
            item["updated_at"] = current_time()
            state["updated_at"] = item["updated_at"]
            self._write_state(job_id, state)
        if str(updates.get("status") or "").lower() in {"success", "failed", "not_ready", "ready_for_real_publish", "skipped"}:
            self._update_job_status(job_id)

    def _update_job_status(self, job_id: str) -> None:
        with self._lock:
            state = self._read_state(job_id)
            statuses = [str(item.get("status") or "").lower() for item in state.get("platforms", {}).values()]
            if any(status in {"running", "retrying"} for status in statuses):
                state["status"] = "running"
            elif statuses and all(status in {"success", "failed", "not_ready", "ready_for_real_publish", "skipped"} for status in statuses):
                state["status"] = "completed"
            else:
                state["status"] = "queued"
            state["updated_at"] = current_time()
            self._write_state(job_id, state)
            if (
                state["status"] == "completed"
                and self.terminal_callback is not None
                and not state.get("terminal_results_persisted")
            ):
                try:
                    persisted = self.terminal_callback(
                        copy.deepcopy(state)
                    )
                    if isinstance(persisted, dict):
                        state = persisted
                    state["terminal_results_persisted"] = True
                    state.pop("terminal_persistence_error", None)
                except Exception as exc:
                    state["terminal_persistence_error"] = str(exc)
                state["updated_at"] = current_time()
                self._write_state(job_id, state)

    def _read_state(self, job_id: str) -> dict[str, Any]:
        state = self.store.load_publish_job(job_id)
        if not state:
            raise FileNotFoundError(f"发布任务不存在：{job_id}")
        return state

    def _write_state(self, job_id: str, state: dict[str, Any]) -> None:
        state["job_id"] = str(state.get("job_id") or job_id)
        self.store.save_publish_job(state)


def current_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

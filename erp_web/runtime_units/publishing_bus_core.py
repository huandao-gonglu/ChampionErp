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

    def enqueue(self, product: dict[str, Any], platforms: list[str]) -> dict[str, Any]:
        selected = [platform for platform in platforms if platform in self.adapters]
        if not selected:
            raise ValueError("请选择至少一个可发布平台。")

        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        now = current_time()
        state = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "product_name": str(product.get("name") or ""),
            "product": copy.deepcopy(product),
            "platforms": {
                platform: self._new_platform_state(platform, now)
                for platform in selected
            },
        }
        self._write_state(job_id, state)
        self._submit_job(job_id, product, selected)
        self._update_job_status(job_id)
        return {"ok": True, "job_id": job_id, "platforms": selected, "status": "queued"}

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

    def _new_platform_state(self, platform: str, now: str) -> dict[str, Any]:
        return {
            "platform": platform,
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
                        result=result if isinstance(result, dict) else None,
                        attempts=attempts,
                    )
                    return
                self._set_platform(job_id, platform, status="success", stage="finished", result=result, attempts=attempts)
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

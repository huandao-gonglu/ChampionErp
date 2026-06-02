# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol


class PublishingAdapter(Protocol):
    def resolve_category(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        ...

    def validate_required_attributes(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> list[str]:
        ...

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        ...


class PublishingBus:
    def __init__(
        self,
        state_dir: Path,
        adapters: dict[str, PublishingAdapter],
        max_workers: int = 6,
        max_retries: int = 1,
        retry_delay_seconds: float = 0.25,
        auto_resume_pending: bool = True,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.adapters = adapters
        self.max_retries = max(0, int(max_retries))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="PublishingBus")
        self._lock = threading.RLock()
        self._futures: dict[str, list[Future[Any]]] = {}
        if auto_resume_pending:
            self.recover_pending_jobs()

    def enqueue(self, product: dict[str, Any], platforms: list[str], config: dict[str, Any]) -> dict[str, Any]:
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
            "config": copy.deepcopy(config),
            "platforms": {
                platform: self._new_platform_state(platform, now)
                for platform in selected
            },
        }
        self._write_state(job_id, state)
        self._submit_job(job_id, product, selected, config)
        self._update_job_status(job_id)
        return {"ok": True, "job_id": job_id, "platforms": selected, "status": "queued"}

    def recover_pending_jobs(self) -> list[str]:
        recovered: list[str] = []
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            job_id = str(state.get("job_id") or path.stem)
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

    def _submit_job(self, job_id: str, product: dict[str, Any], platforms: list[str], config: dict[str, Any]) -> None:
        futures = [
            self.executor.submit(self._run_platform, job_id, copy.deepcopy(product), platform, copy.deepcopy(config))
            for platform in platforms
        ]
        self._futures[job_id] = futures

    def _resume_state(self, job_id: str, state: dict[str, Any]) -> bool:
        product = state.get("product") if isinstance(state.get("product"), dict) else {}
        config = state.get("config") if isinstance(state.get("config"), dict) else {}
        pending = []
        platforms = state.get("platforms") if isinstance(state.get("platforms"), dict) else {}
        for platform, item in platforms.items():
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            if status in {"queued", "running", "retrying"} and platform in self.adapters:
                item["status"] = "queued"
                item["stage"] = "resuming"
                item["error"] = str(item.get("error") or "")
                item["updated_at"] = current_time()
                pending.append(platform)
        if not pending:
            return False
        state["status"] = "queued"
        state["updated_at"] = current_time()
        self._write_state(job_id, state)
        self._submit_job(job_id, product, pending, config)
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

    def _run_platform(self, job_id: str, product: dict[str, Any], platform: str, config: dict[str, Any]) -> None:
        adapter = self.adapters[platform]
        attempts = 0
        max_attempts = self.max_retries + 1

        while attempts < max_attempts:
            attempts += 1
            try:
                self._set_platform(job_id, platform, status="running", stage="resolving_category", attempts=attempts)
                resolved = adapter.resolve_category(product, platform, config)
                product = resolved if isinstance(resolved, dict) else product
                self._set_platform(
                    job_id,
                    platform,
                    stage="validating_required_attributes",
                    category_id=str(product.get("category_id") or product.get("wb_subject_id") or product.get("ozon_category_id") or ""),
                    attempts=attempts,
                )
                missing = adapter.validate_required_attributes(product, platform, config)
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
                if isinstance(result, dict) and result.get("ok") is False:
                    result_status = str(result.get("status") or "failed").strip().lower()
                    self._set_platform(
                        job_id,
                        platform,
                        status=result_status if result_status in {"failed", "not_ready", "ready_for_real_publish", "skipped"} else "failed",
                        stage=result_status or "failed",
                        error=str(result.get("error") or "publish failed"),
                        result=result,
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

    def _state_path(self, job_id: str) -> Path:
        safe = "".join(char for char in str(job_id) if char.isalnum() or char in "-_")
        return self.state_dir / f"{safe}.json"

    def _read_state(self, job_id: str) -> dict[str, Any]:
        path = self._state_path(job_id)
        if not path.exists():
            raise FileNotFoundError(f"发布任务不存在：{job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_state(self, job_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def current_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

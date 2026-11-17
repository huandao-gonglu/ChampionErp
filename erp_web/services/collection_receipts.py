"""个人工作区内的短期采集回执，桌面断线后只查询原任务。"""
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from uuid import UUID

from erp_web.release_guard import release_guard
from erp_web.schemas.requests import RequestValidationError


@dataclass
class _Receipt:
    finished: float = 0
    result: dict | None = None
    error: tuple[str, int, str] | None = None


class CollectionReceipts:
    def __init__(self):
        self._lock = Lock()
        self._rows: dict[str, _Receipt] = {}

    def start(self, key, collect, body):
        try:
            if str(UUID(key)) != key:
                raise ValueError()
        except (TypeError, ValueError, AttributeError):
            raise RequestValidationError("采集回执编号无效。") from None
        with self._lock:
            self._rows = {k: r for k, r in self._rows.items() if not r.finished or monotonic() - r.finished < 900}
            if key in self._rows:
                raise RequestValidationError("该采集请求已受理。", status_code=409, error_code="COLLECT_ALREADY_STARTED")
            if sum(not r.finished for r in self._rows.values()) >= 8:
                raise RequestValidationError("当前工作区有多项采集正在执行，请稍后再试。", status_code=429)
            if len(self._rows) >= 32:
                oldest = min((k for k, r in self._rows.items() if r.finished), key=lambda k: self._rows[k].finished)
                del self._rows[oldest]
            row = self._rows[key] = _Receipt()
        try:
            release_guard.start_thread("业务请求", "collection-receipt", self._run, row, collect, body)
        except Exception:
            with self._lock:
                del self._rows[key]
            raise
        return {"ok": True, "status": "running", "receipt_id": key}

    def _run(self, row, collect, body):
        result = error = None
        try:
            result = collect(body)
        except RequestValidationError as exc:
            error = (str(exc), exc.status_code, exc.error_code)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("采集执行中断")
            error = ("采集执行中断，请核对采集箱。", 500, "COLLECT_RESULT_UNCONFIRMED")
        with self._lock:
            row.result, row.error, row.finished = result, error, monotonic()

    def read(self, key):
        with self._lock:
            row = self._rows.get(key)
            if row is None or (row.finished and monotonic() - row.finished >= 900):
                raise RequestValidationError("采集回执不存在或已过期。", status_code=404, error_code="COLLECT_RECEIPT_MISSING")
            if not row.finished:
                return {"ok": True, "status": "running", "receipt_id": key}
            if row.error:
                message, status, code = row.error
                raise RequestValidationError(message, status_code=status, error_code=code)
            return {"ok": True, "status": "finished", "receipt_id": key, "result": row.result}

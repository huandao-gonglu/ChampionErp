"""可见商品的销售数据按批更新，跨页面请求共用同一 SKU 正在进行的读取。"""
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from threading import Lock

from erp_web.services.external_request_context import request_operation


SALES_MAX_AGE = timedelta(minutes=15)
SALES_BATCH_SIZE = 100


def sales_due(listing, cutoff):
    stamp = listing.sales_checked_at or listing.synced_at
    if not stamp:
        return True
    try:
        return datetime.fromisoformat(stamp) < cutoff
    except (TypeError, ValueError):
        return True


class OnlineProductSalesReader:
    def __init__(self, store, config, adapter):
        self.store, self.config, self.adapter = store, config, adapter
        self._lock = Lock()
        self._pending = {}

    def refresh(self, account, listing_ids):
        if not account or not 1 <= len(listing_ids) <= SALES_BATCH_SIZE:
            raise ValueError("请指定当前账号和 1–100 件在线商品")
        self.config("yandex", account)
        cutoff = datetime.now(timezone.utc) - SALES_MAX_AGE
        with self._lock:
            records = self.store.sales_targets(account, list(dict.fromkeys(listing_ids)))
            due = [row for row in records if sales_due(row, cutoff)]
            owned = [row for row in due if row.id not in self._pending]
            for row in owned:
                self._pending[row.id] = Future()
            waits = {row.id: self._pending[row.id] for row in due}
        if owned:
            try:
                with request_operation("online_products"):
                    adapter = self.adapter("yandex", account)
                    batch = adapter.read_sales([row.remote_id for row in owned])
                self.config("yandex", account)
                fresh = {row.id: row for row in batch.listings}
                for row in owned:
                    if row.remote_id in batch.errors or row.id not in fresh:
                        waits[row.id].set_exception(ValueError(batch.errors.get(row.remote_id) or "平台未返回该商品的销售数据"))
                    else:
                        self.store.update_sales(row, fresh[row.id])
                        waits[row.id].set_result(True)
            except Exception as exc:
                for row in owned:
                    if not waits[row.id].done():
                        waits[row.id].set_exception(exc)
            finally:
                with self._lock:
                    for row in owned:
                        del self._pending[row.id]
        for future in waits.values():
            future.result()
        return {"ok": True}

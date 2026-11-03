"""在线 SKU 完整详情读取；同轮复用成功快照，并合并正在执行的查询。"""
from concurrent.futures import Future
import threading
import time


def _snapshot_key(listing):
    return (listing.version, listing.synced_at, listing.basic_synced_at,
            listing.status_checked_at, listing.details_state, tuple(listing.errors))


class OnlineProductDetailReader:
    def __init__(self, store, config, adapter):
        self.store = store
        self.config = config
        self.adapter = adapter
        self._lock = threading.Lock()
        self._pending = {}
        self._reads = {}

    def invalidate(self, listing_id):
        with self._lock:
            self._reads = {key: value for key, value in self._reads.items() if key[-1] != listing_id}

    def read(self, listing_id, *, read_scope=(), expires_at=0):
        with self._lock:
            before = self.store.get(listing_id)
            self.config(before.platform, before.account_id)
            self.store.assert_status_refreshable(before)
            target = (before.platform, before.account_id, before.id)
            key = (read_scope, *target)
            now = time.time()
            self._reads = {key: value for key, value in self._reads.items() if value[0] > now}
            previous = self._reads.get(key)
            if read_scope and previous and previous[1] == _snapshot_key(before):
                return before
            pending = self._pending.get(target)
            owner = pending is None
            if owner:
                pending = self._pending[target] = Future()
        if owner:
            try:
                adapter = self.adapter(before.platform, before.account_id)
                detail = adapter.read(before.remote_id)
                if detail.errors:
                    raise ValueError("商品详情读取不完整：" + "；".join(detail.errors))
                self.config(before.platform, before.account_id)
                listing = self.store.update_detail(before, detail)
            except Exception as exc:
                with self._lock:
                    pending.set_exception(exc)
                    del self._pending[target]
                raise
            with self._lock:
                if read_scope:
                    self._reads[key] = (expires_at, _snapshot_key(listing))
                pending.set_result(listing)
                del self._pending[target]
        listing = pending.result(timeout=max(0, expires_at - time.time()) if expires_at else None)
        self.config(listing.platform, listing.account_id)
        with self._lock:
            if read_scope:
                self._reads[key] = (expires_at, _snapshot_key(listing))
        return listing

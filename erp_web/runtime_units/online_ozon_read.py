"""Ozon 全目录分页先行，详情和价格每批最多 100 件，最多 3 批并发，失败不回退为逐件请求。"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from threading import Event

from erp_web.marketplaces.online_sync import catalog_listing, raise_if_access_blocked
from erp_web.runtime_units.online_ozon_snapshot import build_listing
from erp_web.schemas.online_products import OnlineSyncBatch

BATCH_SIZE = 100
MAX_BATCH_WORKERS = 3


def index_rows(rows, key, *, allowed=None):
    if not isinstance(rows, list):
        raise ValueError("Ozon 响应缺少商品数组")
    indexed = {}
    for row in rows:
        remote_id = str(row.get(key) or "") if isinstance(row, dict) else ""
        if not remote_id.isdigit() or int(remote_id) <= 0 or remote_id in indexed or allowed is not None and remote_id not in allowed:
            raise ValueError("Ozon 响应商品身份缺失、重复或超出请求范围")
        indexed[remote_id] = row
    return indexed


def catalog_pages(adapter):
    seen = set()
    for visibility in ("ALL", "ARCHIVED"):
        cursor, cursors, branch_seen = "", set(), set()
        while True:
            response = adapter.request("/v3/product/list", {"filter": {"visibility": visibility}, "last_id": cursor, "limit": BATCH_SIZE})
            page = response.get("result") if isinstance(response, dict) else None
            if not isinstance(page, dict):
                raise ValueError("Ozon 列表响应缺少 result")
            indexed = index_rows(page.get("items"), "product_id")
            total = page.get("total")
            if not indexed:
                if isinstance(total, int) and len(branch_seen) < total:
                    raise ValueError("Ozon 列表提前返回空页，未完成目录读取")
                break
            if not indexed.keys() - branch_seen:
                raise ValueError("Ozon 列表重复返回同一页，未完成目录读取")
            branch_seen.update(indexed)
            yield [row for remote_id, row in indexed.items() if remote_id not in seen]
            seen.update(indexed)
            if isinstance(total, int) and len(branch_seen) >= total:
                break
            cursor = str(page.get("last_id") or "")
            if not cursor:
                if isinstance(total, int) and len(branch_seen) < total or len(indexed) >= BATCH_SIZE:
                    raise ValueError("Ozon 列表游标缺失，无法确认全量同步完成")
                break
            if cursor in cursors:
                raise ValueError("Ozon 列表游标重复，旧快照保留")
            cursors.add(cursor)


def price_rows(adapter, ids, visibility):
    indexed, cursor, cursors = {}, "", set()
    while True:
        response = adapter.request("/v5/product/info/prices", {
            "filter": {"product_id": [int(key) for key in ids], "visibility": visibility}, "cursor": cursor, "limit": BATCH_SIZE})
        if not isinstance(response, dict):
            raise ValueError("Ozon 价格响应无效")
        page = index_rows(response.get("items"), "product_id", allowed=set(ids))
        if indexed.keys() & page.keys():
            raise ValueError("Ozon 价格分页重复商品，未完成读取")
        indexed.update(page)
        total = response.get("total")
        if not page:
            if isinstance(total, int) and len(indexed) < total:
                raise ValueError("Ozon 价格提前返回空页，未完成读取")
            return indexed
        if len(indexed) == len(ids) or isinstance(total, int) and len(indexed) >= total:
            return indexed
        cursor = str(response.get("cursor") or "")
        if not cursor:
            return indexed
        if cursor in cursors:
            raise ValueError("Ozon 价格分页游标重复，未完成读取")
        cursors.add(cursor)


def read_batch(adapter, ids, *, phase="details"):
    result = OnlineSyncBatch(phase)
    try:
        response = adapter.request("/v3/product/info/list", {"product_id": [int(key) for key in ids]})
        info = index_rows(response.get("items") if isinstance(response, dict) else None, "id", allowed=set(ids))
    except Exception as exc:
        raise_if_access_blocked(exc)
        result.errors = dict.fromkeys(ids, str(exc))
        return result
    for remote_id in ids:
        if remote_id not in info:
            result.errors[remote_id] = "Ozon 未返回该商品的详情"
            result.missing_candidates.append(remote_id)
    # 归档商品单独使用 ARCHIVED，避免价格查询的可见性过滤遗漏商品；两类并存时并行拉取。
    groups = [
        (visibility, selected)
        for archived, visibility in ((False, "ALL"), (True, "ARCHIVED"))
        if (selected := [key for key, row in info.items() if bool(row.get("is_archived")) == archived])
    ]
    price_results: dict[str, dict] = {}
    price_errors: dict[str, str] = {}
    if len(groups) == 1:
        visibility, selected = groups[0]
        try:
            price_results[visibility] = price_rows(adapter, selected, visibility)
        except Exception as exc:
            raise_if_access_blocked(exc)
            price_errors[visibility] = str(exc)
    elif len(groups) > 1:
        stopped = Event()
        def _fetch_prices(vis, sel):
            if stopped.is_set():
                raise RuntimeError("本批次已因访问受阻而停止")
            try:
                return price_rows(adapter, sel, vis)
            except Exception as exc:
                try:
                    raise_if_access_blocked(exc)
                except Exception:
                    stopped.set()
                    raise
                raise
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ozon-sync-price") as pool:
            futures = {pool.submit(copy_context().run, _fetch_prices, vis, sel): vis for vis, sel in groups}
            for fut in futures:
                vis = futures[fut]
                try:
                    price_results[vis] = fut.result()
                except Exception as exc:
                    if stopped.is_set():
                        for other in futures:
                            other.cancel()
                    raise_if_access_blocked(exc)
                    price_errors[vis] = str(exc)
    for visibility, selected in groups:
        if visibility in price_errors:
            result.errors.update(dict.fromkeys(selected, price_errors[visibility]))
            continue
        prices = price_results[visibility]
        for remote_id in selected:
            try:
                if remote_id not in prices:
                    raise ValueError("Ozon 未返回该商品的价格，详情尚不完整")
                result.listings.append(build_listing(info[remote_id], prices[remote_id], account_id=adapter.account_id))
            except Exception as exc:
                result.errors[remote_id] = str(exc)
    return result


def _stream_batches(adapter, remote_ids, *, phase):
    chunks = [remote_ids[offset:offset + BATCH_SIZE] for offset in range(0, len(remote_ids), BATCH_SIZE)]
    if not chunks:
        return
    if len(chunks) == 1:
        yield read_batch(adapter, chunks[0], phase=phase)
        return
    remaining = iter(chunks)
    stopped = Event()

    def _run_chunk(chunk):
        if stopped.is_set():
            raise RuntimeError("本批次已因访问受阻而停止")
        try:
            return read_batch(adapter, chunk, phase=phase)
        except Exception as exc:
            try:
                raise_if_access_blocked(exc)
            except Exception:
                stopped.set()
                raise
            raise

    with ThreadPoolExecutor(max_workers=MAX_BATCH_WORKERS, thread_name_prefix="ozon-sync-read") as pool:
        pending = {}

        def submit():
            if stopped.is_set():
                return
            chunk = next(remaining, None)
            if chunk is not None:
                pending[pool.submit(copy_context().run, _run_chunk, chunk)] = chunk

        try:
            for _ in range(MAX_BATCH_WORKERS):
                submit()
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    try:
                        batch = future.result()
                    except Exception:
                        stopped.set()
                        for other in pending:
                            other.cancel()
                        raise
                    yield batch
                for _ in done:
                    submit()
        finally:
            stopped.set()
            for other in pending:
                other.cancel()


def sync_ozon(adapter, ids=None):
    discovered = []
    if ids is None:
        pages = catalog_pages(adapter)
    else:
        pages = [[{"product_id": key} for key in dict.fromkeys(ids)]]
    for page in pages:
        indexed = index_rows(page, "product_id")
        discovered.extend(indexed)
        yield OnlineSyncBatch("catalog", [catalog_listing(adapter.platform, adapter.account_id, key,
            model="ozon_product", seller_sku=str(row.get("offer_id") or "")) for key, row in indexed.items()])
    yield OnlineSyncBatch("details", discovery_complete=True)
    yield from _stream_batches(adapter, discovered, phase="details")


def sync_ozon_hot(adapter, remote_ids):
    """热数据快刷：跳过 /v3/product/list 全店翻页，按批并发刷新已有商品详情与价格。"""
    targets = [str(key) for key in dict.fromkeys(remote_ids) if str(key).isdigit() and int(str(key)) > 0]
    if not targets:
        return
    yield from _stream_batches(adapter, targets, phase="hot")


def reconcile_ozon_missing(adapter, remote_ids):
    """对账复核：按 /v3/product/info/list 确认缺失 product_id 是否已在平台删除。"""
    targets = [str(key) for key in dict.fromkeys(remote_ids) if str(key).isdigit() and int(str(key)) > 0]
    if not targets:
        return [], {}
    found: set[str] = set()
    for offset in range(0, len(targets), BATCH_SIZE):
        chunk = targets[offset:offset + BATCH_SIZE]
        response = adapter.request("/v3/product/info/list", {"product_id": [int(key) for key in chunk]})
        info = index_rows(response.get("items") if isinstance(response, dict) else None, "id", allowed=set(chunk))
        found.update(info)
    removed = [sku for sku in targets if sku not in found]
    return removed, {}

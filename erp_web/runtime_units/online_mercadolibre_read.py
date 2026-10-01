"""Mercado 目录先行，完整商品详情最多三路并发；不丢弃站点映射和净收入。"""
from contextvars import copy_context
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import quote, urlencode

from erp_web.marketplaces.online_sync import catalog_listing, raise_if_access_blocked
from erp_web.schemas.online_products import OnlineSyncBatch

MAX_REQUESTS = 3


def catalog_pages(adapter):
    cursor, seen, cursors = "", set(), set()
    while True:
        query = {"search_type": "scan", "limit": "50"}
        if cursor:
            query["scroll_id"] = cursor
        page = adapter.get(f"/marketplace/users/{quote(adapter.account_id, safe='')}/items/search?" + urlencode(query))
        if not isinstance(page, dict) or str(page.get("seller_id")) != adapter.account_id or not isinstance(page.get("results"), list):
            raise ValueError("Mercado 搜索响应身份或分页格式无效")
        total = page.get("paging", {}).get("total")
        fresh = []
        for item_id in page["results"]:
            if not isinstance(item_id, str) or not item_id.startswith("CBT") or not item_id[3:].isdigit():
                raise ValueError("全局账号搜索返回非 CBT 身份，不能猜测父商品")
            if item_id not in seen:
                fresh.append(item_id)
                seen.add(item_id)
        if not page["results"]:
            if isinstance(total, int) and len(seen) < total:
                raise ValueError("Mercado 搜索提前返回空页，未完成全量目录读取")
            return
        if not fresh:
            raise ValueError("Mercado 搜索重复返回同一页，已停止；旧快照保留")
        yield fresh
        if isinstance(total, int) and len(seen) >= total:
            return
        cursor = str(page.get("scroll_id") or "")
        if not cursor or cursor in cursors:
            raise ValueError("Mercado scan 游标缺失或重复，无法确认全量同步完成")
        cursors.add(cursor)


def sync_mercado(adapter, ids=None):
    discovered = []
    pages = [list(dict.fromkeys(ids))] if ids is not None else catalog_pages(adapter)
    for page in pages:
        if any(not isinstance(key, str) or not key.startswith("CBT") or not key[3:].isdigit() for key in page):
            raise ValueError("Mercado 同步范围必须为 CBT 商品身份")
        discovered.extend(page)
        yield OnlineSyncBatch("catalog", [catalog_listing(adapter.platform, adapter.account_id, key, model="pending") for key in page])
    yield OnlineSyncBatch("details", discovery_complete=True)
    # 窗口只放三个商品，不预先提交全店；生成器关闭后不再启动新请求。
    remaining = iter(discovered)
    with ThreadPoolExecutor(max_workers=MAX_REQUESTS, thread_name_prefix="mercado-sync-read") as pool:
        pending = {}

        def submit():
            remote_id = next(remaining, None)
            if remote_id is not None:
                pending[pool.submit(copy_context().run, adapter.read, remote_id)] = remote_id

        for _ in range(MAX_REQUESTS):
            submit()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            batch = OnlineSyncBatch("details")
            for future in done:
                remote_id = pending.pop(future)
                try:
                    batch.listings.append(future.result())
                except Exception as exc:
                    raise_if_access_blocked(exc)
                    batch.errors[remote_id] = str(exc)
            yield batch
            for _ in done:
                submit()

"""Ozon 全目录分页先行，详情和价格每批最多 100 件，失败不回退为逐件请求。"""
from erp_web.marketplaces.online_sync import catalog_listing, raise_if_access_blocked
from erp_web.runtime_units.online_ozon_snapshot import build_listing
from erp_web.schemas.online_products import OnlineSyncBatch

BATCH_SIZE = 100


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


def read_batch(adapter, ids):
    result = OnlineSyncBatch("details")
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
    # 归档商品单独使用 ARCHIVED，避免价格查询的可见性过滤遗漏商品。
    for archived, visibility in ((False, "ALL"), (True, "ARCHIVED")):
        selected = [key for key, row in info.items() if bool(row.get("is_archived")) == archived]
        if not selected:
            continue
        try:
            prices = price_rows(adapter, selected, visibility)
        except Exception as exc:
            raise_if_access_blocked(exc)
            result.errors.update(dict.fromkeys(selected, str(exc)))
            continue
        for remote_id in selected:
            try:
                if remote_id not in prices:
                    raise ValueError("Ozon 未返回该商品的价格，详情尚不完整")
                result.listings.append(build_listing(info[remote_id], prices[remote_id], account_id=adapter.account_id))
            except Exception as exc:
                result.errors[remote_id] = str(exc)
    return result


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
    for offset in range(0, len(discovered), BATCH_SIZE):
        yield read_batch(adapter, discovered[offset:offset + BATCH_SIZE])

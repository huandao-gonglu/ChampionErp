"""在线列表的纯查询投影：按平台组合关系分组、筛选和分页，不改变刊登身份。"""
from __future__ import annotations

from typing import Any

from erp_web.schemas.online_products import OnlineListing, OnlineProductGroup, digest


def _group_identity(listing: OnlineListing) -> str:
    """只使用平台返回的组合身份；标题、SKU 前缀和本地草稿不作为合并依据。"""
    if listing.platform == "yandex":
        value = listing.snapshot.get("offer", {}).get("groupId")
    elif listing.platform == "mercadolibre" and listing.model == "user_products":
        value = listing.snapshot.get("user_product", {}).get("family_id")
    else:
        value = None
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip():
        return ""
    return "group-" + digest([listing.platform, listing.account_id, str(value)])[:32]


def listing_page(records: list[OnlineListing], *, query: str, status: str, market: str,
                 page: int, per_page: int = 25) -> dict[str, Any]:
    groups: dict[str, list[OnlineListing]] = {}
    for listing in records:
        key = _group_identity(listing) or "single-" + listing.id
        groups.setdefault(key, []).append(listing)

    needle = query.strip().casefold()
    matched_groups: list[tuple[OnlineProductGroup, list[OnlineListing]]] = []
    for key, members in groups.items():
        matched = [row for row in members
                   if (not needle or needle in f"{row.title} {row.seller_sku} {row.remote_id}".casefold())
                   and (not status or row.raw_status == status)
                   and (not market or any(m.site_id == market for m in row.markets))]
        if not matched:
            continue
        first = members[0]
        group = OnlineProductGroup(id=key, title=first.title or first.remote_id,
                                   kind="group" if len(members) > 1 else "single",
                                   item_ids=[row.id for row in matched], total_count=len(members))
        matched_groups.append((group, matched))

    page = max(1, page)
    selected = matched_groups[(page - 1) * per_page:page * per_page]
    return {
        "items": [row.model_dump(exclude={"snapshot"}) for _, members in selected for row in members],
        "groups": [group.model_dump() for group, _ in selected],
        "total": len(matched_groups),
        "listing_total": sum(len(members) for _, members in matched_groups),
        "page": page,
        "per_page": per_page,
    }

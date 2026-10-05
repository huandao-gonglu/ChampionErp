"""在线列表的纯查询投影：按平台组合关系分组、筛选和分页，不改变刊登身份。"""
from __future__ import annotations

from typing import Any, Literal

from erp_web.schemas.online_product_capabilities import OnlineGroupSummary, OnlineListingSummary
from erp_web.schemas.online_products import OnlineFeedbackSummary, OnlineListing, OnlineProductGroup, digest


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


def _matching_groups(records: list[OnlineListing], *, query: str, status: str, market: str,
                     group_id: str = "") -> list[tuple[OnlineProductGroup, list[OnlineListing]]]:
    """页面与工具共用组合身份、顺序和筛选，避免两种入口解释不同的第一项。"""
    groups: dict[str, list[OnlineListing]] = {}
    for listing in records:
        key = _group_identity(listing) or "single-" + listing.id
        groups.setdefault(key, []).append(listing)

    needle = query.strip().casefold()
    matched_groups: list[tuple[OnlineProductGroup, list[OnlineListing]]] = []
    for key, members in groups.items():
        if group_id and key != group_id:
            continue
        matched = [row for row in members
                   if (not needle or needle in f"{row.title} {row.seller_sku} {row.remote_id}".casefold())
                   and (not status or row.raw_status == status)
                   and (not market or any(m.site_id == market for m in row.markets))]
        if not matched:
            continue
        first = members[0]
        group = OnlineProductGroup(id=key, title=first.title or first.remote_id,
                                   kind="group" if len(members) > 1 else "single",
                                   item_ids=[row.id for row in matched], total_count=len(members),
                                   feedback_summary=OnlineFeedbackSummary(
                                       affected_sku_count=sum(bool(row.platform_issues) for row in members),
                                       error_count=sum(issue.severity == "error" for row in members for issue in row.platform_issues),
                                       warning_count=sum(issue.severity == "warning" for row in members for issue in row.platform_issues)))
        matched_groups.append((group, matched))
    return matched_groups


def listing_page(records: list[OnlineListing], *, query: str, status: str, market: str,
                 page: int, per_page: int = 25) -> dict[str, Any]:
    matched_groups = _matching_groups(records, query=query, status=status, market=market)
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


def listing_summary_page(records: list[OnlineListing], *, query: str, status: str, market: str,
                         page: int, limit: int, view: Literal["groups", "listings"],
                         group_id: str = "") -> dict[str, Any]:
    """先按节点或刊登分页，再投影摘要；不序列化整组详情或完整成员 ID 列表。"""
    matched = _matching_groups(records, query=query, status=status, market=market, group_id=group_id)
    listing_total = sum(len(members) for _, members in matched)
    offset = (page - 1) * limit
    selected: list[tuple[OnlineProductGroup, list[OnlineListing], list[OnlineListing]]] = []
    if view == "groups":
        total = len(matched)
        selected = [(group, members, members[:1]) for group, members in matched[offset:offset + limit]]
    else:
        total = listing_total
        position = 0
        for group, members in matched:
            start, end = max(0, offset - position), min(len(members), offset + limit - position)
            if start < end:
                selected.append((group, members, members[start:end]))
            position += len(members)

    fields = set(OnlineListingSummary.model_fields) - {"group_id"}
    return {
        "view": view,
        "items": [{**row.model_dump(include=fields), "group_id": group.id}
                  for group, _, rows in selected for row in rows],
        "groups": [OnlineGroupSummary(
            id=group.id, title=group.title, kind=group.kind, total_count=group.total_count,
            matched_count=len(members), representative_id=members[0].id,
            feedback_summary=group.feedback_summary,
        ).model_dump() for group, members, _ in selected],
        "total": total, "listing_total": listing_total, "page": page, "per_page": limit,
        "next_page": page + 1 if offset + limit < total else None,
    }

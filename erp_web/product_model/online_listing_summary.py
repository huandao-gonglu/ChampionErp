"""在线商品在当前仓库范围内的状态汇总，不发起请求或改写持久记录。"""
from __future__ import annotations

from erp_web.schemas.online_products import PlatformIssue


def yandex_issues(payload, *, source: str = "card") -> list[PlatformIssue]:
    """保留平台错误和警告的原文及说明，不把警告推断成商品不可售。"""
    if not isinstance(payload, dict):
        return []
    return [
        PlatformIssue(
            severity=severity,
            source=source,
            code=str(row.get("code") or ""),
            message=str(row.get("message") or row.get("comment") or "平台未提供反馈说明"),
            comment=str(row.get("comment") or ""),
        )
        for field, severity in (("errors", "error"), ("warnings", "warning"))
        for row in payload.get(field, []) or []
        if isinstance(row, dict)
    ]


_YANDEX_STATUS_PRIORITY = {
    status: rank for rank, status in enumerate((
        'PUBLISHED', 'CHECKING', 'CREATING_CARD', 'NO_CARD',
        'REJECTED_BY_MARKET', 'DISABLED_AUTOMATICALLY', 'unknown',
        'DISABLED_BY_PARTNER', 'NO_STOCKS', 'ARCHIVED',
    ))
}


def summarize_yandex_status(listing):
    """有在售就显示在售，审核和待处理优先于缺货；库存数量不推断上架结果。"""
    if listing.archived:
        listing.raw_status, listing.sale_state = 'ARCHIVED', 'unknown'
        return listing
    statuses = []
    hidden = listing.snapshot.get('hidden_campaigns', {})
    authorization_campaign = listing.account_id.partition(':')[2]
    markets = {market.id for market in listing.markets}
    for market in listing.markets:
        own_hidden = listing.snapshot.get('hidden', False) if market.id == authorization_campaign else False
        paused = hidden.get(market.id, own_hidden) or 'hidden' in market.raw_sub_status
        statuses.append('DISABLED_BY_PARTNER' if paused else market.raw_status or 'unknown')
    warehouses = {str(row['id']): row.get('campaign_id', '')
                  for row in listing.snapshot.get('warehouse_catalog', [])}
    for stock in listing.stocks:
        campaign = stock.campaign_id or warehouses.get(stock.id, '')
        if stock.id == 'campaign' and len(markets) == 1:
            campaign = next(iter(markets))
        if not campaign or campaign not in markets:
            statuses.append('DISABLED_BY_PARTNER' if stock.sale_state == 'paused' else stock.raw_status or 'unknown')
    if not statuses:
        statuses = ['DISABLED_BY_PARTNER' if listing.sale_state == 'paused' else listing.raw_status or 'unknown']
    # 缺少其他仓库的状态时保留“待核实”，不拿默认店铺的缺货代表全部仓库。
    status = min(statuses, key=lambda value: _YANDEX_STATUS_PRIORITY.get(value, _YANDEX_STATUS_PRIORITY['unknown']))
    listing.raw_status = status
    listing.sale_state = 'active' if status == 'PUBLISHED' else 'paused' if status == 'DISABLED_BY_PARTNER' else 'unknown'
    return listing

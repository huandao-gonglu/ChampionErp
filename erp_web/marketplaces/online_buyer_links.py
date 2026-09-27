"""从官方商品响应提取买家链接；不访问网络，不推测商品地址。"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from erp_web.schemas.online_products import BuyerLink


_MERCADO_SITES = {
    "MLM": "墨西哥", "MLB": "巴西", "MLC": "智利", "MCO": "哥伦比亚",
    "MLA": "阿根廷", "MLU": "乌拉圭", "MPE": "秘鲁", "MEC": "厄瓜多尔",
}


def _append_link(links: list[BuyerLink], url: Any, label: str, site_id: str) -> None:
    if not isinstance(url, str) or not url.strip():
        return
    try:
        link = BuyerLink(url=url.strip(), label=label, site_id=site_id)
    except ValidationError:
        return
    if all(existing.url != link.url for existing in links):
        links.append(link)


def yandex_buyer_links(mapping: dict[str, Any]) -> list[BuyerLink]:
    """保留 B2C 链接及其卖家参数，不把 B2B 入口当成普通买家页面。"""
    links: list[BuyerLink] = []
    rows = mapping.get("showcaseUrls")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("showcaseType") == "B2C":
            _append_link(links, row.get("showcaseUrl"), "Yandex Market", "B2C")
    return links


def mercado_buyer_links(children: dict[str, Any]) -> list[BuyerLink]:
    """使用站点子刊登的 permalink；同站点的不同刊登也保留明确身份。"""
    links: list[BuyerLink] = []
    for child in children.values():
        if not isinstance(child, dict):
            continue
        site_id = str(child.get("site_id") or "")
        item_id = str(child.get("id") or "")
        if not site_id or site_id == "CBT" or not item_id:
            continue
        label = f"{_MERCADO_SITES.get(site_id, site_id)} · {item_id}"
        _append_link(links, child.get("permalink"), label, site_id)
    return links

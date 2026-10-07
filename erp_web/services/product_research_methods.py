"""选品数据源适配；只返回可以追溯到真实 ASIN 的商品事实。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable

from erp_web.schemas.product_research import HotProductCandidate
from erp_web.services.sorftime_client import SorftimeClient, number, rows

RunProgressEvent = str | dict[str, Any]
RunProgressCallback = Callable[[RunProgressEvent], None]


class ProductResearchSearchMethod(ABC):
    last_diagnostics: dict[str, Any]

    @abstractmethod
    def run(self, **kwargs: Any) -> list[HotProductCandidate]:
        """查询一个市场的一页商品，不自动扩词、翻页或补齐数量。"""


def amazon_candidates(data: Any, keyword: str, limit: int) -> list[HotProductCandidate]:
    import re

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    candidates = []
    seen = set()
    for row in rows(data):
        asin = str(row.get("Asin") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", asin) or not row.get("Title") or asin in seen:
            continue
        seen.add(asin)
        price = number(row.get("Price"))
        candidate = {
            "id": f"sorftime:amazon-us:{asin}", "asin": asin,
            "title": str(row["Title"]), "image_url": str(row.get("Photo") or ""),
            "rank": len(candidates) + 1, "source_url": f"https://www.amazon.com/dp/{asin}",
            "market_id": "amazon-us", "platform": "amazon", "site": "amazon.com",
            "keyword": keyword, "rating": number(row.get("Ratings")),
            "review_count": number(row.get("RatingsCount")),
            "monthly_sales": number(row.get("ListingSalesVolumeOfMonth")),
            "source_name": "Sorftime", "collected_at": now,
            "data_updated_at": str(row.get("UpdateDate") or ""),
        }
        if price is not None:
            candidate["price"] = {"amount": price / 100, "currency": "USD"}
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


class SorftimeSearchMethod(ProductResearchSearchMethod):
    def run(self, *, market, method, keywords, limit, **kwargs):
        if market.get("platform") != "amazon" or market.get("site") != "amazon.com":
            raise ValueError("当前 Sorftime 选品仅支持 Amazon US，请选择美国站。")
        if len(keywords) != 1 or not keywords[0].strip():
            raise ValueError("请输入一个明确的商品关键词。")
        client = SorftimeClient(method.get("config_json", {}).get("api_key", ""))
        self.last_diagnostics = {}
        try:
            data = client.call("ProductSearchFromName", 1, {"Name": keywords[0], "PageIndex": 1})
            result = amazon_candidates(data, keywords[0], limit)
            for item in result:
                item["market_id"] = market["id"]
            self.last_diagnostics.update(raw_items_found=len(rows(data)), items_filtered=len(rows(data)) - len(result))
            return result
        finally:
            self.last_diagnostics["quota_receipts"] = client.receipts


def search_method_for(method: dict[str, Any]) -> ProductResearchSearchMethod:
    if method.get("config_json", {}).get("provider_strategy") == "sorftime":
        return SorftimeSearchMethod()
    raise ValueError("此选品数据源尚未适配，请在设置中启用 Sorftime。")


__all__ = ["ProductResearchSearchMethod", "SorftimeSearchMethod", "amazon_candidates", "search_method_for"]

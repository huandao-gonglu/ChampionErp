"""美国站相关热搜词：显式查询一页，保留额度回执并复用一天内的结果。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import sqlite3
import time
from typing import Any

from erp_web.schemas.product_research import ProductResearchKeyword, ProductResearchKeywordResponse
from erp_web.services.sorftime_client import configured_client, number, rows
from erp_web.stores.research_keyword_store import ResearchKeywordStore

CACHE_SECONDS = 24 * 60 * 60


def _signed_number(value: Any) -> float | None:
    # 排名涨跌允许负值；不能沿用销量字段的负数缺失规则。
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def keyword_items(data: Any) -> list[ProductResearchKeyword]:
    result: list[ProductResearchKeyword] = []
    seen: set[str] = set()
    for row in rows(data):
        keyword = str(row.get("Keyword") or "").strip()
        if not keyword or keyword.casefold() in seen:
            continue
        seen.add(keyword.casefold())
        result.append({"keyword": keyword, "chinese_name": str(row.get("KeywordCNName") or ""),
                       "search_volume": number(row.get("SearchVolume")), "rank": number(row.get("Rank")),
                       "rank_change": _signed_number(row.get("RankChangeOfWeekly")),
                       "data_updated_at": str(row.get("Update") or "")})
    return result[:100]


def search_keywords(body: dict[str, Any], config: dict[str, Any], store: ResearchKeywordStore) -> ProductResearchKeywordResponse:
    keyword = " ".join(str(body.get("keyword") or "").split())
    market_id = str(body.get("market_id") or "").strip()
    if not keyword or len(keyword) > 200:
        raise ValueError("请输入 1～200 个字符的商品方向，建议使用英文。")
    market = next((m for m in config.get("target_markets", []) if m.get("id") == market_id), {})
    if market.get("platform") != "amazon" or market.get("site") != "amazon.com":
        raise ValueError("相关热搜词目前仅支持 Amazon US。")
    refresh = body.get("refresh", False)
    if not isinstance(refresh, bool):
        raise ValueError("重新获取参数必须为布尔值。")
    client = configured_client(config)
    # 缓存与凭据隔离，磁盘上不保存凭据本身；查询参数固定为第一页 100 条。
    key = hashlib.sha256(f"KeywordQuery:1:100:{client.api_key}:{keyword.casefold()}".encode()).hexdigest()
    token, cached = store.reserve(key, refresh=refresh, now=time.time())
    if cached is not None:
        return {**cached, "market_id": market_id, "keyword": keyword, "cached": True, "quota_receipts": []}
    response: ProductResearchKeywordResponse = {
        "ok": False, "error": "", "market_id": market_id, "keyword": keyword, "items": [],
        "cached": False, "fetched_at": "", "expires_at": "", "quota_receipts": [],
    }
    try:
        data = client.call("KeywordQuery", 1, {"Pattern": {"Keyword": keyword}, "PageIndex": 1, "PageSize": 100})
        response["items"] = keyword_items(data)
        now = time.time()
        response.update(ok=True, fetched_at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                        expires_at=datetime.fromtimestamp(now + CACHE_SECONDS, timezone.utc).isoformat(),
                        quota_receipts=client.receipts)
        store.save(key, token, response, now + CACHE_SECONDS)
    except ValueError as exc:
        response.update(ok=False, error=str(exc), items=[], quota_receipts=client.receipts)
    except (OSError, sqlite3.Error):
        response.update(ok=False, error="热搜词结果保存失败，请核对本次额度回执后再操作。", items=[], quota_receipts=client.receipts)
    finally:
        try:
            store.release(key, token)
        except sqlite3.Error:
            response.update(ok=False, error="热搜词查询占位释放失败，请稍后查看缓存并核对额度回执。", items=[], quota_receipts=client.receipts)
    return response


__all__ = ["search_keywords"]

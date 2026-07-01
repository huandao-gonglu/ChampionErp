"""Search-method runtime contracts for product research.

Target markets own search-method bindings. Each concrete search method must
implement ProductResearchSearchMethod.run and return HotProductCandidate rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from erp_web.schemas.product_research import HotProductCandidate
from erp_web.services import ai_gateway, ai_model_config, ai_prompt_templates


RunProgressEvent = str | dict[str, Any]
RunProgressCallback = Callable[[RunProgressEvent], None]


class ProductResearchSearchMethod(ABC):
    """Abstract product-research search method."""

    last_diagnostics: dict[str, Any]

    @abstractmethod
    def run(
        self,
        *,
        market: dict[str, Any],
        method: dict[str, Any],
        binding: dict[str, Any],
        keywords: list[str],
        limit: int,
        config: dict[str, Any],
        app_dir: Path | str = ".",
        app_config: dict[str, Any] | None = None,
        progress_callback: RunProgressCallback | None = None,
    ) -> list[HotProductCandidate]:
        """Return HotProductCandidate rows for one target-market binding."""


DEFAULT_CURRENCY_BY_MARKET = {
    "amazon-us": "USD",
    "amazon-uk": "GBP",
    "amazon-ca": "CAD",
    "amazon-au": "AUD",
}


def _stable_digest(value: Any, length: int = 12) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def _int_value(value: Any, default: int, min_value: int = 0, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    number = max(number, min_value)
    if max_value is not None:
        number = min(number, max_value)
    return number


def _float_value(value: Any, default: float = 0.0, min_value: float = 0.0, max_value: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    number = max(number, min_value)
    if max_value is not None:
        number = min(number, max_value)
    return number


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _first_matching_keyword(title: str, keywords: list[str]) -> str:
    lowered = title.lower()
    for keyword in keywords:
        candidate = keyword.strip()
        if candidate and candidate.lower() in lowered:
            return candidate
    return keywords[0] if keywords else ""


def _price_value(value: Any, fallback_currency: str) -> dict[str, Any] | None:
    raw = value if isinstance(value, dict) else {}
    amount = _float_value(raw.get("amount"), 0.0)
    if amount <= 0:
        return None
    currency = str(raw.get("currency") or fallback_currency or "USD").strip().upper()
    return {"amount": amount, "currency": currency}


def _image_url_value(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    lowered = url.lower()
    if not lowered.startswith(("https://", "http://")):
        return ""
    if lowered.startswith("http://"):
        return ""
    if lowered.startswith(("data:", "blob:")):
        return ""
    return url


def _normalize_ai_candidate(
    value: Any,
    *,
    market: dict[str, Any],
    method: dict[str, Any],
    keywords: list[str],
    index: int,
    require_source_url: bool,
    require_image_url: bool,
) -> HotProductCandidate | None:
    raw = value if isinstance(value, dict) else {}
    title = str(raw.get("title") or raw.get("name") or "").strip()
    source_url = str(raw.get("source_url") or raw.get("sourceUrl") or raw.get("url") or raw.get("product_url") or "").strip()
    image_url = _image_url_value(raw.get("image_url") or raw.get("imageUrl") or raw.get("image"))
    if not title or (require_source_url and not source_url) or (require_image_url and not image_url):
        return None

    market_id = str(market.get("id") or market.get("market_id") or market.get("marketId") or "").strip()
    platform = str(market.get("platform") or "").strip().lower()
    site = str(market.get("site") or "").strip().lower()
    rank = _int_value(raw.get("rank") or raw.get("order"), index + 1, 1, 100000)
    keyword = str(raw.get("keyword") or "").strip() or _first_matching_keyword(title, keywords)
    fallback_currency = DEFAULT_CURRENCY_BY_MARKET.get(market_id, "USD")

    item: HotProductCandidate = {
        "id": str(raw.get("id") or f"hot_{market_id}_{_stable_digest([title, source_url, keyword, rank])}").strip(),
        "title": title,
        "image_url": image_url,
        "rank": rank,
        "source_url": source_url,
        "market_id": market_id,
        "platform": platform,
        "site": site,
        "keyword": keyword,
        "rating": _float_value(raw.get("rating"), 0.0, 0.0, 5.0),
        "review_count": _int_value(raw.get("review_count", raw.get("reviewCount")), 0, 0),
        "hot_score": _float_value(raw.get("hot_score", raw.get("hotScore")), 0.0, 0.0, 100.0),
        "source_name": str(method.get("name") or method.get("id") or "AI 搜索").strip(),
        "collected_at": _utc_now(),
    }
    price = _price_value(raw.get("price"), fallback_currency)
    if price:
        item["price"] = price
    return item


def _candidate_unique_key(item: HotProductCandidate) -> str:
    return str(item.get("source_url") or item.get("id") or item.get("title") or "").strip()


def _json_rows_from_line(line: str) -> list[dict[str, Any]]:
    text = str(line or "").strip()
    if not text or text.startswith("```"):
        return []
    if text.startswith("- "):
        text = text[2:].strip()
    if text.endswith(","):
        text = text[:-1].rstrip()
    if "{" in text and "}" in text and not text.startswith("{"):
        text = text[text.find("{") : text.rfind("}") + 1]
    try:
        payload = json.loads(text)
    except Exception:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    results: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("items"), list):
            results.extend(item for item in row["items"] if isinstance(item, dict))
        elif isinstance(row.get("item"), dict):
            results.append(row["item"])
        elif row.get("title") or row.get("name") or row.get("source_url") or row.get("sourceUrl"):
            results.append(row)
    return results


class _JsonlCandidateAccumulator:
    def __init__(
        self,
        *,
        market: dict[str, Any],
        method: dict[str, Any],
        keywords: list[str],
        limit: int,
        require_source_url: bool,
        require_image_url: bool,
    ) -> None:
        self.market = market
        self.method = method
        self.keywords = keywords
        self.limit = limit
        self.require_source_url = require_source_url
        self.require_image_url = require_image_url
        self.buffer = ""
        self.raw_count = 0
        self.dropped_missing_title = 0
        self.dropped_missing_source_url = 0
        self.dropped_missing_image_url = 0
        self.items: list[HotProductCandidate] = []
        self.seen: set[str] = set()

    def feed(self, token: str) -> list[HotProductCandidate]:
        self.buffer += token
        if "\n" not in self.buffer:
            return []
        lines = self.buffer.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.buffer = lines.pop()
        else:
            self.buffer = ""
        return self._consume_lines(lines)

    def flush(self) -> list[HotProductCandidate]:
        if not self.buffer:
            return []
        line = self.buffer
        self.buffer = ""
        return self._consume_lines([line])

    def _consume_lines(self, lines: list[str]) -> list[HotProductCandidate]:
        emitted: list[HotProductCandidate] = []
        for line in lines:
            for row in _json_rows_from_line(line):
                if len(self.items) >= self.limit:
                    return emitted
                self.raw_count += 1
                title = str(row.get("title") or row.get("name") or "").strip()
                source_url = str(row.get("source_url") or row.get("sourceUrl") or row.get("url") or row.get("product_url") or "").strip()
                image_url = _image_url_value(row.get("image_url") or row.get("imageUrl") or row.get("image"))
                if not title:
                    self.dropped_missing_title += 1
                elif self.require_source_url and not source_url:
                    self.dropped_missing_source_url += 1
                elif self.require_image_url and not image_url:
                    self.dropped_missing_image_url += 1
                item = _normalize_ai_candidate(
                    row,
                    market=self.market,
                    method=self.method,
                    keywords=self.keywords,
                    index=self.raw_count - 1,
                    require_source_url=self.require_source_url,
                    require_image_url=self.require_image_url,
                )
                if item is None:
                    continue
                key = _candidate_unique_key(item)
                if key and key in self.seen:
                    continue
                if key:
                    self.seen.add(key)
                self.items.append(item)
                emitted.append(item)
        return emitted


def _market_prompt_context(market: dict[str, Any], keywords: list[str], limit: int) -> dict[str, Any]:
    market_id = str(market.get("id") or market.get("market_id") or market.get("marketId") or "").strip()
    display_name = str(market.get("display_name") or market.get("displayName") or market_id).strip()
    platform = str(market.get("platform") or "").strip()
    site = str(market.get("site") or "").strip()
    currency = str(market.get("currency") or DEFAULT_CURRENCY_BY_MARKET.get(market_id, "USD")).strip().upper() or "USD"
    return {
        "keywords": ", ".join(keywords),
        "keyword": ", ".join(keywords),
        "limit": limit,
        "market_id": market_id,
        "marketId": market_id,
        "display_name": display_name,
        "displayName": display_name,
        "platform": platform,
        "site": site,
        "currency": currency,
    }


def _ai_search_prompt(template: str, market: dict[str, Any], keywords: list[str], limit: int) -> str:
    return ai_prompt_templates.render_prompt_template(template, _market_prompt_context(market, keywords, limit))


def _should_retry_ai_search_without_stream(exc: ai_gateway.AIHTTPError) -> bool:
    if exc.status_code in {400, 406, 415}:
        return True
    if exc.status_code != 403:
        return False
    detail = f"{exc.detail} {exc.reason}".lower()
    return any(marker in detail for marker in ("stream", "streaming", "event-stream", "sse"))


class AiSearchMethod(ProductResearchSearchMethod):
    def __init__(self) -> None:
        self.last_diagnostics: dict[str, Any] = {}

    def run(
        self,
        *,
        market: dict[str, Any],
        method: dict[str, Any],
        binding: dict[str, Any],
        keywords: list[str],
        limit: int,
        config: dict[str, Any],
        app_dir: Path | str = ".",
        app_config: dict[str, Any] | None = None,
        progress_callback: RunProgressCallback | None = None,
    ) -> list[HotProductCandidate]:
        method_config = method.get("config_json") if isinstance(method.get("config_json"), dict) else {}
        binding_config = binding.get("config_json") if isinstance(binding.get("config_json"), dict) else {}
        runtime = config.get("provider_runtime") if isinstance(config.get("provider_runtime"), dict) else {}
        max_items = _int_value(method_config.get("max_items"), limit, 1, 100)
        result_limit = min(limit, max_items)
        model_id = str(binding_config.get("ai_model_id") or method_config.get("ai_model_id") or method_config.get("model_id") or "").strip()
        timeout_seconds = _int_value(binding_config.get("timeout_seconds") or method_config.get("timeout_seconds") or runtime.get("source_timeout_seconds"), 120, 30, 300)
        stream_enabled = _bool_value(binding_config.get("stream", method_config.get("stream")), True)
        require_source_url = _bool_value(method_config.get("require_source_url"), True)
        require_image_url = _bool_value(method_config.get("require_image_url"), True)

        model = ai_gateway.resolve_model_for_use_case(app_dir, app_config, "research.web_search", model_id=model_id)
        resolved_model_id = str(model.get("id") or model_id or "").strip()
        api_style = ai_model_config.normalize_api_style(model.get("api_style"))
        stream_fallback_used = False
        self.last_diagnostics = {
            "ai_model_id": resolved_model_id,
            "api_style": api_style,
            "stream_enabled": stream_enabled,
            "stream_fallback_used": stream_fallback_used,
        }
        capabilities = ai_model_config.normalize_capabilities(model.get("capabilities"))
        if ai_model_config.CAP_WEB_SEARCH not in capabilities:
            raise RuntimeError("AI 搜索需要选择一个支持 web_search 的 AI 模型。")
        prompt_pair = ai_prompt_templates.load_ai_use_case_prompt_pair(app_dir, app_config, "research.web_search")
        user_prompt = str(binding.get("prompt") or "").strip()
        if not user_prompt:
            user_prompt = _ai_search_prompt(prompt_pair["user"], market, keywords, result_limit)

        stream_parts: list[str] = []
        streamed = _JsonlCandidateAccumulator(
            market=market,
            method=method,
            keywords=keywords,
            limit=result_limit,
            require_source_url=require_source_url,
            require_image_url=require_image_url,
        )

        def handle_token(token: str) -> None:
            if not progress_callback:
                return
            stream_parts.append(token)
            for item in streamed.feed(token):
                progress_callback({"type": "candidate", "item": item})
            stream_text = "".join(stream_parts).strip()
            if len(stream_text) > 1000:
                stream_text = "..." + stream_text[-1000:]
            if stream_text:
                progress_callback(f"AI 正在返回结果：{stream_text}")

        messages = [
            {
                "role": "system",
                "content": prompt_pair["system"],
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ]
        request_temperature = _float_value(method_config.get("temperature"), 0.2, 0.0, 2.0)
        request_max_tokens = _int_value(method_config.get("max_tokens"), 1800, 256, 12000)

        def request_ai_json(enabled_stream: bool) -> dict[str, Any]:
            return ai_gateway.chat_json(
                app_dir,
                app_config,
                "research.web_search",
                messages,
                model_id=model_id,
                temperature=request_temperature,
                max_tokens=request_max_tokens,
                timeout_seconds=timeout_seconds,
                stream=enabled_stream,
                token_callback=handle_token if enabled_stream else None,
                response_format=False,
            )

        try:
            parsed = request_ai_json(stream_enabled)
        except ai_gateway.AIHTTPError as exc:
            if not stream_enabled or not _should_retry_ai_search_without_stream(exc):
                raise
            stream_fallback_used = True
            stream_enabled = False
            self.last_diagnostics.update(
                {
                    "stream_enabled": stream_enabled,
                    "stream_fallback_used": stream_fallback_used,
                }
            )
            if progress_callback:
                progress_callback("AI 搜索流式请求被拒绝，正在改用非流式请求重试。")
            parsed = request_ai_json(False)
        if progress_callback:
            for item in streamed.flush():
                progress_callback({"type": "candidate", "item": item})
        raw_items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
        normalized: list[HotProductCandidate] = []
        dropped_missing_title = 0
        dropped_missing_source_url = 0
        dropped_missing_image_url = 0
        for index, raw_item in enumerate(raw_items[:result_limit]):
            raw = raw_item if isinstance(raw_item, dict) else {}
            title = str(raw.get("title") or raw.get("name") or "").strip()
            source_url = str(raw.get("source_url") or raw.get("sourceUrl") or raw.get("url") or raw.get("product_url") or "").strip()
            image_url = _image_url_value(raw.get("image_url") or raw.get("imageUrl") or raw.get("image"))
            if not title:
                dropped_missing_title += 1
            elif require_source_url and not source_url:
                dropped_missing_source_url += 1
            elif require_image_url and not image_url:
                dropped_missing_image_url += 1
            item = _normalize_ai_candidate(
                raw_item,
                market=market,
                method=method,
                keywords=keywords,
                index=index,
                require_source_url=require_source_url,
                require_image_url=require_image_url,
            )
            if item is not None:
                normalized.append(item)
        combined: list[HotProductCandidate] = []
        seen: set[str] = set()
        for item in [*streamed.items, *normalized]:
            key = _candidate_unique_key(item)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            combined.append(item)
            if len(combined) >= result_limit:
                break
        filtered_count = len(raw_items[:result_limit]) - len(normalized)
        dropped_missing_title += streamed.dropped_missing_title
        dropped_missing_source_url += streamed.dropped_missing_source_url
        dropped_missing_image_url += streamed.dropped_missing_image_url
        streamed_filtered_count = (
            streamed.dropped_missing_title
            + streamed.dropped_missing_source_url
            + streamed.dropped_missing_image_url
        )
        raw_count = max(len(raw_items), streamed.raw_count)
        total_filtered_count = filtered_count + streamed_filtered_count
        if total_filtered_count:
            reasons: list[str] = []
            if dropped_missing_title:
                reasons.append(f"{dropped_missing_title} 条缺少商品标题")
            if dropped_missing_source_url:
                reasons.append(f"{dropped_missing_source_url} 条缺少来源 URL")
            if dropped_missing_image_url:
                reasons.append(f"{dropped_missing_image_url} 条缺少图片 URL")
            reason_text = "，".join(reasons) or "字段不完整"
            source_label = "AI 流式返回" if not raw_items and streamed.raw_count else "AI 返回"
            diagnostic_message = f"{source_label} {raw_count} 条原始候选，整理后 {len(combined)} 条；过滤原因：{reason_text}。"
        elif not raw_items:
            diagnostic_message = (
                f"AI 流式返回 {streamed.raw_count} 条原始候选，整理后 {len(combined)} 条。"
                if streamed.raw_count
                else "AI 返回了 0 条原始候选。"
            )
        else:
            diagnostic_message = f"AI 返回 {raw_count} 条原始候选，整理后 {len(combined)} 条。"
        self.last_diagnostics = {
            "raw_items_found": raw_count,
            "items_filtered": total_filtered_count,
            "dropped_missing_title": dropped_missing_title,
            "dropped_missing_source_url": dropped_missing_source_url,
            "dropped_missing_image_url": dropped_missing_image_url,
            "ai_model_id": resolved_model_id,
            "api_style": api_style,
            "stream_enabled": stream_enabled,
            "stream_fallback_used": stream_fallback_used,
            "streamed_items_found": len(streamed.items),
            "diagnostic_message": diagnostic_message,
        }
        if progress_callback:
            progress_callback(diagnostic_message)
        return sorted(combined, key=lambda item: int(item.get("rank") or 999999))[:result_limit]


def search_method_for(method: dict[str, Any]) -> ProductResearchSearchMethod:
    source_type = str(method.get("source_type") or method.get("sourceType") or "").strip()
    config_json = method.get("config_json") if isinstance(method.get("config_json"), dict) else {}
    strategy = str(config_json.get("provider_strategy") or method.get("provider_strategy") or "").strip()
    if source_type == "ai_search" or strategy == "ai_web_search":
        return AiSearchMethod()
    raise ValueError(f"搜索手段 {method.get('name') or method.get('id') or ''} 暂不支持生成候选商品。")


__all__ = [
    "AiSearchMethod",
    "ProductResearchSearchMethod",
    "search_method_for",
]

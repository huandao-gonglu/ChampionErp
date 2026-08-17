"""绑定式平台类目检索对象。

调用方只在任务入口按当前平台创建一次对象。Mercado Libre 使用关键字发现；
Ozon 同一对象同时支持人工关键字搜索与自动匹配的树导航，后续调用均不再传递
或判断 platform/site。
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping

from erp_web.marketplaces.category_provider import CategoryProvider, CategorySearcher
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.category import (
    CategoryBrowseResult,
    CategoryCandidate,
    CategorySearchResult,
    CategoryTreeNode,
)

from .category_providers import require_category_provider


class CategorySearchError(AiToolExecutionError):
    """平台类目搜索失败；零候选是正常结果，不使用异常表示。"""


def _classified_error(exc: Exception) -> CategorySearchError:
    if isinstance(exc, CategorySearchError):
        return exc
    message = str(exc) or exc.__class__.__name__
    lowered = message.casefold()
    if any(
        marker in lowered
        for marker in (
            "请先填写",
            "credentials missing",
            "missing credential",
            "client id and api key",
        )
    ):
        return CategorySearchError(
            "CATEGORY_CREDENTIALS_MISSING",
            message,
            retryable=False,
        )
    if any(
        marker in lowered
        for marker in (" 401", " 403", "unauthorized", "forbidden")
    ):
        return CategorySearchError(
            "CATEGORY_AUTH_REJECTED",
            message,
            retryable=False,
        )
    if " 429" in lowered or " 420" in lowered or "rate limit" in lowered or "限流" in lowered:
        return CategorySearchError(
            "CATEGORY_RATE_LIMITED",
            message,
            retryable=True,
        )
    if isinstance(exc, TimeoutError) or "timeout" in lowered or "timed out" in lowered:
        return CategorySearchError(
            "CATEGORY_PROVIDER_TIMEOUT",
            message,
            retryable=True,
        )
    if "未返回可发布" in message or "corpus" in lowered:
        return CategorySearchError(
            "CATEGORY_CORPUS_UNAVAILABLE",
            message,
            retryable=True,
        )
    return CategorySearchError(
        "CATEGORY_PROVIDER_ERROR",
        message,
        retryable=True,
    )


def _path_segments(record: Mapping[str, Any]) -> list[str]:
    raw_path = record.get("path_original")
    if isinstance(raw_path, (list, tuple)):
        path = [
            str(item).strip()[:500]
            for item in raw_path[:20]
            if str(item).strip()
        ]
        if path:
            return path
    path_text = str(record.get("category_path") or record.get("path") or "").strip()
    if path_text:
        return [
            item.strip()[:500]
            for item in path_text.replace(">", "/").split("/")[:20]
            if item.strip()
        ]
    name = str(
        record.get("name_original")
        or record.get("name")
        or record.get("category_name")
        or ""
    ).strip()
    return [name[:500]] if name else []


def _candidate(
    record: Mapping[str, Any],
    *,
    platform: str,
    site: str,
    rank: int,
) -> CategoryCandidate | None:
    category_id = str(
        record.get("category_id")
        or record.get("id")
        or record.get("type_id")
        or ""
    ).strip()
    if not category_id:
        return None
    path = _path_segments(record)
    name = str(
        record.get("name_original")
        or record.get("name")
        or record.get("category_name")
        or (path[-1] if path else category_id)
    ).strip()[:500]
    candidate: CategoryCandidate = {
        "category_id": category_id,
        "name": name,
        "path_segments": path or [name or category_id],
        "search_rank": max(0, int(rank)),
        "publishable": not bool(record.get("disabled")),
        "platform": platform,
        "site": str(record.get("site") or site),
    }
    for field_name in ("description_category_id", "type_id"):
        value = str(record.get(field_name) or "").strip()
        if value:
            candidate[field_name] = value
    return candidate


def _candidates(
    rows: Any,
    *,
    platform: str,
    site: str,
    limit: int,
) -> list[CategoryCandidate]:
    normalized: list[CategoryCandidate] = []
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, Mapping):
            continue
        candidate = _candidate(
            row,
            platform=platform,
            site=site,
            rank=index,
        )
        if candidate is not None:
            normalized.append(candidate)
        if len(normalized) >= limit:
            break
    return normalized


@dataclass(frozen=True)
class OzonCategorySearcher:
    provider: Any
    site: str
    limit: int = 8
    timeout_seconds: float | None = 8
    deadline_at: float | None = None

    def _timeout(self) -> float | None:
        if self.deadline_at is None:
            return self.timeout_seconds
        remaining = self.deadline_at - time.monotonic()
        if remaining <= 0:
            raise CategorySearchError(
                "CATEGORY_PROVIDER_TIMEOUT",
                "类目搜索总 deadline 已耗尽。",
                retryable=True,
            )
        return (
            remaining
            if self.timeout_seconds is None
            else min(self.timeout_seconds, remaining)
        )

    def search_categories(self, keyword: str) -> CategorySearchResult:
        normalized = str(keyword or "").strip()[:300]
        if not normalized:
            return {"keyword": "", "candidates": [], "source": "ozon_cache"}
        try:
            rows = self.provider.search(
                normalized,
                site=self.site,
                limit=self.limit,
                timeout_seconds=self._timeout(),
            )
        except Exception as exc:
            raise _classified_error(exc) from exc
        candidates = _candidates(
            rows,
            platform="ozon",
            site=self.site,
            limit=self.limit,
        )
        source_rows = rows if isinstance(rows, list) else []
        cache_source = next(
            (
                str(row.get("cache_source") or "").strip()
                for row in source_rows
                if isinstance(row, Mapping) and row.get("cache_source")
            ),
            "ozon_cache",
        )
        return {
            "keyword": normalized,
            "candidates": candidates,
            "source": cache_source,
        }

    def root_categories(self) -> CategoryBrowseResult:
        try:
            result = self.provider.roots(timeout_seconds=self._timeout())
        except Exception as exc:
            raise _classified_error(exc) from exc
        return self._browse_result(result)

    def browse_categories(self, parent_ids: list[str]) -> CategoryBrowseResult:
        normalized = list(
            dict.fromkeys(
                str(parent_id or "").strip()[:160]
                for parent_id in parent_ids
                if str(parent_id or "").strip()
            )
        )
        if not normalized:
            raise CategorySearchError(
                "CATEGORY_NAVIGATION_PARENT_REQUIRED",
                "类目树导航至少需要一个 parent_id。",
            )
        if len(normalized) > 2:
            raise CategorySearchError(
                "CATEGORY_NAVIGATION_TOO_BROAD",
                "类目树导航每次最多展开两个父节点。",
            )
        try:
            result = self.provider.browse(
                normalized,
                timeout_seconds=self._timeout(),
            )
        except Exception as exc:
            raise _classified_error(exc) from exc
        return self._browse_result(result)

    def _browse_result(self, result: Any) -> CategoryBrowseResult:
        source = (
            str(result.get("source") or "ozon_cache").strip()[:80]
            if isinstance(result, Mapping)
            else "ozon_cache"
        )
        parent_ids = (
            [
                str(parent_id).strip()[:160]
                for parent_id in result.get("parent_ids") or []
                if str(parent_id).strip()
            ]
            if isinstance(result, Mapping)
            else []
        )
        nodes: list[CategoryTreeNode] = []
        raw_nodes = result.get("nodes") if isinstance(result, Mapping) else []
        for row in raw_nodes if isinstance(raw_nodes, list) else []:
            if not isinstance(row, Mapping):
                continue
            node_id = str(row.get("node_id") or "").strip()
            name = str(row.get("name") or "").strip()
            level = str(row.get("level") or "").strip()
            if not node_id or not name or level not in {"branch", "product_type"}:
                continue
            node: CategoryTreeNode = {
                "node_id": node_id[:160],
                "name": name[:500],
                "level": level,  # type: ignore[typeddict-item]
                "depth": max(1, int(row.get("depth") or 1)),
                "parent_id": str(row.get("parent_id") or "").strip()[:160],
                "path_segments": [
                    str(segment).strip()[:500]
                    for segment in (row.get("path_segments") or [])[:20]
                    if str(segment).strip()
                ],
                "child_count": max(0, int(row.get("child_count") or 0)),
                "publishable": bool(row.get("publishable")),
                "platform": "ozon",
                "site": self.site,
            }
            if level == "product_type":
                category_id = str(
                    row.get("category_id") or row.get("node_id") or ""
                ).strip()
                node["category_id"] = category_id[:160]
                node["type_id"] = str(
                    row.get("type_id") or category_id
                ).strip()[:160]
                node["description_category_id"] = str(
                    row.get("description_category_id") or ""
                ).strip()[:160]
            nodes.append(node)
        return {"parent_ids": parent_ids, "nodes": nodes, "source": source}


@dataclass(frozen=True)
class YandexCategorySearcher:
    """Yandex 类目搜索：本地缓存树上的规范化匹配。"""

    provider: Any
    site: str
    limit: int = 8
    timeout_seconds: float | None = 8
    deadline_at: float | None = None

    def _timeout(self) -> float | None:
        if self.deadline_at is None:
            return self.timeout_seconds
        remaining = self.deadline_at - time.monotonic()
        if remaining <= 0:
            raise CategorySearchError(
                "CATEGORY_PROVIDER_TIMEOUT",
                "类目搜索总 deadline 已耗尽。",
                retryable=True,
            )
        return (
            remaining
            if self.timeout_seconds is None
            else min(self.timeout_seconds, remaining)
        )

    def search_categories(self, keyword: str) -> CategorySearchResult:
        normalized = str(keyword or "").strip()[:300]
        if not normalized:
            return {"keyword": "", "candidates": [], "source": "yandex_cache"}
        try:
            rows = self.provider.search(
                normalized,
                site=self.site,
                limit=self.limit,
                timeout_seconds=self._timeout(),
            )
        except Exception as exc:
            raise _classified_error(exc) from exc
        candidates = _candidates(
            rows,
            platform="yandex",
            site=self.site,
            limit=self.limit,
        )
        source_rows = rows if isinstance(rows, list) else []
        cache_source = next(
            (
                str(row.get("cache_source") or "").strip()
                for row in source_rows
                if isinstance(row, Mapping) and row.get("cache_source")
            ),
            "yandex_cache",
        )
        return {
            "keyword": normalized,
            "candidates": candidates,
            "source": cache_source,
        }


@dataclass(frozen=True)
class MercadoLibreCategorySearcher:
    provider: Any
    site: str
    limit: int = 8
    timeout_seconds: float | None = 8
    deadline_at: float | None = None

    def _timeout(self) -> float | None:
        if self.deadline_at is None:
            return self.timeout_seconds
        remaining = self.deadline_at - time.monotonic()
        if remaining <= 0:
            raise CategorySearchError(
                "CATEGORY_PROVIDER_TIMEOUT",
                "类目搜索总 deadline 已耗尽。",
                retryable=True,
            )
        return (
            remaining
            if self.timeout_seconds is None
            else min(self.timeout_seconds, remaining)
        )

    def search_categories(self, keyword: str) -> CategorySearchResult:
        normalized = str(keyword or "").strip()[:300]
        if not normalized:
            return {
                "keyword": "",
                "candidates": [],
                "source": "mercadolibre_api",
            }
        try:
            rows = self.provider.discover(
                normalized,
                site=self.site,
                limit=self.limit,
                timeout_seconds=self._timeout(),
            )
        except Exception as exc:
            raise _classified_error(exc) from exc
        candidates = _candidates(
            rows,
            platform="mercadolibre",
            site=self.site,
            limit=self.limit,
        )
        return {
            "keyword": normalized,
            "candidates": candidates,
            "source": "mercadolibre_api",
        }


CategorySearcherFactory = Callable[
    [CategoryProvider, str, int, float | None, float | None],
    CategorySearcher,
]


def _ozon_searcher(
    provider: CategoryProvider,
    site: str,
    limit: int,
    timeout_seconds: float | None,
    deadline_at: float | None,
) -> CategorySearcher:
    return OzonCategorySearcher(provider, site, limit, timeout_seconds, deadline_at)


def _mercadolibre_searcher(
    provider: CategoryProvider,
    site: str,
    limit: int,
    timeout_seconds: float | None,
    deadline_at: float | None,
) -> CategorySearcher:
    return MercadoLibreCategorySearcher(
        provider,
        site,
        limit,
        timeout_seconds,
        deadline_at,
    )


def _yandex_searcher(
    provider: CategoryProvider,
    site: str,
    limit: int,
    timeout_seconds: float | None,
    deadline_at: float | None,
) -> CategorySearcher:
    return YandexCategorySearcher(provider, site, limit, timeout_seconds, deadline_at)


_CATEGORY_SEARCHER_FACTORIES: dict[str, CategorySearcherFactory] = {
    "ozon": _ozon_searcher,
    "mercadolibre": _mercadolibre_searcher,
    "yandex": _yandex_searcher,
}


def create_category_searcher(
    platform: str,
    *,
    site: str = "",
    limit: int = 8,
    timeout_seconds: float | None = 8,
    deadline_at: float | None = None,
    provider_resolver: Callable[[str], CategoryProvider] = require_category_provider,
) -> CategorySearcher:
    """根据当前平台创建一次绑定对象，后续调用不再携带平台参数。"""

    key = str(platform or "").strip().lower()
    factory = _CATEGORY_SEARCHER_FACTORIES.get(key)
    if factory is None:
        raise CategorySearchError(
            "CATEGORY_PROVIDER_UNSUPPORTED",
            f"平台 {key or '<empty>'} 未实现类目搜索器。",
        )
    provider = provider_resolver(key)
    resolved_site = provider.resolve_site(site)
    safe_limit = max(1, min(8, int(limit)))
    safe_timeout = (
        None
        if timeout_seconds is None
        else max(0.1, float(timeout_seconds))
    )
    return factory(provider, resolved_site, safe_limit, safe_timeout, deadline_at)


__all__ = [
    "CategorySearchError",
    "MercadoLibreCategorySearcher",
    "OzonCategorySearcher",
    "create_category_searcher",
]

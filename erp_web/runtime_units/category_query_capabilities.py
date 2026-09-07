from __future__ import annotations

"""类目搜索、属性定义/枚举查询与类目预检的只读 Capability。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.product_model import validate_category_precheck
from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import CATEGORY_SEARCH_CANDIDATES_PER_KEYWORD, CategoryCandidateLedger
from erp_web.schemas.category_query_capabilities import (
    CategoryAttributeValuesQueryRequest,
    CategoryAttributeValuesQueryResult,
    CategoryAttributesQueryRequest,
    CategoryAttributesQueryResult,
    CategoryPrecheckRequest,
    CategoryPrecheckResult,
    CategorySearchRequest,
    CategorySearchResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict_rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _live_api_error(exc: Exception) -> BusinessCapabilityError:
    return BusinessCapabilityError(
        "CATEGORY_LIVE_API_FAILED",
        str(exc) or "类目实时接口调用失败。",
        retryable=True,
    )


@dataclass(frozen=True)
class CategoryQueryCapabilityScope:
    """类目查询的可信依赖边界。"""

    searcher: Callable[..., list[dict[str, Any]]]
    attributes_loader: Callable[..., dict[str, Any]]
    attribute_values_loader: Callable[..., dict[str, Any]]
    record_loader: Callable[..., dict[str, Any]]
    draft_context_loader: Callable[
        [dict[str, Any]],
        tuple[dict[str, Any], dict[str, Any] | None, int],
    ]
    product_loader: Callable[
        [dict[str, Any]],
        tuple[dict[str, Any], dict[str, Any] | None, int],
    ]


CATEGORY_SEARCH_TOOL = "category_search"
CATEGORY_ATTRIBUTES_QUERY_TOOL = "category_attributes_query"
CATEGORY_ATTRIBUTE_VALUES_QUERY_TOOL = "category_attribute_values_query"
CATEGORY_PRECHECK_TOOL = "category_precheck"


@dataclass(frozen=True)
class _CategoryQuerySearcher:
    """把通用查询的可信平台和实时接口绑定到批量检索器。"""

    platform: str
    site: str
    loader: Callable[..., list[dict[str, Any]]]
    execution: AiExecutionContext

    def search_categories(self, keyword: str) -> dict[str, Any]:
        try:
            rows = self.loader(
                self.platform, query=keyword, site=self.site,
                limit=CATEGORY_SEARCH_CANDIDATES_PER_KEYWORD,
                timeout_seconds=self.execution.bounded_timeout_seconds(8),
            )
        except BusinessCapabilityError as exc:
            raise AiToolExecutionError(exc.code, str(exc), retryable=exc.retryable) from exc
        except AiToolExecutionError:
            raise
        except Exception as exc:
            raise AiToolExecutionError(
                "CATEGORY_LIVE_API_FAILED", "类目实时查询失败，请稍后重试", retryable=True,
            ) from exc
        return {
            "keyword": keyword,
            "source": f"{self.platform}_live",
            "candidates": [
                {
                    **row,
                    "category_id": _text(row.get("category_id") or row.get("id")),
                    "path_segments": [
                        part.strip()
                        for part in _text(row.get("category_path") or row.get("path")).split(" / ")
                        if part.strip()
                    ],
                }
                for row in _dict_rows(rows)
            ],
        }


@ai_tool(
    name=CATEGORY_SEARCH_TOOL,
    description=(
        "先根据商品实物规划主要相关搜索方向，首轮通过 keywords 列表一次批量查询；"
        "已有合适候选直接选择，只有具体缺口才补查。结果按类目 ID 去重，"
        "matched_keywords 标明命中词，errors 按词报告失败；limit 是合并后的候选总量。"
        "truncated=true 时可缩小词组继续查询。"
    ),
    permission="category.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="2",
)
def category_search(
    request: CategorySearchRequest,
    scope: Annotated[CategoryQueryCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategorySearchResult:
    platform = _text(request.platform).lower()
    site = _text(request.site)
    ledger = CategoryCandidateLedger()
    batch = CategoryKeywordBatchSearch(
        searcher=_CategoryQuerySearcher(platform, site, scope.searcher, execution),
        ledger=ledger,
        limit=request.limit,
    ).execute({"keywords": request.keywords}, execution)
    results = []
    for candidate in batch["candidates"]:
        # 保留通用查询所需的 Ozon ID 配对等实时字段。
        row = ledger.get(candidate["category_id"])
        if row is not None:
            results.append(row)
    return CategorySearchResult(
        platform=platform,
        site=site,
        keywords=tuple(batch["keywords"]),
        source=f"{platform}_live",
        results=_dict_rows(results),
        errors=tuple(batch["errors"]),
        truncated=batch["truncated"],
    )


@ai_tool(
    name=CATEGORY_ATTRIBUTES_QUERY_TOOL,
    description="分页查询目标类目的属性定义摘要；用 cursor 继续读取后续页。",
    permission="category.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="2",
)
def category_attributes_query(
    request: CategoryAttributesQueryRequest,
    scope: Annotated[CategoryQueryCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategoryAttributesQueryResult:
    platform = _text(request.platform).lower()
    site = _text(request.site)
    try:
        payload = scope.attributes_loader(
            platform,
            request.category_id,
            site=site,
            cursor=request.cursor,
            limit=request.limit,
            timeout_seconds=execution.bounded_timeout_seconds(),
        )
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise _live_api_error(exc) from exc
    if not isinstance(payload, dict):
        raise BusinessCapabilityError(
            "CATEGORY_ATTRIBUTES_QUERY_FAILED",
            "类目属性定义查询失败。",
        )
    return CategoryAttributesQueryResult(
        platform=_text(payload.get("platform")) or platform,
        site=_text(payload.get("site")) or site,
        category_id=_text(payload.get("category_id")) or request.category_id,
        category_path=_text(payload.get("category_path")),
        limit=request.limit,
        cursor=request.cursor,
        attributes=_dict_rows(payload.get("attributes")),
        next_cursor=_text(payload.get("next_cursor")),
        has_more=bool(payload.get("has_more")),
    )


@ai_tool(
    name=CATEGORY_ATTRIBUTE_VALUES_QUERY_TOOL,
    description="分页查询类目属性的候选枚举值（支持关键词过滤）；用 cursor 继续读取。",
    permission="category.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="2",
)
def category_attribute_values_query(
    request: CategoryAttributeValuesQueryRequest,
    scope: Annotated[CategoryQueryCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategoryAttributeValuesQueryResult:
    platform = _text(request.platform).lower()
    try:
        payload = scope.attribute_values_loader(
            platform,
            request.category_id,
            request.attribute_id,
            site=_text(request.site),
            query=request.query,
            cursor=request.cursor,
            limit=request.limit,
            timeout_seconds=execution.bounded_timeout_seconds(),
        )
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise _live_api_error(exc) from exc
    if not isinstance(payload, dict):
        raise BusinessCapabilityError(
            "CATEGORY_ATTRIBUTE_VALUES_QUERY_FAILED",
            "类目属性枚举查询失败。",
        )
    values = payload.get("values")
    if not isinstance(values, (list, tuple)):
        values = payload.get("options")
    return CategoryAttributeValuesQueryResult(
        platform=platform,
        category_id=_text(payload.get("category_id")) or request.category_id,
        attribute_id=_text(payload.get("attribute_id")) or request.attribute_id,
        query=request.query,
        cursor=request.cursor,
        values=_dict_rows(values),
        next_cursor=_text(payload.get("next_cursor")),
        has_more=bool(payload.get("has_more")),
    )


@ai_tool(
    name=CATEGORY_PRECHECK_TOOL,
    description="对商品/草稿在目标类目下执行确定性类目预检，返回缺失字段。",
    permission="category.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def category_precheck(
    request: CategoryPrecheckRequest,
    scope: Annotated[CategoryQueryCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategoryPrecheckResult:
    platform = _text(request.platform).lower()
    site = _text(request.site)
    if request.draft_id:
        context, error, _status = scope.draft_context_loader(
            {
                "draft_id": request.draft_id,
                "platform": platform,
                "site": site,
            }
        )
        if error is not None:
            raise BusinessCapabilityError(
                _text(error.get("error_code")) or "CATEGORY_PRECHECK_CONTEXT_INVALID",
                _text(error.get("error")) or "发布上下文无效。",
            )
        product = (
            context.get("product")
            if isinstance(context.get("product"), dict)
            else {}
        )
        platform = _text(context.get("platform")).lower() or platform
        site = _text(context.get("site")) or site
    else:
        product, error, _status = scope.product_loader(
            {"product_id": request.product_id}
        )
        if error is not None:
            raise BusinessCapabilityError(
                _text(error.get("error_code")) or "PRODUCT_NOT_FOUND",
                _text(error.get("error")) or "商品不存在。",
            )
    try:
        record = scope.record_loader(
            platform,
            request.category_id,
            site=site,
            include_attributes=True,
            timeout_seconds=execution.bounded_timeout_seconds(),
        )
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise _live_api_error(exc) from exc
    missing = validate_category_precheck(product, platform, record)
    path_value = record.get("category_path") if isinstance(record, dict) else ""
    if not (_text(path_value)):
        raw_path = (
            record.get("path_original")
            if isinstance(record, dict)
            and isinstance(record.get("path_original"), list)
            else []
        )
        path_value = " / ".join(_text(item) for item in raw_path if _text(item))
    return CategoryPrecheckResult(
        platform=platform,
        site=site,
        category_id=request.category_id,
        category_path=_text(path_value),
        missing_fields=tuple(_text(item) for item in missing if _text(item)),
    )


CATEGORY_QUERY_AI_CAPABILITIES = (
    category_search,
    category_attributes_query,
    category_attribute_values_query,
    category_precheck,
)


__all__ = [
    "CATEGORY_ATTRIBUTES_QUERY_TOOL",
    "CATEGORY_ATTRIBUTE_VALUES_QUERY_TOOL",
    "CATEGORY_PRECHECK_TOOL",
    "CATEGORY_QUERY_AI_CAPABILITIES",
    "CATEGORY_SEARCH_TOOL",
    "CategoryQueryCapabilityScope",
    "category_attribute_values_query",
    "category_attributes_query",
    "category_precheck",
    "category_search",
]

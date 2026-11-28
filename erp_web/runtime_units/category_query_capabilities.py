from __future__ import annotations

"""类目搜索、属性定义/枚举查询与类目预检的只读 Capability。"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.context import get_context
from erp_web.product_model import validate_category_precheck
from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch
from erp_web.runtime_units.category_attribute_access import attribute_write_scope
from erp_web.services.ai_run_cancellation import check_cancellation
from erp_web.services.forbidden_category_service import forbidden_category_issue
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.category_search_language import category_search_language
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import CategoryCandidateLedger
from erp_web.schemas.category_query_capabilities import (
    CategoryAttributeValuesQueryRequest,
    CategoryAttributeValuesQueryResult,
    CategoryAttributesQueryBatchItem,
    CategoryAttributesQueryBatchRequest,
    CategoryAttributesQueryBatchResult,
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
    if hasattr(exc, "code") and hasattr(exc, "retryable"):
        return BusinessCapabilityError(exc.code, str(exc), retryable=exc.retryable, details=getattr(exc, "details", None))
    if isinstance(exc, ValueError):
        return BusinessCapabilityError("CATEGORY_QUERY_INVALID", str(exc), retryable=False)
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
CATEGORY_ATTRIBUTES_QUERY_BATCH_TOOL = "category_attributes_query_batch"
CATEGORY_ATTRIBUTE_VALUES_QUERY_TOOL = "category_attribute_values_query"
CATEGORY_PRECHECK_TOOL = "category_precheck"
_CATEGORY_ATTRIBUTES_BATCH_MAX_WORKERS = 8
_CATEGORY_ATTRIBUTES_BATCH_MAX_PAGES = 20
_COMPACT_OMITTED_ATTR_KEYS = frozenset({
    "raw",
    "platform_binding",
    "description",
    "description_zh",
    "default_unit_id",
    "skip_value_localization",
    "category_dependent",
    "managed_by",
    "read_only",
})


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
                limit=None,
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
        "关键词使用目标市场固定语言：Yandex/Ozon 俄语、美客多巴西葡语、其他本地站西语、CBT 英语。禁止中文或换语言试搜。"
        "先根据商品实物规划主要相关搜索方向，首轮通过 keywords 列表一次批量查询；"
        "已有合适候选直接选择，只有具体缺口才补查。结果按类目 ID 去重，"
        "带有 forbidden=true 的候选属于禁售类目，严禁选择；若商品兼容多个类目应优先选非禁售候选，无合规替代时不得硬选。"
        "matched_keywords 标明命中词，errors 按词报告失败；默认按回执字节预算分页，limit 可主动缩小本页。"
        "has_more=true 时保持关键词不变，以 next_offset 作为 offset 继续查询；total_candidate_count 是去重命中总量，query_candidate_counts 为每词取回数量（远端预测不代表全库）。"
    ),
    permission="category.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="4",
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
        search_language=category_search_language(platform, site),
        limit=request.limit,
        # 主对话沿用同一类目回执预算，ID 配对也计入分页大小。
        include_binding_ids=True,
    ).execute({"keywords": [request.product_type, *request.keywords], "offset": request.offset}, execution)
    return CategorySearchResult(
        platform=platform,
        site=site,
        keywords=tuple(batch["keywords"]),
        source=f"{platform}_live",
        results=_dict_rows(batch["candidates"]),
        errors=tuple(batch["errors"]),
        truncated=batch["truncated"],
        has_more=batch["has_more"],
        next_offset=batch["next_offset"],
        total_candidate_count=batch["total_candidate_count"],
        returned_candidate_count=batch["returned_candidate_count"],
        remaining_candidate_count=batch["remaining_candidate_count"],
        query_candidate_counts=batch["query_candidate_counts"],
    )


def _matches_attribute_scope(requested_scope: str, write_scope: str) -> bool:
    if requested_scope == "all":
        return True
    if requested_scope == "writable":
        return write_scope in {"common", "sku"}
    return write_scope == requested_scope


def _project_attribute_definition(
    definition: dict[str, Any],
    *,
    write_scope: str,
    compact: bool,
    max_options: int | None = None,
) -> dict[str, Any]:
    omitted = _COMPACT_OMITTED_ATTR_KEYS if compact else {"raw", "platform_binding"}
    projected = {
        key: value
        for key, value in definition.items()
        if key not in omitted and value is not None and value != "" and value != [] and (not compact or value is not False)
    }
    if compact and "unit_options" in projected and isinstance(projected["unit_options"], list):
        names = [
            _text(item.get("name") if isinstance(item, dict) else item)
            for item in projected["unit_options"]
        ]
        names = [item for item in names if item]
        if names:
            projected["unit_options"] = names
        else:
            projected.pop("unit_options", None)
    if compact and "options" in projected and isinstance(projected["options"], list):
        if max_options == 0:
            projected.pop("options", None)
        else:
            sliced = projected["options"][:max_options] if max_options is not None else projected["options"]
            compact_opts = [
                {k: v for k, v in item.items() if v is not None and v != ""}
                if isinstance(item, dict)
                else item
                for item in sliced
            ]
            if compact_opts:
                projected["options"] = compact_opts
            else:
                projected.pop("options", None)
    projected["write_scope"] = write_scope
    return projected


@ai_tool(
    name=CATEGORY_ATTRIBUTES_QUERY_TOOL,
    description="分页查询单个类目属性：默认 scope=common 只读可写公共属性，SKU 任务用 scope=sku，writable 一次读全部可写公共与 SKU 属性。多类目批量查询请优先使用 category_attributes_query_batch（默认 scope=writable 且自动翻页返回紧凑视图）。write_scope 标明写入范围；excluded_attributes 仅说明排除字段，不可写入。遵循 value_mode/text_format/format_hint，按 has_more/cursor 续页；已有 options 可直接用，大字典（has_more_values=true）用 category_attribute_values_query 按关键词查。",
    permission="category.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="3",
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
    attributes = []
    excluded = []
    for definition in _dict_rows(payload.get("attributes")):
        write_scope = attribute_write_scope(platform, definition)
        if not _matches_attribute_scope(request.scope, write_scope):
            if not request.compact:
                excluded.append({"id": definition["id"], "name": definition.get("name", ""), "write_scope": write_scope})
            continue
        attributes.append(_project_attribute_definition(
            definition,
            write_scope=write_scope,
            compact=request.compact,
        ))
    return CategoryAttributesQueryResult(
        platform=_text(payload.get("platform")) or platform,
        site=_text(payload.get("site")) or site,
        category_id=_text(payload.get("category_id")) or request.category_id,
        category_path=_text(payload.get("category_path")),
        limit=request.limit,
        cursor=request.cursor,
        attributes=tuple(attributes),
        excluded_attributes=tuple(excluded),
        next_cursor=_text(payload.get("next_cursor")),
        has_more=bool(payload.get("has_more")),
    )


@ai_tool(
    name=CATEGORY_ATTRIBUTES_QUERY_BATCH_TOOL,
    description="批量查询多个类目的属性定义（最多 30 个类目，自动去重保序并翻完所有分页）。默认 scope=writable 且 compact=true，一次返回全部可写公共与 SKU 变体属性（含 write_scope、variation_role、format_hint、单位及有界 options 预览），免去在脚本中逐类目分页或反复切片打印；大字典（has_more_values=true）继续用 category_attribute_values_query 按关键词查询。",
    permission="category.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def category_attributes_query_batch(
    request: CategoryAttributesQueryBatchRequest,
    scope: Annotated[CategoryQueryCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategoryAttributesQueryBatchResult:
    platform = _text(request.platform).lower()
    site = _text(request.site)
    category_ids = list(dict.fromkeys(_text(cid) for cid in request.category_ids if _text(cid)))

    def _load_one(category_id: str) -> CategoryAttributesQueryBatchItem:
        check_cancellation()
        attributes: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        category_path = ""
        cursor = ""
        seen_cursors: set[str] = set()
        try:
            for _page in range(_CATEGORY_ATTRIBUTES_BATCH_MAX_PAGES):
                check_cancellation()
                payload = scope.attributes_loader(
                    platform,
                    category_id,
                    site=site,
                    cursor=cursor,
                    limit=100,
                    timeout_seconds=execution.bounded_timeout_seconds(),
                )
                if not isinstance(payload, dict):
                    raise BusinessCapabilityError(
                        "CATEGORY_ATTRIBUTES_QUERY_FAILED",
                        "类目属性定义查询失败。",
                    )
                if not category_path:
                    category_path = _text(payload.get("category_path"))
                for definition in _dict_rows(payload.get("attributes")):
                    write_scope = attribute_write_scope(platform, definition)
                    if not _matches_attribute_scope(request.scope, write_scope):
                        if request.include_excluded:
                            excluded.append({"id": definition["id"], "name": definition.get("name", ""), "write_scope": write_scope})
                        continue
                    attributes.append(_project_attribute_definition(
                        definition,
                        write_scope=write_scope,
                        compact=request.compact,
                        max_options=request.max_options_per_attribute if request.compact else None,
                    ))
                has_more = bool(payload.get("has_more"))
                next_cursor = _text(payload.get("next_cursor"))
                if not has_more or not next_cursor or next_cursor in seen_cursors:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
        except BusinessCapabilityError as exc:
            return CategoryAttributesQueryBatchItem(
                category_id=category_id,
                ok=False,
                error_code=exc.code,
                error=str(exc),
            )
        except Exception as exc:
            err = _live_api_error(exc)
            return CategoryAttributesQueryBatchItem(
                category_id=category_id,
                ok=False,
                error_code=err.code,
                error=str(err),
            )
        return CategoryAttributesQueryBatchItem(
            category_id=category_id,
            ok=True,
            category_path=category_path,
            attribute_count=len(attributes),
            attributes=tuple(attributes),
            excluded_attributes=tuple(excluded),
        )

    workers = min(_CATEGORY_ATTRIBUTES_BATCH_MAX_WORKERS, max(1, len(category_ids)))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cat-attrs-batch") as pool:
            futures = [pool.submit(copy_context().run, _load_one, cid) for cid in category_ids]
            items = [future.result() for future in futures]
    else:
        items = [_load_one(cid) for cid in category_ids]

    ok_count = sum(1 for item in items if item.ok)
    return CategoryAttributesQueryBatchResult(
        platform=platform,
        site=site,
        total=len(items),
        ok_count=ok_count,
        error_count=len(items) - ok_count,
        items=items,
    )


@ai_tool(
    name=CATEGORY_ATTRIBUTE_VALUES_QUERY_TOOL,
    description="用当前类目定义返回的属性 ID 分页查询真实候选。有限的小字典优先空 query 读取首屏，避免同义词搜空后再读全表；品牌等大字典使用关键词。多个独立属性可同时查询。has_more 时用 cursor 继续；搜索为空不能证明字典无匹配。",
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
    if platform == "yandex":
        settings = get_context().config.forbidden_categories()
        categories = {item["category_id"]: item["name"] for item in settings["categories"]}
        draft_category_id = _text(
            (product.get("drafts") or {}).get(platform, {}).get("category_id")
            if isinstance(product.get("drafts"), dict)
            else ""
        )
        record_ancestors = tuple(
            _text(item)
            for item in (record.get("ancestor_ids") or ())
            if _text(item)
        ) if isinstance(record, dict) else ()
        for checked_id in dict.fromkeys((draft_category_id, request.category_id)):
            ancestors = (
                record_ancestors
                if checked_id == request.category_id and record_ancestors
                else get_context().category_catalog.cached_category_ancestor_ids(platform, checked_id)
            )
            if issue := forbidden_category_issue(
                platform, checked_id, categories, ancestor_ids=ancestors,
            ):
                missing.append(issue["message"])
        missing = list(dict.fromkeys(missing))
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
    category_attributes_query_batch,
    category_attribute_values_query,
    category_precheck,
)


__all__ = [
    "CATEGORY_ATTRIBUTES_QUERY_BATCH_TOOL",
    "CATEGORY_ATTRIBUTES_QUERY_TOOL",
    "CATEGORY_ATTRIBUTE_VALUES_QUERY_TOOL",
    "CATEGORY_PRECHECK_TOOL",
    "CATEGORY_QUERY_AI_CAPABILITIES",
    "CATEGORY_SEARCH_TOOL",
    "CategoryQueryCapabilityScope",
    "category_attribute_values_query",
    "category_attributes_query",
    "category_attributes_query_batch",
    "category_precheck",
    "category_search",
]

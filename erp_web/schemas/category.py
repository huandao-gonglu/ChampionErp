from __future__ import annotations

"""类目搜索与匹配的规范化数据形状。"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Literal, TypedDict, cast


CATEGORY_SEARCH_PERMISSION = "category.read"
CATEGORY_SEARCH_TOOLSET_ID = "category.search"
CategoryAttributeValueMode = Literal["strict_enum", "open_enum", "free_text"]


def normalize_category_dictionary_id(value: Any) -> str:
    """规范化平台字典 ID；Ozon 用 ``0`` 表示普通非字典属性。"""

    text = str(value or "").strip()
    return "" if text == "0" else text


def category_attribute_dictionary_id(definition: dict[str, Any]) -> str:
    """读取规范化字典 ID，兼容平台原始定义嵌套在 ``raw`` 中。"""

    raw = definition.get("raw") if isinstance(definition.get("raw"), dict) else {}
    value = definition.get("dictionary_id")
    if value in (None, ""):
        value = raw.get("dictionary_id")
    return normalize_category_dictionary_id(value)


def is_category_dictionary_attribute(definition: dict[str, Any]) -> bool:
    """判断属性是否必须从平台字典中选择。"""

    raw = definition.get("raw") if isinstance(definition.get("raw"), dict) else {}
    value = definition.get("dictionary_id")
    if value in (None, ""):
        value = raw.get("dictionary_id")
    if value not in (None, ""):
        return bool(normalize_category_dictionary_id(value))
    return bool(definition.get("is_dictionary"))


def _category_attribute_options(definition: dict[str, Any]) -> list[str]:
    raw_options = definition.get("options")
    if isinstance(raw_options, list):
        options = [str(item).strip() for item in raw_options if str(item).strip()]
    elif isinstance(raw_options, str):
        options = [
            item.strip()
            for item in re.split(r"[,，;；\n]+", raw_options)
            if item.strip()
        ]
    else:
        options = []
    for item in definition.get("values") or []:
        if isinstance(item, dict):
            label = str(
                item.get("name")
                or item.get("value_name")
                or item.get("value")
                or item.get("id")
                or ""
            ).strip()
        else:
            label = str(item or "").strip()
        if label:
            options.append(label)
    return list(dict.fromkeys(options))[:80]


def category_attribute_value_mode(
    definition: dict[str, Any],
) -> CategoryAttributeValueMode:
    """返回平台属性的唯一值约束模式。"""

    declared = str(definition.get("value_mode") or "").strip()
    if declared in {"strict_enum", "open_enum", "free_text"}:
        return cast(CategoryAttributeValueMode, declared)
    if is_category_dictionary_attribute(definition):
        return "strict_enum"
    raw = definition.get("raw") if isinstance(definition.get("raw"), dict) else {}
    if (
        definition.get("is_dictionary")
        or raw.get("is_dictionary")
        or _category_attribute_options(definition)
    ):
        return "open_enum"
    return "free_text"


def _category_attribute_unit_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("name") or value.get("value") or value.get("id") or ""
        ).strip()
    return str(value or "").strip()


def normalize_category_attribute_definition(
    definition: Any,
    *,
    required_fallback: bool = False,
) -> dict[str, Any]:
    """规范化类目属性定义，供规则、Agent 和发布预检共同使用。"""

    source = definition if isinstance(definition, dict) else {}
    raw = source.get("raw") if isinstance(source.get("raw"), dict) else {}
    value_mode = category_attribute_value_mode(source)
    unit_options_source = (
        source.get("unit_options")
        if isinstance(source.get("unit_options"), list)
        else raw.get("unit_options") if isinstance(raw.get("unit_options"), list) else []
    )
    unit_options = [
        unit_name
        for item in unit_options_source
        if (unit_name := _category_attribute_unit_name(item))
    ]
    default_unit = str(
        source.get("default_unit") or raw.get("default_unit") or ""
    ).strip()
    unit = str(source.get("unit") or "").strip()
    unit_ids_source = (
        source.get("unit_ids")
        if isinstance(source.get("unit_ids"), dict)
        else raw.get("unit_ids") if isinstance(raw.get("unit_ids"), dict) else {}
    )
    unit_ids = {
        str(key or "").strip(): str(value or "").strip()
        for key, value in unit_ids_source.items()
        if str(key or "").strip() and str(value or "").strip()
    }
    constraints_source = (
        source.get("constraints")
        if isinstance(source.get("constraints"), dict)
        else raw.get("constraints") if isinstance(raw.get("constraints"), dict) else {}
    )
    constraints = {
        str(key): value
        for key, value in constraints_source.items()
        if value is not None
    }
    return {
        "id": str(
            source.get("id")
            or source.get("attribute_id")
            or source.get("code")
            or ""
        ).strip(),
        "name": str(
            source.get("name")
            or source.get("label")
            or source.get("id")
            or ""
        ).strip(),
        "required": bool(source.get("required", required_fallback)),
        "value_type": str(source.get("value_type") or "").strip(),
        "value_mode": value_mode,
        "allow_custom_values": bool(
            source.get("allow_custom_values") or raw.get("allow_custom_values")
        ),
        "read_only": bool(source.get("read_only") or raw.get("read_only")),
        "unit": unit or default_unit,
        # 需要选择单位的平台属性通过通用 unit_options/default_unit 暴露，
        # 通用面板只消费共享字段，不理解平台专用单位键。
        "unit_options": list(dict.fromkeys(unit_options))[:80],
        "default_unit": default_unit or unit,
        # 单位名称 → 平台单位 ID 的映射供发布边界编译 wire unitId；
        # 通用面板不消费该字段。
        "unit_ids": unit_ids,
        "default_unit_id": str(
            source.get("default_unit_id") or raw.get("default_unit_id") or ""
        ).strip(),
        "constraints": constraints,
        "description": str(source.get("description") or "").strip()[:1500],
        "options": _category_attribute_options(source),
        "dictionary_id": category_attribute_dictionary_id(source),
        "is_collection": bool(
            source.get("is_collection") or raw.get("is_collection")
        ),
        "max_value_count": int(
            source.get("max_value_count") or raw.get("max_value_count") or 0
        ),
        "category_dependent": bool(
            source.get("category_dependent") or raw.get("category_dependent")
        ),
    }


def category_attribute_schema(
    category_record: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """返回去除无 ID 项后的规范化类目属性定义。"""

    record = category_record if isinstance(category_record, dict) else {}
    attributes = (
        record.get("attributes")
        if isinstance(record.get("attributes"), dict)
        else {}
    )
    required = [
        normalize_category_attribute_definition(item, required_fallback=True)
        for item in (
            attributes.get("required")
            if isinstance(attributes.get("required"), list)
            else []
        )
    ]
    optional = [
        normalize_category_attribute_definition(item)
        for item in (
            attributes.get("optional")
            if isinstance(attributes.get("optional"), list)
            else []
        )
    ]
    return [item for item in required + optional if item.get("id")]


def category_attribute_unit_is_valid(
    definition: dict[str, Any],
    unit: Any,
) -> bool:
    """单位选择约束：无单位选项时任意非空单位放行，有选项时必须命中。"""

    unit_options = [
        unit_name
        for item in (definition.get("unit_options") or [])
        if (unit_name := _category_attribute_unit_name(item))
    ]
    text = str(unit or "").strip()
    if not text:
        # 未显式选择单位：构造 payload 时回落 default_unit。
        return True
    if not unit_options:
        return True
    return text in unit_options


def category_attribute_uses_unit(definition: dict[str, Any]) -> bool:
    """判断属性值是否包含平台单位这一正交组成部分。"""

    return bool(
        str(definition.get("value_type") or "").strip().casefold()
        == "number_unit"
        or definition.get("unit_options")
    )


def category_attribute_uses_numeric_unit(definition: dict[str, Any]) -> bool:
    """判断带单位属性的值部分是否必须是有限数值。"""

    value_type = str(definition.get("value_type") or "").strip().casefold()
    return category_attribute_uses_unit(definition) and value_type in {
        "number_unit",
        "numeric",
        "number",
        "integer",
        "decimal",
        "float",
    }


def normalize_category_attribute_unit(
    definition: dict[str, Any],
    unit: Any,
) -> str | None:
    """校验单位并返回平台定义中的规范名称；绝不自动补默认单位。"""

    unit_text = str(unit or "").strip()
    if not unit_text:
        return None
    unit_options = [
        unit_name
        for item in (definition.get("unit_options") or [])
        if (unit_name := _category_attribute_unit_name(item))
    ]
    if not unit_options:
        fallback_unit = str(
            definition.get("default_unit") or definition.get("unit") or ""
        ).strip()
        if fallback_unit:
            unit_options = [fallback_unit]
    if not unit_options:
        return None
    return next(
        (
            option
            for option in unit_options
            if option.casefold() == unit_text.casefold()
        ),
        None,
    )


def normalize_category_attribute_number_unit_value(
    definition: dict[str, Any],
    value: Any,
    unit: Any,
) -> dict[str, str] | None:
    """把数值单位属性规范化为共享 ``{value, unit}`` 结构。

    ``unit_options`` 是平台给出的有限候选；若平台仅提供默认单位，则该
    默认单位就是唯一可接受单位。两者都缺失时拒绝填充，且绝不根据默认值
    替调用方补单位。
    """

    value_text = ("" if value is None else str(value)).strip().replace(",", ".")
    try:
        number = Decimal(value_text)
    except (InvalidOperation, ValueError):
        return None
    if not value_text or not number.is_finite():
        return None

    unit_text = normalize_category_attribute_unit(definition, unit)
    if unit_text is None:
        return None
    return {"value": value_text, "unit": unit_text}


def category_attribute_value_is_valid(
    definition: dict[str, Any],
    value: Any,
) -> bool:
    """按唯一值模式判断草稿属性值是否满足平台结构约束。"""

    attr_id = str(definition.get("id") or "").strip()
    value_mode = category_attribute_value_mode(definition)
    uses_unit = category_attribute_uses_unit(definition)
    if value_mode != "strict_enum":
        if isinstance(value, dict):
            if isinstance(value.get("values"), list):
                selected = [
                    item
                    for item in value.get("values") or []
                    if isinstance(item, dict)
                ]
                if not selected or not all(
                    str(item.get("value") or "").strip()
                    and str(item.get("value") or "").strip().upper()
                    != attr_id.upper()
                    for item in selected
                ):
                    return False
                if not definition.get("is_collection") and len(selected) > 1:
                    return False
                maximum = int(definition.get("max_value_count") or 0)
                if maximum > 0 and len(selected) > maximum:
                    return False
                return not uses_unit or normalize_category_attribute_unit(
                    definition,
                    value.get("unit"),
                ) is not None
            # 带单位的共享值 shape：{"value": 文本, "unit": 单位}。
            raw_text = value.get("value")
            text = ("" if raw_text is None else str(raw_text)).strip()
            if not text or text.upper() == attr_id.upper():
                return False
            if category_attribute_uses_numeric_unit(definition):
                return normalize_category_attribute_number_unit_value(
                    definition,
                    text,
                    value.get("unit"),
                ) is not None
            if uses_unit:
                return normalize_category_attribute_unit(
                    definition,
                    value.get("unit"),
                ) is not None
            return category_attribute_unit_is_valid(
                definition, value.get("unit")
            )
        if isinstance(value, (list, tuple, set)):
            return False
        text = str(value or "").strip()
        return (
            not uses_unit
            and bool(text)
            and text.upper() != attr_id.upper()
        )
    if not isinstance(value, dict) or not isinstance(value.get("values"), list):
        return False
    if uses_unit and normalize_category_attribute_unit(
        definition,
        value.get("unit"),
    ) is None:
        return False
    if not uses_unit and not category_attribute_unit_is_valid(
        definition,
        value.get("unit"),
    ):
        return False
    selected = [item for item in value.get("values") or [] if isinstance(item, dict)]
    if not selected or not all(
        item.get("dictionary_value_id") not in (None, "")
        and str(item.get("value") or "").strip()
        for item in selected
    ):
        return False
    maximum = int(definition.get("max_value_count") or 0)
    if not definition.get("is_collection") and len(selected) > 1:
        return False
    return maximum <= 0 or len(selected) <= maximum


class CategoryCorpusInfo(TypedDict, total=False):
    """平台搜索实现内部使用的缓存语料身份，不进入 AI 上下文。"""

    corpus_hash: str
    taxonomy_version: str | None
    locale: str
    retrieved_at: str
    expires_at: str
    stale_until: str
    credential_scope_hash: str
    cache_source: Literal["remote_cache", "persistent_cache", "stale_cache"]
    stale: bool


class CategoryCandidate(TypedDict, total=False):
    category_id: str
    name: str
    path_segments: list[str]
    search_rank: int
    publishable: bool
    platform: str
    site: str
    description_category_id: str
    type_id: str


class CategorySearchResult(TypedDict):
    keyword: str
    candidates: list[CategoryCandidate]
    source: str


CategoryTreeNodeLevel = Literal["branch", "product_type"]


class CategoryTreeNode(TypedDict, total=False):
    """类目树导航节点；只有 ``product_type`` 可以作为最终发布类目。"""

    node_id: str
    name: str
    level: CategoryTreeNodeLevel
    depth: int
    parent_id: str
    path_segments: list[str]
    child_count: int
    category_id: str
    description_category_id: str
    type_id: str
    publishable: bool
    platform: str
    site: str


class CategoryBrowseResult(TypedDict):
    parent_ids: list[str]
    nodes: list[CategoryTreeNode]
    source: str


@dataclass
class CategoryCandidateLedger:
    """记录当前 Agent run 中工具真实返回的叶子候选与检索轨迹。"""

    _candidates: dict[str, CategoryCandidate] = field(default_factory=dict)
    searches: list[CategorySearchResult] = field(default_factory=list)
    browses: list[CategoryBrowseResult] = field(default_factory=list)
    attempts: list[str] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    retrieval_mode: Literal["keyword_search", "tree_navigation"] = "keyword_search"

    def record_attempt(self, keyword: str) -> None:
        self.attempts.append(str(keyword or "").strip()[:300])

    def record_error(self, exc: Exception) -> None:
        self.errors.append(exc)

    def add_result(self, result: CategorySearchResult) -> None:
        stored_result: CategorySearchResult = {
            "keyword": str(result.get("keyword") or "").strip()[:300],
            "candidates": [],
            "source": str(result.get("source") or "").strip()[:80],
        }
        for row in result.get("candidates") or []:
            category_id = str(row.get("category_id") or "").strip()
            if not category_id:
                continue
            candidate = dict(row)
            stored_result["candidates"].append(candidate)
            self._candidates.setdefault(category_id, candidate)
        self.searches.append(stored_result)

    def add_browse_result(self, result: CategoryBrowseResult) -> None:
        stored_result: CategoryBrowseResult = {
            "parent_ids": [
                str(parent_id).strip()[:160]
                for parent_id in result.get("parent_ids") or []
                if str(parent_id).strip()
            ],
            "nodes": [],
            "source": str(result.get("source") or "").strip()[:80],
        }
        for row in result.get("nodes") or []:
            node = dict(row)
            stored_result["nodes"].append(node)
            if node.get("level") != "product_type":
                continue
            category_id = str(node.get("category_id") or node.get("node_id") or "").strip()
            if not category_id:
                continue
            candidate: CategoryCandidate = {
                "category_id": category_id,
                "name": str(node.get("name") or category_id).strip()[:500],
                "path_segments": [
                    str(segment).strip()[:500]
                    for segment in (node.get("path_segments") or [])[:20]
                    if str(segment).strip()
                ],
                "search_rank": len(self._candidates),
                "publishable": bool(node.get("publishable", True)),
                "platform": str(node.get("platform") or "").strip(),
                "site": str(node.get("site") or "").strip(),
            }
            for field_name in ("description_category_id", "type_id"):
                value = str(node.get(field_name) or "").strip()
                if value:
                    candidate[field_name] = value
            self._candidates.setdefault(category_id, candidate)
        self.browses.append(stored_result)

    @property
    def search_count(self) -> int:
        return len(self.attempts)

    @property
    def successful_search_count(self) -> int:
        return len(self.searches) + len(self.browses)

    @property
    def navigation_count(self) -> int:
        return len(self.browses)

    @property
    def has_leaf_candidates(self) -> bool:
        return bool(self._candidates)

    @property
    def can_abstain(self) -> bool:
        if self.retrieval_mode == "tree_navigation":
            return self.has_leaf_candidates or self.navigation_count >= 4
        return self.search_count >= 3

    @property
    def last_error(self) -> Exception | None:
        return self.errors[-1] if self.errors else None

    @property
    def last_keyword(self) -> str:
        if self.searches:
            return self.searches[-1]["keyword"]
        if self.browses:
            nodes = self.browses[-1].get("nodes") or []
            if nodes:
                parent_path = [
                    str(segment).strip()
                    for segment in (nodes[0].get("path_segments") or [])[:-1]
                    if str(segment).strip()
                ]
                if parent_path:
                    return " > ".join(parent_path)
            return "tree:" + ",".join(self.browses[-1]["parent_ids"])
        return ""

    def get(self, category_id: str) -> CategoryCandidate | None:
        candidate = self._candidates.get(str(category_id or "").strip())
        return dict(candidate) if candidate is not None else None

    def candidates(self, *, limit: int = 24) -> list[CategoryCandidate]:
        return [
            dict(candidate)
            for candidate in list(self._candidates.values())[: max(1, int(limit))]
        ]


CategoryMatchStatus = Literal["completed", "unresolved", "failed"]
CategoryConfidenceBand = Literal["high", "medium", "low"]


class CategoryMatchFailure(TypedDict, total=False):
    code: str
    message: str
    stage: str
    retryable: bool


class CategoryMatchDecision(TypedDict):
    confidence_band: CategoryConfidenceBand
    model_confidence: float
    decision_score: float
    abstained: bool
    evidence: list[str]
    search_count: int


class CategoryMatchTrace(TypedDict, total=False):
    task_run_id: str
    run_id: str
    trace_id: str


class CategoryMatchResult(TypedDict):
    ok: bool
    status: CategoryMatchStatus
    target: dict[str, str]
    selected_category_id: str | None
    query: str
    candidates: list[CategoryCandidate]
    decision: CategoryMatchDecision
    failure: CategoryMatchFailure | None
    trace: CategoryMatchTrace


__all__ = [
    "CATEGORY_SEARCH_PERMISSION",
    "CATEGORY_SEARCH_TOOLSET_ID",
    "category_attribute_dictionary_id",
    "normalize_category_attribute_number_unit_value",
    "normalize_category_attribute_unit",
    "category_attribute_schema",
    "category_attribute_unit_is_valid",
    "category_attribute_uses_numeric_unit",
    "category_attribute_uses_unit",
    "category_attribute_value_is_valid",
    "category_attribute_value_mode",
    "CategoryAttributeValueMode",
    "CategoryCandidate",
    "CategoryCandidateLedger",
    "CategoryBrowseResult",
    "CategoryCorpusInfo",
    "CategorySearchResult",
    "is_category_dictionary_attribute",
    "normalize_category_attribute_definition",
    "normalize_category_dictionary_id",
    "CategoryTreeNode",
    "CategoryTreeNodeLevel",
    "CategoryConfidenceBand",
    "CategoryMatchDecision",
    "CategoryMatchFailure",
    "CategoryMatchResult",
    "CategoryMatchStatus",
    "CategoryMatchTrace",
]

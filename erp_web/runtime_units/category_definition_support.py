# -*- coding: utf-8 -*-
"""CategoryDefinition 归一化支撑（类目 Schema 分离计划 Phase 1）。

职责：
- 平台 legacy 属性字典 → 类型化 :class:`CategoryAttributeDefinition` 转换；
- 临时/瞬时错误分类，供定义缓存 stale 决策使用；
- 内部定义 → 有界公共视图（CategoryAttributePage）投影；
- ``CATEGORY_ATTRIBUTES_UNAVAILABLE`` 可重试错误。

完整枚举候选一律通过 ``attribute_values`` 分页读取；本模块的任何输出都
不携带平台原始报文或候选全集。
"""

from __future__ import annotations

import ssl
import urllib.error
from typing import Any

from erp_web.schemas.category_definition import (
    ATTRIBUTE_OPTIONS_PREVIEW_LIMIT,
    CategoryAttributeDefinition,
    CategoryAttributeOptionPreview,
    CategoryAttributePage,
    CategoryAttributePlatformBinding,
    CategoryAttributeSummary,
    CategoryDefinition,
    CategoryUnitOption,
)

CATEGORY_ATTRIBUTES_UNAVAILABLE = "CATEGORY_ATTRIBUTES_UNAVAILABLE"


class CategoryAttributesUnavailableError(RuntimeError):
    """类目属性定义暂时不可用（可重试）；不得用旧规则副本掩盖。"""

    def __init__(self, message: str = "类目属性定义暂时不可用，请稍后重试。") -> None:
        super().__init__(message)
        self.error_code = CATEGORY_ATTRIBUTES_UNAVAILABLE
        self.retryable = True


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------

_AUTH_ERROR_MARKERS = (
    " 401",
    " 403",
    "unauthorized",
    "forbidden",
    "请先填写",
    "credentials missing",
    "missing credential",
)

_TRANSIENT_ERROR_MARKERS = (
    " 408",
    " 425",
    " 429",
    " 500",
    " 502",
    " 503",
    " 504",
    "timeout",
    "timed out",
    "rate limit",
    "限流",
    "temporarily unavailable",
    "connection",
    "network",
    "ssl",
    "unexpected_eof",
    "unexpected eof",
    "connection reset",
)


def is_transient_category_api_error(exc: BaseException) -> bool:
    """判断平台类目 API 错误是否允许 stale 回退。

    401/403/凭据缺失/类目禁用等确定性错误一律不得掩盖；timeout、连接失败、
    429 与平台 5xx 视为瞬时错误。
    """

    retryable = getattr(exc, "retryable", None)
    message = str(exc).casefold()
    if any(marker in message for marker in _AUTH_ERROR_MARKERS):
        return False
    if isinstance(retryable, bool):
        return retryable
    if isinstance(
        exc,
        (TimeoutError, ConnectionError, urllib.error.URLError, ssl.SSLError),
    ):
        return True
    return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)


# ---------------------------------------------------------------------------
# legacy 属性字典 → 类型化定义
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_dictionary_id(value: Any) -> str:
    """规范化平台字典 ID；Ozon 用 ``0`` 表示普通非字典属性。"""

    text = _text(value)
    return "" if text in {"0", "None"} else text


def _platform_binding_from_legacy(
    legacy: dict[str, Any],
    *,
    platform: str,
) -> CategoryAttributePlatformBinding:
    raw = legacy.get("raw") if isinstance(legacy.get("raw"), dict) else {}

    def complex_id() -> str:
        for source in (legacy, raw):
            for key in ("attribute_complex_id", "complex_id"):
                value = source.get(key)
                if value is None or value == "":
                    continue
                # ``_text`` 会把整数 0 当空值；0 是 Ozon 非复合属性的合法值。
                return str(value).strip()
        return ""

    return CategoryAttributePlatformBinding(
        complex_id=complex_id(),
        aspect=_text(legacy.get("aspect") or raw.get("is_aspect") or ""),
        platform_type=_text(legacy.get("platform_type") or raw.get("type") or legacy.get("value_type") or ""),
    )


def legacy_attribute_to_definition(
    legacy: dict[str, Any],
    *,
    platform: str,
) -> CategoryAttributeDefinition | None:
    """把既有规范化属性字典转换为类型化定义；无 id 返回 None。

    legacy 字典来自 ``normalize_ml_attribute`` / ``_normalize_attribute`` /
    ``_yandex_parameter_definition`` 或 ``normalize_category_attribute_definition``
    的输出。转换丢弃 ``raw`` 与完整 ``values``，只保留有界预览。
    """

    attribute_id = _text(legacy.get("id"))
    if not attribute_id:
        return None

    unit_ids_source = (
        legacy.get("unit_ids") if isinstance(legacy.get("unit_ids"), dict) else {}
    )
    unit_ids = {
        _text(name): _text(unit_id)
        for name, unit_id in unit_ids_source.items()
        if _text(name) and _text(unit_id)
    }
    unit_option_names: list[str] = []
    for item in legacy.get("unit_options") or []:
        if isinstance(item, dict):
            name = _text(item.get("name") or item.get("id"))
        else:
            name = _text(item)
        if name and name not in unit_option_names:
            unit_option_names.append(name)
    unit_options = tuple(
        CategoryUnitOption(id=unit_ids.get(name, ""), name=name)
        for name in unit_option_names[:ATTRIBUTE_OPTIONS_PREVIEW_LIMIT]
    )

    previews: list[CategoryAttributeOptionPreview] = []
    seen: set[str] = set()

    def add_preview(value: str, dictionary_value_id: str = "") -> None:
        value = _text(value)
        if not value or value in seen:
            return
        seen.add(value)
        previews.append(
            CategoryAttributeOptionPreview(
                value=value,
                dictionary_value_id=_text(dictionary_value_id),
            )
        )

    values_rows = (
        legacy.get("values") if isinstance(legacy.get("values"), list) else []
    )
    for row in values_rows:
        if len(previews) >= ATTRIBUTE_OPTIONS_PREVIEW_LIMIT:
            break
        if isinstance(row, dict):
            add_preview(
                row.get("value") or row.get("name") or row.get("id"),
                row.get("id") or row.get("dictionary_value_id"),
            )
        else:
            add_preview(row)
    candidate_total = len(values_rows)

    for row in legacy.get("options") or []:
        if len(previews) >= ATTRIBUTE_OPTIONS_PREVIEW_LIMIT:
            break
        if isinstance(row, dict):
            add_preview(
                row.get("name") or row.get("value") or row.get("id"),
                row.get("id") or row.get("dictionary_value_id"),
            )
        else:
            add_preview(row)

    raw_source = legacy.get("raw") if isinstance(legacy.get("raw"), dict) else {}
    dictionary_id = _normalize_dictionary_id(
        legacy.get("dictionary_id")
        if legacy.get("dictionary_id") not in (None, "")
        else raw_source.get("dictionary_id")
    )
    is_dictionary = bool(
        legacy.get("is_dictionary") if dictionary_id == "" else dictionary_id
    )
    if dictionary_id == "" and bool(legacy.get("is_dictionary")):
        is_dictionary = True
    # 字典属性的候选全集一律分页读取：预览被截断，或字典存在但本地无候选
    # （Ozon 按需加载）时，都必须提示调用方继续分页。
    has_more_values = bool(
        candidate_total > ATTRIBUTE_OPTIONS_PREVIEW_LIMIT
        or (is_dictionary and candidate_total >= ATTRIBUTE_OPTIONS_PREVIEW_LIMIT)
        or (is_dictionary and candidate_total == 0 and not previews)
    )

    max_value_count_raw = legacy.get("max_value_count")
    try:
        max_value_count = int(max_value_count_raw or 0) or None
    except (TypeError, ValueError):
        max_value_count = None

    constraints = {
        _text(key): _text(value)
        for key, value in (
            legacy.get("constraints")
            if isinstance(legacy.get("constraints"), dict)
            else {}
        ).items()
        if value is not None and _text(key)
    }

    declared_mode = _text(legacy.get("value_mode"))
    if declared_mode in {"strict_enum", "open_enum", "free_text"}:
        value_mode = declared_mode
    elif dictionary_id:
        value_mode = "strict_enum"
    elif is_dictionary or previews:
        value_mode = "open_enum"
    else:
        value_mode = "free_text"

    return CategoryAttributeDefinition(
        id=attribute_id,
        name=_text(legacy.get("name") or attribute_id),
        required=bool(legacy.get("required")),
        value_type=_text(legacy.get("value_type")),
        value_mode=value_mode,
        allow_custom_values=bool(legacy.get("allow_custom_values")),
        constraints=constraints,
        dictionary_id=dictionary_id,
        is_dictionary=is_dictionary,
        is_collection=bool(legacy.get("is_collection")),
        max_value_count=max_value_count,
        category_dependent=bool(legacy.get("category_dependent")),
        default_unit=_text(legacy.get("default_unit") or legacy.get("unit")),
        default_unit_id=_text(legacy.get("default_unit_id")),
        unit_options=unit_options,
        unit_ids=tuple(dict.fromkeys(unit_ids.values())),
        platform_binding=_platform_binding_from_legacy(legacy, platform=platform),
        options=tuple(previews),
        has_more_values=has_more_values,
    )


def definition_to_legacy_attribute(
    definition: CategoryAttributeDefinition,
) -> dict[str, Any]:
    """类型化定义 → 通用校验函数消费的 legacy 字典（不含 raw/values）。"""

    return {
        "id": definition.id,
        "name": definition.name,
        "required": definition.required,
        "value_type": definition.value_type,
        "value_mode": definition.value_mode,
        "allow_custom_values": definition.allow_custom_values,
        "unit": definition.default_unit,
        "unit_options": [unit.name for unit in definition.unit_options],
        "default_unit": definition.default_unit,
        "unit_ids": {
            unit.name: unit.id
            for unit in definition.unit_options
            if unit.name and unit.id
        },
        "default_unit_id": definition.default_unit_id,
        "constraints": dict(definition.constraints),
        "description": "",
        "options": [option.value for option in definition.options],
        "dictionary_id": definition.dictionary_id,
        "is_dictionary": definition.is_dictionary,
        "is_collection": definition.is_collection,
        "max_value_count": definition.max_value_count or 0,
        "category_dependent": definition.category_dependent,
    }


def definition_from_legacy_attributes(
    *,
    platform: str,
    site: str,
    category_id: str,
    category_path: str = "",
    description_category_id: str = "",
    required: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    optional: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> CategoryDefinition:
    """把平台归一化后的 required/optional legacy 列表装配成类型化定义。"""

    required_definitions = tuple(
        definition
        for definition in (
            legacy_attribute_to_definition(item, platform=platform)
            for item in required
            if isinstance(item, dict)
        )
        if definition is not None
    )
    optional_definitions = tuple(
        definition
        for definition in (
            legacy_attribute_to_definition(item, platform=platform)
            for item in optional
            if isinstance(item, dict)
        )
        if definition is not None
    )
    return CategoryDefinition(
        platform=platform,
        site=site,
        category_id=category_id,
        category_path=category_path,
        description_category_id=description_category_id,
        required=required_definitions,
        optional=optional_definitions,
    )


# ---------------------------------------------------------------------------
# 有界公共视图投影
# ---------------------------------------------------------------------------


def public_attribute_summary(
    definition: CategoryAttributeDefinition,
) -> CategoryAttributeSummary:
    return CategoryAttributeSummary(
        id=definition.id,
        name=definition.name,
        required=definition.required,
        value_type=definition.value_type,
        value_mode=definition.value_mode,
        allow_custom_values=definition.allow_custom_values,
        is_dictionary=definition.is_dictionary,
        is_collection=definition.is_collection,
        max_value_count=definition.max_value_count,
        dictionary_id=definition.dictionary_id,
        default_unit=definition.default_unit,
        default_unit_id=definition.default_unit_id,
        unit_options=definition.unit_options,
        options=definition.options,
        has_more_values=definition.has_more_values,
    )


def _decode_cursor(cursor: str) -> int:
    text = _text(cursor)
    if not text:
        return 0
    if not text.startswith("offset:"):
        raise ValueError("非法的属性分页 cursor。")
    try:
        offset = int(text.split(":", 1)[1])
    except ValueError as exc:
        raise ValueError("非法的属性分页 cursor。") from exc
    if offset < 0:
        raise ValueError("非法的属性分页 cursor。")
    return offset


def paginate_value_candidates(
    candidates: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    platform: str,
    site: str,
    category_id: str,
    attribute_id: str,
    query: str = "",
    cursor: str = "",
    limit: int = 50,
) -> "CategoryAttributeValuePage":
    """对内存候选列表做关键词过滤 + offset 游标分页。

    ``candidates`` 项为 ``(value, dictionary_value_id)``。用于候选全集可
    一次性从平台读取（Mercado Libre / Yandex）的场景；Ozon 走服务端分页。
    """

    from erp_web.schemas.category_definition import (
        CategoryAttributeValue,
        CategoryAttributeValuePage,
    )

    safe_limit = max(1, min(100, int(limit or 50)))
    offset = _decode_cursor(cursor)
    normalized_query = _text(query).casefold()
    filtered = [
        (value, dictionary_value_id)
        for value, dictionary_value_id in candidates
        if value and (not normalized_query or normalized_query in value.casefold())
    ]
    window = filtered[offset : offset + safe_limit]
    has_more = offset + safe_limit < len(filtered)
    return CategoryAttributeValuePage(
        platform=platform,
        site=site,
        category_id=category_id,
        attribute_id=attribute_id,
        limit=safe_limit,
        cursor=_text(cursor),
        values=tuple(
            CategoryAttributeValue(value=value, dictionary_value_id=dictionary_value_id)
            for value, dictionary_value_id in window
        ),
        next_cursor=f"offset:{offset + safe_limit}" if has_more else "",
        has_more=has_more,
    )


def project_attribute_page(
    definition: CategoryDefinition,
    *,
    cursor: str = "",
    limit: int = 50,
) -> CategoryAttributePage:
    """内部定义 → 前端/Agent 的有界分页公共视图。"""

    safe_limit = max(1, min(100, int(limit or 50)))
    offset = _decode_cursor(cursor)
    attributes = [*definition.required, *definition.optional]
    window = attributes[offset : offset + safe_limit]
    has_more = offset + safe_limit < len(attributes)
    return CategoryAttributePage(
        platform=definition.platform,
        site=definition.site,
        category_id=definition.category_id,
        category_path=definition.category_path,
        limit=safe_limit,
        cursor=cursor,
        attributes=tuple(public_attribute_summary(item) for item in window),
        next_cursor=f"offset:{offset + safe_limit}" if has_more else "",
        has_more=has_more,
    )

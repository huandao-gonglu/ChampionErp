"""类目品牌属性与“无品牌”语义的依赖轻量公共规则。"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


# 商品侧可能出现的“无品牌/白牌”事实。它们只用于判断语义，最终写入
# strict_enum 属性的值仍必须来自平台字典查询。
_NO_BRAND_FACT_MARKERS = frozenset(
    {
        "无品牌",
        "无商标",
        "白牌",
        "其他",
        "其它",
        "generic",
        "no brand",
        "nobrand",
        "other",
        "others",
        "unbranded",
        "нет бренда",
    }
)

# 只有平台已经明确稳定的属性身份才按 ID 识别；属性 ID 必须带平台作用域。
# 例如 Yandex 的属性 85 可以是颜色，不能误用 Ozon 的品牌含义。
_BRAND_ATTRIBUTE_IDS = {
    "ozon": frozenset({"85"}),
    "mercadolibre": frozenset({"BRAND"}),
}
_BRAND_NAME_WORDS = frozenset({"бренд", "brand", "marca"})

# 平台官方“无品牌”值的查询词与规范文本。这里不保存 dictionary_value_id：
# 即使文本稳定，ID 仍必须从当前类目、当前凭据作用域的实时平台结果取得。
_NO_BRAND_QUERY_TERMS = {
    "ozon": "нет бренда",
}
_OFFICIAL_NO_BRAND_VALUES = {
    "ozon": frozenset({"нет бренда"}),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def is_no_brand_fact(value: Any) -> bool:
    """商品字段是否明确表达“无品牌”，而不是某个真实品牌名。"""

    return _text(value).casefold() in _NO_BRAND_FACT_MARKERS


def no_brand_query_term(platform: str) -> str:
    """返回目标平台官方“无品牌”字典文本的检索词。"""

    return _NO_BRAND_QUERY_TERMS.get(_text(platform).lower(), "")


def is_official_no_brand_value(platform: str, value: Any) -> bool:
    """平台候选文本是否为该平台的官方“无品牌”值。"""

    values = _OFFICIAL_NO_BRAND_VALUES.get(_text(platform).lower(), frozenset())
    return _text(value).casefold() in values


def is_brand_attribute_id(platform: str, attribute_id: Any) -> bool:
    """按平台作用域判断稳定的品牌属性 ID。"""

    platform_key = _text(platform).lower()
    attr_id = _text(attribute_id)
    known_ids = _BRAND_ATTRIBUTE_IDS.get(platform_key, frozenset())
    if platform_key == "mercadolibre":
        return attr_id.upper() in known_ids
    return attr_id in known_ids


def is_brand_attribute(
    definition: Mapping[str, Any],
    *,
    platform: str,
) -> bool:
    """结合平台作用域、属性 ID 与名称判断品牌属性。"""

    attr_id = _text(definition.get("id"))
    if is_brand_attribute_id(platform, attr_id):
        return True
    name = _text(definition.get("name")).casefold()
    words = set(re.findall(r"[^\W_]+", name, flags=re.UNICODE))
    # 拉丁/西里尔品牌词必须是完整单词；否则 `marca` 会误命中
    # 西语 `marcador`（标记器）。中文没有空格分词，保留明确的“品牌”子串。
    return bool(words & _BRAND_NAME_WORDS) or "品牌" in name


def normalize_attribute_value_query(
    platform: str,
    attribute_id: Any,
    query: Any,
) -> str:
    """把品牌“无品牌”别名转换为平台官方检索词，其余查询保持原样。"""

    raw_query = _text(query)
    if not is_brand_attribute_id(platform, attribute_id):
        return raw_query
    term = no_brand_query_term(platform)
    if raw_query and term and is_no_brand_fact(raw_query):
        return term
    return raw_query


def product_context_declares_no_brand(product_context: Mapping[str, Any]) -> bool:
    """商品上下文是否只有“无品牌”事实且不存在更具体的真实品牌。"""

    brand_values: list[str] = []
    for scope_name in ("draft", "product", "source"):
        scope = product_context.get(scope_name)
        if not isinstance(scope, Mapping) or "brand" not in scope:
            continue
        brand_values.append(_text(scope.get("brand")))
    if not brand_values:
        return False
    if any(value and not is_no_brand_fact(value) for value in brand_values):
        return False
    return any(is_no_brand_fact(value) for value in brand_values)


__all__ = [
    "is_brand_attribute",
    "is_brand_attribute_id",
    "is_no_brand_fact",
    "is_official_no_brand_value",
    "no_brand_query_term",
    "normalize_attribute_value_query",
    "product_context_declares_no_brand",
]

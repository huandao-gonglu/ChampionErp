"""类目检索语言契约；独立于草稿语言，不根据商品原文切换搜索语言。"""

from __future__ import annotations

from unicodedata import name
from collections.abc import Sequence

from erp_web.marketplace_registry import marketplace_spec
from erp_web.schemas.ai_tools import AiToolExecutionError


def category_search_language(platform: str, site: str) -> str:
    spec = marketplace_spec(platform)
    if spec is None:
        raise AiToolExecutionError("CATEGORY_SEARCH_PLATFORM_INVALID", "类目搜索需要已注册的目标平台。")
    key = str(site or "").strip().casefold()
    selected = next((row for row in spec.sites if row["key"].casefold() == key), None)
    if not key:
        selected = spec.sites[0]
    if selected is None:
        raise AiToolExecutionError("CATEGORY_SEARCH_SITE_INVALID", "类目搜索需要有效的目标站点。")
    overrides = dict(spec.category_search_languages)
    return overrides.get(selected["key"], str(selected["language"]))


def validate_category_keywords(keywords: Sequence[str], language: str) -> None:
    """拒绝错误文字体系；短词的英/西/葡语语义由固定指令与评测约束。"""
    required_script = "CYRILLIC" if language.startswith("ru") else "LATIN"
    allowed_scripts = {required_script, "LATIN"}
    for keyword in keywords:
        letters = [name(char, "") for char in keyword if char.isalpha()]
        if (
            not any(required_script in char for char in letters)
            or any(not any(script in char for script in allowed_scripts) for char in letters)
        ):
            raise AiToolExecutionError(
                "CATEGORY_SEARCH_LANGUAGE_MISMATCH",
                f"本次类目搜索固定使用 {language}。请把整批品名改为该语言，禁止中文或换语言试搜；"
                "品牌/型号可保留，但必须包含该语言的商品通用名。本批尚未执行搜索。",
            )


__all__ = ["category_search_language", "validate_category_keywords"]

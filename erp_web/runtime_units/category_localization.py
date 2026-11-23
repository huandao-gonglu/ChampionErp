# -*- coding: utf-8 -*-
"""三平台类目与属性的三级自动本地化辅助层（L1 官方中文 / L2 SQLite 词典 / L3 过滤规则）。

展示文案与发布值严格分离：
- 本模块只补全 ``name_zh`` / ``path_zh`` / ``description_zh`` / ``label_zh``；
- 绝不修改原文 ``name`` / ``path`` / ``value`` / ``dictionary_value_id``；
- 同步查询仅访问本地 SQLite L2 词典（0ms、0 Token），绝不阻塞发起网络或 AI 请求。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from erp_web.context import get_context
from erp_web.russian_text import has_cjk, is_non_translatable_value
from erp_web.runtime_units.category_definition_support import (
    skip_attribute_value_localization,
)
from erp_web.schemas.category_definition import (
    CategoryAttributeOptionPreview,
    CategoryAttributePage,
    CategoryAttributeValue,
    CategoryAttributeValuePage,
)
from erp_web.schemas.category_search_language import category_search_language
from erp_web.services.operation_availability import require_model_platform


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_zh(original: str, candidate_zh: str) -> str:
    zh = _text(candidate_zh)
    if not zh or not has_cjk(zh):
        return ""
    return zh


def seed_glossary_from_bilingual(
    platform: str,
    pairs: Mapping[str, str],
    *,
    target_language: str = "zh-CN",
) -> int:
    """将 Ozon L1 官方中文等已确认的双语映射沉淀进 L2 词典。"""

    valid: dict[str, str] = {}
    for orig, zh in pairs.items():
        src = _text(orig)
        dst = _valid_zh(src, zh)
        if src and dst and src != dst and not has_cjk(src):
            valid[src] = dst
    if not valid:
        return 0
    try:
        return get_context().category_catalog_store.put_glossary(
            platform,
            target_language,
            valid,
        )
    except Exception:
        return 0


def _assemble_path_zh(
    path_text: str,
    path_segments: list[Any] | tuple[Any, ...] | None,
    glossary: Mapping[str, str],
) -> str:
    """优先查整串命中，否则按路径节点段从 L2 词典拼装中文路径。"""

    if not path_text:
        return ""
    direct_zh = _valid_zh(path_text, glossary.get(path_text, ""))
    if direct_zh:
        return direct_zh
    raw_segments = path_segments or [
        part.strip() for part in path_text.split(" / ") if part.strip()
    ]
    zh_segments: list[str] = []
    any_zh = False
    for segment in raw_segments:
        seg = _text(segment)
        if not seg:
            continue
        seg_zh = _valid_zh(seg, glossary.get(seg, ""))
        if seg_zh:
            zh_segments.append(seg_zh)
            any_zh = True
        else:
            zh_segments.append(seg)
    return " / ".join(zh_segments) if any_zh else ""


def _candidate_path_text(item: Mapping[str, Any]) -> str:
    explicit = _text(item.get("path") or item.get("category_path"))
    if explicit:
        return explicit
    segments = item.get("path_segments")
    if isinstance(segments, (list, tuple)):
        parts = [_text(seg) for seg in segments if _text(seg)]
        if parts:
            return " / ".join(parts)
    return ""


def enrich_category_candidates_l2(
    platform: str,
    candidates: list[dict[str, Any]],
    *,
    target_language: str = "zh-CN",
) -> list[dict[str, Any]]:
    """对类目候选列表执行 0ms L2 词典回填，补齐 ``name_zh`` 与 ``path_zh``。"""

    if not candidates:
        return candidates
    needed: list[str] = []
    for item in candidates:
        name = _text(item.get("name") or item.get("name_original"))
        if name and not _valid_zh(name, item.get("name_zh")) and not is_non_translatable_value(name):
            needed.append(name)
        path_text = _candidate_path_text(item)
        if path_text and not _valid_zh(path_text, item.get("path_zh")) and not is_non_translatable_value(path_text):
            needed.append(path_text)
            for segment in (item.get("path_segments") or path_text.split(" / ")):
                seg = _text(segment)
                if seg and not is_non_translatable_value(seg):
                    needed.append(seg)
    glossary: dict[str, str] = {}
    if needed:
        try:
            glossary = get_context().category_catalog_store.get_glossary(
                platform,
                target_language,
                needed,
                fallback_general=True,
            )
        except Exception:
            glossary = {}

    enriched: list[dict[str, Any]] = []
    for item in candidates:
        row = dict(item)
        name = _text(row.get("name") or row.get("name_original"))
        name_zh = _valid_zh(name, row.get("name_zh"))
        if not name_zh and name:
            name_zh = _valid_zh(name, glossary.get(name, ""))
        if name_zh:
            row["name_zh"] = name_zh

        path_text = _candidate_path_text(row)
        path_zh = _valid_zh(path_text, row.get("path_zh"))
        if not path_zh and path_text:
            path_zh = _assemble_path_zh(
                path_text,
                row.get("path_segments"),
                glossary,
            )
        if path_zh:
            row["path_zh"] = path_zh
        enriched.append(row)
    return enriched


def enrich_attribute_page_l2(
    page: CategoryAttributePage,
    *,
    target_language: str = "zh-CN",
) -> CategoryAttributePage:
    """对属性分页公共视图执行 0ms L2 词典回填（路径按段拼装、属性名、说明、非跳过型预览选项）。"""

    needed: list[str] = []
    if page.category_path and not _valid_zh(page.category_path, page.category_path_zh):
        needed.append(page.category_path)
        for segment in page.category_path.split(" / "):
            seg = _text(segment)
            if seg and not is_non_translatable_value(seg):
                needed.append(seg)
    for attr in page.attributes:
        if attr.name and not _valid_zh(attr.name, attr.name_zh) and not is_non_translatable_value(attr.name):
            needed.append(attr.name)
        if (
            attr.description
            and not _valid_zh(attr.description, attr.description_zh)
            and not is_non_translatable_value(attr.description)
        ):
            needed.append(attr.description)
        if not attr.skip_value_localization:
            for option in attr.options:
                if (
                    option.value
                    and not _valid_zh(option.value, option.label_zh)
                    and not is_non_translatable_value(option.value)
                ):
                    needed.append(option.value)

    if not needed:
        return page
    try:
        glossary = get_context().category_catalog_store.get_glossary(
            page.platform,
            target_language,
            needed,
            fallback_general=True,
        )
    except Exception:
        return page
    if not glossary:
        return page

    category_path_zh = _valid_zh(page.category_path, page.category_path_zh) or _assemble_path_zh(
        page.category_path,
        None,
        glossary,
    )
    updated_attrs = []
    for attr in page.attributes:
        name_zh = _valid_zh(attr.name, attr.name_zh) or _valid_zh(
            attr.name, glossary.get(attr.name, "")
        )
        description_zh = _valid_zh(attr.description, attr.description_zh) or _valid_zh(
            attr.description, glossary.get(attr.description, "")
        )
        if attr.skip_value_localization or not attr.options:
            updated_options = attr.options
        else:
            updated_options = tuple(
                CategoryAttributeOptionPreview(
                    value=opt.value,
                    dictionary_value_id=opt.dictionary_value_id,
                    label_zh=(
                        _valid_zh(opt.value, opt.label_zh)
                        or _valid_zh(opt.value, glossary.get(opt.value, ""))
                    ),
                )
                for opt in attr.options
            )
        updated_attrs.append(
            attr.model_copy(
                update={
                    "name_zh": name_zh,
                    "description_zh": description_zh,
                    "options": updated_options,
                }
            )
        )
    return page.model_copy(
        update={
            "category_path_zh": category_path_zh,
            "attributes": tuple(updated_attrs),
        }
    )


def enrich_attribute_value_page_l2(
    page: CategoryAttributeValuePage,
    *,
    attribute_name: str = "",
    target_language: str = "zh-CN",
) -> CategoryAttributeValuePage:
    """对枚举值分页视图执行 0ms L2 词典回填；品牌/型号/纯数字自动跳过。"""

    if not page.values or skip_attribute_value_localization(
        page.platform, page.attribute_id, attribute_name
    ):
        return page
    needed = [
        item.value
        for item in page.values
        if item.value
        and not _valid_zh(item.value, item.label_zh)
        and not is_non_translatable_value(item.value)
    ]
    if not needed:
        return page
    try:
        glossary = get_context().category_catalog_store.get_glossary(
            page.platform,
            target_language,
            needed,
            fallback_general=True,
        )
    except Exception:
        return page
    if not glossary:
        return page
    return page.model_copy(
        update={
            "values": tuple(
                CategoryAttributeValue(
                    value=item.value,
                    dictionary_value_id=item.dictionary_value_id,
                    label_zh=(
                        _valid_zh(item.value, item.label_zh)
                        or _valid_zh(item.value, glossary.get(item.value, ""))
                    ),
                )
                for item in page.values
            )
        }
    )


def resolve_manual_search_query(
    platform: str,
    site: str,
    query: str,
) -> tuple[str, str, str]:
    """将人工类目搜索中的中文关键词转换为目标平台检索语言（带 L2 永久缓存）。

    返回 ``(effective_query, target_language, source)``：
    - Ozon 已有官方中文 FTS5 索引，以及所有非中文/纯 ID 查询直接透传；
    - Yandex / Mercado Libre 中文查询依次走：
      1) L2 正向词典 0ms 命中（``search_query:{platform}``）；
      2) L2 反向精确/同义词典 0ms 命中（并回种正向词典）；
      3) 复用唯一 ``text.translate`` 翻译并永久入库 L2；
      4) 若模型未配置或失败，先尝试 L2 反向子串兜底，否则给出明确可操作提示。
    """

    plat = _text(platform).lower()
    cleaned = _text(query)
    if not cleaned or plat == "ozon" or not has_cjk(cleaned):
        return cleaned, "", ""

    try:
        target_language = category_search_language(plat, site)
    except Exception:
        if plat == "yandex":
            target_language = "ru-RU"
        elif _text(site).upper() == "CBT":
            target_language = "en-US"
        else:
            target_language = "es-MX"

    from erp_web.runtime_units import text_translation

    query_domain = f"search_query:{plat}"
    # 1) 0ms L2 正向缓存查询
    try:
        cached = text_translation.translate_texts_with_meta(
            target_language,
            {"q": cleaned},
            domain=query_domain,
            cache_only=True,
        )
        hit = _text((cached.get("translations") or {}).get("q"))
        if hit and not has_cjk(hit):
            return hit, target_language, "cache"
    except Exception:
        pass

    store = get_context().category_catalog_store
    # 2) 0ms L2 反向精确/同义词命中（如 Yandex 已有“活动扳手”→“разводной”）
    try:
        exact_matches = store.reverse_lookup_glossary(
            plat, "zh-CN", cleaned, exact_only=True, limit=1
        )
        if exact_matches and not has_cjk(exact_matches[0]):
            matched = exact_matches[0]
            try:
                store.put_glossary(query_domain, target_language, {cleaned: matched})
            except Exception:
                pass
            return matched, target_language, "glossary"
    except Exception:
        pass

    # 3) 首次生词走唯一 text.translate 并永久写入 L2
    try:
        require_model_platform(
            get_context().config.load_app_config(),
            "text.translate",
        )
        translated_meta = text_translation.translate_texts_with_meta(
            target_language,
            {"q": cleaned},
            domain=query_domain,
            timeout_seconds=30,
        )
        translated = _text((translated_meta.get("translations") or {}).get("q"))
        if translated and not has_cjk(translated):
            return translated, target_language, str(translated_meta.get("source") or "ai")
    except Exception as exc:
        # 4) 未配模型或瞬时失败时，再尝试一次反向子串兜底，避免完全无法搜索
        try:
            fallback_matches = store.reverse_lookup_glossary(
                plat, "zh-CN", cleaned, exact_only=False, limit=1
            )
            if fallback_matches and not has_cjk(fallback_matches[0]):
                return fallback_matches[0], target_language, "glossary_fallback"
        except Exception:
            pass
        raise RuntimeError(
            "使用中文搜索该平台类目需要先在模型设置中配置可用的「文本翻译」模型，"
            f"或直接输入平台原文关键词 / 类目 ID。（原因：{exc}）"
        ) from exc

    return cleaned, target_language, ""


__all__ = [
    "enrich_attribute_page_l2",
    "enrich_attribute_value_page_l2",
    "enrich_category_candidates_l2",
    "resolve_manual_search_query",
    "seed_glossary_from_bilingual",
]

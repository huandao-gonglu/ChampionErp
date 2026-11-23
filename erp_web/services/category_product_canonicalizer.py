"""跨平台共享的 1× 多模态商品实体标准化器（1+N 类目匹配 Phase 1）。

本模块位于 ``erp_web/services/``，不依赖任何 ``runtime_units``：
1. ``build_product_facts``：按分类信号优先级提取结构化商品事实（材质/品类/结构/用途优先 + SKU 规格维度摘要）；
2. ``resolve_product_main_image``：仅当 ``category.product_match`` 绑定模型具备 ``image_understanding`` 能力时，
   才加载一张合法本地主图（≤8 MiB）为 ``BinaryContent``；否则静默退化为纯文本；
3. ``canonicalize_product_for_category_match``：通过 ``ai_direct_request_service.chat_structured``
   一次调用同时输出商品实物身份与各目标检索语言的规范品类搜索词，供三平台并行预召回复用。
"""

from __future__ import annotations

from html import unescape
import json
import logging
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart

from erp_web.schemas.category_search_language import validate_category_keywords
from erp_web.stores.product_store import normalize_product_fields

from . import ai_direct_request_service, ai_model_config, image_service
from .image_content import image_content

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_MAX_MAIN_IMAGE_BYTES = 8 * 1024 * 1024
_CANONICALIZER_USE_CASE_ID = "category.product_match"

_NOISY_ATTRIBUTE_MARKERS = (
    "price",
    "payment",
    "shipping",
    "delivery",
    "logistics",
    "promotion",
    "discount",
    "coupon",
    "warranty policy",
    "disclaimer",
    "价格",
    "付款",
    "支付",
    "运费",
    "物流",
    "发货",
    "促销",
    "优惠",
    "免责声明",
    "цена",
    "доставка",
    "скидка",
    "оплата",
    "envío",
    "precio",
    "descuento",
    "pago",
)

_PRIORITY_ATTRIBUTE_MARKERS = (
    "材质",
    "材料",
    "面料",
    "成分",
    "类别",
    "品类",
    "类型",
    "分类",
    "结构",
    "形态",
    "用途",
    "适用",
    "功能",
    "款式",
    "风格",
    "工艺",
    "供电",
    "电源",
    "安装",
    "规格",
    "material",
    "fabric",
    "type",
    "category",
    "style",
    "use",
    "usage",
    "purpose",
    "power",
    "voltage",
    "installation",
    "structure",
    "материал",
    "тип",
    "вид",
    "назначение",
)


class ProductCanonicalIdentity(BaseModel):
    """跨平台复用的商品实物身份与多语言规范搜索词。"""

    model_config = ConfigDict(extra="forbid")

    physical_form: str = Field(
        min_length=1,
        max_length=200,
        description="用中文简明描述实际出售的物件形态与核心结构（不带营销词）。",
    )
    material_or_structure: str = Field(
        default="",
        max_length=200,
        description="核心材质、穿戴/工作方式或结构特征；无明确依据时留空。",
    )
    primary_use: str = Field(
        default="",
        max_length=200,
        description="核心日常用途或适用场景；不凭空添加医疗/工业等专门资质。",
    )
    search_terms_by_language: dict[str, list[str]] = Field(
        default_factory=dict,
        description="按请求语言代码返回 3 到 5 个当地市场常用规范品名/别称（去掉品牌、型号与营销修饰语）。",
    )


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _clean_description(value: Any, limit: int = 1600) -> str:
    text = unescape(_HTML_TAG_RE.sub(" ", str(value or "")))
    return _SPACE_RE.sub(" ", text).strip()[:limit]


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _attribute_value(value: Any) -> str | int | float | bool | list[str] | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = _clean_description(value, 240)
        return text or None
    if isinstance(value, (list, tuple)):
        rows = [_clean_description(item, 120) for item in value[:8]]
        rows = [item for item in rows if item]
        return rows or None
    if isinstance(value, Mapping):
        raw_values = value.get("values")
        if isinstance(raw_values, list):
            rows = [
                _clean_description(
                    item.get("value") if isinstance(item, Mapping) else item,
                    120,
                )
                for item in raw_values[:6]
            ]
            rows = [item for item in rows if item]
            return rows or None
        raw_val = value.get("value")
        unit = _text(value.get("unit"), 40)
        if raw_val not in (None, ""):
            val_text = _clean_description(raw_val, 180)
            return f"{val_text} {unit}".strip() if unit else (val_text or None)
    return None


def _is_priority_attribute(name: str) -> bool:
    lowered = name.casefold()
    return any(marker in lowered for marker in _PRIORITY_ATTRIBUTE_MARKERS)


def _key_attributes(*values: Any, limit: int = 16) -> dict[str, Any]:
    prioritized: list[tuple[str, Any]] = []
    regular: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        for key, item in value.items():
            name = _text(key, 100)
            normalized_name = name.casefold()
            if (
                not name
                or name.isdigit()
                or any(marker in normalized_name for marker in _NOISY_ATTRIBUTE_MARKERS)
                or name in seen
            ):
                continue
            normalized_value = _attribute_value(item)
            if normalized_value is None:
                continue
            seen.add(name)
            if _is_priority_attribute(name):
                prioritized.append((name, normalized_value))
            else:
                regular.append((name, normalized_value))
    combined = (prioritized + regular)[:limit]
    return dict(combined)


def _sku_options_summary(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
) -> dict[str, list[str]]:
    """提取活跃 SKU 的规格维度去重摘要，辅助识别实物形态与类型。"""

    selected_ids: set[str] = set()
    for row in draft.get("sku_items") or []:
        if isinstance(row, Mapping) and row.get("selected", True):
            sku_id = _text(row.get("sku_id") or row.get("id"), 120)
            if sku_id:
                selected_ids.add(sku_id)

    grouped: dict[str, list[str]] = {}
    for sku in product.get("sku_items") or []:
        if not isinstance(sku, Mapping) or sku.get("active") is False:
            continue
        sku_id = _text(sku.get("id") or sku.get("sku_id"), 120)
        if selected_ids and sku_id and sku_id not in selected_ids:
            continue
        options = sku.get("options")
        if isinstance(options, Mapping):
            for raw_k, raw_v in options.items():
                k = _text(raw_k, 60)
                v = _text(raw_v, 80)
                if not k or not v:
                    continue
                bucket = grouped.setdefault(k, [])
                if v not in bucket and len(bucket) < 8:
                    bucket.append(v)
        elif isinstance(options, list):
            for item in options:
                if not isinstance(item, Mapping):
                    continue
                k = _text(item.get("name") or item.get("key"), 60)
                v = _text(item.get("value"), 80)
                if not k or not v:
                    continue
                bucket = grouped.setdefault(k, [])
                if v not in bucket and len(bucket) < 8:
                    bucket.append(v)
        if len(grouped) >= 6:
            break
    return grouped


def build_product_facts(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成用于类目匹配的精炼商品事实；分类信号优先，附带 SKU 规格摘要。"""

    normalized = normalize_product_fields(dict(product))
    source = _first_mapping(normalized.get("source"), product.get("source"))
    draft_data = draft if isinstance(draft, Mapping) else {}
    target_data = target if isinstance(target, Mapping) else {}

    source_title = _text(
        source.get("title") or normalized.get("name") or product.get("name"),
        500,
    )
    target_title = _text(draft_data.get("title"), 500)
    target_description = _clean_description(
        draft_data.get("description")
        or draft_data.get("description_html")
        or draft_data.get("body")
    )
    facts_block: dict[str, Any] = {
        "source_category": _text(
            normalized.get("category") or source.get("category"),
            240,
        ),
        "brand": _text(
            draft_data.get("brand")
            or source.get("brand")
            or normalized.get("brand"),
            160,
        ),
        "model": _text(
            draft_data.get("model")
            or source.get("model")
            or normalized.get("model"),
            160,
        ),
        "attributes": _key_attributes(
            source.get("attributes"),
            normalized.get("attributes"),
            draft_data.get("attributes"),
        ),
    }
    sku_options = _sku_options_summary(normalized, draft_data)
    if sku_options:
        facts_block["sku_options"] = sku_options

    return {
        "source": {
            "language": _text(
                source.get("language")
                or source.get("locale")
                or normalized.get("source_language"),
                80,
            ),
            "title": source_title,
        },
        "target": {
            "language": _text(
                draft_data.get("language")
                or target_data.get("language")
                or target_data.get("locale"),
                80,
            ),
            "title": target_title,
            "description": target_description,
        },
        "facts": facts_block,
    }


def _candidate_image_entries(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    source = product.get("source") if isinstance(product.get("source"), Mapping) else {}
    raw_pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    pool_by_id = {
        _text(item.get("id"), 160): item
        for item in raw_pool
        if isinstance(item, Mapping) and _text(item.get("id"), 160)
    }

    ordered: list[Mapping[str, Any]] = []
    seen_ids: set[str] = set()

    # 优先使用草稿选定的主图顺序
    for key in ("selected_image_ids", "image_ids"):
        ids = draft.get(key)
        if isinstance(ids, list):
            for img_id in ids:
                key_id = _text(img_id, 160)
                if key_id and key_id in pool_by_id and key_id not in seen_ids:
                    ordered.append(pool_by_id[key_id])
                    seen_ids.add(key_id)

    # 其次按 image_pool 中的主图标记与自然顺序
    for item in raw_pool:
        if not isinstance(item, Mapping):
            continue
        item_id = _text(item.get("id"), 160)
        if item.get("is_main") and item_id not in seen_ids:
            ordered.insert(0, item)
            if item_id:
                seen_ids.add(item_id)
    for item in raw_pool:
        if not isinstance(item, Mapping):
            continue
        item_id = _text(item.get("id"), 160)
        if item_id and item_id in seen_ids:
            continue
        ordered.append(item)
        if item_id:
            seen_ids.add(item_id)

    # 兼容直接挂在 product.images / draft.images 的图片列表
    for container in (draft.get("images"), product.get("images")):
        if isinstance(container, list):
            for item in container:
                if isinstance(item, Mapping):
                    ordered.append(item)
                elif isinstance(item, str) and item.strip():
                    ordered.append({"path": item.strip()})
    return ordered


def resolve_product_main_image(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    *,
    app_dir: Path | str,
    app_config: Mapping[str, Any],
    use_case_id: str = _CANONICALIZER_USE_CASE_ID,
) -> BinaryContent | None:
    """仅当目标模型声明支持识图时加载一张合法本地主图；否则静默返回 None。"""

    try:
        model = ai_model_config.resolve_ai_model(dict(app_config), use_case_id)
    except Exception:
        return None
    if not ai_model_config.model_has_capabilities(
        model,
        [ai_model_config.CAP_IMAGE_UNDERSTANDING],
    ):
        return None

    for entry in _candidate_image_entries(product, draft):
        for field_name in ("path", "local_path", "file_path"):
            raw_path = _text(entry.get(field_name), 1000)
            if not raw_path:
                continue
            try:
                resolved = image_service.resolve_local_path(raw_path, Path(app_dir))
            except Exception:
                continue
            if resolved is None or not resolved.is_file():
                continue
            try:
                raw_bytes = resolved.read_bytes()
                if not raw_bytes or len(raw_bytes) > _MAX_MAIN_IMAGE_BYTES:
                    continue
                _, _, media_type = image_content(raw_bytes)
                return BinaryContent(data=raw_bytes, media_type=media_type)
            except Exception:
                continue
    return None


def _sanitize_search_terms(
    raw_terms: Mapping[str, Sequence[str]],
    languages: Sequence[str],
) -> dict[str, list[str]]:
    sanitized: dict[str, list[str]] = {}
    for lang in languages:
        candidates = raw_terms.get(lang) or raw_terms.get(lang.lower()) or []
        valid: list[str] = []
        seen: set[str] = set()
        for term in candidates:
            cleaned = " ".join(str(term or "").split()).strip()[:100]
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            try:
                validate_category_keywords([cleaned], lang)
            except Exception:
                continue
            seen.add(key)
            valid.append(cleaned)
            if len(valid) >= 6:
                break
        if valid:
            sanitized[lang] = valid
    return sanitized


def canonicalize_product_for_category_match(
    facts: Mapping[str, Any],
    languages: Sequence[str],
    *,
    app_dir: Path | str,
    app_config: Mapping[str, Any],
    image_content_part: BinaryContent | None = None,
    timeout_seconds: float = 30.0,
) -> ProductCanonicalIdentity | None:
    """一次结构化调用识别商品实物身份并生成各检索语言的规范品名。

    若模型不可用、超时或输出异常，返回 ``None`` 以便调用方无缝退回常规工具检索。
    """

    unique_languages = list(dict.fromkeys(str(lang).strip() for lang in languages if str(lang).strip()))
    if not unique_languages:
        return None
    try:
        model = ai_model_config.resolve_ai_model(dict(app_config), _CANONICALIZER_USE_CASE_ID)
    except Exception:
        return None

    system_prompt = (
        "你是跨境电商商品实体识别器。根据商品原始标题、属性、SKU 规格（及可选主图），"
        "准确判断实际出售的是什么物件、核心材质/结构与日常用途，并为请求的每种语言"
        "生成 3 到 5 个当地电商平台常用的规范品类搜索词（含简短通用名与常见别称）。\n"
        "严格约束：\n"
        "1. 原始规格和图片视觉事实优先于营销标题和译文；\n"
        "2. 去掉品牌、型号、人群和促销修饰语，不要凭空添加医疗/工业认证或换成结构不同的邻近商品；\n"
        "3. search_terms_by_language 的键必须严格等于请求的语言代码，每种语言的搜索词必须严格使用该语言的文字体系（例如 ru 使用俄语西里尔字母，es 使用西班牙语，pt 使用葡萄牙语，en 使用英语）。"
    )
    user_payload = json.dumps(
        {
            "requested_languages": unique_languages,
            "product": dict(facts),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    user_text = (
        "请识别商品实物身份并输出各目标语言的规范类目搜索词（JSON）：\n"
        f"{user_payload}"
    )

    required_caps: list[str] = [ai_model_config.CAP_CHAT, ai_model_config.CAP_JSON]
    if image_content_part is not None:
        required_caps.append(ai_model_config.CAP_IMAGE_UNDERSTANDING)
        messages: list[Any] = [
            ModelRequest(
                parts=[UserPromptPart(content=[user_text, image_content_part])],
                instructions=system_prompt,
            )
        ]
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]

    try:
        result = ai_direct_request_service.chat_structured(
            app_dir=app_dir,
            use_case_id=_CANONICALIZER_USE_CASE_ID,
            model=model,
            required_capabilities=required_caps,
            messages=messages,
            generation_settings=None,
            temperature=0.1,
            max_tokens=None,
            timeout_seconds=max(5, int(timeout_seconds)),
            stream=False,
            output_type=ProductCanonicalIdentity,
        )
    except Exception as exc:
        logger.info("商品实体标准化调用降级（将回退常规工具检索）：%s", exc)
        return None

    sanitized_terms = _sanitize_search_terms(
        result.search_terms_by_language,
        unique_languages,
    )
    return result.model_copy(update={"search_terms_by_language": sanitized_terms})


__all__ = [
    "ProductCanonicalIdentity",
    "build_product_facts",
    "canonicalize_product_for_category_match",
    "resolve_product_main_image",
]

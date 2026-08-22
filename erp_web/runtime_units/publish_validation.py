# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.context import get_context
from erp_web.product_model import validate_category_precheck
from erp_web.services.pricing_service import pricing_calculation_fingerprint
from erp_web.stores.product_store import normalize_product_fields

from .collect_helpers import collect_time_iso
from .publish_helpers import (
    _draft_for_platform,
    _draft_images,
    _has_main_image,
    _masked_auth_status,
    _required_attribute_summary,
    precheck_item,
)
from .publish_ozon import (
    ozon_category_pair,
    ozon_invalid_dictionary_attributes,
    ozon_required_attributes_missing,
)
from .publish_yandex import (
    _public_picture_invalid,
    yandex_invalid_dictionary_attributes,
    yandex_invalid_unit_attributes,
    yandex_offer_identity_conflict,
    yandex_required_attributes_missing,
)


def _review_field_from_item(item: Any) -> str:
    if isinstance(item, dict):
        if str(item.get("code") or "") != "NEED_REVIEW_ATTRIBUTES":
            return ""
        return str(item.get("field") or "").strip()
    return str(item or "").strip()


def _review_attr_id(field: str) -> str:
    value = str(field or "").strip()
    if value == "attributes":
        return ""
    return value.split(".", 1)[-1] if value.startswith("attributes.") else value


def _review_attr_field(attr_id: str) -> str:
    value = _review_attr_id(attr_id)
    return f"attributes.{value}" if value else "attributes"


def _review_precheck_items(need_review: list[str], severity: str) -> list[dict[str, str]]:
    attr_ids = sorted({_review_attr_id(item) for item in need_review if _review_attr_id(item)})
    return [
        precheck_item(
            "NEED_REVIEW_ATTRIBUTES",
            _review_attr_field(attr_id),
            f"属性待复核：{attr_id}",
            severity,
            f"前往类目属性页补齐或确认 {attr_id}",
        )
        for attr_id in attr_ids
    ]


def _local_category_record(product: dict[str, Any], platform: str, category_id: str) -> dict[str, Any] | None:
    categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
    record = categories.get(platform)
    if not isinstance(record, dict):
        return None
    wanted = str(category_id or "").strip()
    record_id = str(record.get("category_id") or record.get("subject_id") or record.get("type_id") or "").strip()
    if wanted and record_id and wanted != record_id:
        return None
    return record


def _normalized_number(value: Any) -> str:
    try:
        return format(float(str(value or "0").replace(",", ".")), ".8f").rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return "0"


def _selected_price_errors(product: dict[str, Any], draft: dict[str, Any]) -> list[dict[str, str]]:
    listing_currency = str(draft.get("listing_currency") or "").strip().upper()
    selected = draft.get("selected_pricing") if isinstance(draft.get("selected_pricing"), dict) else {}
    if not selected:
        platform = str(draft.get("platform") or "").strip().lower()
        site = str(draft.get("site") or draft.get("site_id") or "").strip().lower()
        target_key = f"{platform}:{site}"
        pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
        targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
        selected = next(
            (item for key, item in targets.items() if str(key).lower() == target_key and isinstance(item, dict)),
            {},
        )
        target_sites = draft.get("target_sites") if isinstance(draft.get("target_sites"), list) else []
        target = next(
            (
                item for item in target_sites
                if isinstance(item, dict)
                and str(item.get("platform") or "").lower() == platform
                and str(item.get("site") or "").lower() == site
            ),
            {},
        )
        listing_currency = str(
            target.get("listing_currency")
            or selected.get("listing_currency")
            or listing_currency
        ).strip().upper()
    applied = selected.get("applied_price") if isinstance(selected.get("applied_price"), dict) else {}
    applied_currency = str(applied.get("currency") or "").strip().upper()
    basis = selected.get("calculation_basis") if isinstance(selected.get("calculation_basis"), dict) else {}
    fingerprint = str(selected.get("calculation_fingerprint") or "").strip()
    try:
        amount_valid = float(str(applied.get("amount") or "0").replace(",", ".")) > 0
    except (TypeError, ValueError):
        amount_valid = False
    if not listing_currency:
        return [precheck_item("LISTING_CURRENCY_UNRESOLVED", "listing_currency", "发布币种尚未核验", "error", "先测试店铺授权，再重新核价")]
    if not amount_valid or applied_currency != listing_currency:
        return [precheck_item("PRICING_STALE", "pricing", f"{listing_currency} 发布目标没有有效核价结果", "error", "前往核价页重新计算并应用该目标售价")]
    if (
        not basis
        or fingerprint != pricing_calculation_fingerprint(basis)
        or str(basis.get("listing_currency") or "").upper() != listing_currency
    ):
        return [precheck_item("PRICING_STALE", "pricing", "核价依据缺失或已变化", "error", "前往核价页重新计算并应用售价")]
    product_cost = product.get("cost")
    if product_cost not in (None, "") and _normalized_number(product_cost) != _normalized_number(basis.get("cost_cny")):
        return [precheck_item("PRICING_STALE", "pricing", "商品成本已变化，旧核价结果已失效", "error", "前往核价页重新计算并应用售价")]
    package = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    for package_key, basis_key in (
        ("weight_kg", "weight_kg"),
        ("length_cm", "length_cm"),
        ("width_cm", "width_cm"),
        ("height_cm", "height_cm"),
    ):
        if _normalized_number(package.get(package_key)) != _normalized_number(basis.get(basis_key)):
            return [precheck_item("PRICING_STALE", "pricing", "重量或包装尺寸已变化，旧核价结果已失效", "error", "前往核价页重新计算并应用售价")]
    return []


def validate_mercadolibre_draft(product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "mercadolibre")
    store = config.get("mercadolibre", {}) if isinstance(config.get("mercadolibre"), dict) else {}
    summary = _required_attribute_summary(product, "mercadolibre")
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    auth_status, auth_next = _masked_auth_status("mercadolibre", config)
    title_limit = int(
        get_context().config.load_app_config().get("mercadolibre_title_limit")
        or 60
    )
    title = str(draft.get("title") or "").strip()
    description = str(draft.get("description") or "").strip()
    category_id = str(draft.get("category_id") or "").strip()
    category_id_upper = category_id.upper()
    site_id = str(draft.get("site") or draft.get("site_id") or store.get("site_id") or "").strip().upper()
    category_path = str(draft.get("category_path") or "").strip()
    attrs = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    images = _draft_images(product, "mercadolibre", draft)
    if auth_status in {"未配置", "已保存，未测试", "测试失败", "Token 过期", "权限不足", "被限流"}:
        code = "AUTH_TOKEN_EXPIRED" if auth_status == "Token 过期" else "AUTH_NOT_CONFIGURED"
        errors.append(precheck_item(code, "auth", f"Mercado Libre 授权状态：{auth_status}", "error", auth_next or "前往授权页测试授权"))
    if not title:
        errors.append(precheck_item("TITLE_MISSING", "title", "缺少标题", "error", "前往商品编辑页补齐标题"))
    elif len(title) > title_limit:
        errors.append(precheck_item("TITLE_TOO_LONG", "title", f"标题长度超过 {title_limit} 字符限制", "error", "压缩 Mercado Libre 标题长度"))
    if not description:
        errors.append(precheck_item("DESCRIPTION_MISSING", "description", "缺少描述", "error", "前往商品编辑页补齐描述"))
    if not category_id:
        errors.append(precheck_item("CATEGORY_MISSING", "category_id", "缺少 Mercado Libre 类目 ID", "error", "前往类目属性页选择类目"))
    elif not category_path:
        warnings.append(precheck_item("CATEGORY_PATH_MISSING", "category_path", "类目路径为空，建议重新实时匹配类目", "warning", "前往类目属性页重新选择类目"))
    if site_id == "CBT" and category_id and not category_id_upper.startswith("CBT"):
        errors.append(precheck_item("CATEGORY_SITE_MISMATCH", "category_id", "CBT 发布必须使用 CBT 类目 ID", "error", "前往类目属性页重新实时选择 CBT 类目"))
    elif site_id and site_id != "CBT" and category_id and (category_id_upper.startswith("CBT") or not category_id_upper.startswith(site_id)):
        errors.append(precheck_item("CATEGORY_SITE_MISMATCH", "category_id", f"{site_id} 发布必须使用 {site_id} 类目 ID", "error", "前往类目属性页重新实时选择该站点类目"))
    if summary["missing"]:
        for field in summary["missing"]:
            attr_id = str(field).split(".", 1)[-1]
            errors.append(precheck_item("REQUIRED_ATTRIBUTE_MISSING", field, f"缺少必填属性：{attr_id}", "error", "前往类目属性页补齐必填属性"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "Brand 为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "Model 为空", "error", "前往类目属性页确认 Model"))
    if not str(draft.get("sku") or "").strip():
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    errors.extend(_selected_price_errors(product, draft))
    try:
        if int(float(str(draft.get("stock") or "0").strip() or "0")) <= 0:
            raise ValueError
    except Exception:
        errors.append(precheck_item("STOCK_MISSING", "stock", "库存缺失或无效", "error", "前往商品编辑页填写库存"))
    if not images:
        errors.append(precheck_item("IMAGE_MISSING", "images", "缺少商品图片", "error", "前往图片池导入并勾选图片"))
    if images and not _has_main_image(product, "mercadolibre", draft):
        errors.append(precheck_item("MAIN_IMAGE_MISSING", "images", "缺少主图", "error", "前往图片池设置主图"))
    if images and any(not str(image).startswith(("http://", "https://", "/file?path=")) for image in images):
        warnings.append(precheck_item("IMAGE_NOT_UPLOADED", "images", "存在尚未上传的平台图片引用", "warning", "真实发布前确认图片可访问或已上传平台"))
    for field in ("length_cm", "width_cm", "height_cm"):
        if not str(pkg.get(field) or "").strip():
            errors.append(precheck_item("PACKAGE_DIMENSIONS_MISSING", f"package_dimensions.{field}", f"{field} 缺失", "error", "前往核价页或类目属性页补齐尺寸"))
    if not str(pkg.get("weight_kg") or "").strip():
        errors.append(precheck_item("WEIGHT_MISSING", "package_dimensions.weight_kg", "重量缺失", "error", "前往核价页或类目属性页补齐重量"))

    def review_item_resolved(item: str) -> bool:
        field = str(item or "").strip()
        attr_id = field.split(".", 1)[-1] if field.startswith("attributes.") else field
        package_map = {
            "PACKAGE_LENGTH": "length_cm",
            "PACKAGE_WIDTH": "width_cm",
            "PACKAGE_HEIGHT": "height_cm",
            "PACKAGE_WEIGHT": "weight_kg",
            "SELLER_PACKAGE_LENGTH": "length_cm",
            "SELLER_PACKAGE_WIDTH": "width_cm",
            "SELLER_PACKAGE_HEIGHT": "height_cm",
            "SELLER_PACKAGE_WEIGHT": "weight_kg",
        }
        if attr_id in package_map and str(pkg.get(package_map[attr_id]) or "").strip():
            return True
        return bool(attr_id and str(attrs.get(attr_id) or "").strip())

    need_review: list[str] = []
    local_record = _local_category_record(product, "mercadolibre", category_id)
    local_missing_attributes = [
        field
        for field in validate_category_precheck(product, "mercadolibre", local_record)
        if str(field).startswith("attributes.") and not review_item_resolved(str(field))
    ] if local_record else []
    for item in draft.get("validation_errors") or []:
        raw_field = _review_field_from_item(item)
        if raw_field == "attributes":
            need_review.extend(local_missing_attributes)
        elif raw_field and not review_item_resolved(raw_field):
            need_review.append(raw_field)
    if need_review:
        errors.extend(_review_precheck_items(need_review, "error"))
    if not str(draft.get("upc") or product.get("upc") or "").strip():
        allow_gtin_exemption = bool(draft.get("allow_gtin_exemption") or draft.get("gtin_exempt") or config.get("listing", {}).get("allow_gtin_exemption"))
        if allow_gtin_exemption:
            warnings.append(precheck_item("UPC_MISSING", "upc", "UPC / GTIN 为空，已按配置允许豁免", "warning", "确认 Mercado Libre 类目允许 EMPTY_GTIN_REASON"))
        else:
            errors.append(precheck_item("UPC_MISSING", "upc", "UPC / GTIN 为空，且未确认允许豁免", "error", "前往商品编辑页分配 UPC 或显式确认豁免"))
    terms_raw = product.get("marketplace_terms", {}).get("mercadolibre") if isinstance(product.get("marketplace_terms"), dict) else {}
    sale_terms: Any = []
    if isinstance(terms_raw, dict):
        sale_terms = terms_raw.get("sale_terms") or terms_raw.get("warranty") or []
    elif isinstance(terms_raw, list):
        sale_terms = terms_raw
    if not sale_terms:
        sale_terms = draft.get("sale_terms") or draft.get("warranty") or []
    if not sale_terms:
        sale_terms = config.get("listing", {}).get("mercadolibre_sale_terms") if isinstance(config.get("listing"), dict) else []
    if not sale_terms:
        errors.append(precheck_item("SALE_TERMS_MISSING", "sale_terms", "sale_terms / warranty 尚未配置完整", "error", "前往平台属性页补齐保修条款"))
    draft_shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else {}
    logistic_type = str(draft_shipping.get("logistic_type") or draft_shipping.get("mode") or config.get("listing", {}).get("mercadolibre_logistic_type") or "").strip()
    if not logistic_type:
        errors.append(precheck_item("LOGISTIC_MODE_MISSING", "logistic_type", "未读取 shipping / logistics mode", "error", "发布前在店铺后台确认物流模式，不要自动修改后台模式"))
    return {"platform": "mercadolibre", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


def validate_yandex_draft(product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "yandex")
    store = config.get("yandex", {}) if isinstance(config.get("yandex"), dict) else {}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    auth_status, auth_next = _masked_auth_status("yandex", config)
    # “已保存，未测试”同样阻断发布：凭证或 Campaign ID 切换后必须重新
    # 通过在线授权校验，才能派生 business_id 与价格/库存能力。
    if auth_status in {"未配置", "已保存，未测试", "测试失败", "Token 过期", "权限不足", "被限流"}:
        code = "AUTH_TOKEN_EXPIRED" if auth_status == "Token 过期" else "AUTH_NOT_CONFIGURED"
        errors.append(precheck_item(code, "auth", f"Yandex 授权状态：{auth_status}", "error", auth_next or "前往授权页测试授权"))
    if auth_status == "测试成功" and not str(store.get("business_id") or "").strip():
        errors.append(precheck_item("AUTH_DETAIL_MISSING", "auth", "Yandex business_id 尚未通过在线授权校验派生", "error", "前往授权页重新测试授权"))
    title = str(draft.get("title") or "").strip()
    description = str(draft.get("description") or "").strip()
    category_id = str(draft.get("category_id") or store.get("category_id") or "").strip()
    sku = str(draft.get("sku") or "").strip()
    if not title:
        errors.append(precheck_item("TITLE_MISSING", "title", "缺少标题", "error", "前往商品编辑页补齐标题"))
    if not description:
        errors.append(precheck_item("DESCRIPTION_MISSING", "description", "缺少描述", "error", "前往商品编辑页补齐描述"))
    if not category_id:
        errors.append(precheck_item("CATEGORY_MISSING", "category_id", "缺少 Yandex 类目 ID", "error", "前往类目属性页选择叶子类目"))
    elif not category_id.isdigit() or int(category_id) <= 0:
        errors.append(precheck_item("CATEGORY_INVALID", "category_id", "Yandex 类目 ID 必须是正整数（只能选择叶子类目）", "error", "前往类目属性页重新选择叶子类目"))
    # 稳定 offerId 身份：服务端比较 draft.sku 与历史发布身份，前端锁定
    # 输入只是交互提示，不能充当约束。
    conflict = yandex_offer_identity_conflict(draft)
    if conflict:
        errors.append(precheck_item("OFFER_IDENTITY_CHANGED", "sku", conflict, "error", "确认要创建新的远端商品，或恢复原 SKU 后再发布"))
    invalid_dictionary_ids = set(yandex_invalid_dictionary_attributes(product))
    for attr_id in sorted(invalid_dictionary_ids):
        errors.append(
            precheck_item(
                "ATTRIBUTE_DICTIONARY_VALUE_REQUIRED",
                f"attributes.{attr_id}",
                f"Yandex 属性 {attr_id} 必须从平台枚举中选择",
                "error",
                "前往类目属性页搜索并选择平台允许的值",
            )
        )
    for attr_id in yandex_invalid_unit_attributes(product):
        errors.append(
            precheck_item(
                "ATTRIBUTE_UNIT_INVALID",
                f"attributes.{attr_id}",
                f"Yandex 属性 {attr_id} 的单位不在类目允许范围内",
                "error",
                "前往类目属性页选择类目允许的单位",
            )
        )
    # 必填属性是否解决由共享 owner 唯一裁定，这里不复制规则。
    for field in yandex_required_attributes_missing(product):
        attr_id = str(field).split(".", 1)[-1]
        if attr_id in invalid_dictionary_ids:
            continue
        errors.append(precheck_item("REQUIRED_ATTRIBUTE_MISSING", field, f"缺少 Yandex 必填属性：{attr_id}", "error", "前往类目属性页补齐必填属性"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "品牌为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "型号为空", "error", "前往类目属性页确认 Model"))
    if not sku:
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    errors.extend(_selected_price_errors(product, draft))
    if not str(draft.get("stock") or "").strip():
        errors.append(precheck_item("STOCK_MISSING", "stock", "库存缺失", "error", "前往商品编辑页填写库存"))
    images = _draft_images(product, "yandex", draft)
    if not images:
        errors.append(precheck_item("IMAGE_MISSING", "images", "缺少图片", "error", "前往图片池导入图片"))
    elif any(_public_picture_invalid(image) for image in images):
        source = product.get("source") if isinstance(product.get("source"), dict) else {}
        delivery_errors = [
            str(item.get("delivery_error") or "").strip()
            for item in source.get("image_pool") or []
            if isinstance(item, dict) and str(item.get("delivery_error") or "").strip()
        ]
        message = (
            "；".join(dict.fromkeys(delivery_errors))
            if delivery_errors
            else "Yandex 发布图片必须是平台可访问的 HTTPS 公网 URL"
        )
        errors.append(precheck_item("IMAGE_NOT_PUBLIC", "images", message, "error", "配置图片 HTTPS provider 后重新执行发布预检"))
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    for field in ("length_cm", "width_cm", "height_cm"):
        try:
            if float(str(pkg.get(field) or "0").replace(",", ".")) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(precheck_item("PACKAGE_DIMENSIONS_MISSING", f"package_dimensions.{field}", f"{field} 缺失", "error", "前往核价页补齐尺寸"))
    try:
        if float(str(pkg.get("weight_kg") or "0").replace(",", ".")) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(precheck_item("WEIGHT_MISSING", "package_dimensions.weight_kg", "重量缺失", "error", "前往核价页补齐重量"))
    need_review = [field for item in draft.get("validation_errors") or [] if (field := _review_field_from_item(item))]
    if need_review:
        warnings.extend(_review_precheck_items(need_review, "warning"))
    if not str(draft.get("language") or "").strip():
        warnings.append(precheck_item("LANGUAGE_MISSING", "language", "俄语标题/描述尚未确认", "warning", "发布前确认 Yandex 文案语言"))
    return {"platform": "yandex", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


def validate_ozon_draft(product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "ozon")
    store = config.get("ozon", {}) if isinstance(config.get("ozon"), dict) else {}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    auth_status, auth_next = _masked_auth_status("ozon", config)
    if auth_status in {"未配置", "测试失败", "Token 过期", "权限不足", "被限流"}:
        errors.append(precheck_item("AUTH_NOT_CONFIGURED", "auth", f"Ozon 授权状态：{auth_status}", "error", auth_next or "前往授权页测试授权"))
    if not str(draft.get("title") or "").strip():
        errors.append(precheck_item("TITLE_MISSING", "title", "缺少标题", "error", "前往商品编辑页补齐标题"))
    if not str(draft.get("description") or "").strip():
        errors.append(precheck_item("DESCRIPTION_MISSING", "description", "缺少描述", "error", "前往商品编辑页补齐描述"))
    type_id, description_category_id = ozon_category_pair(product)
    if not type_id:
        errors.append(precheck_item("CATEGORY_MISSING", "category_id", "缺少 Ozon Category / Type ID", "error", "前往类目属性页选择类目"))
    elif not description_category_id:
        errors.append(precheck_item("CATEGORY_PAIR_MISSING", "category_id", "Ozon 类目缺少 type_id 与 description_category_id 配对", "error", "前往类目属性页重新选择 Ozon 实时类目"))
    invalid_dictionary_ids = set(ozon_invalid_dictionary_attributes(product))
    for attr_id in sorted(invalid_dictionary_ids):
        errors.append(
            precheck_item(
                "ATTRIBUTE_DICTIONARY_VALUE_REQUIRED",
                f"attributes.{attr_id}",
                f"Ozon 属性 {attr_id} 必须从平台枚举中选择",
                "error",
                "前往类目属性页搜索并选择平台允许的值",
            )
        )
    for field in ozon_required_attributes_missing(product):
        attr_id = str(field).split(".", 1)[-1]
        if attr_id in invalid_dictionary_ids:
            continue
        errors.append(precheck_item("REQUIRED_ATTRIBUTE_MISSING", field, f"缺少 Ozon 必填属性：{attr_id}", "error", "前往类目属性页补齐必填属性"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "品牌为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "型号为空", "error", "前往类目属性页确认 Model"))
    if not str(draft.get("sku") or "").strip():
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    errors.extend(_selected_price_errors(product, draft))
    if not str(draft.get("stock") or "").strip():
        errors.append(precheck_item("STOCK_MISSING", "stock", "库存缺失", "error", "前往商品编辑页填写库存"))
    images = _draft_images(product, "ozon", draft)
    if not images:
        errors.append(precheck_item("IMAGE_MISSING", "images", "缺少图片", "error", "前往图片池导入图片"))
    elif any(not str(image).startswith(("https://", "http://")) for image in images):
        source = product.get("source") if isinstance(product.get("source"), dict) else {}
        delivery_errors = [
            str(item.get("delivery_error") or "").strip()
            for item in source.get("image_pool") or []
            if isinstance(item, dict) and str(item.get("delivery_error") or "").strip()
        ]
        message = (
            "；".join(dict.fromkeys(delivery_errors))
            if delivery_errors
            else "Ozon 发布图片必须是平台可访问的 HTTP(S) 公网 URL"
        )
        errors.append(precheck_item("IMAGE_NOT_PUBLIC", "images", message, "error", "配置图片 HTTPS provider 后重新执行发布预检"))
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    for field in ("length_cm", "width_cm", "height_cm"):
        try:
            if float(str(pkg.get(field) or "0").replace(",", ".")) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(precheck_item("PACKAGE_DIMENSIONS_MISSING", f"package_dimensions.{field}", f"{field} 缺失", "error", "前往核价页补齐尺寸"))
    try:
        if float(str(pkg.get("weight_kg") or "0").replace(",", ".")) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(precheck_item("WEIGHT_MISSING", "package_dimensions.weight_kg", "重量缺失", "error", "前往核价页补齐重量"))
    need_review = [field for item in draft.get("validation_errors") or [] if (field := _review_field_from_item(item))]
    if need_review:
        warnings.extend(_review_precheck_items(need_review, "warning"))
    return {"platform": "ozon", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


def validate_platform_draft(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform == "mercadolibre":
        return validate_mercadolibre_draft(product, config)
    if platform == "yandex":
        return validate_yandex_draft(product, config)
    if platform == "ozon":
        return validate_ozon_draft(product, config)
    return {
        "platform": platform,
        "ok": False,
        "errors": [precheck_item("UNSUPPORTED_PLATFORM", "platform", "不支持的平台", "error", "切换到受支持的平台")],
        "warnings": [],
        "checked_at": collect_time_iso(),
    }


def apply_precheck_to_product(product: dict[str, Any], platform: str, precheck: dict[str, Any], status: str = "") -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    draft = deepcopy(_draft_for_platform(normalized, platform))
    combined = list(precheck.get("errors") or []) + list(precheck.get("warnings") or [])
    draft["validation_errors"] = combined
    requested_status = status or ("ready" if precheck.get("ok") else "not_ready")
    current_publish_status = str(draft.get("publish_status") or "").strip().lower()
    if current_publish_status in {"published", "real_publish_success", "success"} and requested_status in {"ready", "not_ready", "local_precheck_passed"}:
        draft["publish_status"] = current_publish_status
    else:
        draft["publish_status"] = requested_status
    # 预检历史不再内嵌 draft_json（publish_logs 统一走 SQLite 表）。
    draft.pop("publish_logs", None)
    normalized.setdefault("drafts", {})[platform] = draft
    normalized["publish_preview"] = {
        **(normalized.get("publish_preview") if isinstance(normalized.get("publish_preview"), dict) else {}),
        platform: precheck,
    }
    return normalize_product_fields(normalized)


__all__ = [
    "apply_precheck_to_product",
    "validate_mercadolibre_draft",
    "validate_ozon_draft",
    "validate_platform_draft",
    "validate_yandex_draft",
]

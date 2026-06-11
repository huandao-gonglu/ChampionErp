# -*- coding: utf-8 -*-
from __future__ import annotations

from .runtime_common import *

from .publish_helpers import *

def validate_mercadolibre_draft(product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "mercadolibre")
    store = config.get("mercadolibre", {}) if isinstance(config.get("mercadolibre"), dict) else {}
    summary = _required_attribute_summary(product, "mercadolibre")
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    auth_status, auth_next = _masked_auth_status("mercadolibre", config)
    title_limit = int(load_app_config().get("mercadolibre_title_limit") or 60)
    title = str(draft.get("title") or "").strip()
    description = str(draft.get("description") or "").strip()
    category_id = str(draft.get("category_id") or "").strip()
    category_path = str(draft.get("category_path") or "").strip()
    attrs = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
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
        warnings.append(precheck_item("CATEGORY_PATH_MISSING", "category_path", "类目路径为空，建议重新选择本地类目缓存", "warning", "前往类目属性页重新选择类目"))
    if summary["missing"]:
        for field in summary["missing"]:
            attr_id = str(field).split(".", 1)[-1]
            errors.append(precheck_item("REQUIRED_ATTRIBUTE_MISSING", field, f"缺少必填属性：{attr_id}", "error", "前往类目属性页补齐必填属性"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "Brand 为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "Model 为空", "error", "前往类目属性页确认 Model"))
    if not str(draft.get("sku") or product.get("sku") or "").strip():
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    try:
        if float(str(draft.get("price") or "0").strip() or "0") <= 0:
            raise ValueError
    except Exception:
        errors.append(precheck_item("PRICE_MISSING", "price", "价格缺失或无效", "error", "前往核价页计算并应用售价"))
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
    if not str(pricing.get("suggested_price") or "").strip() and not str(draft.get("price") or "").strip():
        errors.append(precheck_item("PRICING_NOT_APPLIED", "pricing", "尚未应用核价结果", "error", "前往核价页应用售价"))
    elif not str(pricing.get("suggested_price") or "").strip():
        warnings.append(precheck_item("PRICING_NOT_APPLIED", "pricing", "当前使用草稿售价，建议回核价页确认已应用最新核价结果", "warning", "前往核价页复核售价"))

    def review_item_resolved(item: str) -> bool:
        field = str(item or "").strip()
        attr_id = field.split(".", 1)[-1] if field.startswith("attributes.") else field
        package_map = {
            "PACKAGE_LENGTH": "length_cm",
            "PACKAGE_WIDTH": "width_cm",
            "PACKAGE_HEIGHT": "height_cm",
            "PACKAGE_WEIGHT": "weight_kg",
        }
        if attr_id in package_map and str(pkg.get(package_map[attr_id]) or "").strip():
            return True
        return bool(attr_id and str(attrs.get(attr_id) or "").strip())

    need_review: list[str] = []
    for item in draft.get("validation_errors") or []:
        if isinstance(item, dict):
            if str(item.get("code") or "") != "NEED_REVIEW_ATTRIBUTES":
                continue
            raw_field = str(item.get("field") or "").strip()
        else:
            raw_field = str(item or "").strip()
        if raw_field and not review_item_resolved(raw_field):
            need_review.append(raw_field)
    if need_review:
        errors.append(precheck_item("NEED_REVIEW_ATTRIBUTES", "attributes", f"仍有 {len(need_review)} 个属性待复核", "error", "前往类目属性页确认 need_review 字段"))
    if not str(draft.get("upc") or draft.get("gtin") or draft.get("barcode") or product.get("upc") or product.get("gtin") or product.get("barcode") or "").strip():
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
        errors.append(precheck_item("SALE_TERMS_MISSING", "sale_terms", "sale_terms / warranty 尚未配置完整", "error", "前往平台属性页补齐售后条款"))
    draft_shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else {}
    logistic_type = str(draft_shipping.get("logistic_type") or draft_shipping.get("mode") or config.get("listing", {}).get("mercadolibre_logistic_type") or "").strip()
    if not logistic_type:
        errors.append(precheck_item("LOGISTIC_MODE_MISSING", "logistic_type", "未读取 shipping / logistics mode", "error", "发布前在店铺后台确认物流模式，不要自动修改后台模式"))
    return {"platform": "mercadolibre", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


def validate_wildberries_draft(product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "wildberries")
    store = config.get("wildberries", {}) if isinstance(config.get("wildberries"), dict) else {}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    auth_status, auth_next = _masked_auth_status("wildberries", config)
    if auth_status in {"未配置", "测试失败", "Token 过期", "权限不足", "被限流"}:
        errors.append(precheck_item("AUTH_NOT_CONFIGURED", "auth", f"Wildberries 授权状态：{auth_status}", "error", auth_next or "前往授权页测试 Token"))
    if not str(draft.get("title") or "").strip():
        errors.append(precheck_item("TITLE_MISSING", "title", "缺少标题", "error", "前往商品编辑页补齐标题"))
    if not str(draft.get("description") or "").strip():
        errors.append(precheck_item("DESCRIPTION_MISSING", "description", "缺少描述", "error", "前往商品编辑页补齐描述"))
    if not str(draft.get("category_id") or store.get("subject_id") or "").strip():
        errors.append(precheck_item("CATEGORY_MISSING", "category_id", "缺少 Wildberries Subject ID", "error", "前往类目属性页选择类目"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "品牌为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "型号为空", "error", "前往类目属性页确认 Model"))
    if not str(draft.get("sku") or product.get("sku") or "").strip():
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    if not str(draft.get("price") or "").strip():
        errors.append(precheck_item("PRICE_MISSING", "price", "价格缺失", "error", "前往核价页应用 Wildberries 价格"))
    if not str(draft.get("stock") or "").strip():
        errors.append(precheck_item("STOCK_MISSING", "stock", "库存缺失", "error", "前往商品编辑页填写库存"))
    images = _draft_images(product, "wildberries", draft)
    if not images:
        errors.append(precheck_item("IMAGE_MISSING", "images", "缺少图片", "error", "前往图片池导入图片"))
    elif len(images) < 1:
        errors.append(precheck_item("IMAGE_MISSING", "images", "图片数量不足", "error", "前往图片池补图"))
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    for field in ("length_cm", "width_cm", "height_cm"):
        if not str(pkg.get(field) or "").strip():
            errors.append(precheck_item("PACKAGE_DIMENSIONS_MISSING", f"package_dimensions.{field}", f"{field} 缺失", "error", "前往核价页补齐尺寸"))
    if not str(pkg.get("weight_kg") or "").strip():
        errors.append(precheck_item("WEIGHT_MISSING", "package_dimensions.weight_kg", "重量缺失", "error", "前往核价页补齐重量"))
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    if not str(pricing.get("suggested_price") or "").strip():
        errors.append(precheck_item("PRICING_NOT_APPLIED", "pricing", "尚未应用核价结果", "error", "前往核价页应用 Wildberries 价格"))
    need_review = [str(item) for item in draft.get("validation_errors") or [] if str(item).strip()]
    if need_review:
        warnings.append(precheck_item("NEED_REVIEW_ATTRIBUTES", "attributes", f"仍有 {len(need_review)} 个属性待复核", "warning", "前往类目属性页确认属性"))
    if not str(draft.get("language") or "").strip():
        warnings.append(precheck_item("LANGUAGE_MISSING", "language", "俄语标题/描述尚未确认", "warning", "发布前确认 Wildberries 文案语言"))
    return {"platform": "wildberries", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


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
    if not str(draft.get("category_id") or store.get("category_id") or "").strip():
        errors.append(precheck_item("CATEGORY_MISSING", "category_id", "缺少 Ozon Category / Type ID", "error", "前往类目属性页选择类目"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "品牌为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "型号为空", "error", "前往类目属性页确认 Model"))
    if not str(draft.get("sku") or product.get("sku") or "").strip():
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    if not str(draft.get("price") or "").strip():
        errors.append(precheck_item("PRICE_MISSING", "price", "价格缺失", "error", "前往核价页应用 Ozon 价格"))
    if not str(draft.get("stock") or "").strip():
        errors.append(precheck_item("STOCK_MISSING", "stock", "库存缺失", "error", "前往商品编辑页填写库存"))
    images = _draft_images(product, "ozon", draft)
    if not images:
        errors.append(precheck_item("IMAGE_MISSING", "images", "缺少图片", "error", "前往图片池导入图片"))
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    for field in ("length_cm", "width_cm", "height_cm"):
        if not str(pkg.get(field) or "").strip():
            errors.append(precheck_item("PACKAGE_DIMENSIONS_MISSING", f"package_dimensions.{field}", f"{field} 缺失", "error", "前往核价页补齐尺寸"))
    if not str(pkg.get("weight_kg") or "").strip():
        errors.append(precheck_item("WEIGHT_MISSING", "package_dimensions.weight_kg", "重量缺失", "error", "前往核价页补齐重量"))
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    if not str(pricing.get("suggested_price") or "").strip():
        errors.append(precheck_item("PRICING_NOT_APPLIED", "pricing", "尚未应用核价结果", "error", "前往核价页应用 Ozon 价格"))
    need_review = [str(item) for item in draft.get("validation_errors") or [] if str(item).strip()]
    if need_review:
        warnings.append(precheck_item("NEED_REVIEW_ATTRIBUTES", "attributes", f"仍有 {len(need_review)} 个属性待复核", "warning", "前往类目属性页确认属性"))
    return {"platform": "ozon", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


def validate_platform_draft(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform == "mercadolibre":
        return validate_mercadolibre_draft(product, config)
    if platform == "wildberries":
        return validate_wildberries_draft(product, config)
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
    draft["publish_status"] = status or ("ready" if precheck.get("ok") else "not_ready")
    publish_logs = draft.get("publish_logs") if isinstance(draft.get("publish_logs"), list) else []
    publish_logs.insert(
        0,
        {
            "time": collect_time_iso(),
            "status": draft["publish_status"],
            "error_count": len(precheck.get("errors") or []),
            "warning_count": len(precheck.get("warnings") or []),
        },
    )
    draft["publish_logs"] = publish_logs[:20]
    normalized.setdefault("drafts", {})[platform] = draft
    normalized["publish_preview"] = {
        **(normalized.get("publish_preview") if isinstance(normalized.get("publish_preview"), dict) else {}),
        platform: precheck,
    }
    return normalize_product_fields(normalized)

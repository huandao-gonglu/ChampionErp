"""从已同步的 Yandex 快照生成 Ozon 类目文件；全程不请求平台、不写业务数据。"""
from __future__ import annotations

import base64
import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from urllib.parse import urlsplit

from erp_web.schemas.online_products import OnlineListing, digest
from erp_web.schemas.ozon_template_export import OzonExportDownload, OzonExportPreview, OzonExportRequest, OzonExportRow
from erp_web.services.ozon_category_template import OzonCategoryTemplate, decode_template
from erp_web.stores.online_product_store import OnlineConflict, OnlineProductStore

NUMBERS = {"price": "价格", "weight_g": "毛重", "width_mm": "包装宽度", "height_mm": "包装高度", "length_mm": "包装长度"}
LABELS = {**NUMBERS, "offer_id": "货号", "main_image": "主图", "brand": "品牌", "model": "型号名称", "type": "类型"}
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af]")


def _model_identity(listing: OnlineListing) -> tuple[str, str]:
    group = str(listing.snapshot.get("offer", {}).get("groupId") or "").strip()
    return ("group", group) if group else ("listing", listing.id)


def _model_source(listing: OnlineListing) -> str:
    return str(listing.snapshot.get("offer", {}).get("groupId") or listing.seller_sku or listing.remote_id).strip()


def _model_defaults(listings: list[OnlineListing]) -> dict[tuple[str, str], str]:
    """只复用整组一致的俄文标题；不能因本次只选部分 SKU 改变合卡关系。"""
    titles: dict[tuple[str, str], set[str]] = {}
    sources = {}
    for listing in listings:
        identity = _model_identity(listing)
        # 列表快照也带有 offer.name，unloaded 只表示尚未读取管理详情。
        title = listing.title.strip() if listing.details_state in {"ready", "unloaded"} else ""
        titles.setdefault(identity, set()).add(title)
        sources[identity] = _model_source(listing)
    candidates = {}
    for identity, values in titles.items():
        if CJK.search(sources[identity]) and len(values) == 1:
            title = next(iter(values))
            if re.search(r"[А-Яа-яЁё]", title) and not CJK.search(title) and len(title) <= 255:
                candidates[identity] = title
    owners: dict[str, set[tuple[str, str]]] = {}
    for identity, source in sources.items():
        owners.setdefault(candidates.get(identity, source), set()).add(identity)
    # 不同源组碰巧同名时不能自动合成一张卡，交给导出编辑明确区分。
    return {identity: title for identity, title in candidates.items() if len(owners[title]) == 1}


def _number(value: object, factor: int = 1, *, whole: bool = False) -> str:
    try:
        amount = Decimal(str(value)) * factor
        if not amount.is_finite() or amount <= 0 or amount > 10**12:
            return ""
        if whole:
            amount = amount.to_integral_value(rounding=ROUND_CEILING)
        return format(amount, "f").rstrip("0").rstrip(".") if "." in format(amount, "f") else format(amount, "f")
    except (InvalidOperation, ValueError, TypeError):
        return ""


def _public_url(value: object) -> str:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return ""
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.port:
            return ""
        if url.hostname in {"localhost", "127.0.0.1", "::1"}:
            return ""
    except ValueError:
        return ""
    return value


def _pictures(offer: dict, listing: OnlineListing) -> tuple[list[str], int]:
    records = offer.get("mediaFiles", {}).get("pictures", [])
    failed = sum(isinstance(row, dict) and row.get("uploadState") == "FAILED" for row in records)
    candidates = [row.get("marketUrl") or row.get("url") for row in records
                  if isinstance(row, dict) and row.get("uploadState") == "UPLOADED"] if records else listing.content.get("pictures", [])
    urls = [_public_url(row.get("url") if isinstance(row, dict) else row) for row in candidates]
    return list(dict.fromkeys(url for url in urls if url))[:15], failed


def _variant_facts(listing: OnlineListing) -> tuple[str, str, str]:
    parameters = {str(row.get("parameterId", row.get("id", ""))): row for row in listing.content.get("attributes", []) if isinstance(row, dict)}
    variant = str(parameters.get("14871214", {}).get("value") or "")
    # 已核对当前类目的宽/高参数；只处理显式厘米单位，其他单位不猜测。
    sizes = [parameters.get(key, {}) for key in ("23679910", "14805336")]
    width, height = [_number(row.get("value")) if row.get("unitId") == 8 else "" for row in sizes]
    return variant, width, height


def _row(listing: OnlineListing, template: OzonCategoryTemplate, changes: dict[str, str], *, model: str) -> OzonExportRow:
    offer = listing.snapshot.get("offer", {})
    package = offer.get("weightDimensions", {})
    base = next((p for p in listing.prices if p.id == "business" and p.kind == "base_price"), None)
    pictures, failed = _pictures(offer, listing)
    variant, width, height = _variant_facts(listing) if template.category == "圣诞装饰品" else ("", "", "")
    title = listing.title
    if width and height:
        size_pattern = r"(?<!\d)\d+(?:[.,]\d+)?\s*[×xхXХ*]\s*\d+(?:[.,]\d+)?\s*(см|cm|厘米|公分)"
        if len(re.findall(size_pattern, title)) == 1:
            title = re.sub(size_pattern, lambda m: f"{width}×{height} {m[1]}", title)
    barcodes = offer.get("barcodes") or []
    fields = {"offer_id": listing.seller_sku or listing.remote_id, "title": title,
              "price": _number(base.amount) if base and base.currency == template.currency else "", "brand": str(offer.get("vendor") or ""),
              "model": model,
              "barcode": str(barcodes[0]) if barcodes else "", "weight_g": _number(package.get("weight"), 1000, whole=True),
              "width_mm": _number(package.get("width"), 10, whole=True), "height_mm": _number(package.get("height"), 10, whole=True),
              "length_mm": _number(package.get("length"), 10, whole=True),
              "main_image": pictures[0] if pictures else "", "images": "\n".join(pictures[1:]),
              "variant": variant, "size_cm": width if width == height else "", "type": template.product_type, "review_promo": template.promotion_no}
    fields.update({key: "" for key in template.extra_fields if key != "type"})
    fields.update(changes)
    fields = {key: value.strip() for key, value in fields.items()}
    errors, warnings = [], []
    brand = fields["brand"]
    if template.no_brand and "brand" in template.field_columns and brand and brand not in template.brand_values and brand.casefold() in {"其他", "无品牌", "без бренда", "нет бренда"}:
        fields["brand"] = template.no_brand
        if brand != template.no_brand:
            warnings.append(f"品牌“{brand}”已转换为模板选项“{template.no_brand}”，请确认商品确实无品牌")
    if "brand" in template.field_columns and fields["brand"] and fields["brand"] != template.no_brand and fields["brand"] not in template.brand_values:
        if template.brand_dictionary_complete:
            errors.append("品牌不在模板选项中，请填写 Ozon 品牌名称；无品牌商品请选择“" + template.no_brand + "”")
        else:
            warnings.append("模板未包含完整品牌字典，请在 Ozon 确认该品牌可选；无品牌商品请填写“" + template.no_brand + "”")
    if "model" in template.field_columns and CJK.search(fields["model"]):
        errors.append("型号名称含中文。请在“编辑”中填写同组共用的俄文名称或字母数字型号。")
    if "model" in template.field_columns and len(fields["model"]) > 255:
        errors.append("型号名称不能超过 255 个字符")
    if "model" in template.field_columns and "model" not in changes and model != _model_source(listing):
        warnings.append("中文型号名称已使用同组一致的俄文商品名称，请核对合卡分组")
    if listing.details_state == "pending":
        errors.append("商品基础资料正在同步，请在同步完成后重新检查")
    elif listing.details_state == "failed":
        errors.append("商品资料读取失败，请重试同步后重新检查")
    if template.category == "圣诞装饰品" and not re.search(r"圣诞|新年|новогод|рождеств|christmas|new year", listing.title + " " + str(offer.get("groupId") or ""), re.I):
        warnings.append("请核对所选商品是否符合圣诞装饰品类目")
    if "price" in template.field_columns and "price" not in changes and base and base.currency != template.currency:
        errors.append(f"原价格币种为 {base.currency or '未知'}，请填写 CNY 价格；不会自动换汇")
    for key, label in template.required_fields.items():
        if not fields.get(key):
            errors.append(f"缺少{LABELS.get(key, label)}")
    if fields["type"] and template.type_options and fields["type"] not in template.type_options:
        errors.append("请选择模板字典中的商品类型")
    for key, kind in template.extra_numeric.items():
        if fields[key]:
            try:
                value = Decimal(fields[key])
                valid = value.is_finite() and (kind != "Integer" or value == value.to_integral_value())
            except InvalidOperation:
                valid = False
            if not valid:
                errors.append(template.extra_fields[key] + ("须为整数" if kind == "Integer" else "须为数字"))
    for key, label in NUMBERS.items():
        if fields[key]:
            normalized = _number(fields[key])
            if not normalized or key != "price" and Decimal(normalized) != Decimal(normalized).to_integral_value():
                errors.append(f"{label}须为正数" + ("整数" if key != "price" else ""))
            else:
                fields[key] = normalized
    for key, value in fields.items():
        if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
            errors.append(f"{LABELS.get(key, key)}包含无效字符")
        if len(value) > 32767:
            errors.append(f"{LABELS.get(key, key)}内容过长")
    if not fields["barcode"] and "barcode" not in template.required_fields:
        warnings.append("未提供条码，将留空；请按 Ozon 上传提示补齐")
    if failed:
        warnings.append(f"已排除 {failed} 张上传失败的附图")
    if template.category == "圣诞装饰品" and re.search(r"DIY|мозаик|点钻|钻石画", listing.title, re.I):
        warnings.append("DIY 套装请核对圣诞装饰品类目是否准确")
    return {"listing_id": listing.id, "seller_sku": fields["offer_id"], "version": listing.version,
            "title_before": listing.title, "title_changed": fields["title"] != listing.title,
            "fields": fields, "errors": errors, "warnings": warnings}


class OnlineOzonExportService:
    def __init__(self, store: OnlineProductStore, current_account: Callable[[], str]):
        self.store, self.current_account = store, current_account

    def _prepare(self, request: OzonExportRequest) -> tuple[OzonCategoryTemplate, OzonExportPreview]:
        if request.account_id != self.current_account():
            raise OnlineConflict("Yandex 店铺已切换，请重新选择商品")
        if set(request.overrides) - set(request.listing_ids):
            raise ValueError("导出资料只能属于本次选中的商品")
        data = decode_template(request.template_base64)
        template = OzonCategoryTemplate(data)
        listings = []
        for listing_id in request.listing_ids:
            listing = self.store.get(listing_id)
            if listing.platform != "yandex" or listing.account_id != request.account_id:
                raise ValueError("只能导出当前 Yandex 店铺的商品")
            listings.append(listing)
        defaults = {}
        if "model" in template.field_columns and any(CJK.search(_model_source(listing)) for listing in listings):
            related = {listing.id: listing for listing in self.store.listings("yandex", request.account_id)}
            related.update({listing.id: listing for listing in listings})
            defaults = _model_defaults(list(related.values()))
        rows = []
        for listing in listings:
            changes = request.overrides.get(listing.id)
            values = changes.model_dump(exclude={"template_fields"}, exclude_none=True) if changes else {}
            if changes:
                if changes.template_fields.keys() - template.extra_fields.keys():
                    raise ValueError("补充字段与当前模板不匹配，请重新检查商品")
                values.update(changes.template_fields)
            rows.append(_row(listing, template, values,
                             model=defaults.get(_model_identity(listing), _model_source(listing))))
        if len({row["seller_sku"] for row in rows}) != len(rows):
            raise ValueError("所选商品货号重复，请核对商品身份")
        if request.account_id != self.current_account():
            raise OnlineConflict("Yandex 店铺已切换，请重新选择商品")
        fingerprint = digest([request.account_id, request.template_base64, rows])
        result: OzonExportPreview = {"ok": True, "template": template.describe(), "rows": rows,
            "summary": {"total": len(rows), "ready": sum(not row["errors"] for row in rows),
                        "missing": sum(bool(row["errors"]) for row in rows),
                        "barcode_missing": sum(not row["fields"]["barcode"] for row in rows),
                        "title_changed": sum(row["title_changed"] for row in rows)}, "preview_fingerprint": fingerprint}
        return template, result

    def preview(self, body: dict) -> OzonExportPreview:
        return self._prepare(OzonExportRequest.model_validate(body))[1]

    def download(self, body: dict) -> OzonExportDownload:
        request = OzonExportRequest.model_validate(body)
        template, preview = self._prepare(request)
        if not request.preview_fingerprint or request.preview_fingerprint != preview["preview_fingerprint"]:
            raise OnlineConflict("商品资料或模板已变化，请重新检查后生成文件")
        if preview["summary"]["missing"]:
            raise ValueError("请补齐或修正必填资料后再生成文件")
        data = template.fill([row["fields"] for row in preview["rows"]])
        category_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", template.category)[:80]
        return {"ok": True, "filename": f"Ozon_{category_name}_{len(preview['rows'])}个SKU_{datetime.now():%Y%m%d}.xlsx",
                "file_base64": base64.b64encode(data).decode("ascii")}


__all__ = ["OnlineOzonExportService"]

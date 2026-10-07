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


def _row(listing: OnlineListing, template: OzonCategoryTemplate, changes: dict[str, str]) -> OzonExportRow:
    offer = listing.snapshot.get("offer", {})
    package = offer.get("weightDimensions", {})
    base = next((p for p in listing.prices if p.id == "business" and p.kind == "base_price"), None)
    pictures, failed = _pictures(offer, listing)
    variant, width, height = _variant_facts(listing)
    title = listing.title
    if width and height:
        size_pattern = r"(?<!\d)\d+(?:[.,]\d+)?\s*[×xхXХ*]\s*\d+(?:[.,]\d+)?\s*(см|cm|厘米|公分)"
        if len(re.findall(size_pattern, title)) == 1:
            title = re.sub(size_pattern, lambda m: f"{width}×{height} {m[1]}", title)
    barcodes = offer.get("barcodes") or []
    fields = {"offer_id": listing.seller_sku or listing.remote_id, "title": title,
              "price": _number(base.amount) if base else "", "brand": str(offer.get("vendor") or ""),
              "model": str(offer.get("groupId") or listing.seller_sku or listing.remote_id),
              "barcode": str(barcodes[0]) if barcodes else "", "weight_g": _number(package.get("weight"), 1000, whole=True),
              "width_mm": _number(package.get("width"), 10, whole=True), "height_mm": _number(package.get("height"), 10, whole=True),
              "length_mm": _number(package.get("length"), 10, whole=True),
              "main_image": pictures[0] if pictures else "", "images": "\n".join(pictures[1:]),
              "variant": variant, "size_cm": width if width == height else "", "type": "圣诞装饰品"}
    fields.update(changes)
    fields = {key: value.strip() for key, value in fields.items()}
    errors, warnings = [], []
    if listing.details_state != "ready":
        errors.append("商品详情尚未同步完整，请先完成同步")
    if listing.sale_state != "active":
        errors.append("当前商品不是在售状态，请核对后重新选择")
    if not re.search(r"圣诞|新年|новогод|рождеств|christmas|new year", listing.title + " " + str(offer.get("groupId") or ""), re.I):
        errors.append("未发现圣诞或新年主题资料，无法确认符合该类目")
    if "price" not in changes and base and base.currency != template.currency:
        errors.append(f"原价格币种为 {base.currency or '未知'}，请填写 CNY 价格；不会自动换汇")
    for key in template.required_fields:
        if not fields.get(key):
            errors.append(f"缺少{LABELS[key]}")
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
    if not fields["barcode"]:
        warnings.append("未提供条码，将留空；请按 Ozon 上传提示补齐")
    if failed:
        warnings.append(f"已排除 {failed} 张上传失败的附图")
    if re.search(r"DIY|мозаик|点钻|钻石画", listing.title, re.I):
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
        rows = []
        for listing_id in request.listing_ids:
            listing = self.store.get(listing_id)
            if listing.platform != "yandex" or listing.account_id != request.account_id:
                raise ValueError("只能导出当前 Yandex 店铺的商品")
            changes = request.overrides.get(listing_id)
            rows.append(_row(listing, template, changes.model_dump(exclude_none=True) if changes else {}))
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
        return {"ok": True, "filename": f"Ozon_圣诞装饰品_{len(preview['rows'])}个SKU_{datetime.now():%Y%m%d}.xlsx",
                "file_base64": base64.b64encode(data).decode("ascii")}


__all__ = ["OnlineOzonExportService"]

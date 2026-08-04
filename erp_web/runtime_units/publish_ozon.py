# -*- coding: utf-8 -*-
"""Ozon Seller API 商品创建/更新发布实现。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
import time
from typing import Any

from erp_web.marketplaces.config_http import request_ozon_json
from erp_web.stores.product_store import normalize_product_fields

from .publish_helpers import (
    _draft_for_platform,
    _draft_images,
    _required_attribute_summary,
)


OZON_PRODUCT_IMPORT_URL = "https://api-seller.ozon.ru/v3/product/import"
OZON_PRODUCT_IMPORT_INFO_URL = (
    "https://api-seller.ozon.ru/v1/product/import/info"
)
OZON_IMPORT_TIMEOUT_SECONDS = 30.0
OZON_IMPORT_POLL_INTERVAL_SECONDS = 0.5


def _positive_decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value or "").strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} 必须是有效数字") from exc
    if number <= 0:
        raise ValueError(f"{field} 必须大于 0")
    return number


def _positive_int(value: Any, field: str) -> int:
    number = _positive_decimal(value, field)
    return max(1, int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def _decimal_text(value: Any, field: str) -> str:
    number = _positive_decimal(value, field)
    return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")


def _category_record(product: dict[str, Any]) -> dict[str, Any]:
    categories = (
        product.get("local_platform_categories")
        if isinstance(product.get("local_platform_categories"), dict)
        else {}
    )
    record = categories.get("ozon")
    return record if isinstance(record, dict) else {}


def ozon_category_pair(product: dict[str, Any]) -> tuple[str, str]:
    """返回 Ozon 发布所需的 ``type_id`` 与 ``description_category_id``。"""

    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "ozon")
    record = _category_record(product)
    draft_type_id = str(draft.get("category_id") or "").strip()
    record_type_id = str(
        record.get("type_id") or record.get("category_id") or ""
    ).strip()
    if draft_type_id and record_type_id and draft_type_id != record_type_id:
        return draft_type_id, ""
    type_id = draft_type_id or record_type_id
    description_category_id = str(
        record.get("description_category_id")
        or record.get("subject_id")
        or ""
    ).strip()
    return type_id, description_category_id


def _attribute_definitions(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    attributes = (
        record.get("attributes")
        if isinstance(record.get("attributes"), dict)
        else {}
    )
    definitions: dict[str, dict[str, Any]] = {}
    for group in ("required", "optional"):
        rows = attributes.get(group) if isinstance(attributes.get(group), list) else []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            attr_id = str(raw.get("id") or "").strip()
            if attr_id:
                definitions[attr_id] = raw
    return definitions


def _description_attribute_id(
    definitions: dict[str, dict[str, Any]],
) -> str:
    for attr_id, definition in definitions.items():
        name = str(definition.get("name") or "").strip().casefold()
        if any(marker in name for marker in ("аннотац", "описание товара", "description")):
            return attr_id
    return ""


def _attribute_values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("values"), list):
        raw_values = value.get("values") or []
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = [value]

    values: list[dict[str, Any]] = []
    for raw in raw_values:
        if isinstance(raw, dict):
            text = str(raw.get("value") or raw.get("label") or "").strip()
            dictionary_value_id = raw.get("dictionary_value_id")
            item: dict[str, Any] = {"value": text}
            if dictionary_value_id not in (None, ""):
                try:
                    item["dictionary_value_id"] = int(dictionary_value_id)
                except (TypeError, ValueError):
                    pass
        else:
            text = str(raw or "").strip()
            item = {"value": text}
        if text:
            values.append(item)
    return values


def _attribute_complex_id(definition: dict[str, Any]) -> int:
    raw = definition.get("raw") if isinstance(definition.get("raw"), dict) else {}
    value = (
        definition.get("attribute_complex_id")
        or definition.get("complex_id")
        or raw.get("attribute_complex_id")
        or raw.get("complex_id")
        or 0
    )
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ozon_attributes(
    product: dict[str, Any],
    draft: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    record = _category_record(product)
    definitions = _attribute_definitions(record)
    raw_attributes = (
        deepcopy(draft.get("attributes"))
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    description_attr_id = _description_attribute_id(definitions)
    description = str(draft.get("description") or "").strip()
    if description_attr_id and description_attr_id not in raw_attributes and description:
        raw_attributes[description_attr_id] = description

    regular: list[dict[str, Any]] = []
    complex_groups: dict[int, list[dict[str, Any]]] = {}
    for raw_id, raw_value in raw_attributes.items():
        attr_id = str(raw_id or "").strip()
        if not attr_id.isdigit():
            # Ozon 的属性 ID 是整数；BRAND/MODEL 等跨平台辅助字段不得发给 API。
            continue
        values = _attribute_values(raw_value)
        if not values:
            continue
        definition = definitions.get(attr_id, {})
        complex_id = _attribute_complex_id(definition)
        item = {
            "complex_id": complex_id,
            "id": int(attr_id),
            "values": values,
        }
        if complex_id:
            complex_groups.setdefault(complex_id, []).append(item)
        else:
            regular.append(item)

    complex_attributes = [
        {"attributes": rows}
        for _, rows in sorted(complex_groups.items())
    ]
    return regular, complex_attributes


def ozon_required_attributes_missing(product: dict[str, Any]) -> list[str]:
    """返回排除发布构造器可自动补齐字段后的 Ozon 必填属性。"""

    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "ozon")
    regular, complex_attributes = _ozon_attributes(product, draft)
    filled_ids = {str(item.get("id") or "") for item in regular}
    for group in complex_attributes:
        for item in group.get("attributes", []):
            if isinstance(item, dict):
                filled_ids.add(str(item.get("id") or ""))
    return [
        field
        for field in _required_attribute_summary(product, "ozon").get("missing") or []
        if str(field).split(".", 1)[-1] not in filled_ids
    ]


def build_ozon_publish_payload(
    product: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "ozon")
    type_id, description_category_id = ozon_category_pair(product)
    package = (
        draft.get("package_dimensions")
        if isinstance(draft.get("package_dimensions"), dict)
        else {}
    )
    attributes, complex_attributes = _ozon_attributes(product, draft)
    images = _draft_images(product, "ozon", draft)[:15]
    store = config.get("ozon") if isinstance(config.get("ozon"), dict) else {}
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}

    item: dict[str, Any] = {
        "attributes": attributes,
        "description_category_id": int(description_category_id),
        "complex_attributes": complex_attributes,
        "currency_code": str(
            draft.get("currency") or store.get("currency_code") or "RUB"
        ).strip().upper(),
        "depth": _positive_int(
            _positive_decimal(package.get("length_cm"), "包装长度") * 10,
            "包装长度",
        ),
        "dimension_unit": "mm",
        "height": _positive_int(
            _positive_decimal(package.get("height_cm"), "包装高度") * 10,
            "包装高度",
        ),
        "images": images,
        "name": str(draft.get("title") or product.get("name") or "").strip(),
        "offer_id": str(draft.get("sku") or product.get("sku") or "").strip(),
        "price": _decimal_text(draft.get("price"), "价格"),
        "type_id": int(type_id),
        "vat": str(draft.get("vat") or store.get("vat") or "0").strip(),
        "weight": _positive_int(
            _positive_decimal(package.get("weight_kg"), "包装重量") * 1000,
            "包装重量",
        ),
        "weight_unit": "g",
        "width": _positive_int(
            _positive_decimal(package.get("width_cm"), "包装宽度") * 10,
            "包装宽度",
        ),
    }
    barcode = str(draft.get("upc") or product.get("upc") or "").strip()
    if barcode:
        item["barcode"] = barcode
    old_price = str(
        draft.get("old_price")
        or pricing.get("old_price")
        or pricing.get("list_price")
        or ""
    ).strip()
    if old_price:
        item["old_price"] = old_price
    return {"items": [item]}


def validate_ozon_publish_payload(
    payload: Any,
    config: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    store = config.get("ozon") if isinstance(config.get("ozon"), dict) else {}
    if not str(store.get("client_id") or "").strip():
        errors.append("Ozon Client ID")
    if not str(store.get("api_key") or "").strip():
        errors.append("Ozon API Key")
    payload = payload if isinstance(payload, dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if len(items) != 1 or not isinstance(items[0], dict):
        return [*errors, "Ozon items 必须包含一个商品"]
    item = items[0]
    for key, label in (
        ("name", "标题"),
        ("offer_id", "SKU / offer_id"),
        ("description_category_id", "description_category_id"),
        ("type_id", "type_id"),
        ("price", "价格"),
        ("currency_code", "币种"),
        ("depth", "包装长度"),
        ("width", "包装宽度"),
        ("height", "包装高度"),
        ("weight", "包装重量"),
    ):
        if item.get(key) in (None, "", 0):
            errors.append(label)
    attributes = item.get("attributes") if isinstance(item.get("attributes"), list) else []
    complex_attributes = (
        item.get("complex_attributes")
        if isinstance(item.get("complex_attributes"), list)
        else []
    )
    if not attributes and not complex_attributes:
        errors.append("Ozon 类目属性")
    images = item.get("images") if isinstance(item.get("images"), list) else []
    if not images:
        errors.append("图片")
    elif any(not str(url).startswith(("https://", "http://")) for url in images):
        errors.append("图片必须是 Ozon 可访问的 HTTP(S) 公网 URL")
    return errors


def _import_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    items = result.get("items") if isinstance(result.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def _item_errors(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for item in items:
        rows = item.get("errors") if isinstance(item.get("errors"), list) else []
        for row in rows:
            if isinstance(row, dict):
                errors.append(row)
            elif row:
                errors.append({"message": str(row)})
    return errors


def publish_ozon_payload(
    payload: dict[str, Any],
    client_id: str,
    api_key: str,
    *,
    timeout_seconds: float = OZON_IMPORT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = OZON_IMPORT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """提交商品导入并等待 Ozon 返回逐商品 ``imported`` 终态。"""

    created = request_ozon_json(
        "POST",
        OZON_PRODUCT_IMPORT_URL,
        client_id,
        api_key,
        payload,
    )
    result = created.get("result") if isinstance(created.get("result"), dict) else {}
    task_id = result.get("task_id")
    if task_id in (None, "", 0):
        raise RuntimeError(
            "Ozon 创建/更新商品未返回 task_id："
            + json.dumps(created, ensure_ascii=False)
        )

    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    last_response: dict[str, Any] = {}
    while True:
        last_response = request_ozon_json(
            "POST",
            OZON_PRODUCT_IMPORT_INFO_URL,
            client_id,
            api_key,
            {"task_id": task_id},
        )
        items = _import_items(last_response)
        item_errors = _item_errors(items)
        if item_errors:
            raise RuntimeError(
                "Ozon 商品导入失败："
                + json.dumps(
                    {"task_id": task_id, "items": items},
                    ensure_ascii=False,
                )
            )
        statuses = {
            str(item.get("status") or "").strip().lower()
            for item in items
            if str(item.get("status") or "").strip()
        }
        if items and statuses == {"imported"}:
            first = items[0]
            product_id = first.get("product_id")
            return {
                "ok": True,
                "status": "imported",
                "task_id": task_id,
                "external_id": str(product_id or task_id),
                "product_id": product_id,
                "offer_id": str(first.get("offer_id") or ""),
                "import_info": last_response,
            }
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Ozon 商品导入状态确认超时："
                + json.dumps(
                    {"task_id": task_id, "response": last_response},
                    ensure_ascii=False,
                )
            )
        time.sleep(max(0.05, float(poll_interval_seconds)))


def map_ozon_publish_error(error: Exception) -> dict[str, Any]:
    raw = str(error)
    lowered = raw.casefold()
    field_errors: dict[str, list[str]] = {}
    error_code = "OZON_PUBLISH_FAILED"
    if any(marker in lowered for marker in ("403", "unauthorized", "api-key", "client-id")):
        field_errors["auth"] = ["Ozon Client ID 或 API Key 无效或权限不足"]
        error_code = "OZON_AUTH_FAILED"
    if "timeout" in lowered or "超时" in raw:
        field_errors["publish"] = ["Ozon 已接收导入任务，但未在等待时间内返回终态"]
        error_code = "OZON_IMPORT_TIMEOUT"

    json_start = raw.find("{")
    json_end = raw.rfind("}")
    parsed: dict[str, Any] = {}
    if json_start >= 0 and json_end > json_start:
        try:
            value = json.loads(raw[json_start : json_end + 1])
            parsed = value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            parsed = {}
    rows: list[dict[str, Any]] = []
    for item in parsed.get("items", []) if isinstance(parsed.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        rows.extend(
            row for row in item.get("errors", [])
            if isinstance(row, dict)
        )
    for row in rows:
        message = str(
            row.get("description")
            or row.get("message")
            or row.get("code")
            or "Ozon 校验失败"
        ).strip()
        attribute_id = str(row.get("attribute_id") or "").strip()
        field = str(row.get("field") or "").strip()
        if attribute_id:
            field = f"attributes.{attribute_id}"
        elif field:
            match = re.search(r"attributes?[^0-9]*(\d+)", field)
            field = f"attributes.{match.group(1)}" if match else field
        else:
            field = "publish"
        field_errors.setdefault(field, []).append(message)
        if row.get("code"):
            error_code = str(row.get("code"))

    summary = next(
        (
            message
            for messages in field_errors.values()
            for message in messages
            if message
        ),
        raw[:500] or "Ozon 发布失败",
    )
    return {
        "summary": summary,
        "field_errors": field_errors,
        "error_code": error_code,
        "raw": raw,
    }


__all__ = [
    "OZON_PRODUCT_IMPORT_INFO_URL",
    "OZON_PRODUCT_IMPORT_URL",
    "build_ozon_publish_payload",
    "map_ozon_publish_error",
    "ozon_category_pair",
    "ozon_required_attributes_missing",
    "publish_ozon_payload",
    "validate_ozon_publish_payload",
]

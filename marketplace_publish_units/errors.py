from __future__ import annotations

from .common import *

def parse_mercadolibre_error(error: Exception | str) -> dict[str, Any]:
    """解析美客多 API 返回的错误，提取缺失字段、错误码和可读消息。

    返回结构:
    {
        "error": str,            # 错误码，如 "body.required_fields"
        "message": str,          # 可读错误信息
        "missing_attributes": list[str],  # 缺失属性 ID 列表
        "cause": list[dict],     # 平台原始 cause 列表
        "raw": str,              # 原始错误文本
    }
    """
    raw = str(error)
    result: dict[str, Any] = {
        "error": "",
        "message": raw,
        "missing_attributes": [],
        "missing_fields": [],
        "cause": [],
        "raw": raw,
    }

    # 尝试从错误文本中提取 JSON 部分
    json_start = raw.find("{")
    json_end = raw.rfind("}")
    if json_start < 0 or json_end < json_start:
        lowered = raw.lower()
        for needle, field_name in [
            ("invalid access token", "auth"),
            ("invalid_token", "auth"),
            ("token expired", "auth"),
            ("title", "title"),
            ("price", "price"),
            ("available_quantity", "available_quantity"),
            ("category_id", "category_id"),
            ("category id", "category_id"),
            ("sale_terms", "sale_terms"),
            ("warranty", "sale_terms"),
            ("picture", "pictures"),
            ("images", "pictures"),
            ("shipping_mode", "logistic_type"),
            ("logistic", "logistic_type"),
            ("package_length", "package_length"),
            ("package_width", "package_width"),
            ("package_height", "package_height"),
            ("package_weight", "package_weight"),
        ]:
            if needle in lowered and field_name not in result["missing_fields"]:
                result["missing_fields"].append(field_name)
        return result

    try:
        data = json.loads(raw[json_start: json_end + 1])
    except Exception:
        return result

    result["error"] = str(data.get("error") or data.get("status") or "")
    result["message"] = str(data.get("message") or data.get("error") or raw)

    cause_raw = data.get("cause") or []
    if isinstance(cause_raw, list):
        result["cause"] = cause_raw
    elif isinstance(cause_raw, dict):
        result["cause"] = [cause_raw]

    missing: list[str] = []
    missing_fields: list[str] = []

    # body.required_fields —— cause 里每条是 {"field": "attributes.COLOR", ...}
    for item in result["cause"]:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("id") or item.get("code") or "")
        if field:
            # 去掉 "attributes." 前缀，保留属性 ID
            attr_id = field.removeprefix("attributes.").removeprefix("attribute.")
            if attr_id:
                if field.lower().startswith(("attributes.", "attribute.")):
                    missing.append(attr_id)
                else:
                    missing_fields.append(normalize_mercadolibre_error_field(attr_id))

    # item.attributes.missing_required —— message 里有时直接列出属性名
    if not missing:
        for pattern in [
            r'"id"\s*:\s*"([A-Z_]{2,})"',
            r"'([A-Z_]{2,})'",
            r"\b([A-Z][A-Z_]{2,})\b",
        ]:
            hits = re.findall(pattern, raw)
            for hit in hits:
                if hit not in missing and hit not in {
                    "GET", "POST", "PUT", "DELETE", "HTTP", "HTTPS", "JSON", "URL", "API",
                    "USD", "MXN", "BRL", "ARS", "COP", "CLP", "PEN", "UYU",
                }:
                    missing.append(hit)
            if missing:
                break

    result["missing_attributes"] = missing
    lowered = raw.lower()
    keyword_map = [
        ("invalid access token", "auth"),
        ("invalid_token", "auth"),
        ("token expired", "auth"),
        ("title", "title"),
        ("price", "price"),
        ("available_quantity", "available_quantity"),
        ("category_id", "category_id"),
        ("category id", "category_id"),
        ("sale_terms", "sale_terms"),
        ("warranty", "sale_terms"),
        ("picture", "pictures"),
        ("images", "pictures"),
        ("shipping_mode", "logistic_type"),
        ("logistic", "logistic_type"),
        ("package_length", "package_length"),
        ("package_width", "package_width"),
        ("package_height", "package_height"),
        ("package_weight", "package_weight"),
    ]
    for needle, field_name in keyword_map:
        if needle in lowered and field_name not in missing_fields:
            missing_fields.append(field_name)
    result["missing_fields"] = list(
        dict.fromkeys(
            normalized
            for normalized in (normalize_mercadolibre_error_field(item) for item in missing_fields)
            if normalized
        )
    )
    return result


def normalize_mercadolibre_error_field(field: str) -> str:
    lowered = str(field or "").strip().lower()
    if not lowered:
        return ""
    if "shipping.mode" in lowered or "shipping_mode" in lowered or "logistic" in lowered:
        return "logistic_type"
    if "invalid access token" in lowered or "invalid_token" in lowered or "token expired" in lowered or lowered == "auth":
        return "auth"
    if "picture" in lowered or "image" in lowered:
        return "pictures"
    if "warranty" in lowered or "sale_terms" in lowered:
        return "sale_terms"
    if "category_id" in lowered or "category id" in lowered:
        return "category_id"
    if "title" in lowered:
        return "title"
    if "price" in lowered:
        return "price"
    if "available_quantity" in lowered or "quantity" in lowered or "stock" in lowered:
        return "stock"
    return str(field or "").strip()

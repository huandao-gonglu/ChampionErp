from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from .common import CN_CATEGORY_TERMS, CN_WB_TERMS, ML_CATEGORY_CN_HINTS, ML_CATEGORY_SHIPPING_CACHE_PATH, ML_CATEGORY_WORDS
from .config_http import request_json, request_ozon_json


def has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def expanded_category_keywords(keyword: str, mapping: dict[str, list[str]]) -> list[str]:
    raw = " ".join(keyword.split())
    terms: list[str] = []
    ascii_term = re.sub(r"[^\w\s-]", " ", raw, flags=re.ASCII)
    ascii_term = " ".join(ascii_term.split())
    if ascii_term:
        terms.append(ascii_term)
    for cn, mapped in mapping.items():
        if cn in raw:
            terms.extend(mapped)
    if not terms and has_cjk(raw):
        for char in raw:
            terms.extend(mapping.get(char, []))
    unique: list[str] = []
    for term in terms:
        term = term.strip()
        if term and term.lower() not in [item.lower() for item in unique]:
            unique.append(term)
    return unique


def localize_mercadolibre_category_path(path: str) -> str:
    cn_parts: list[str] = []
    for part in [item.strip() for item in path.split("/") if item.strip()]:
        hit = ""
        for en, cn in sorted(ML_CATEGORY_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(rf"\b{re.escape(en)}\b", part, flags=re.I):
                hit = re.sub(rf"\b{re.escape(en)}\b", cn, part, flags=re.I)
                break
        for en, cn in ML_CATEGORY_CN_HINTS.items():
            if not hit and en.casefold() in part.casefold():
                hit = cn
                break
        cn_parts.append(hit or part)
    cn_path = " / ".join(cn_parts)
    return f"{cn_path}  |  {path}" if cn_path and cn_path != path else path

def fetch_wildberries_shop_name(token: str) -> str:
    if not token.strip():
        raise RuntimeError("WB Token 为空。")
    try:
        data = request_json("GET", "https://common-api.wildberries.ru/api/v1/seller-info", token)
    except RuntimeError as exc:
        message = str(exc)
        if "429" in message or "Too Many Requests" in message:
            return "WB Token 已保存（接口限流，稍后再查店铺名）"
        if "401" in message or "403" in message:
            raise RuntimeError(
                "WB Token 无法通过正式环境验证。请确认创建令牌时没有勾选 Test Environment/测试环境，"
                "并且复制的是完整 Token。原始错误: " + message
            ) from exc
        raise
    if not isinstance(data, dict):
        return ""
    return data.get("tradeMark") or data.get("name") or data.get("sid") or ""


def search_mercadolibre_categories(keyword: str, token: str = "") -> list[tuple[str, str]]:
    if not keyword.strip():
        raise RuntimeError("请先填写产品名或品类。")
    results = []
    seen = set()
    terms = expanded_category_keywords(keyword, CN_CATEGORY_TERMS)
    if not terms:
        raise RuntimeError("没有识别到可查询的分类关键词，请换一个中文品类词或输入英文/西语关键词。")
    for term in terms[:8]:
        if token:
            url = "https://api.mercadolibre.com/marketplace/domain_discovery/search?" f"q={urllib.parse.quote(term)}"
            data = request_json("GET", url, token)
        else:
            url = (
                "https://api.mercadolibre.com/sites/MLM/domain_discovery/search?"
                f"limit=8&q={urllib.parse.quote(term)}"
            )
            data = request_json("GET", url)
        for item in data if isinstance(data, list) else []:
            category_id = str(item.get("category_id") or "")
            name = item.get("category_name") or item.get("domain_name") or ""
            path = mercadolibre_category_path(category_id, token) if category_id else ""
            if category_id and category_id not in seen:
                seen.add(category_id)
                label = localize_mercadolibre_category_path(path or name)
                results.append((category_id, f"{label}  |  关键词: {term}"))
    if results:
        results = filter_mercadolibre_remote_categories(results, token)
    return results[:50]


def mercadolibre_category_path(category_id: str, token: str = "") -> str:
    if not category_id:
        return ""
    try:
        data = request_json("GET", f"https://api.mercadolibre.com/categories/{category_id}", token)
        path = data.get("path_from_root", []) if isinstance(data, dict) else []
        names = [str(item.get("name") or "").strip() for item in path if isinstance(item, dict)]
        return " / ".join(name for name in names if name)
    except Exception:
        return ""


def load_ml_category_shipping_cache() -> dict[str, dict[str, Any]]:
    try:
        if ML_CATEGORY_SHIPPING_CACHE_PATH.exists():
            data = json.loads(ML_CATEGORY_SHIPPING_CACHE_PATH.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_ml_category_shipping_cache(cache: dict[str, dict[str, Any]]) -> None:
    try:
        ML_CATEGORY_SHIPPING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ML_CATEGORY_SHIPPING_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def collect_shipping_modes(value: Any) -> set[str]:
    modes: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"mode", "modes", "shipping_mode", "shipping_modes", "logistic_type", "logistic_types"}:
                if isinstance(item, str):
                    modes.add(item.lower())
                elif isinstance(item, list):
                    for element in item:
                        if isinstance(element, str):
                            modes.add(element.lower())
                        elif isinstance(element, dict):
                            modes.update(collect_shipping_modes(element))
            modes.update(collect_shipping_modes(item))
    elif isinstance(value, list):
        for item in value:
            modes.update(collect_shipping_modes(item))
    return modes


def mercadolibre_category_remote_status(category_id: str, token: str = "") -> tuple[bool, str]:
    cache = load_ml_category_shipping_cache()
    cached = cache.get(category_id)
    if isinstance(cached, dict) and "supported" in cached:
        return bool(cached.get("supported")), str(cached.get("reason") or "")
    urls = [
        f"https://api.mercadolibre.com/categories/{category_id}/shipping_preferences",
        f"https://api.mercadolibre.com/catalog_categories/{category_id}/shipping_preferences",
    ]
    last_error = ""
    for url in urls:
        try:
            data = request_json("GET", url, token)
        except Exception as exc:
            last_error = str(exc)
            continue
        modes = collect_shipping_modes(data)
        supported = "me1" in modes or "remote" in modes
        reason = "支持 remote/me1" if supported else f"不支持 remote/me1，平台返回模式: {', '.join(sorted(modes)) or '空'}"
        cache[category_id] = {"supported": supported, "reason": reason, "modes": sorted(modes)}
        save_ml_category_shipping_cache(cache)
        return supported, reason
    cache[category_id] = {"supported": False, "reason": f"无法校验物流偏好: {last_error}"}
    save_ml_category_shipping_cache(cache)
    return False, f"无法校验物流偏好: {last_error}"


def filter_mercadolibre_remote_categories(options: list[tuple[str, str]], token: str = "") -> list[tuple[str, str]]:
    if not token:
        return options
    filtered: list[tuple[str, str]] = []
    rejected: list[str] = []
    for category_id, label in options:
        supported, reason = mercadolibre_category_remote_status(category_id, token)
        if supported:
            filtered.append((category_id, f"[可发墨西哥] {label}"))
        else:
            rejected.append(f"{category_id}: {reason}")
    if filtered:
        return filtered
    return [(category_id, f"[未通过发货校验] {label}") for category_id, label in options]


def search_wildberries_subjects(keyword: str, token: str) -> list[tuple[str, str]]:
    if not keyword.strip():
        raise RuntimeError("请先填写产品名或品类。")
    if not token.strip():
        raise RuntimeError("WB Token 为空，请先在授权店铺里保存 WB Token。")
    results = []
    seen = set()
    terms = expanded_category_keywords(keyword, CN_WB_TERMS) or [keyword.strip()]
    for term in terms[:8]:
        data = request_json(
            "GET",
            "https://content-api.wildberries.ru/content/v2/object/all?"
            f"name={urllib.parse.quote(term)}&locale=ru",
            token,
        )
        raw = data.get("data", []) if isinstance(data, dict) else []
        for item in raw:
            subject_id = str(item.get("subjectID") or item.get("objectID") or item.get("id") or "")
            name = item.get("subjectName") or item.get("objectName") or item.get("name") or ""
            if subject_id and subject_id not in seen:
                seen.add(subject_id)
                results.append((subject_id, f"{name}  |  关键词: {term}"))
    return results[:50]


def estimate_mercadolibre_shipping(
    token: str,
    zip_from: str,
    zip_to: str,
    length_cm: str,
    width_cm: str,
    height_cm: str,
    weight_kg: str,
    price: str,
) -> float:
    if not token.strip():
        raise RuntimeError("缺少美客多 Access Token。")
    def num(value: str) -> float:
        return float((value or "0").replace(",", "."))
    length = max(1, int(round(num(length_cm))))
    width = max(1, int(round(num(width_cm))))
    height = max(1, int(round(num(height_cm))))
    grams = max(1, int(round(num(weight_kg) * 1000)))
    params = urllib.parse.urlencode(
        {
            "zip_code_from": zip_from or "01000",
            "zip_code_to": zip_to or "05000",
            "dimensions": f"{length}x{width}x{height},{grams}",
            "item_price": str(num(price)),
        }
    )
    data = request_json("GET", f"https://api.mercadolibre.com/sites/MLM/shipping_options?{params}", token)
    options = data.get("options", []) if isinstance(data, dict) else []
    costs = []
    for option in options:
        cost = option.get("cost") if isinstance(option, dict) else None
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
    if not costs:
        raise RuntimeError(f"未返回美客多运费选项: {data}")
    return round(min(costs), 2)


def fetch_ozon_shop_name(client_id: str, api_key: str) -> str:
    def result_name(data: dict[str, Any]) -> str:
        result = data.get("result") if isinstance(data, dict) else None
        candidates: list[Any] = []
        if isinstance(result, list):
            candidates = result
        elif isinstance(result, dict):
            for key in ("warehouses", "items", "products"):
                values = result.get(key)
                if isinstance(values, list):
                    candidates = values
                    break
        for item in candidates:
            if isinstance(item, dict):
                name = item.get("name") or item.get("warehouse_name") or item.get("offer_id")
                if name:
                    return str(name)
        return ""

    checks = [
        (
            "warehouse/list",
            "https://api-seller.ozon.ru/v1/warehouse/list",
            {},
        ),
        (
            "product/list v3",
            "https://api-seller.ozon.ru/v3/product/list",
            {"filter": {"visibility": "ALL"}, "limit": 1, "last_id": ""},
        ),
        (
            "product/info/stocks",
            "https://api-seller.ozon.ru/v3/product/info/stocks",
            {"filter": {"visibility": "ALL"}, "limit": 1, "last_id": ""},
        ),
    ]
    errors = []
    for name, url, payload in checks:
        try:
            data = request_ozon_json("POST", url, client_id, api_key, payload)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        return result_name(data) or f"Ozon {client_id}"
    raise RuntimeError(" / ".join(errors))


def required_mercadolibre_attributes(category_id: str, token: str) -> list[dict[str, Any]]:
    attrs = mercadolibre_category_attributes(category_id, token)
    required = []
    for attr in attrs if isinstance(attrs, list) else []:
        if attr.get("required"):
            required.append(
                {
                    "id": attr.get("id"),
                    "name": attr.get("name"),
                    "value_type": attr.get("value_type"),
                    "values": attr.get("values", [])[:20],
                }
            )
    return required


def mercadolibre_category_attributes(category_id: str, token: str) -> list[dict[str, Any]]:
    if not category_id:
        return []
    attrs = request_json(
        "GET",
        f"https://api.mercadolibre.com/categories/{category_id}/attributes",
        token,
    )
    normalized = []
    for attr in attrs if isinstance(attrs, list) else []:
        tags = attr.get("tags", {})
        normalized.append(
            {
                "id": attr.get("id"),
                "name": attr.get("name") or attr.get("id"),
                "value_type": attr.get("value_type"),
                "values": attr.get("values", [])[:50],
                "required": bool(
                    tags.get("required")
                    or tags.get("catalog_required")
                    or tags.get("conditional_required")
                    or tags.get("new_required")
                ),
            }
        )
    normalized.sort(key=lambda item: (not item.get("required"), str(item.get("name") or "")))
    return normalized

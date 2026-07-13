# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from copy import deepcopy
from typing import Any, Callable


SANDBOX_BASE_URL = "https://openapi-sbx.yunexpress.cn"
PRODUCTION_BASE_URL = "https://openapi.yunexpress.cn"
TOKEN_PATH = "/openapi/oauth2/token"
CREATE_PACKAGE_PATH = "/v1/order/package/create"

UrlOpen = Callable[..., Any]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any, fallback: str) -> str:
    text = _text(value).upper()
    return text or fallback


def _timeout_seconds(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = 20.0
    return max(1.0, min(timeout, 120.0))


def _base_url_for_environment(environment: str) -> str:
    return PRODUCTION_BASE_URL if environment == "production" else SANDBOX_BASE_URL


def normalize_yunexpress_config(config: dict[str, Any] | None) -> dict[str, Any]:
    raw = config if isinstance(config, dict) else {}
    environment = _text(raw.get("environment")).lower()
    if environment not in {"sandbox", "production"}:
        environment = "sandbox"
    base_url = _text(raw.get("base_url") or raw.get("baseUrl")) or _base_url_for_environment(environment)
    return {
        "environment": environment,
        "base_url": base_url.rstrip("/"),
        "app_id": _text(raw.get("app_id") or raw.get("appId")),
        "app_secret": _text(raw.get("app_secret") or raw.get("appSecret")),
        "source_key": _text(raw.get("source_key") or raw.get("sourceKey")),
        "product_code": _text(raw.get("product_code") or raw.get("productCode")),
        "source_code": _text(raw.get("source_code") or raw.get("sourceCode")),
        "platform_account_code": _text(raw.get("platform_account_code") or raw.get("platformAccountCode")),
        "label_type": _upper(raw.get("label_type") or raw.get("labelType"), "PDF"),
        "weight_unit": _upper(raw.get("weight_unit") or raw.get("weightUnit"), "KG"),
        "size_unit": _upper(raw.get("size_unit") or raw.get("sizeUnit"), "CM"),
        "timeout_seconds": str(raw.get("timeout_seconds") or raw.get("timeoutSeconds") or "20").strip() or "20",
    }


def ensure_yunexpress_config_ready(config: dict[str, Any]) -> None:
    missing = [
        label
        for key, label in (
            ("app_id", "App ID"),
            ("app_secret", "App Secret"),
            ("source_key", "SourceKey"),
        )
        if not _text(config.get(key))
    ]
    if missing:
        raise RuntimeError("云途 API 配置缺失：" + "、".join(missing))


def canonical_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def signature_content(method: str, uri: str, date_ms: int | str, body_text: str = "") -> str:
    fields = {
        "date": str(date_ms),
        "method": _text(method).upper(),
        "uri": _text(uri),
    }
    if body_text:
        fields["body"] = body_text
    return "&".join(f"{key}={fields[key]}" for key in sorted(fields))


def calculate_signature(content: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), content.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_signed_headers(
    *,
    method: str,
    uri: str,
    body_text: str,
    token: str,
    secret: str,
    date_ms: int | None = None,
    language: str = "zh-CN",
) -> dict[str, str]:
    timestamp = int(date_ms if date_ms is not None else time.time() * 1000)
    content = signature_content(method, uri, timestamp, body_text)
    return {
        "Content-Type": "application/json;charset=utf-8",
        "Accept": "application/json",
        "Accept-Language": language,
        "token": token,
        "date": str(timestamp),
        "sign": calculate_signature(content, secret),
    }


def _response_json(response: Any) -> dict[str, Any]:
    raw = response.read()
    charset = "utf-8"
    headers = getattr(response, "headers", None)
    if headers is not None:
        charset = headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, errors="replace")
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("云途 API 返回了非对象 JSON。")
    return data


def _request_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    body = body.strip()
    return f"云途 API 请求失败：HTTP {exc.code}" + (f"，{body[:500]}" if body else "")


class YunExpressClient:
    def __init__(self, config: dict[str, Any], urlopen: UrlOpen | None = None) -> None:
        self.config = normalize_yunexpress_config(config)
        self.urlopen = urlopen or urllib.request.urlopen
        self.timeout = _timeout_seconds(self.config.get("timeout_seconds"))

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or SANDBOX_BASE_URL).rstrip("/")

    def request_access_token(self) -> dict[str, Any]:
        ensure_yunexpress_config_ready(self.config)
        payload = {
            "grantType": "client_credentials",
            "appId": self.config["app_id"],
            "appSecret": self.config["app_secret"],
            # 云途沙盒实际校验小写 sourcekey；sourceKey 会被误报为应用密钥错误。
            "sourcekey": self.config["source_key"],
        }
        result = self._post_json(TOKEN_PATH, payload, signed=False)
        result_payload = result.get("result") if isinstance(result.get("result"), dict) else {}
        access_token = _text(result.get("accessToken") or result.get("access_token") or result_payload.get("accessToken") or result_payload.get("access_token"))
        if not access_token:
            message = _text(result.get("msg") or result.get("message") or result.get("error")) or "云途没有返回 accessToken。"
            raise RuntimeError(message)
        return {
            "access_token": access_token,
            "expires_in": result.get("expiresIn") or result.get("expires_in") or result_payload.get("expiresIn") or result_payload.get("expires_in") or "",
            "raw": result,
        }

    def create_package_order(self, payload: dict[str, Any], access_token: str = "") -> dict[str, Any]:
        token = _text(access_token)
        token_result: dict[str, Any] = {}
        if not token:
            token_result = self.request_access_token()
            token = str(token_result.get("access_token") or "")
        result = self._post_json(CREATE_PACKAGE_PATH, payload, signed=True, access_token=token)
        return {"request_payload": payload, "token": {"expires_in": token_result.get("expires_in", "")}, "response": result}

    def _post_json(self, path: str, payload: dict[str, Any], *, signed: bool, access_token: str = "") -> dict[str, Any]:
        body_text = canonical_json(payload)
        headers = {"Content-Type": "application/json;charset=utf-8", "Accept": "application/json"}
        if signed:
            headers = build_signed_headers(
                method="POST",
                uri=path,
                body_text=body_text,
                token=access_token,
                secret=str(self.config.get("app_secret") or ""),
            )
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body_text.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self.urlopen(request, timeout=self.timeout) as response:
                return _response_json(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_request_error_message(exc)) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"云途 API 连接失败：{exc.reason}") from exc


def build_create_package_payload(shipment: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    source = deepcopy(shipment if isinstance(shipment, dict) else {})
    if isinstance(source.get("yunexpress_payload"), dict):
        source = deepcopy(source["yunexpress_payload"])
    cfg = normalize_yunexpress_config(config)
    payload: dict[str, Any] = {
        "product_code": _text(source.get("product_code") or source.get("productCode") or cfg.get("product_code")),
        "receiver": source.get("receiver") if isinstance(source.get("receiver"), dict) else {},
        "packages": source.get("packages") if isinstance(source.get("packages"), list) else [],
        "declaration_info": source.get("declaration_info") if isinstance(source.get("declaration_info"), list) else source.get("declarationInfo") if isinstance(source.get("declarationInfo"), list) else [],
        "customer_order_number": _text(source.get("customer_order_number") or source.get("customerOrderNumber")),
        "label_type": _text(source.get("label_type") or source.get("labelType") or cfg.get("label_type")),
        "weight_unit": _text(source.get("weight_unit") or source.get("weightUnit") or cfg.get("weight_unit")),
        "size_unit": _text(source.get("size_unit") or source.get("sizeUnit") or cfg.get("size_unit")),
    }
    optional_aliases = {
        "source_code": ("source_code", "sourceCode"),
        "platform_account_code": ("platform_account_code", "platformAccountCode"),
        "sensitive_type": ("sensitive_type", "sensitiveType"),
        "dangerous_goods_type": ("dangerous_goods_type", "dangerousGoodsType"),
        "point_relais_num": ("point_relais_num", "pointRelaisNum"),
        "manufacture_sales_name": ("manufacture_sales_name", "manufactureSalesName"),
        "credit_code": ("credit_code", "creditCode"),
        "customer_label": ("customer_label", "customerLabel"),
    }
    for target, aliases in optional_aliases.items():
        value = next((_text(source.get(alias)) for alias in aliases if _text(source.get(alias))), "")
        if not value and target in {"source_code", "platform_account_code"}:
            value = _text(cfg.get(target))
        if value:
            payload[target] = value
    for object_key in ("order_numbers", "sender", "customs_number", "platform", "payment"):
        if isinstance(source.get(object_key), dict):
            payload[object_key] = deepcopy(source[object_key])
    for array_key in ("extra_services",):
        if isinstance(source.get(array_key), list):
            payload[array_key] = deepcopy(source[array_key])
    return {key: value for key, value in payload.items() if value not in ("", None, [], {}) or key in {"receiver", "packages", "declaration_info"}}


def validate_create_package_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _text(payload.get("product_code")):
        errors.append("缺少物流产品编码 product_code。")
    if not isinstance(payload.get("receiver"), dict) or not payload.get("receiver"):
        errors.append("缺少收件人 receiver。")
    if not isinstance(payload.get("packages"), list) or not payload.get("packages"):
        errors.append("缺少包裹信息 packages。")
    if not isinstance(payload.get("declaration_info"), list) or not payload.get("declaration_info"):
        errors.append("缺少申报信息 declaration_info。")
    return errors


def build_create_package_preview(config: dict[str, Any], shipment: dict[str, Any], *, date_ms: int | None = None, token: str = "ACCESS_TOKEN") -> dict[str, Any]:
    cfg = normalize_yunexpress_config(config)
    payload = build_create_package_payload(shipment, cfg)
    body_text = canonical_json(payload)
    headers = build_signed_headers(
        method="POST",
        uri=CREATE_PACKAGE_PATH,
        body_text=body_text,
        token=token,
        secret=str(cfg.get("app_secret") or ""),
        date_ms=date_ms,
    ) if cfg.get("app_secret") else {
        "Content-Type": "application/json;charset=utf-8",
        "token": token,
        "date": str(date_ms or int(time.time() * 1000)),
        "sign": "",
    }
    return {
        "method": "POST",
        "url": f"{cfg.get('base_url')}{CREATE_PACKAGE_PATH}",
        "headers": headers,
        "payload": payload,
        "body": body_text,
        "errors": validate_create_package_payload(payload),
    }


__all__ = [
    "CREATE_PACKAGE_PATH",
    "PRODUCTION_BASE_URL",
    "SANDBOX_BASE_URL",
    "TOKEN_PATH",
    "YunExpressClient",
    "build_create_package_payload",
    "build_create_package_preview",
    "build_signed_headers",
    "calculate_signature",
    "canonical_json",
    "ensure_yunexpress_config_ready",
    "normalize_yunexpress_config",
    "signature_content",
    "validate_create_package_payload",
]

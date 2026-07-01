# -*- coding: utf-8 -*-
from __future__ import annotations

import urllib.parse
from typing import Any

from erp_web import marketplaces as publisher
from erp_web.services import ai_gateway, ai_model_config

from .product_store import (
    _store_auth_result_fields,
    load_app_config,
    load_store_config,
    mask_secret,
    save_store_config,
    summarize_store_auth_states,
)
from .publish_logs_runtime import append_ml_auth_test_log
from .runtime_common import APP_DIR

def _merge_saved_ai_model_config(model_config: dict[str, Any]) -> dict[str, Any]:
    incoming = dict(model_config if isinstance(model_config, dict) else {})
    model_id = str(incoming.get("id") or "").strip()
    if not model_id:
        return incoming
    source_model_id = str(incoming.get("copy_source_id") or "").strip()
    stored_models = ai_model_config.normalize_ai_models(load_app_config().get("ai_models"))
    stored = next((model for model in stored_models if str(model.get("id") or "") == model_id), {})
    source = next((model for model in stored_models if str(model.get("id") or "") == source_model_id), {}) if source_model_id else {}
    if not stored and not source:
        return incoming
    merged = dict(stored or source)
    for key, value in incoming.items():
        if key == "api_key" and not str(value or "").strip():
            continue
        if key in {"model_options", "available_models", "api_key_configured", "api_key_masked", "copy_source_id"}:
            continue
        merged[key] = value
    return merged


def test_ai_model_config(model_config: dict[str, Any]) -> dict[str, Any]:
    return ai_gateway.test_ai_model(APP_DIR, _merge_saved_ai_model_config(model_config))


def test_api_config(kind: str, config: dict[str, Any], test_value: str = "") -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    if kind in {"exchange_rate", "exchange", "pricing"}:
        from .pricing_runtime import fetch_pricing_exchange_rates

        result = fetch_pricing_exchange_rates(True, {"pricing_defaults": config if isinstance(config, dict) else {}})
        if not result.get("ok"):
            return {
                "ok": False,
                "channel": "exchange_rate",
                "error": str(result.get("error") or "汇率 API 测试失败"),
                "next_action": "请检查汇率 API URL、超时秒数，以及接口响应是否包含 CNY 和 MXN 汇率。",
                "raw": result,
            }
        rates = result.get("rates") if isinstance(result.get("rates"), dict) else {}
        return {
            "ok": True,
            "channel": "exchange_rate",
            "message": f"汇率 API 测试成功：USD/CNY {rates.get('usd_cny_rate')}，MXN/USD {rates.get('mxn_usd_rate')}。",
            "next_action": "可以保存配置并在核价时使用实时汇率。",
            "source": result.get("source"),
            "rates": rates,
        }
    if kind in {"1688", "alibaba", "1688_api"}:
        from .source_collect_1688_api import (
            build_1688_api_params,
            ensure_1688_api_ready,
            extract_1688_offer_id,
            normalize_1688_api_config,
            parse_1688_api_product,
            request_1688_product_detail,
        )

        api_config = normalize_1688_api_config(config if isinstance(config, dict) else {})
        ensure_1688_api_ready(api_config)
        offer_id = extract_1688_offer_id(test_value)
        if not offer_id:
            params = build_1688_api_params(api_config, "123456789")
            return {
                "ok": True,
                "channel": "1688",
                "message": "1688 API 配置校验通过：凭证、请求地址和签名参数可生成。",
                "next_action": "如需真实连通测试，请填写一个 1688 商品 ID 或详情链接后再点测试。",
                "request": {
                    "base_url": api_config.get("base_url"),
                    "method": api_config.get("method"),
                    "app_key": mask_secret(api_config.get("app_key")),
                    "sign_length": len(str(params.get("sign") or "")),
                },
            }
        response = request_1688_product_detail(api_config, offer_id)
        raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
        source = parse_1688_api_product(raw, f"https://detail.1688.com/offer/{offer_id}.html", offer_id)
        return {
            "ok": True,
            "channel": "1688",
            "message": f"1688 API 测试成功：已读取商品 {source.get('title') or offer_id}。",
            "next_action": "可以保存配置，并在采集页选择 API 采集。",
            "http_status": response.get("http_status"),
            "offer_id": offer_id,
            "title": source.get("title"),
            "images_count": len(source.get("images") if isinstance(source.get("images"), list) else []),
        }
    raise RuntimeError("未知 API 测试类型。")


def build_mercadolibre_auth_link(app_id: str, redirect_uri: str) -> dict[str, str]:
    if not app_id or not redirect_uri:
        raise RuntimeError("请先填写 Mercado Libre 的 client_id 和 redirect_uri。")
    parsed = urllib.parse.urlparse(str(redirect_uri or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise RuntimeError("REDIRECT_URI_MUST_BE_HTTPS：Mercado Libre Developers 要求 Redirect URI 使用 https://")
    verifier, challenge = publisher.generate_pkce_pair()
    url = (
        "https://global-selling.mercadolibre.com/authorization?"
        f"response_type=code&client_id={urllib.parse.quote(app_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&code_challenge={urllib.parse.quote(challenge)}&code_challenge_method=S256"
    )
    config = load_store_config()
    config.setdefault("mercadolibre", {})["code_verifier"] = verifier
    config["mercadolibre"]["redirect_uri"] = redirect_uri
    config["mercadolibre"]["app_id"] = app_id
    save_store_config(config)
    return {"url": url, "code_verifier": verifier}


def preview_mercadolibre_auth_link(app_id: str, redirect_uri: str) -> str:
    if not app_id or not redirect_uri:
        raise RuntimeError("请先填写 Mercado Libre 的 client_id 和 redirect_uri。")
    _, challenge = publisher.generate_pkce_pair()
    return (
        "https://global-selling.mercadolibre.com/authorization?"
        f"response_type=code&client_id={urllib.parse.quote(app_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&code_challenge={urllib.parse.quote(challenge)}&code_challenge_method=S256"
    )


def _mercadolibre_app_secret(store: dict[str, Any]) -> str:
    return str(store.get("app_secret") or store.get("client_secret") or "").strip()


def _update_store_auth_state(config: dict[str, Any], platform: str, updates: dict[str, Any]) -> dict[str, Any]:
    config = config if isinstance(config, dict) else {}
    platform = str(platform or "").strip().lower()
    platform_cfg = config.setdefault(platform, {})
    if isinstance(platform_cfg, dict):
        platform_cfg.update({key: value for key, value in updates.items() if value is not None})
    return config


def exchange_mercadolibre_code_from_body(body: dict[str, Any]) -> dict[str, Any]:
    config = load_store_config()
    ml = config.setdefault("mercadolibre", {})
    app_id = str(body.get("app_id") or ml.get("app_id") or "").strip()
    app_secret = str(body.get("app_secret") or body.get("client_secret") or _mercadolibre_app_secret(ml)).strip()
    redirect_uri = str(body.get("redirect_uri") or ml.get("redirect_uri") or "").strip()
    code_verifier = str(body.get("code_verifier") or ml.get("code_verifier") or "").strip()
    code_or_url = str(body.get("code_or_url") or body.get("code") or "").strip()
    exchanged = False
    if not code_or_url:
        raise RuntimeError("请先粘贴包含 code= 的回调地址，或直接粘贴授权 code。")
    if not code_verifier:
        raise RuntimeError("CODE_VERIFIER_MISSING：请重新生成授权链接后再换 token。")
    ml["app_id"] = app_id
    ml["app_secret"] = app_secret
    ml["client_secret"] = app_secret
    ml["redirect_uri"] = redirect_uri
    ml["site_id"] = str(body.get("site_id") or ml.get("site_id") or "MLM").strip() or "MLM"
    try:
        result = publisher.exchange_mercadolibre_code(app_id, app_secret, redirect_uri, code_or_url, code_verifier)
        token = str(result.get("access_token") or "").strip()
        shop_name = ""
        if token:
            try:
                shop_name = publisher.fetch_mercadolibre_shop_name(token)
            except Exception:
                shop_name = ""
        ml["access_token"] = token
        if result.get("refresh_token"):
            ml["refresh_token"] = str(result.get("refresh_token") or "").strip()
        ml["shop_name"] = shop_name or str(result.get("user_id") or "").strip() or ml.get("shop_name", "")
        ml["user_id"] = str(result.get("user_id") or ml.get("user_id") or "").strip()
        ml.update(_store_auth_result_fields("mercadolibre", "测试成功", ml.get("shop_name") or ml.get("user_id") or token))
        ml["auth_error_code"] = ""
        ml["auth_error_message"] = ""
        exchanged = True
        append_ml_auth_test_log(
            "exchange_code",
            "success",
            {"redirect_uri": redirect_uri, "code_present": bool(code_or_url)},
            {"status": "success", "masked_account": ml.get("auth_masked_account") or "", "checked_at": ml.get("auth_checked_at") or ""},
            next_action="code 已使用，不要长期保存。继续测试 user_info 和 refresh token。",
        )
        return {
            "platform": "mercadolibre",
            "status": "测试成功",
            "shop_name": ml.get("shop_name") or "",
            "user_info_checked": True,
            "user_info": {
                "user_id": ml.get("user_id") or "",
                "shop_name": ml.get("shop_name") or "",
                "site_id": ml.get("site_id") or "",
            },
            "masked_account": ml.get("auth_masked_account") or "",
            "checked_at": ml.get("auth_checked_at") or "",
            "storeAuthSummary": summarize_store_auth_states(config),
            "message": "Mercado Libre 授权成功，已自动读取用户信息。",
            "next_action": "授权成功。下一步可直接在授权页点击“立即刷新类目缓存”，同步 Mercado Libre 官方类目和必填属性。",
        }
    finally:
        if exchanged and "code_verifier" in ml:
            ml.pop("code_verifier", None)
        save_store_config(config, preserve_empty_sensitive=False)


def refresh_mercadolibre_token_from_body(body: dict[str, Any]) -> dict[str, Any]:
    config = load_store_config()
    ml = config.setdefault("mercadolibre", {})
    app_id = str(body.get("app_id") or ml.get("app_id") or "").strip()
    app_secret = str(body.get("app_secret") or body.get("client_secret") or _mercadolibre_app_secret(ml)).strip()
    refresh_token = str(body.get("refresh_token") or ml.get("refresh_token") or "").strip()
    if not app_id or not app_secret or not refresh_token:
        raise RuntimeError("请先填写 App ID、App Secret 和 Refresh Token。")
    result = publisher.refresh_mercadolibre_token(app_id, app_secret, refresh_token)
    token = str(result.get("access_token") or "").strip()
    shop_name = ""
    if token:
        try:
            shop_name = publisher.fetch_mercadolibre_shop_name(token)
        except Exception:
            shop_name = ""
    ml["app_id"] = app_id
    ml["app_secret"] = app_secret
    ml["client_secret"] = ml.get("client_secret") or app_secret
    ml["refresh_token"] = str(result.get("refresh_token") or refresh_token).strip()
    ml["access_token"] = token
    ml["shop_name"] = shop_name or ml.get("shop_name", "")
    ml.update(_store_auth_result_fields("mercadolibre", "测试成功", ml.get("shop_name") or token))
    ml["auth_error_code"] = ""
    ml["auth_error_message"] = ""
    save_store_config(config)
    return {
        "platform": "mercadolibre",
        "status": "测试成功",
        "shop_name": ml.get("shop_name") or "",
        "masked_account": ml.get("auth_masked_account") or "",
        "checked_at": ml.get("auth_checked_at") or "",
        "storeAuthSummary": summarize_store_auth_states(config),
        "message": "Mercado Libre token 已刷新。",
        "next_action": "Token 已刷新。下一步可直接在授权页点击“立即刷新类目缓存”，同步 Mercado Libre 官方类目和必填属性。",
    }


def test_store_auth(platform: str, scope: str = "") -> dict[str, Any]:
    platform = (platform or "").strip().lower()
    scope = (scope or "").strip().lower()
    config = load_store_config()
    try:
        if platform == "mercadolibre":
            token = str(config.get("mercadolibre", {}).get("access_token") or "").strip()
            if not token:
                raise RuntimeError("请先填写 Mercado Libre access token，或通过授权链接换取 token。")
            name = publisher.fetch_mercadolibre_shop_name(token)
            store = config.setdefault("mercadolibre", {})
            store["shop_name"] = name or store.get("shop_name", "")
            store.update(_store_auth_result_fields("mercadolibre", "测试成功", name or token))
            store["auth_error_code"] = ""
            store["auth_error_message"] = ""
        elif platform == "wildberries":
            wb = config.setdefault("wildberries", {})
            token_key = {
                "content": "content_token",
                "prices": "prices_token",
                "stocks": "stocks_token",
                "marketplace": "marketplace_token",
            }.get(scope, "content_token")
            token = str(wb.get(token_key) or wb.get("content_token") or "").strip()
            if not token:
                raise RuntimeError("请先填写 Wildberries Token。")
            name = publisher.fetch_wildberries_shop_name(token)
            wb["shop_name"] = name or wb.get("shop_name", "")
            wb.update(_store_auth_result_fields("wildberries", "测试成功", name or mask_secret(token)))
            wb["auth_error_code"] = ""
            wb["auth_error_message"] = ""
        elif platform == "ozon":
            ozon = config.get("ozon", {})
            client_id = str(ozon.get("client_id") or "").strip()
            api_key = str(ozon.get("api_key") or "").strip()
            if not client_id or not api_key:
                raise RuntimeError("请先填写 Ozon Client ID 和 API Key。")
            if scope == "category" and not str(ozon.get("category_id") or "").strip():
                raise RuntimeError("请先填写 Ozon category_id，再测试类目读取。")
            name = publisher.fetch_ozon_shop_name(client_id, api_key)
            config.setdefault("ozon", {})["shop_name"] = name or config.get("ozon", {}).get("shop_name", "")
            config["ozon"].update(_store_auth_result_fields("ozon", "测试成功", name or client_id))
            config["ozon"]["auth_error_code"] = ""
            config["ozon"]["auth_error_message"] = ""
        else:
            raise RuntimeError("不支持的平台。")
    except Exception as exc:
        error_message = str(exc)
        raise RuntimeError(f"测试失败：{error_message}") from exc
    save_store_config(config)
    return {
        "ok": True,
        "platform": platform,
        "scope": scope,
        "shop_name": str(config.get(platform, {}).get("shop_name") or "已授权店铺"),
        "masked_account": str(config.get(platform, {}).get("auth_masked_account") or ""),
        "checked_at": str(config.get(platform, {}).get("auth_checked_at") or ""),
        "status": str(config.get(platform, {}).get("auth_status") or "ok"),
        "message": "测试成功：授权可用。",
        "storeAuthSummary": summarize_store_auth_states(config),
    }


__all__ = [
    "build_mercadolibre_auth_link",
    "exchange_mercadolibre_code_from_body",
    "mercadolibre_auth_checklist",
    "preview_mercadolibre_auth_link",
    "refresh_mercadolibre_token_from_body",
    "test_ai_model_config",
    "test_api_config",
    "test_store_auth",
]

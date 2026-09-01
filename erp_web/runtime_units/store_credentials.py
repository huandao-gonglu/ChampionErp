# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import urllib.parse
from importlib import import_module
from typing import Any, Callable

from erp_web.context import get_context
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.marketplace_registry import MarketplaceSpec, marketplace_spec, platform_label
from erp_web import marketplaces as publisher
from erp_web.services import ai_gateway, ai_model_config
from erp_web.services.config_service import public_store_config
from erp_web.services.listing_currency_service import (
    apply_currency_discovery,
    public_currency_configuration,
    reset_currency_state_in_store,
    store_identity_for_platform,
    store_listing_currency_from_auth,
    store_listing_currency_ready,
    write_currency_state,
)
from erp_web.services.mercadolibre_credential_lock import (
    MERCADOLIBRE_AUTH_LOCK,
)
from erp_web.stores.config_store import (
    _store_auth_result_fields,
    store_auth_failure_code,
    summarize_store_auth_states,
)
from erp_web.stores.product_store import mask_secret

from .collect_helpers import collect_time_iso
from .mercadolibre_auth import (
    discover_mercadolibre_listing_currency,
    sync_mercadolibre_auth_and_currency,
    sync_mercadolibre_identity,
)
from .publish_logs_runtime import append_ml_auth_test_log


def _apply_store_currency_discovery(
    platform: str,
    store: dict[str, Any],
    discovery: dict[str, Any],
) -> dict[str, Any]:
    """共享发现状态机落库点：tester 只返回发现结果，不各自拼接状态。"""

    identity = store_identity_for_platform(platform, store)
    previous = store_listing_currency_from_auth(platform, identity, store)
    state = apply_currency_discovery(platform, identity, discovery, previous=previous)
    write_currency_state(store, state)
    return state


def _store_publish_readiness_next_action(
    platform: str,
    currency_state: dict[str, Any],
) -> str:
    """把授权成功与实际发布就绪状态分开呈现。"""

    if store_listing_currency_ready(currency_state):
        return "已可用于发布"
    error_code = str(currency_state.get("currency_error_code") or "").strip()
    if (
        str(platform or "").strip().lower() == "mercadolibre"
        and error_code == "MERCADOLIBRE_USER_PRODUCTS_REQUIRED"
    ):
        return (
            "授权有效，但账号尚未开通 User Products；"
            "请联系 Mercado Libre 负责团队启用，重新授权不会自动开通"
        )
    status = str(currency_state.get("currency_status") or "").strip()
    if status == "selection_required":
        return "授权有效；请选择发布货币后再核价与发布"
    if status == "manual_required":
        return "授权有效；请填写发布货币后再核价与发布"
    if status == "refresh_failed":
        return "授权有效，但发布货币读取失败；请重新检查平台接口"
    return "授权有效，但发布货币尚未就绪"

def _merge_saved_ai_model_config(model_config: dict[str, Any]) -> dict[str, Any]:
    incoming = dict(model_config if isinstance(model_config, dict) else {})
    model_id = str(incoming.get("id") or "").strip()
    if not model_id:
        return incoming
    source_model_id = str(incoming.get("copy_source_id") or "").strip()
    stored_models = ai_model_config.normalize_ai_models(
        get_context().config.load_app_config().get("ai_models")
    )
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
    return ai_gateway.test_ai_model(
        get_context().paths.app_dir,
        _merge_saved_ai_model_config(model_config),
    )


def test_api_config(kind: str, config: dict[str, Any], test_value: str = "") -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    if kind in {"exchange_rate", "exchange", "pricing"}:
        from .pricing_runtime import fetch_pricing_exchange_rates

        result = fetch_pricing_exchange_rates(True, {"pricing_defaults": config if isinstance(config, dict) else {}})
        # stale 表回落对核价是兜底，但对“测试 API 连通性”就是失败。
        if not result.get("ok") or result.get("stale"):
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
            parse_1688_api_product,
            request_1688_product_detail,
            resolve_1688_api_config,
        )

        api_config = resolve_1688_api_config(
            config if isinstance(config, dict) else {}
        )
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
    if kind in {"yunexpress", "yunexpress_api", "yuntu"}:
        from ..facades.logistics_facade import test_yunexpress_config

        return test_yunexpress_config(config if isinstance(config, dict) else {})
    raise RuntimeError("未知 API 测试类型。")


def build_mercadolibre_auth_link(app_id: str, redirect_uri: str) -> dict[str, Any]:
    with MERCADOLIBRE_AUTH_LOCK:
        return _build_mercadolibre_auth_link_unlocked(app_id, redirect_uri)


def _build_mercadolibre_auth_link_unlocked(
    app_id: str,
    redirect_uri: str,
) -> dict[str, Any]:
    if not app_id or not redirect_uri:
        raise RuntimeError("请先填写 Mercado Libre 的 client_id 和 redirect_uri。")
    parsed = urllib.parse.urlparse(str(redirect_uri or "").strip())
    if parsed.scheme != "https":
        raise RuntimeError("REDIRECT_URI_MUST_BE_HTTPS：Mercado Libre Developers 要求 Redirect URI 使用 https://")
    verifier, challenge = publisher.generate_pkce_pair()
    url = (
        "https://global-selling.mercadolibre.com/authorization?"
        f"response_type=code&client_id={urllib.parse.quote(app_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&code_challenge={urllib.parse.quote(challenge)}&code_challenge_method=S256"
    )
    config = get_context().config.load_store_config()
    config.setdefault("mercadolibre", {})["code_verifier"] = verifier
    config["mercadolibre"]["redirect_uri"] = redirect_uri
    config["mercadolibre"]["app_id"] = app_id
    get_context().config.save_store_config(config)
    return {"url": url, "verifier_saved": True}


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
    with MERCADOLIBRE_AUTH_LOCK:
        return _exchange_mercadolibre_code_from_body_unlocked(body)


def _exchange_mercadolibre_code_from_body_unlocked(
    body: dict[str, Any],
) -> dict[str, Any]:
    config = get_context().config.load_store_config()
    ml = config.setdefault("mercadolibre", {})
    app_id = str(body.get("app_id") or ml.get("app_id") or "").strip()
    app_secret = str(body.get("app_secret") or body.get("client_secret") or _mercadolibre_app_secret(ml)).strip()
    redirect_uri = str(body.get("redirect_uri") or ml.get("redirect_uri") or "").strip()
    code_verifier = str(body.get("code_verifier") or ml.get("code_verifier") or "").strip()
    code_or_url = str(body.get("code_or_url") or body.get("code") or "").strip()
    if not code_or_url:
        raise RuntimeError("请先粘贴包含 code= 的回调地址，或直接粘贴授权 code。")
    if not code_verifier:
        raise RuntimeError("CODE_VERIFIER_MISSING：请重新生成授权链接后再换 token。")
    ml["app_id"] = app_id
    ml["app_secret"] = app_secret
    ml["client_secret"] = app_secret
    ml["redirect_uri"] = redirect_uri
    ml["site_id"] = str(body.get("site_id") or ml.get("site_id") or "CBT").strip() or "CBT"
    result = publisher.exchange_mercadolibre_code(
        app_id,
        app_secret,
        redirect_uri,
        code_or_url,
        code_verifier,
    )
    token = str(result.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Mercado Libre 换取 token 后未返回 access_token。")
    ml["access_token"] = token
    if result.get("refresh_token"):
        ml["refresh_token"] = str(result.get("refresh_token") or "").strip()
    oauth_user_id = str(result.get("user_id") or "").strip()

    # 远端已消费 code：先在 SQLite 单独提交新 token 并移除
    # code_verifier，再发现身份和币种。静态 JSON 写入失败不得
    # 回滚已经轮换的单次 OAuth 凭据。
    config_store = get_context().config
    config_store.commit_mercadolibre_oauth_credentials(
        ml,
        oauth_user_id=oauth_user_id,
        consume_code_verifier=True,
    )
    config = config_store.load_store_config()
    ml = config.setdefault("mercadolibre", {})
    sync_result = sync_mercadolibre_auth_and_currency(config)
    currency_state = sync_result.get("currency_state")
    currency_state = currency_state if isinstance(currency_state, dict) else {}
    identity_ready = bool(sync_result.get("identity_ready"))
    publish_ready = store_listing_currency_ready(currency_state)
    publish_next_action = _store_publish_readiness_next_action(
        "mercadolibre", currency_state
    )
    status = "测试成功" if identity_ready else "测试失败"
    ml.update(
        _store_auth_result_fields(
            "mercadolibre",
            status,
            ml.get("shop_name") or ml.get("user_id") or token,
            error_code=str(sync_result.get("identity_error_code") or ""),
            error_message=str(sync_result.get("identity_error_message") or ""),
            next_action=publish_next_action,
        )
    )
    config_store.save_store_config(config)
    append_ml_auth_test_log(
        "exchange_code",
        "success" if identity_ready else "failed",
        {"redirect_uri": redirect_uri, "code_present": bool(code_or_url)},
        {
            "status": "success" if identity_ready else "failed",
            "masked_account": ml.get("auth_masked_account") or "",
            "checked_at": ml.get("auth_checked_at") or "",
        },
        next_action=(
            "code 已使用，新 token 已保存。"
            if not identity_ready
            else "code 已使用，不要长期保存。"
        ),
    )
    return {
        "platform": "mercadolibre",
        "status": status,
        "shop_name": ml.get("shop_name") or "",
        "user_info_checked": identity_ready,
        "user_info": {
            "user_id": ml.get("user_id") or "",
            "shop_name": ml.get("shop_name") or "",
            "site_id": ml.get("site_id") or "",
            "account_site_id": ml.get("account_site_id") or "",
        },
        "masked_account": ml.get("auth_masked_account") or "",
        "checked_at": ml.get("auth_checked_at") or "",
        "publish_ready": publish_ready,
        "currency_configuration": (
            public_currency_configuration(currency_state)
            if currency_state
            else {}
        ),
        "storeAuthSummary": summarize_store_auth_states(config),
        "message": (
            "Mercado Libre token 已保存，但用户信息同步失败。"
            if not identity_ready
            else "Mercado Libre 授权成功，已自动读取用户信息。"
        ),
        "next_action": publish_next_action,
    }


def refresh_mercadolibre_token_from_body(
    body: dict[str, Any],
    *,
    failed_access_token: str = "",
) -> dict[str, Any]:
    with MERCADOLIBRE_AUTH_LOCK:
        return _refresh_mercadolibre_token_from_body_unlocked(
            body,
            failed_access_token=failed_access_token,
        )


def _refresh_mercadolibre_token_from_body_unlocked(
    body: dict[str, Any],
    *,
    failed_access_token: str = "",
) -> dict[str, Any]:
    config = get_context().config.load_store_config()
    ml = config.setdefault("mercadolibre", {})
    current_access_token = str(ml.get("access_token") or "").strip()
    failed_access_token = str(failed_access_token or "").strip()
    if (
        failed_access_token
        and current_access_token
        and current_access_token != failed_access_token
    ):
        # 另一个发布线程已经完成轮换；复用锁内重新读取到的新凭据，避免
        # 对单次 refresh_token 做第二次交换。
        identity = store_identity_for_platform("mercadolibre", ml)
        currency_state = store_listing_currency_from_auth(
            "mercadolibre",
            identity,
            ml,
        )
        publish_ready = store_listing_currency_ready(currency_state)
        identity_ready = str(ml.get("auth_status") or "") == "测试成功"
        return {
            "platform": "mercadolibre",
            "status": str(ml.get("auth_status") or "测试成功"),
            "identity_ready": identity_ready,
            "identity_error_code": str(
                ml.get("auth_error_code")
                or currency_state.get("currency_error_code")
                or ""
            ),
            "identity_error_message": str(
                ml.get("auth_error_message")
                or currency_state.get("currency_error_message")
                or ""
            ),
            "publish_ready": publish_ready,
            "message": "Mercado Libre token 已由并发发布任务刷新。",
            "next_action": _store_publish_readiness_next_action(
                "mercadolibre",
                currency_state,
            ),
            "reused_refreshed_token": True,
        }
    app_id = str(body.get("app_id") or ml.get("app_id") or "").strip()
    app_secret = str(body.get("app_secret") or body.get("client_secret") or _mercadolibre_app_secret(ml)).strip()
    refresh_token = str(body.get("refresh_token") or ml.get("refresh_token") or "").strip()
    if not app_id or not app_secret or not refresh_token:
        raise RuntimeError("请先填写 App ID、App Secret 和 Refresh Token。")
    result = publisher.refresh_mercadolibre_token(app_id, app_secret, refresh_token)
    token = str(result.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Mercado Libre 刷新 token 后未返回 access_token。")
    ml["app_id"] = app_id
    ml["app_secret"] = app_secret
    ml["client_secret"] = ml.get("client_secret") or app_secret
    ml["refresh_token"] = str(result.get("refresh_token") or refresh_token).strip()
    ml["access_token"] = token
    # 远端 refresh_token 已被消费：先在 SQLite 中独立提交
    # 新凭据，再发现账号模型、市场映射与币种。此路径
    # 保留可能正在进行的 PKCE code_verifier。
    config_store = get_context().config
    config_store.commit_mercadolibre_oauth_credentials(
        ml,
        oauth_user_id=str(result.get("user_id") or "").strip(),
    )
    config = config_store.load_store_config()
    ml = config.setdefault("mercadolibre", {})
    # 刷新成功后同步用户信息与币种发现，保持店铺币种状态与远端一致。
    sync_result = sync_mercadolibre_auth_and_currency(config)
    currency_state = sync_result.get("currency_state")
    currency_state = currency_state if isinstance(currency_state, dict) else {}
    identity_ready = bool(sync_result.get("identity_ready"))
    publish_ready = store_listing_currency_ready(currency_state)
    publish_next_action = _store_publish_readiness_next_action(
        "mercadolibre", currency_state
    )
    status = "测试成功" if identity_ready else "测试失败"
    ml.update(
        _store_auth_result_fields(
            "mercadolibre",
            status,
            ml.get("shop_name") or ml.get("user_id") or token,
            error_code=str(sync_result.get("identity_error_code") or ""),
            error_message=str(
                sync_result.get("identity_error_message") or ""
            ),
            next_action=publish_next_action,
        )
    )
    config_store.save_store_config(config)
    return {
        "platform": "mercadolibre",
        "status": status,
        "identity_ready": identity_ready,
        "identity_error_code": str(
            sync_result.get("identity_error_code") or ""
        ),
        "identity_error_message": str(
            sync_result.get("identity_error_message") or ""
        ),
        "shop_name": ml.get("shop_name") or "",
        "masked_account": ml.get("auth_masked_account") or "",
        "checked_at": ml.get("auth_checked_at") or "",
        "publish_ready": publish_ready,
        "currency_configuration": public_currency_configuration(currency_state) if currency_state else {},
        "storeAuthSummary": summarize_store_auth_states(config),
        "message": (
            "Mercado Libre token 已刷新，但用户信息同步失败。"
            if not identity_ready
            else "Mercado Libre token 已刷新。"
        ),
        "next_action": publish_next_action,
    }


def ensure_mercadolibre_auth_ready(
    config: dict[str, Any],
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """解析当前可用的 Mercado Libre 授权，并串行处理 token 轮换。"""

    with MERCADOLIBRE_AUTH_LOCK:
        return _ensure_mercadolibre_auth_ready_unlocked(
            config,
            force_refresh=force_refresh,
        )


def _ensure_mercadolibre_auth_ready_unlocked(
    config: dict[str, Any],
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    store = config.setdefault("mercadolibre", {})
    caller_token = str(store.get("access_token") or "").strip()
    persisted = get_context().config.load_store_config()
    persisted_store = (
        persisted.get("mercadolibre")
        if isinstance(persisted.get("mercadolibre"), dict)
        else {}
    )
    if persisted_store:
        # 调用方可能持有排队前的旧快照。拿锁后始终以 SQLite 的最新凭据
        # 为准，防止旧 access token 覆盖已经轮换的 refresh token。
        store.clear()
        store.update(persisted_store)
    token = str(store.get("access_token") or "").strip()
    if not token:
        return {
            "ok": False,
            "error_code": "AUTH_NOT_CONFIGURED",
            "platform_error_code": "MERCADOLIBRE_AUTH_FAILED",
            "retryable": False,
            "message": (
                "Mercado Libre Access Token 为空，"
                "请先填写有效凭据或重新授权。"
            ),
            "next_action": "请先完成授权测试",
        }

    def http_status(error: Exception | str) -> int:
        if not isinstance(error, PublishAdapterError):
            return 0
        try:
            return int(error.details.get("http_status") or 0)
        except (TypeError, ValueError):
            return 0

    def refreshable_access_rejection(error: Exception | str) -> bool:
        if isinstance(error, PublishAdapterError):
            return (
                error.code == "MERCADOLIBRE_AUTH_FAILED"
                and http_status(error) == 401
                and error.details.get("remote_write_dispatched") is not True
                and error.details.get("outcome_unknown") is not True
            )
        text = str(error).lower()
        return (
            "invalid access token" in text
            or "invalid_token" in text
            or bool(re.search(r"failed:\s*401(?:\s|$)", text))
        )

    def explicit_auth_failure(error: Exception | str) -> bool:
        if isinstance(error, PublishAdapterError):
            return (
                error.code == "MERCADOLIBRE_AUTH_FAILED"
                and http_status(error) in {0, 401, 403}
            )
        text = str(error).lower()
        return any(
            marker in text
            for marker in (
                "invalid access token",
                "invalid_token",
                "invalid_grant",
                "invalid grant",
                "refresh token invalid",
                "token expired",
            )
        ) or bool(re.search(r"failed:\s*(?:401|403)(?:\s|$)", text))

    def sync_identity(token_value: str) -> str:
        profile = sync_mercadolibre_identity(store, token_value)
        name = profile.get("nickname") or profile.get("user_id") or ""
        store.update(
            _store_auth_result_fields(
                "mercadolibre",
                "测试成功",
                name or token_value,
                next_action="授权身份有效；发布前仍需通过币种与销售目标预检",
            )
        )
        store["auth_error_code"] = ""
        store["auth_error_message"] = ""
        return str(name)

    def refresh_and_sync(failed_token: str) -> dict[str, Any]:
        nonlocal token
        # refresh 函数先原子持久化新 access_token 与单次 refresh_token，
        # 再同步身份。即使身份查询临时失败，也不会丢失已轮换的凭据。
        refresh_result = refresh_mercadolibre_token_from_body(
            {},
            failed_access_token=failed_token,
        )
        persisted = get_context().config.load_store_config()
        persisted_store = (
            persisted.get("mercadolibre")
            if isinstance(persisted.get("mercadolibre"), dict)
            else {}
        )
        store.clear()
        store.update(persisted_store)
        token = str(store.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Mercado Libre 刷新 token 后未返回 access_token。")
        if refresh_result.get("identity_ready") is False:
            error_code = str(
                refresh_result.get("identity_error_code")
                or "MERCADOLIBRE_AUTH_SYNC_FAILED"
            ).strip()
            message = str(
                refresh_result.get("identity_error_message")
                or refresh_result.get("next_action")
                or "Mercado Libre token 已刷新，但授权身份同步失败。"
            ).strip()
            retryable = any(
                marker in error_code.upper()
                for marker in (
                    "NETWORK",
                    "TIMEOUT",
                    "SERVER_ERROR",
                    "RATE_LIMITED",
                )
            )
            raise PublishAdapterError(
                error_code,
                message,
                retryable=retryable,
            )
        return {
            "ok": True,
            "token": token,
            "seller": store.get("shop_name") or store.get("user_id") or "",
            "refreshed": True,
        }

    def failure(
        error: Exception | str,
        *,
        failed_token: str = "",
    ) -> dict[str, Any]:
        message = str(error)
        code = store_auth_failure_code("mercadolibre", message)
        platform_error_code = (
            error.code
            if isinstance(error, PublishAdapterError)
            else "MERCADOLIBRE_AUTH_FAILED"
        )
        retryable = bool(
            error.retryable
            if isinstance(error, PublishAdapterError)
            else False
        )
        next_action = "请先完成授权测试或刷新 token"
        if failed_token or explicit_auth_failure(error):
            persisted = get_context().config.load_store_config()
            persisted_store = (
                persisted.get("mercadolibre")
                if isinstance(persisted.get("mercadolibre"), dict)
                else {}
            )
            if persisted_store:
                store.clear()
                store.update(persisted_store)
        persisted_token = str(store.get("access_token") or "").strip()
        should_mark_auth_failed = explicit_auth_failure(error) or bool(
            failed_token
            and persisted_token
            and persisted_token == str(failed_token).strip()
        )
        if should_mark_auth_failed:
            store.update(
                _store_auth_result_fields(
                    "mercadolibre",
                    "测试失败",
                    store.get("shop_name") or store.get("user_id") or "",
                    next_action=next_action,
                )
            )
            store["auth_error_code"] = code
            store["auth_error_message"] = message
            get_context().config.update_store_config_fields(
                "mercadolibre",
                store,
            )
        return {
            "ok": False,
            "error_code": (
                "AUTH_UNAVAILABLE"
                if retryable
                else "AUTH_TOKEN_EXPIRED"
                if "expired" in code.lower() or "expired" in message.lower()
                else "AUTH_INVALID"
            ),
            "platform_error_code": platform_error_code,
            "retryable": retryable,
            "message": message,
            "next_action": next_action,
        }

    if force_refresh:
        failed_token = caller_token or token
        try:
            return refresh_and_sync(failed_token)
        except Exception as exc:
            return failure(exc, failed_token=failed_token)

    try:
        name = sync_identity(token)
        get_context().config.update_store_config_fields(
            "mercadolibre",
            store,
        )
        return {
            "ok": True,
            "token": token,
            "seller": name or store.get("user_id") or "",
        }
    except Exception as exc:
        if (
            refreshable_access_rejection(exc)
            and str(store.get("refresh_token") or "").strip()
        ):
            failed_token = token
            try:
                return refresh_and_sync(failed_token)
            except Exception as refresh_exc:
                return failure(refresh_exc, failed_token=failed_token)
        return failure(exc)


def get_mercadolibre_access_token(
    config: dict[str, Any] | None = None,
    *,
    force_refresh: bool = False,
) -> str:
    """返回当前可用 token；无效时统一刷新，失败则抛出类型化认证错误。"""

    target_config = (
        config
        if config is not None
        else get_context().config.load_store_config()
    )
    auth = ensure_mercadolibre_auth_ready(
        target_config,
        force_refresh=force_refresh,
    )
    token = str(auth.get("token") or "").strip()
    if auth.get("ok") and token:
        return token
    message = str(auth.get("message") or "Mercado Libre 授权不可用。")
    raise PublishAdapterError(
        str(auth.get("platform_error_code") or "MERCADOLIBRE_AUTH_FAILED"),
        message,
        retryable=bool(auth.get("retryable")),
        details={
            "auth_error_code": str(auth.get("error_code") or "AUTH_INVALID"),
            "next_action": str(auth.get("next_action") or "请重新授权"),
            "field_errors": {"auth": [message]},
        },
    )


def _test_mercadolibre_auth(config: dict[str, Any], scope: str) -> dict[str, Any]:
    del scope
    store = config.setdefault("mercadolibre", {})
    token = str(store.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("请先填写 Mercado Libre access token，或通过授权链接换取 token。")
    # 统一服务：users/me 身份同步 + 远端站点币种发现。
    profile = sync_mercadolibre_identity(store, token)
    display = profile.get("nickname") or profile.get("user_id") or token
    store.update(_store_auth_result_fields("mercadolibre", "测试成功", display))
    store["auth_error_code"] = ""
    store["auth_error_message"] = ""
    return {"currency_discovery": discover_mercadolibre_listing_currency(store)}


def _test_ozon_auth(config: dict[str, Any], scope: str) -> dict[str, Any]:
    ozon = config.get("ozon", {})
    client_id = str(ozon.get("client_id") or "").strip()
    api_key = str(ozon.get("api_key") or "").strip()
    if not client_id or not api_key:
        raise RuntimeError("请先填写 Ozon Client ID 和 API Key。")
    # seller/info 既校验凭据，也是店铺发布币种的远端来源；币种解析交给
    # 共享发现状态机，tester 不再自行持久化币种字段。
    seller_info = publisher.fetch_ozon_seller_info(client_id, api_key)
    company = (
        seller_info.get("company")
        if isinstance(seller_info.get("company"), dict)
        else {}
    )
    currency = str(company.get("currency") or "").strip().upper()
    currency_discovery: dict[str, Any] = (
        {"supported": True, "currencies": [currency], "source": "account_api"}
        if currency
        else {
            "supported": True,
            "error_code": "OZON_CURRENCY_MISSING",
            "error_message": "Ozon seller/info 未返回店铺发布币种",
        }
    )
    category_summary: dict[str, Any] | None = None
    if scope == "category":
        from .ozon_category_api import fetch_ozon_category_tree_summary

        # 授权测试必须命中远端，不能让有效缓存掩盖已失效的凭据。
        category_summary = fetch_ozon_category_tree_summary(
            force_refresh=True,
            credentials=(client_id, api_key),
        )
        name = str(ozon.get("shop_name") or client_id)
    else:
        name = publisher.fetch_ozon_shop_name(client_id, api_key)
    store = config.setdefault("ozon", {})
    store["shop_name"] = name or store.get("shop_name", "")
    store.update(_store_auth_result_fields("ozon", "测试成功", name or client_id))
    store["auth_error_code"] = ""
    store["auth_error_message"] = ""
    result: dict[str, Any] = {"currency_discovery": currency_discovery}
    if category_summary:
        result["category_tree"] = category_summary
    return result


_YANDEX_WAREHOUSE_PLACEMENT_MODELS = frozenset({"FBS", "DBS", "EXPRESS"})


def _yandex_partner_warehouse_usable(
    warehouse: dict[str, Any],
    placement_type: str,
) -> bool:
    """无分组仓库是否可用作库存写入目标。

    官方 v3 仓库项的 ``models[]`` 元素为 ``{placementType: FBS|DBS|EXPRESS,
    apiAvailability: AVAILABLE|DISABLED_*|MANUALLY_DISABLED}``。只有
    ``models`` 非空、存在 API 状态为 ``AVAILABLE``、且（店铺投放模型已知时）
    与店铺投放模型一致的仓库才可发布库存；``models: []`` 或非 AVAILABLE
    的仓库一律排除。
    """

    models = warehouse.get("models")
    if not isinstance(models, list) or not models:
        return False
    expected = str(placement_type or "").strip().upper()
    if expected not in _YANDEX_WAREHOUSE_PLACEMENT_MODELS:
        expected = ""
    for model in models:
        if not isinstance(model, dict):
            continue
        if str(model.get("apiAvailability") or "").strip().upper() != "AVAILABLE":
            continue
        model_placement = str(model.get("placementType") or "").strip().upper()
        if expected and model_placement != expected:
            continue
        return True
    return False


def _test_yandex_auth(config: dict[str, Any], scope: str) -> dict[str, Any]:
    """在线校验 Yandex API-Key、scope、店铺可用状态并派生店铺能力。

    只更新传入的内存配置对象；持久化统一由 ``test_store_auth()`` 经
    ``ConfigStore.save_store_config()`` 完成（preview 使用副本不落盘）。
    """

    from erp_web.marketplaces.yandex_currency import yandex_internal_currency
    from erp_web.marketplaces.yandex_http import (
        YandexApiError,
        fetch_yandex_business_settings,
        fetch_yandex_campaign,
        fetch_yandex_partner_warehouses,
        fetch_yandex_token_info,
        fetch_yandex_warehouses,
        yandex_missing_publish_scopes,
    )
    from erp_web.schemas.yandex import YandexCampaignInfo, YandexTokenInfo

    yandex = config.get("yandex") if isinstance(config.get("yandex"), dict) else {}
    api_token = str(yandex.get("api_token") or "").strip()
    campaign_id = str(yandex.get("campaign_id") or "").strip()
    if not api_token or not campaign_id:
        raise RuntimeError("请先填写 Yandex API-Key Token 和 Campaign ID。")

    token_info = YandexTokenInfo(**fetch_yandex_token_info(api_token))
    missing_scopes = yandex_missing_publish_scopes(token_info.auth_scopes)
    if missing_scopes:
        raise RuntimeError(
            "测试失败：Yandex API-Key 权限不足，缺少 "
            + "、".join(missing_scopes)
            + "。请到卖家后台为 token 增加商品管理、价格管理以及"
            "库存与订单处理（INVENTORY_AND_ORDER_PROCESSING）权限。"
        )

    campaign_raw = fetch_yandex_campaign(api_token, campaign_id)
    campaign = YandexCampaignInfo(
        campaign_id=campaign_id,
        business_id=str(
            (campaign_raw.get("business") or {}).get("id")
            if isinstance(campaign_raw.get("business"), dict)
            else ""
        ).strip(),
        business_name=str(
            (campaign_raw.get("business") or {}).get("name")
            if isinstance(campaign_raw.get("business"), dict)
            else ""
        ).strip(),
        shop_name=str(campaign_raw.get("domain") or "").strip(),
        placement_type=str(campaign_raw.get("placementType") or "").strip(),
        api_availability=str(campaign_raw.get("apiAvailability") or "").strip(),
    )
    if not campaign.business_id:
        raise RuntimeError("Yandex campaign 响应缺少 business.id，无法派生店铺能力。")
    if not campaign.api_available:
        raise RuntimeError(
            f"测试失败：Yandex 店铺 API 状态为 {campaign.api_availability or '未知'}，"
            "店铺不可用。请到卖家后台恢复店铺或联系 Yandex 支持后重试。"
        )

    settings: dict[str, Any] = {}
    settings_error_code = ""
    settings_error_message = ""
    try:
        settings = fetch_yandex_business_settings(api_token, campaign.business_id)
    except YandexApiError as exc:
        settings_error_code = str(getattr(exc, "code", "") or "YANDEX_SETTINGS_FAILED")
        settings_error_message = str(exc)
    except Exception as exc:
        settings_error_code = "YANDEX_SETTINGS_FAILED"
        settings_error_message = str(exc)
    only_default_price = bool(settings.get("onlyDefaultPrice"))
    raw_currency = str(settings.get("currency") or "").strip()
    if settings_error_code:
        currency_discovery: dict[str, Any] = {
            "supported": True,
            "error_code": settings_error_code,
            "error_message": settings_error_message,
        }
    elif raw_currency:
        # wire RUR → 内部 ISO RUB；其他 ISO 代码保持大写。
        currency_discovery = {
            "supported": True,
            "currencies": [yandex_internal_currency(raw_currency)],
            "source": "business_settings",
        }
    else:
        currency_discovery = {
            "supported": True,
            "error_code": "YANDEX_CURRENCY_MISSING",
            "error_message": "Yandex Business settings 未返回 settings.currency",
        }
    stock_update_mode = "none"
    warehouse_ids: list[int] = []
    placement_type = campaign.placement_type.strip().upper()
    if placement_type not in {"FBY"}:
        # 仓库组判定依据官方 v2 仓库响应中的 groupInfo；无仓库组时改用
        # v3 Business 仓库（其 id 即库存写入所需的 partnerWarehouseId）。
        grouped_warehouses = fetch_yandex_warehouses(
            api_token,
            campaign.business_id,
            campaign_ids=[campaign_id] if campaign_id.isdigit() else None,
        )
        v2_ids = [
            int(str(item.get("id")).strip())
            for item in grouped_warehouses
            if str(item.get("id") or "").strip().isdigit()
        ]
        group_ids = [
            int(str(item.get("id")).strip())
            for item in grouped_warehouses
            if isinstance(item.get("groupInfo"), dict)
            and str(item.get("id") or "").strip().isdigit()
        ]
        if group_ids:
            stock_update_mode = "campaign_warehouses"
            warehouse_ids = v2_ids
        else:
            try:
                partner_warehouses = fetch_yandex_partner_warehouses(
                    api_token, campaign.business_id
                )
            except YandexApiError as exc:
                if exc.retryable:
                    raise RuntimeError(
                        f"测试失败：Yandex 仓库探测遇到临时错误（{exc.code}），请稍后重试。"
                    ) from exc
                partner_warehouses = []
            partner_ids = [
                int(str(item.get("id")).strip())
                for item in partner_warehouses
                if str(item.get("id") or "").strip().isdigit()
            ]
            usable = [
                item
                for item in partner_warehouses
                if str(item.get("id") or "").strip().isdigit()
                and _yandex_partner_warehouse_usable(item, placement_type)
            ]
            if usable:
                # 草稿只保存单一库存数，而 Business 库存按仓库写入：必须
                # 选定唯一发布仓库（确定性取最小 id），不能把同一数量复制
                # 到所有仓库造成可售库存成倍放大。
                selected = min(
                    usable,
                    key=lambda item: int(str(item.get("id")).strip()),
                )
                stock_update_mode = "business"
                warehouse_ids = [int(str(selected.get("id")).strip())]
            elif partner_ids:
                raise RuntimeError(
                    "测试失败：探测到 Yandex 仓库，但没有与店铺投放模型"
                    f"（{placement_type or '未知'}）匹配且 API 状态为 AVAILABLE 的仓库。"
                    "请到卖家后台检查仓库投放模型与 API 可用性后重试。"
                )
            elif v2_ids:
                # v2 有仓库但无 groupInfo：Campaign 库存接口不依赖仓库 ID。
                stock_update_mode = "campaign_warehouses"
                warehouse_ids = v2_ids
            else:
                raise RuntimeError(
                    "测试失败：未探测到可用的 Yandex 仓库。"
                    "请到卖家后台确认店铺仓库配置后重试。"
                )

    verified_at = collect_time_iso()
    store = config.setdefault("yandex", {})
    store["campaign_id"] = campaign_id
    store["business_id"] = campaign.business_id
    store["business_name"] = campaign.business_name
    store["placement_type"] = campaign.placement_type
    store["api_availability"] = campaign.api_availability
    store["api_key_name"] = token_info.name
    store["auth_scopes"] = list(token_info.auth_scopes)
    store["only_default_price"] = only_default_price
    store["stock_update_mode"] = stock_update_mode
    store["warehouse_ids"] = warehouse_ids
    store["capabilities_verified_at"] = verified_at
    store["shop_name"] = campaign.shop_name or campaign.business_name or store.get("shop_name", "")
    store.update(
        _store_auth_result_fields(
            "yandex",
            "测试成功",
            campaign.shop_name or campaign.business_name or campaign_id,
        )
    )
    store["auth_error_code"] = ""
    store["auth_error_message"] = ""

    result: dict[str, Any] = {
        "campaign_id": campaign.campaign_id,
        "business_id": campaign.business_id,
        "business_name": campaign.business_name,
        "placement_type": campaign.placement_type,
        "api_availability": campaign.api_availability,
        "api_key_name": token_info.name,
        "auth_scopes": list(token_info.auth_scopes),
        "only_default_price": only_default_price,
        "stock_update_mode": stock_update_mode,
        "warehouse_ids": warehouse_ids,
        "capabilities_verified_at": verified_at,
        "currency_discovery": currency_discovery,
    }
    if scope == "category":
        from .yandex_category_api import fetch_yandex_category_tree_summary

        # 授权测试必须命中远端，不能让有效缓存掩盖已失效的凭据。
        result["category_tree"] = fetch_yandex_category_tree_summary(
            force_refresh=True,
            credentials=(api_token,),
        )
    return result


StoreAuthTester = Callable[[dict[str, Any], str], dict[str, Any]]


def resolve_store_auth_tester(spec: MarketplaceSpec) -> StoreAuthTester | None:
    """从平台注册项解析在线凭据校验器。

    ``MarketplaceSpec.test_auth`` 保存 ``module:attribute``，因此新增平台只需在
    registry 声明适配器入口；通用授权流程不再维护第二张平台分发表。
    """

    target = str(spec.test_auth or "").strip()
    if not target:
        return None
    module_name, separator, attribute_name = target.partition(":")
    if not separator or not module_name or not attribute_name:
        raise RuntimeError(f"{spec.label} 的 test_auth 注册项无效：{target}")
    tester = getattr(import_module(module_name), attribute_name, None)
    if not callable(tester):
        raise RuntimeError(f"{spec.label} 的授权校验器不可调用：{target}")
    return tester


def _auth_test_result_failed(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "").strip().lower()
    return result.get("ok") is False or status in {
        "error",
        "failed",
        "failure",
        "测试失败",
    }


def _persist_store_auth_test_failure(
    config: dict[str, Any],
    platform: str,
    *,
    error_message: str,
    error_code: str = "",
    next_action: str = "",
) -> str:
    """覆盖陈旧成功态并持久化在线授权校验失败。"""

    message = str(error_message or "").strip() or f"{platform_label(platform)}在线授权校验失败。"
    code = str(error_code or "").strip() or store_auth_failure_code(platform, message)
    store = config.setdefault(platform, {})
    if not isinstance(store, dict):
        store = {}
        config[platform] = store
    account = str(
        store.get("auth_masked_account")
        or store.get("shop_name")
        or ""
    ).strip()
    store.update(
        _store_auth_result_fields(
            platform,
            "测试失败",
            account,
            error_code=code,
            error_message=message,
            next_action=str(next_action or "").strip(),
        )
    )
    # 授权失败时币种状态重置为 unresolved，不得保留旧可信币种。
    reset_currency_state_in_store(platform, store)
    get_context().config.save_store_config(config)
    return message


def _auth_test_failure_exception(message: str) -> RuntimeError:
    text = str(message or "").strip()
    return RuntimeError(text if text.startswith("测试失败") else f"测试失败：{text}")


def _store_credentials_unchanged(
    saved_config: dict[str, Any],
    merged_config: dict[str, Any],
    platform: str,
) -> bool:
    """客户端随测试提交的凭据与已保存凭据完全一致时返回 True。

    preview 预览测试只应针对“确实改动了的未保存凭据”；当提交值与已保存值
    相同（含脱敏回显 / 空值按原值保留）时，本次等价于对持久化配置的测试，
    允许写入可信授权与币种状态。否则已保存凭据永远无法通过界面落库币种。
    """

    spec = marketplace_spec(platform)
    if spec is None:
        return False
    saved_section = saved_config.get(platform)
    saved_section = saved_section if isinstance(saved_section, dict) else {}
    merged_section = merged_config.get(platform)
    merged_section = merged_section if isinstance(merged_section, dict) else {}
    for key in spec.credential_keys():
        if str(saved_section.get(key) or "") != str(merged_section.get(key) or ""):
            return False
    return True


def test_store_auth(
    platform: str,
    scope: str = "",
    *,
    config_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 任何平台的授权测试都会读取并回写完整 store
    # config。全流程与 Mercado Libre token 轮换串行，避免
    # Ozon/Yandex 测试在远端请求后用旧快照覆盖新 token。
    with MERCADOLIBRE_AUTH_LOCK:
        return _test_store_auth_unlocked(
            platform,
            scope,
            config_override=config_override,
        )


def _test_store_auth_unlocked(
    platform: str,
    scope: str = "",
    *,
    config_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    platform = (platform or "").strip().lower()
    scope = (scope or "").strip().lower()
    spec = marketplace_spec(platform)
    if spec is None:
        raise RuntimeError("不支持的平台。")
    tester = resolve_store_auth_tester(spec)
    if tester is None:
        raise RuntimeError(f"{platform_label(platform)}授权已支持保存；在线校验尚未接入。")
    saved_config = get_context().config.load_store_config()
    is_preview = isinstance(config_override, dict)
    config = (
        get_context().config.merge_store_config_fields(
            saved_config,
            config_override,
            preserve_empty_sensitive=True,
        )
        if is_preview
        else saved_config
    )
    if is_preview and _store_credentials_unchanged(saved_config, config, platform):
        # 提交的凭据与已保存配置一致：等价于测试持久化配置，落库可信状态。
        is_preview = False
    try:
        extra = tester(config, scope)
    except Exception as exc:
        if is_preview:
            raise _auth_test_failure_exception(str(exc)) from exc
        # 类型化错误（如 YandexApiError）携带平台侧错误码与下一步动作；
        # 持久化时保留它们，避免全部退化为通用失败码。
        details = getattr(exc, "details", None)
        message = _persist_store_auth_test_failure(
            config,
            platform,
            error_message=str(exc),
            error_code=str(getattr(exc, "code", "") or "").strip(),
            next_action=(
                str(details.get("next_action") or "").strip()
                if isinstance(details, dict)
                else ""
            ),
        )
        raise _auth_test_failure_exception(message) from exc
    if not isinstance(extra, dict):
        if is_preview:
            raise _auth_test_failure_exception("授权校验器返回格式无效。")
        message = _persist_store_auth_test_failure(
            config,
            platform,
            error_message="授权校验器返回格式无效。",
            error_code="invalid_auth_test_result",
        )
        raise _auth_test_failure_exception(message)
    if _auth_test_result_failed(extra):
        error_message = str(
            extra.get("error_message")
            or extra.get("error")
            or extra.get("message")
            or ""
        )
        if is_preview:
            raise _auth_test_failure_exception(error_message)
        message = _persist_store_auth_test_failure(
            config,
            platform,
            error_message=error_message,
            error_code=str(extra.get("error_code") or ""),
            next_action=str(extra.get("next_action") or ""),
        )
        raise _auth_test_failure_exception(message)
    store = config.get(platform)
    store = store if isinstance(store, dict) else {}
    discovery = extra.pop("currency_discovery", None)
    if isinstance(discovery, dict):
        currency_state = _apply_store_currency_discovery(platform, store, discovery)
    else:
        identity = store_identity_for_platform(platform, store)
        currency_state = store_listing_currency_from_auth(platform, identity, store)
    publish_ready = store_listing_currency_ready(currency_state)
    store["auth_next_action"] = _store_publish_readiness_next_action(
        platform, currency_state
    )
    if not is_preview:
        get_context().config.save_store_config(config)
    response = {
        "ok": True,
        "platform": platform,
        "scope": scope,
        "shop_name": str(store.get("shop_name") or "已授权店铺"),
        "masked_account": str(store.get("auth_masked_account") or ""),
        "checked_at": str(store.get("auth_checked_at") or ""),
        "status": str(store.get("auth_status") or "ok"),
        # ok 表示授权请求本身成功；publish_ready 表示发布币种也已就绪，
        # 两者不得混为一个状态。
        "publish_ready": publish_ready,
        "message": "测试成功：授权可用。",
        "currency_configuration": public_currency_configuration(currency_state),
        "storeConfig": public_store_config(config),
        "storeAuthSummary": summarize_store_auth_states(config),
        **extra,
    }
    if is_preview:
        response["preview"] = True
    category_tree = extra.get("category_tree")
    if isinstance(category_tree, dict):
        response["message"] = f"类目读取测试成功：已读取 {category_tree.get('product_type_count', 0)} 个可发布商品类型。"
    return response


__all__ = [
    "build_mercadolibre_auth_link",
    "ensure_mercadolibre_auth_ready",
    "exchange_mercadolibre_code_from_body",
    "get_mercadolibre_access_token",
    "preview_mercadolibre_auth_link",
    "refresh_mercadolibre_token_from_body",
    "resolve_store_auth_tester",
    "StoreAuthTester",
    "test_ai_model_config",
    "test_api_config",
    "test_store_auth",
]

# -*- coding: utf-8 -*-
from __future__ import annotations

import calendar
import json
import threading
import time
import urllib.request
from copy import deepcopy
from typing import Any

from erp_web import app_config as app_config_runtime
from erp_web.context import get_context
from erp_web.db import ErpDatabase
from erp_web.services.mercadolibre_target_contract import (
    MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED,
    MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED,
    mercadolibre_global_target_contract,
    mercadolibre_sales_target_selectors,
)
from erp_web.services import pricing_service
from erp_web.services.listing_currency_service import (
    StoreCurrencyNotReadyError,
    require_store_listing_currency,
)
from erp_web.product_model import normalize_mercadolibre_sites_to_sell


def _pricing_exchange_rate_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    source_config = (
        config
        if isinstance(config, dict)
        else get_context().config.load_app_config()
    )
    pricing = source_config.get("pricing_defaults") if isinstance(source_config.get("pricing_defaults"), dict) else source_config
    cfg = pricing if isinstance(pricing, dict) else {}
    default_cfg = app_config_runtime.default_app_config()["pricing_defaults"]
    return {
        "api_url": str(cfg.get("exchange_rate_api_url") or default_cfg["exchange_rate_api_url"]).strip(),
        "timeout_seconds": max(1, min(30, int(pricing_service.number_value(cfg.get("exchange_rate_timeout_seconds"), 5) or 5))),
        "cache_ttl_seconds": max(0, int(pricing_service.number_value(cfg.get("exchange_rate_cache_ttl_seconds"), 3600) or 3600)),
    }


def _extract_usd_rates(payload: Any) -> dict[str, float]:
    rates: dict[str, float] = {}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or item.get("currency") or "").upper()
            rate = pricing_service.number_value(item.get("rate"))
            if quote and rate > 0:
                rates[quote] = rate
    elif isinstance(payload, dict):
        raw_rates = payload.get("rates")
        if isinstance(raw_rates, dict):
            rates.update({str(key).upper(): pricing_service.number_value(value) for key, value in raw_rates.items() if pricing_service.number_value(value) > 0})
        elif isinstance(payload.get("conversion_rates"), dict):
            raw_conversion_rates = payload.get("conversion_rates")
            rates.update({str(key).upper(): pricing_service.number_value(value) for key, value in raw_conversion_rates.items() if pricing_service.number_value(value) > 0})
        elif isinstance(payload.get("data"), list):
            rates.update(_extract_usd_rates(payload.get("data")))
        quote = str(payload.get("quote") or "").upper()
        rate = pricing_service.number_value(payload.get("rate"))
        if quote and rate > 0:
            rates[quote] = rate
    return rates


def _rates_section(rates: dict[str, float]) -> dict[str, Any] | None:
    """Build the result ``rates`` block from USD-quoted rates; None when unusable."""
    usd_cny = rates.get("CNY")
    mxn_usd = rates.get("MXN")
    rub_usd = rates.get("RUB")
    if not usd_cny or not mxn_usd:
        return None
    rub_cny = (float(rub_usd) / float(usd_cny)) if rub_usd and usd_cny else 0.0
    return {
        "usd_cny_rate": round(float(usd_cny), 6),
        "mxn_usd_rate": round(float(mxn_usd), 6),
        "rub_usd_rate": round(float(rub_usd or 0), 6),
        "rub_cny_rate": round(float(rub_cny), 6),
        "currency_usd_rates": {currency: round(float(rate), 6) for currency, rate in rates.items() if rate > 0},
    }


class ExchangeRateService:
    """Exchange rates with SQLite persistence (``exchange_rates`` table).

    Lookup order: in-memory hot cache → table snapshot within TTL → external
    API. Successful fetches are written through to the table (pair per
    currency, USD base), so每次核价用的汇率都可复盘; when the external API
    fails, the most recent table snapshot is returned flagged ``stale``.
    """

    def __init__(self, db: ErpDatabase) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._cached_result: dict[str, Any] | None = None
        self._cached_at_ts: float = 0.0

    @staticmethod
    def _pair(quote: str) -> str:
        return f"USD/{str(quote or '').upper()}"

    @staticmethod
    def _quote(pair: str) -> str:
        text = str(pair or "")
        return text.split("/", 1)[1] if "/" in text else text

    @staticmethod
    def _parse_fetched_at(value: str) -> float:
        try:
            return float(calendar.timegm(time.strptime(str(value or ""), "%Y-%m-%dT%H:%M:%SZ")))
        except (ValueError, OverflowError):
            return 0.0

    def _load_stored(self) -> tuple[dict[str, float], str]:
        stored = self._db.load_exchange_rates()
        rates = {self._quote(pair): rate for pair, rate in stored["rates"].items() if rate > 0}
        return rates, stored["fetched_at"]

    def _stored_result(self, *, stale: bool, error: str = "") -> dict[str, Any] | None:
        rates, fetched_at = self._load_stored()
        section = _rates_section(rates)
        if section is None:
            return None
        result: dict[str, Any] = {
            "ok": True,
            "source": "exchange_rates_table",
            "fetched_at": fetched_at,
            "cached": True,
            "rates": section,
        }
        if stale:
            result["stale"] = True
        if error:
            result["error"] = error
        return result

    def get_rates(self, cfg: dict[str, Any], force_refresh: bool = False) -> dict[str, Any]:
        api_url = str(cfg.get("api_url") or "")
        ttl_seconds = float(cfg.get("cache_ttl_seconds") or 0)
        now = time.time()
        if not force_refresh and ttl_seconds > 0:
            with self._lock:
                if self._cached_result is not None and now - self._cached_at_ts < ttl_seconds:
                    return {**deepcopy(self._cached_result), "cached": True}
            # 启动 / 缓存 miss：先查表，fetched_at 在 TTL 内直接用。
            rates, fetched_at = self._load_stored()
            fetched_ts = self._parse_fetched_at(fetched_at)
            if rates and fetched_ts > 0 and now - fetched_ts < ttl_seconds:
                stored_result = self._stored_result(stale=False)
                if stored_result is not None:
                    with self._lock:
                        self._cached_result = deepcopy(stored_result)
                        self._cached_at_ts = fetched_ts
                    return stored_result
        try:
            request = urllib.request.Request(api_url, headers={"Accept": "application/json", "User-Agent": "ChampionERP/1.0"})
            with urllib.request.urlopen(request, timeout=cfg["timeout_seconds"]) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            fallback = self._stored_result(stale=True, error=f"实时汇率获取失败：{exc}")
            if fallback is not None:
                return fallback
            return {"ok": False, "error": f"实时汇率获取失败：{exc}", "source": api_url}
        rates = _extract_usd_rates(payload)
        section = _rates_section(rates)
        if section is None:
            fallback = self._stored_result(stale=True, error="实时汇率响应缺少 CNY 或 MXN 汇率。")
            if fallback is not None:
                return fallback
            return {"ok": False, "error": "实时汇率响应缺少 CNY 或 MXN 汇率。", "source": api_url, "raw": payload}
        fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        result = {
            "ok": True,
            "source": api_url,
            "fetched_at": fetched_at,
            "cached": False,
            "rates": section,
            "raw": payload,
        }
        try:
            self._db.save_exchange_rates(
                {self._pair(currency): float(rate) for currency, rate in rates.items() if rate > 0},
                fetched_at,
            )
        except Exception:
            pass  # 表写失败不阻塞核价；内存缓存仍然生效。
        with self._lock:
            self._cached_result = deepcopy(result)
            self._cached_at_ts = now
        return result


def fetch_pricing_exchange_rates(force_refresh: bool = False, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _pricing_exchange_rate_config(config)
    if not cfg["api_url"]:
        return {"ok": False, "error": "汇率 API URL 未配置，请在系统设置里填写。", "source": "config"}
    return get_context().exchange_rates.get_rates(cfg, bool(force_refresh))


def calculate_price(input_data: dict[str, Any]) -> dict[str, Any]:
    source = dict(input_data) if isinstance(input_data, dict) else {}
    raw_targets = source.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        return {"ok": False, "error": "核价必须指定至少一个发布目标。"}

    store_config = get_context().config.load_store_config()
    normalized_targets: list[dict[str, Any]] = []
    for raw_target in raw_targets:
        target = dict(raw_target) if isinstance(raw_target, dict) else {}
        platform = str(target.get("platform") or "").strip().lower()
        platform_store = (
            store_config.get(platform)
            if isinstance(store_config.get(platform), dict)
            else {}
        )
        # 核价只读 ready 的店铺授权币种配置；不允许核价层产生远端副作用，
        # 也不允许注册表/国家/草稿历史值 fallback。
        try:
            state = require_store_listing_currency(platform, platform_store)
        except StoreCurrencyNotReadyError as exc:
            return {
                "ok": False,
                "error": exc.message,
                "error_code": exc.code,
                "platform": platform,
            }
        target["listing_currency"] = state["listing_currency"]
        target["currency_fingerprint"] = state["currency_fingerprint"]
        if (
            platform == "mercadolibre"
            and str(target.get("site") or target.get("site_id") or "")
            .strip()
            .upper()
            == "CBT"
        ):
            sites_to_sell = normalize_mercadolibre_sites_to_sell(
                target.get("sites_to_sell")
                if isinstance(target.get("sites_to_sell"), list)
                else target.get("sitesToSell")
            )
            target["sites_to_sell"] = sites_to_sell
            _, target_issues = mercadolibre_global_target_contract(
                sites_to_sell,
                platform_store.get("marketplace_bindings"),
                require_user_products=(
                    str(platform_store.get("listing_model") or "").strip()
                    != "traditional_global_items"
                ),
                enforce_binding_pricing_model=(
                    str(platform_store.get("listing_model") or "").strip()
                    != "traditional_global_items"
                ),
                language=str(target.get("language") or "").strip(),
            )
            if target_issues:
                issue = target_issues[0]
                if issue["code"] == MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED:
                    next_action = "前往授权页重新验证账号并读取已开通市场"
                elif issue["code"] == MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED:
                    next_action = (
                        "当前 CBT 卖家属于 Fully Managed，需接入 "
                        "global_net_proceeds 价格流程后才能核价"
                    )
                else:
                    next_action = "先在 CBT 草稿中选择已开通的销售国家与物流方式"
                return {
                    "ok": False,
                    "error": issue["message"],
                    "error_code": issue["code"],
                    "field": issue["field"],
                    "sales_target_options": (
                        mercadolibre_sales_target_selectors(
                            platform_store.get("marketplace_bindings"),
                            require_user_products=(
                                str(platform_store.get("listing_model") or "").strip()
                                != "traditional_global_items"
                            ),
                            language=str(target.get("language") or "").strip(),
                        )
                    ),
                    "next_action": next_action,
                    "errors": [
                        {
                            **issue,
                            "next_action": next_action,
                        }
                    ],
                    "results": [],
                    "platform": platform,
                    "site": "CBT",
                }
        normalized_targets.append(target)
    source["targets"] = normalized_targets

    has_manual_rates = source.get("usd_cny_rate") not in (None, "") and source.get("mxn_usd_rate") not in (None, "")
    exchange_mode = str(source.get("exchange_rate_mode") or ("manual" if has_manual_rates else "live")).strip().lower()
    exchange_rates: dict[str, Any] | None = None
    if exchange_mode != "manual":
        exchange_rates = fetch_pricing_exchange_rates(bool(source.get("force_exchange_rate_refresh")))
        if not exchange_rates.get("ok"):
            return {"ok": False, "error": exchange_rates.get("error") or "实时汇率获取失败", "exchange_rates": exchange_rates}
        rates = exchange_rates.get("rates") if isinstance(exchange_rates.get("rates"), dict) else {}
        source["usd_cny_rate"] = rates.get("usd_cny_rate")
        source["mxn_usd_rate"] = rates.get("mxn_usd_rate")
        source["rub_usd_rate"] = rates.get("rub_usd_rate")
        source["rub_cny_rate"] = rates.get("rub_cny_rate")
        source["currency_usd_rates"] = rates.get("currency_usd_rates")
        common = source.get("common") if isinstance(source.get("common"), dict) else None
        if common is not None:
            common["usd_cny_rate"] = source["usd_cny_rate"]
            common["mxn_usd_rate"] = source["mxn_usd_rate"]
            common["rub_usd_rate"] = source["rub_usd_rate"]
            common["rub_cny_rate"] = source["rub_cny_rate"]
            common["currency_usd_rates"] = source["currency_usd_rates"]
    result = pricing_service.pricing_result(source)
    if exchange_rates:
        result["exchange_rates"] = exchange_rates
        result["exchange_rate_mode"] = "live"
    else:
        result["exchange_rates"] = {
            "ok": True,
            "source": "manual",
            "rates": {
                "usd_cny_rate": source.get("usd_cny_rate"),
                "mxn_usd_rate": source.get("mxn_usd_rate"),
                "rub_usd_rate": source.get("rub_usd_rate"),
                "rub_cny_rate": source.get("rub_cny_rate"),
                "currency_usd_rates": source.get("currency_usd_rates") or {},
            },
        }
        result["exchange_rate_mode"] = "manual"
    return result


__all__ = [
    "ExchangeRateService",
    "calculate_price",
    "fetch_pricing_exchange_rates",
]

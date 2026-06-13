# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from erp_web import app_config as app_config_runtime
from services import pricing_service

from .product_store import load_app_config
from .runtime_common import EXCHANGE_RATE_CACHE

def _pricing_exchange_rate_config() -> dict[str, Any]:
    pricing = load_app_config().get("pricing_defaults")
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


def fetch_pricing_exchange_rates(force_refresh: bool = False) -> dict[str, Any]:
    cfg = _pricing_exchange_rate_config()
    api_url = cfg["api_url"]
    if not api_url:
        return {"ok": False, "error": "汇率 API URL 未配置，请在系统设置里填写。", "source": "config"}
    now = time.time()
    cache_key = api_url
    cached = EXCHANGE_RATE_CACHE.get(cache_key)
    if not force_refresh and isinstance(cached, dict) and cfg["cache_ttl_seconds"] > 0:
        if now - float(cached.get("fetched_at_ts") or 0) < cfg["cache_ttl_seconds"]:
            return {**cached["result"], "cached": True}
    try:
        request = urllib.request.Request(api_url, headers={"Accept": "application/json", "User-Agent": "ChampionERP/1.0"})
        with urllib.request.urlopen(request, timeout=cfg["timeout_seconds"]) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"实时汇率获取失败：{exc}", "source": api_url}
    rates = _extract_usd_rates(payload)
    usd_cny = rates.get("CNY")
    mxn_usd = rates.get("MXN")
    rub_usd = rates.get("RUB")
    if not usd_cny or not mxn_usd:
        return {"ok": False, "error": "实时汇率响应缺少 CNY 或 MXN 汇率。", "source": api_url, "raw": payload}
    rub_cny = (float(rub_usd) / float(usd_cny)) if rub_usd and usd_cny else 0.0
    result = {
        "ok": True,
        "source": api_url,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "cached": False,
        "rates": {
            "usd_cny_rate": round(float(usd_cny), 6),
            "mxn_usd_rate": round(float(mxn_usd), 6),
            "rub_usd_rate": round(float(rub_usd or 0), 6),
            "rub_cny_rate": round(float(rub_cny), 6),
        },
        "raw": payload,
    }
    EXCHANGE_RATE_CACHE[cache_key] = {"fetched_at_ts": now, "result": result}
    return result


def calculate_price(input_data: dict[str, Any]) -> dict[str, Any]:
    source = dict(input_data) if isinstance(input_data, dict) else {}
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
        source["rub_cny_rate"] = rates.get("rub_cny_rate")
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
                "rub_cny_rate": source.get("rub_cny_rate"),
            },
        }
        result["exchange_rate_mode"] = "manual"
    result.setdefault("suggested_price", result.get("suggested_price_mxn", 0))
    result.setdefault("reverse_price", result.get("reverse_price_mxn", 0))
    result.setdefault("profit", result.get("profit_cny", 0))
    result.setdefault("profit_rate", result.get("profit_percent", 0))
    result.setdefault("foreign_price", result.get("sale_price_usd", 0))
    result.setdefault("expected_profit", result.get("profit_cny", 0))
    result.setdefault("net_profit", result.get("profit_cny", 0))
    return result


__all__ = [
    "calculate_price",
    "fetch_pricing_exchange_rates",
]

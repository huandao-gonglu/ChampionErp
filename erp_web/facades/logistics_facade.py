# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.runtime_units.product_store import load_app_config, mask_secret
from erp_web.runtime_units.yunexpress_client import (
    YunExpressClient,
    build_create_package_payload,
    build_create_package_preview,
    ensure_yunexpress_config_ready,
    normalize_yunexpress_config,
    validate_create_package_payload,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _config_from_body(body: dict[str, Any]) -> dict[str, Any]:
    app_config = load_app_config()
    stored = _as_dict(app_config.get("yunexpress"))
    override = _as_dict(body.get("config") or body.get("yunexpress"))
    return normalize_yunexpress_config({**stored, **override})


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(normalize_yunexpress_config(config))
    public["masked_app_id"] = mask_secret(public.get("app_id"))
    public["masked_app_secret"] = mask_secret(public.get("app_secret"))
    public["masked_source_key"] = mask_secret(public.get("source_key"))
    public["status"] = "已配置" if public.get("app_id") and public.get("app_secret") and public.get("source_key") else "未配置"
    return public


def test_yunexpress_config(config: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize_yunexpress_config(config)
    ensure_yunexpress_config_ready(cfg)
    token = YunExpressClient(cfg).request_access_token()
    return {
        "ok": True,
        "channel": "yunexpress",
        "message": f"云途 API 测试成功：{cfg['environment']} 环境已获取 Access Token。",
        "next_action": "可以保存配置；审核通过后可用沙盒订单测试创建发货单。",
        "environment": cfg["environment"],
        "base_url": cfg["base_url"],
        "masked_app_id": mask_secret(cfg.get("app_id")),
        "masked_source_key": mask_secret(cfg.get("source_key")),
        "expires_in": token.get("expires_in"),
    }


def preview_yunexpress_shipment(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    cfg = _config_from_body(body)
    shipment = _as_dict(body.get("shipment") or body.get("order") or body.get("payload"))
    preview = build_create_package_preview(cfg, shipment)
    status = 200 if not preview["errors"] else 400
    return {
        "ok": not bool(preview["errors"]),
        "channel": "yunexpress",
        "config": _public_config(cfg),
        "request": preview,
        "message": "云途发货请求预览已生成。" if not preview["errors"] else "云途发货请求缺少必要字段。",
        "next_action": "确认字段映射后调用创建发货单接口。" if not preview["errors"] else "请补齐收件人、包裹、申报信息和物流产品编码。",
    }, status


def create_yunexpress_shipment(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    cfg = _config_from_body(body)
    shipment = _as_dict(body.get("shipment") or body.get("order") or body.get("payload"))
    payload = build_create_package_payload(shipment, cfg)
    errors = validate_create_package_payload(payload)
    if errors:
        return {
            "ok": False,
            "channel": "yunexpress",
            "error": "；".join(errors),
            "errors": errors,
            "payload": payload,
            "next_action": "请补齐云途下单必填字段后再试。",
        }, 400
    try:
        result = YunExpressClient(cfg).create_package_order(payload)
    except Exception as exc:
        return {
            "ok": False,
            "channel": "yunexpress",
            "error": str(exc),
            "payload": payload,
            "next_action": "请检查云途凭证、沙盒权限、产品编码和收件地址后再试。",
        }, 400
    response = _as_dict(result.get("response"))
    success = response.get("success") is True or response.get("code") in ("", None, "0") or bool(response.get("result"))
    return {
        "ok": bool(success),
        "channel": "yunexpress",
        "message": "云途发货单已创建。" if success else str(response.get("msg") or response.get("message") or "云途返回创建失败。"),
        "payload": payload,
        "response": response,
        "token": result.get("token") if isinstance(result.get("token"), dict) else {},
        "next_action": "保存云途订单号、运单号和面单信息到本地订单。" if success else "请根据云途错误信息修正订单字段后重试。",
    }, 200 if success else 400


__all__ = [
    "create_yunexpress_shipment",
    "preview_yunexpress_shipment",
    "test_yunexpress_config",
]

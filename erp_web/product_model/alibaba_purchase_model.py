"""1688 采购身份的纯校验；只接受采集原文，不从规格文字推算 specId。"""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def alibaba_offer_id(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    match = re.fullmatch(r"/offer/([0-9]+)\.html", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "detail.1688.com" or parsed.username or parsed.password or not match:
        return ""
    return match[1]


def frozen_purchase_identity(product: dict, fact: dict, platform: str) -> dict:
    """上架时冻结来源身份及不可自动采购的原因，保留人工采购能力。"""
    source = product.get("source") or {}
    offer = alibaba_offer_id(str(source.get("source_url") or ""))
    result = {"source_offer_id": str(fact.get("source_offer_id") or ""),
              "source_spec_id": str(fact.get("source_spec_id") or ""), "purchase_block_reason": ""}
    sku = str(fact.get("source_sku_id") or "")
    rows = [r for r in source.get("skus", []) if isinstance(r, dict) and str(r.get("id") or "") == sku]
    snapshot = fact.get("source_snapshot") or {}
    drafts = (product.get("drafts") or {}).get(platform) or {}
    draft_rows = [r for r in drafts.get("sku_items", []) if r.get("sku_id") == fact.get("id")]
    overrides = (draft_rows[0].get("overrides") or {}) if len(draft_rows) == 1 else {}
    effective_options = {**(fact.get("options") or {}), **(overrides.get("options") or {})}
    if not offer:
        reason = "上架记录中的采购链接不是有效的 1688 商品地址"
    elif not sku.isascii() or not sku.isdigit():
        reason = "采集上架记录缺少有效的 1688 SKU 编号"
    elif not result["source_spec_id"]:
        reason = "采集上架记录缺少下单规格标识（specId），请重新采集并核对上架规格"
    elif result["source_offer_id"] != offer:
        reason = "采集商品编号与上架采购链接不一致，请核对来源商品"
    elif len(rows) != 1:
        reason = "采集记录中无法唯一匹配该 SKU，请重新采集并核对规格"
    elif (str(rows[0].get("spec_id") or "") != result["source_spec_id"]
          or str(rows[0].get("offer_id") or "") != offer
          or snapshot.get("source_spec_id") != result["source_spec_id"]
          or snapshot.get("source_offer_id") != offer):
        reason = "采集记录与上架记录的商品或规格标识不一致，请重新核对"
    elif (effective_options != (rows[0].get("options") or {})
          or (not effective_options and str(overrides.get("name", fact.get("name")) or "") != str(rows[0].get("name") or ""))):
        reason = "上架规格已修改，与采集规格不一致，请核对颜色、尺寸等选项"
    elif len(draft_rows) > 1:
        reason = "上架记录重复引用同一 SKU，无法唯一确认销售规格"
    elif fact.get("active") is False:
        reason = "上架记录中的来源 SKU 已停用"
    else:
        reason = ""
    result["purchase_block_reason"] = reason
    return result

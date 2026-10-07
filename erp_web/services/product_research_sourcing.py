"""候选商品找货与人工确认入库；货源结果保存在原调研记录内。"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from erp_web.context import get_context
from erp_web.schemas.product_research import (ProductResearchSupplier, ProductResearchSupplierSearchResponse, ProductResearchSupplierImportResponse)
from erp_web.product_model import default_product_model, merge_source_partial_result
from erp_web.product_model.image_pool_model import normalize_image_pool
from erp_web.product_research_config import normalize_product_research_config
from erp_web.services.sorftime_client import configured_client, number, rows


def _candidate(run_id: str, candidate_id: str):
    run = get_context().research.get(run_id)
    if not run or run.get("status") != "completed":
        raise ValueError("请先完成商品调研。")
    candidate = next((item for item in run.get("items", []) if item.get("id") == candidate_id), None)
    if not candidate or candidate.get("source_name") != "Sorftime":
        raise ValueError("请选择来自 Sorftime 的候选商品。")
    return candidate


def _client():
    return configured_client(normalize_product_research_config(get_context().config.load_app_config().get("product_research")))


def _supplier(row: dict[str, Any]) -> ProductResearchSupplier | None:
    product_id = str(row.get("ProductId") or "")
    if not re.fullmatch(r"\d{6,20}", product_id) or not row.get("Title"):
        return None
    # 1688 的 Price 是人民币元，不能沿用 Amazon 的分转换。
    return {"product_id": product_id, "title": str(row["Title"]),
            "source_url": f"https://detail.1688.com/offer/{product_id}.html",
            "image_url": str(row.get("Photo") or ""), "price_cny": number(row.get("Price")),
            "store_name": str(row.get("StoreName") or ""),
            "service_score": number(row.get("ServiceScore")),
            "monthly_sales": number(row.get("SalesOf30d")),
            "min_order_quantity": number(row.get("MinOrderQuantity")),
            "repurchase_rate": number(row.get("RepurchaseRate")),
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def search_suppliers(body: dict[str, Any]) -> ProductResearchSupplierSearchResponse:
    run_id, candidate_id = str(body.get("run_id") or ""), str(body.get("candidate_id") or "")
    mode = str(body.get("mode") or "keyword")
    keyword = str(body.get("keyword") or "").strip()
    if mode not in {"keyword", "image"} or (mode == "keyword" and not 1 <= len(keyword) <= 100):
        raise ValueError("请选择图片找货，或输入 1–100 字的中文货源关键词。")
    registry = get_context().research
    with registry.candidate_operation(run_id, candidate_id):
        candidate = _candidate(run_id, candidate_id)
        if mode == "image" and not str(candidate.get("image_url") or "").startswith("https://"):
            raise ValueError("此候选缺少可用的 HTTPS 图片，请使用关键词找货。")
        query = {"mode": mode, "keyword": keyword if mode == "keyword" else ""}
        cached = candidate.get("sourcing", {})
        if cached.get("query") == query and cached.get("status") == "completed":
            return {"ok": True, "sourcing": cached, "cached": True}
        client = _client()
        try:
            data = client.call("ProductSearchFromImage" if mode == "image" else "ProductSearchFromName", 601,
                               {"ImageUrl": candidate["image_url"], "Page": 1} if mode == "image" else {"Name": keyword, "Page": 1})
            suppliers = []
            seen = set()
            for row in rows(data):
                item = _supplier(row)
                if item and item["product_id"] not in seen:
                    suppliers.append(item)
                    seen.add(item["product_id"])
            sourcing = {"query": query, "status": "completed", "items": suppliers[:20],
                        "quota_receipts": client.receipts}
            registry.update_candidate(run_id, candidate_id, sourcing=sourcing)
            return {"ok": True, "sourcing": sourcing, "cached": False}
        except ValueError:
            registry.update_candidate(run_id, candidate_id, sourcing={"query": query, "status": "failed", "items": [],
                                                                      "quota_receipts": client.receipts})
            raise


def supplier_product(detail: dict[str, Any], variants: list[dict[str, Any]], candidate: dict[str, Any], run_id: str):
    """转换已验证的商品事实；包装单位未经确认，不写入核价字段。"""
    supplier = _supplier(detail)
    if not supplier:
        raise ValueError("货源详情缺少商品编号或标题，不能入库。")
    skus = []
    for row in variants:
        sku_id = str(row.get("SkuId") or "")
        if not sku_id or any(sku["id"] == sku_id for sku in skus):
            continue
        price, stock = number(row.get("Price")), number(row.get("Stock"))
        skus.append({"id": sku_id, "name": str(row.get("SkuName") or ""),
                     "price": str(price) if price is not None else "",
                     "stock": str(int(stock)) if stock is not None else "", "image": supplier["image_url"]})
    if not skus:
        raise ValueError("Sorftime 未提供有效 SKU，已停止入库；请核对货源规格后再采集。")
    source = {"source_platform": "1688", "source_url": supplier["source_url"], "title": supplier["title"],
              "currency": "CNY", "price": str(supplier["price_cny"]) if supplier["price_cny"] is not None else "",
              "images": [supplier["image_url"]] if supplier["image_url"] else [],
              "image_pool": normalize_image_pool([supplier["image_url"]] if supplier["image_url"] else [], "source"),
              "skus": skus, "collect_status": "partial", "attributes": {"供货商": supplier["store_name"]}}
    diagnostics = {"success": True, "partial_success": True, "collect_mode": "api",
                   "source_url": supplier["source_url"], "platform_detected": "1688",
                   "title_found": True, "images_found_count": len(source["images"]), "sku_found_count": len(skus),
                   "missing_fields": ["包装尺寸", "包装重量", "材质"],
                   "next_action": "请核对货源规格、采购价、包装尺寸和重量后再核价。"}
    product = merge_source_partial_result(default_product_model(), source, diagnostics)
    product["product_id"] = "sorftime-1688-" + supplier["product_id"]
    product["name"] = supplier["title"]
    product["attributes"]["research_evidence"] = {
        "run_id": run_id, "candidate_id": candidate["id"], "amazon_asin": candidate.get("asin"),
        "amazon_url": candidate["source_url"], "supplier": supplier,
        "supplier_variants": variants, "data_source": "Sorftime", "confirmed_match": True,
    }
    return product


def import_supplier(body: dict[str, Any]) -> ProductResearchSupplierImportResponse:
    if body.get("confirmed") is not True:
        raise ValueError("请先人工核对并确认货源。")
    run_id, candidate_id = str(body.get("run_id") or ""), str(body.get("candidate_id") or "")
    supplier_id = str(body.get("supplier_id") or "")
    context = get_context()
    with context.research.candidate_operation(run_id, candidate_id):
        candidate = _candidate(run_id, candidate_id)
        supplier = next((item for item in candidate.get("sourcing", {}).get("items", [])
                         if item.get("product_id") == supplier_id), None)
        if not supplier:
            raise ValueError("请选择该候选已查询到的货源，不能直接提交任意商品编号。")
        existing = context.products.collection_product(supplier["source_url"])
        if existing.get("product_id"):
            context.research.update_candidate(run_id, candidate_id, imported_product_id=existing["product_id"])
            return {"ok": True, "product_id": existing["product_id"], "already_imported": True, "quota_receipts": []}
        client = _client()
        try:
            detail = client.call("ProductRequest", 601, {"ProductId": supplier_id})
            if not isinstance(detail, dict) or str(detail.get("ProductId")) != supplier_id:
                raise ValueError("货源详情与所选商品不一致，已停止入库。")
            variants = rows(client.call("ProductVariations", 601, {"ProductId": supplier_id}))
            product = supplier_product(detail, variants, candidate, run_id)
            saved = context.products.import_research_product(product)
            context.research.update_candidate(run_id, candidate_id, imported_product_id=saved["product_id"],
                                               import_quota_receipts=client.receipts)
            return {"ok": True, "product_id": saved["product_id"], "already_imported": False,
                    "quota_receipts": client.receipts}
        except ValueError:
            context.research.update_candidate(run_id, candidate_id, import_quota_receipts=client.receipts)
            raise


__all__ = ["search_suppliers", "import_supplier", "supplier_product"]

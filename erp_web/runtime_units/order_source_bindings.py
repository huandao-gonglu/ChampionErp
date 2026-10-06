"""从已确认发布任务的冻结商品与店铺身份提取采购关联；不读取当前草稿猜历史。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from erp_web.product_model.draft_image_model import normalize_draft_image_refs
from erp_web.schemas.order_procurement import ProcurementSource, SalesSkuBinding


def published_sku_image(product: dict, platform: str, fact: dict) -> str:
    """只解析冻结发布资料；规格覆盖优先，缺失的指定资产不能换成其他规格。"""
    draft = (product.get("drafts") or {}).get(platform) or {}
    row = next(
        (
            row
            for row in draft.get("sku_items", [])
            if row.get("sku_id") == fact.get("id")
        ),
        {},
    )
    asset_id = (row.get("overrides") or {}).get(
        "image_asset_id", fact.get("image_asset_id")
    )
    if not asset_id:
        refs = normalize_draft_image_refs(draft.get("images"))
        asset_id = next((ref["asset_id"] for ref in refs if ref["role"] == "main"), "")
    if not asset_id:
        return ""
    asset = next(
        (
            item
            for item in (product.get("source") or {}).get("image_pool", [])
            if item.get("id") == asset_id
        ),
        {},
    )
    for key in ("preview_url", "url"):
        value = str(asset.get(key) or "").strip()
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme in {"https", "http"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            ):
                return value
            if (
                not parsed.scheme
                and not parsed.netloc
                and parsed.path == "/file"
                and parse_qs(parsed.query).get("path")
            ):
                return value
        except ValueError:
            continue
    return ""


def bindings_from_publish_job(job: dict) -> list[SalesSkuBinding]:
    product = job.get("product") or {}
    source = product.get("source") or {}
    facts = {row["id"]: row for row in product.get("sku_items", []) if row.get("id")}
    bindings = []
    for platform, item in (job.get("platforms") or {}).items():
        if platform not in {"mercadolibre", "ozon", "yandex"}:
            continue
        approval = (job.get("approved_publications") or {}).get(platform) or {}
        identity = str(approval.get("store_identity") or "")
        if not identity:
            continue
        for state in (item.get("result") or {}).get("sku_results", []):
            fact = facts.get(state.get("sku_id"))
            if state.get("status") != "published" or not fact or not state.get("sku"):
                continue
            options = fact.get("options") or {}
            specification = " / ".join(f"{k}：{v}" for k, v in options.items()) or str(
                fact.get("name") or ""
            )
            try:
                origin = ProcurementSource(
                    source_platform=str(source.get("source_platform") or ""),
                    product_url=str(source.get("source_url") or ""),
                    source_sku_id=str(fact.get("source_sku_id") or ""),
                    specification=specification,
                )
            except (ValidationError, ValueError):
                # 发布不因采购来源缺失而失败，但不能生成猜测的采购映射。
                continue
            remote_ids = [""]
            if platform == "yandex":
                remote_ids = [str(state.get("offer_id") or state["sku"])]
            if platform == "mercadolibre":
                result = state.get("result") or {}
                publication = (
                    result.get("publication")
                    or (result.get("result") or {}).get("publication")
                    or {}
                )
                remote_ids = [
                    str(m["item_id"])
                    for m in publication.get("markets", [])
                    if m.get("item_id")
                ]
                if not remote_ids:
                    remote_ids = [str(state.get("item_id") or "")]
            for remote_id in set(remote_ids):
                bindings.append(
                    SalesSkuBinding(
                        platform=platform,
                        store_identity=identity,
                        seller_sku=state["sku"],
                        remote_id=remote_id,
                        product_id=str(product.get("product_id") or ""),
                        draft_id=str(item.get("draft_id") or ""),
                        sku_id=state["sku_id"],
                        source=origin,
                        publication_id=str(job.get("job_id") or ""),
                        image_url=published_sku_image(product, platform, fact),
                    )
                )
    return bindings

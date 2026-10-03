"""按已发布身份关联源草稿，核验图片归属并准备在线修改的目标图集。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.product_model import normalize_image_pool
from erp_web.schemas.online_products import OnlineListing, SourceImageOption, SourceImageSelection, digest
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services.image_content import image_content, read_image_file
from erp_web.services.image_hosting_config import validate_public_url
from erp_web.services.image_service import file_url


def _records(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _matches(listing: OnlineListing, state: dict) -> bool:
    """远端 ID 与发布账号同时匹配，不将标题或卖家 SKU 当作发布证据。"""
    current = state
    for _ in range(5):
        publication = _records(current.get("publication"))
        if listing.platform == "mercadolibre":
            if str(publication.get("account_user_id") or "") == listing.account_id:
                if str(publication.get("parent_item_id") or "") == listing.remote_id:
                    return True
                siteless_id = str(listing.snapshot.get("siteless_id") or "")
                if siteless_id and publication.get("siteless_user_product_id") == siteless_id:
                    return True
        elif listing.platform == "yandex":
            account = f"{current.get('business_id') or ''}:{current.get('campaign_id') or ''}"
            if account == listing.account_id and str(current.get("offer_id") or "") == listing.remote_id:
                return True
        current = _records(current.get("result"))
        if not current:
            break
    return False


def _draft_matches(listing: OnlineListing, draft: dict) -> bool:
    nodes = [draft, *draft.get("target_sites", [])]
    for sku in draft.get("sku_items", []):
        nodes.extend(_records(sku.get("publications")).values())
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("platform") and node["platform"] != listing.platform:
            continue
        if _matches(listing, node) or _matches(listing, _records(node.get("last_publish_task"))):
            return True
    return False


class OnlineProductImages:
    def __init__(self, context):
        self.context = context

    def source(self, listing: OnlineListing) -> tuple[dict, dict, str]:
        matches = {}
        for record in self.context.db.iter_draft_records(scope="all"):
            if listing.platform not in record.get("platforms", [record.get("platform")]):
                continue
            draft = record["raw"]
            if _draft_matches(listing, draft):
                matches[record["draft_id"]] = draft
        if not matches:
            return {}, {}, "未关联源草稿，暂不能添加图片；可调整现有图片的顺序或移除图片。"
        if len(matches) != 1:
            return {}, {}, "该平台商品对应多个源草稿，请先核对发布记录。"
        draft = next(iter(matches.values()))
        product = self.context.db.load_product_model(str(draft.get("source_product_id") or draft.get("product_id") or ""))
        if not product:
            return {}, {}, "源草稿关联的商品已不存在，暂不能添加图片。"
        return product, draft, ""

    def assets(self, listing: OnlineListing) -> tuple[dict, dict, list[tuple[SourceImageOption, dict]], str]:
        product, draft, reason = self.source(listing)
        if reason:
            return product, draft, [], reason
        options = []
        for item in normalize_image_pool(product.get("source", {}).get("image_pool", [])):
            if item.get("platforms") and listing.platform not in item["platforms"]:
                continue
            if item.get("status", "").lower() in {"empty", "failed", "error", "pending"}:
                continue
            path = self.context.image_delivery._source_path(item)
            url = str(item.get("url") or "")
            if path is None and not url.startswith("https://"):
                continue
            try:
                content_id = image_content(read_image_file(path))[0] if path else url
                if not path:
                    validate_public_url(url)
            except (ImageHostingError, ValueError, OSError):
                continue
            fingerprint = digest([listing.id, product["product_id"], draft["draft_id"], item["id"],
                                  str(path or ""), content_id])
            picture_id = str(item.get("platform_picture_id") or item.get("mercadolibre_picture_id")
                             or _records(_records(item.get("platform_uploads")).get("mercadolibre")).get("picture_id") or "")
            options.append((SourceImageOption(asset_id=item["id"], fingerprint=fingerprint,
                preview_url=file_url(path) if path else str(item.get("preview_url") or url),
                existing_picture_id=picture_id if listing.platform == "mercadolibre" else "",
                existing_url=url), {**item, "path": str(path or "")}))
        return product, draft, options, "" if options else "源草稿暂无可用图片，请先在商品图片池中添加图片。"

    def selection(self, listing: OnlineListing) -> SourceImageSelection:
        product, draft, options, reason = self.assets(listing)
        return SourceImageSelection(local_product_id=str(product.get("product_id") or ""),
            local_draft_id=str(draft.get("draft_id") or ""), images=[option for option, _ in options], reason=reason)

    def validate(self, listing: OnlineListing, pictures: list) -> tuple[dict, dict, dict]:
        refs = [picture for picture in pictures if isinstance(picture, dict) and "asset_id" in picture]
        if not refs:
            return {}, {}, {}
        product, draft, options, reason = self.assets(listing)
        by_id = {option.asset_id: (option, asset) for option, asset in options}
        for ref in refs:
            pair = by_id.get(ref["asset_id"])
            if not pair or pair[0].fingerprint != ref["fingerprint"]:
                raise ValueError(reason or "源图片已变化或不属于该商品，请重新从源草稿选图")
        return product, draft, {ref["asset_id"]: by_id[ref["asset_id"]][1] for ref in refs}

    def prepare(self, listing: OnlineListing, pictures: list, adapter) -> list:
        product, _, assets = self.validate(listing, pictures)
        if not assets:
            return deepcopy(pictures)
        if listing.platform == "mercadolibre":
            prepared = adapter.prepare_pictures(pictures, assets)
        else:
            # 只准备本次选中的素材，不更改源草稿选图或其它 SKU 的默认图片。
            selected = {**product, "sku_items": [], "drafts": {listing.platform: {"images": [
                {"asset_id": asset_id, "role": "detail", "order": index} for index, asset_id in enumerate(assets)
            ]}}}
            selected["source"] = {**product.get("source", {}), "image_pool": list(assets.values())}
            delivered = self.context.image_delivery.prepare_product(selected, listing.platform)
            urls = {item["id"]: item["url"] for item in delivered["source"]["image_pool"]}
            for url in urls.values():
                validate_public_url(url)
            prepared = [urls[picture["asset_id"]] if isinstance(picture, dict) else picture for picture in pictures]
        self.validate(listing, pictures)
        keys = [str(picture.get("id")) if isinstance(picture, dict) else picture for picture in prepared]
        if len(set(keys)) != len(keys):
            raise ValueError("目标图集包含重复图片，请重新选择")
        return prepared


__all__ = ["OnlineProductImages"]

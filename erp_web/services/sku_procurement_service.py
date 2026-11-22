"""独立的销售 SKU ↔ 1688 采购源映射服务；不依赖草稿或历史发布快照。"""

from __future__ import annotations

import hashlib
import threading
import time
from uuid import uuid4
from urllib.parse import urlsplit

from erp_web.product_model.alibaba_purchase_model import alibaba_offer_id
from erp_web.schemas.order_procurement import (
    BindSkuSourceRequest,
    LinkMasterSkuRequest,
    ProcurementSource,
    QuerySkuSourcesRequest,
    RemoveSkuSourceRequest,
    ResolveAlibabaSourceRequest,
    SalesSkuBinding,
    SelectSourceRequest,
    SetDefaultSkuSourceRequest,
)
from erp_web.services.alibaba_api_client import alibaba_provider
from erp_web.services.alibaba_product_payload import find_first_key, first_text
from erp_web.services.alibaba_self_purchase_service import account_scope
from erp_web.services.collection_link_resolution import (
    _input_links,
    _resolve_short_link,
    _short_link_url,
    _target_offer_id,
)

_RESOLVE_TTL_SECONDS = 1800
_RESOLVE_MAX_ENTRIES = 200
_RESOLVE_LOCK = threading.Lock()
_RESOLVE_CACHE: dict[tuple[str, str], dict] = {}


def yandex_business_identity(business_id: str) -> str:
    value = str(business_id or "").strip()
    if not value:
        return ""
    digest = hashlib.sha256(f"yandex:business\0{value}".encode("utf-8")).hexdigest()[:24]
    return f"yandex:biz:{digest}"


def _resolve_1688_url_and_offer_id(raw_text: str) -> tuple[str, str]:
    """支持标准 1688 链接、分享文案及 qr.1688.com 短链，解析出规范 URL 与 offerId。"""
    urls = _input_links(raw_text, "api")
    candidates: list[str] = []
    for url in urls:
        offer = alibaba_offer_id(url) or _target_offer_id(url)
        if offer:
            candidates.append(offer)
            continue
        parsed = urlsplit(url)
        if parsed.hostname == "qr.1688.com":
            resolved = _resolve_short_link(_short_link_url(url), time.monotonic() + 20)
            offer = alibaba_offer_id(resolved)
            if offer:
                candidates.append(offer)
    unique = list(dict.fromkeys(candidates))
    if not unique:
        raise ValueError("未能识别有效的 1688 商品链接，请粘贴完整商品链接或 1688 分享文案")
    if len(unique) > 1:
        raise ValueError("检测到多个不同的 1688 商品链接，请每次仅粘贴一个采购商品链接")
    offer_id = unique[0]
    return f"https://detail.1688.com/offer/{offer_id}.html", offer_id


def _prune_cache(now: float) -> None:
    expired = [key for key, entry in _RESOLVE_CACHE.items() if entry["expires_at"] <= now]
    for key in expired:
        _RESOLVE_CACHE.pop(key, None)
    while len(_RESOLVE_CACHE) > _RESOLVE_MAX_ENTRIES:
        oldest = min(_RESOLVE_CACHE, key=lambda key: _RESOLVE_CACHE[key]["expires_at"])
        _RESOLVE_CACHE.pop(oldest, None)


def _format_sku_specification(raw_sku: dict, fallback_title: str) -> str:
    options = raw_sku.get("options") if isinstance(raw_sku.get("options"), dict) else {}
    spec = " / ".join(f"{k}：{v}" for k, v in options.items() if str(k).strip() and str(v).strip())
    return spec or str(raw_sku.get("name") or "").strip() or fallback_title or "默认规格"


def _extract_supplier(raw_product: dict) -> str:
    api_raw = raw_product.get("api_raw")
    if isinstance(api_raw, dict):
        supplier = first_text(
            find_first_key(
                api_raw,
                {"sellerName", "companyName", "shopName", "supplierName", "sellerLoginId"},
            )
        )
        if supplier:
            return supplier[:200]
    return ""


class SkuProcurementService:
    """管理销售 SKU 到 1688 采购规格的独立映射（支持默认源、备选源和换源）。"""

    def __init__(
        self,
        procurement,
        *,
        config_provider,
        read_identities_provider,
        write_identity_provider,
        owner_scope_provider,
        client_factory=alibaba_provider,
    ):
        self.procurement = procurement
        self.config_provider = config_provider
        self.read_identities_provider = read_identities_provider
        self.write_identity_provider = write_identity_provider
        self.owner_scope_provider = owner_scope_provider
        self.client_factory = client_factory

    def _scope_key(self) -> str:
        return f"{self.owner_scope_provider()}:{account_scope(self.config_provider())}"

    def resolve_1688(self, body: dict) -> dict:
        req = ResolveAlibabaSourceRequest.model_validate(body)
        product_url, offer_id = _resolve_1688_url_and_offer_id(req.url)
        config = self.config_provider()
        scope_key = f"{self.owner_scope_provider()}:{account_scope(config)}"
        payload = self.client_factory(config).get_product(offer_id, product_url)
        product = payload.get("product") if isinstance(payload, dict) else {}
        if not isinstance(product, dict):
            raise ValueError("1688 未返回有效商品信息，请稍后重试")
        title = str(product.get("title") or "").strip() or f"1688 商品 {offer_id}"
        supplier = _extract_supplier(product)
        images = product.get("images") if isinstance(product.get("images"), list) else []
        main_image = str(images[0]).strip() if images else ""
        raw_skus = product.get("skus") if isinstance(product.get("skus"), list) else []
        skus: list[dict] = []
        sku_map: dict[str, dict] = {}
        seen: set[str] = set()
        for row in raw_skus:
            if not isinstance(row, dict):
                continue
            source_sku_id = str(row.get("id") or "").strip()
            if not source_sku_id or source_sku_id in seen:
                continue
            seen.add(source_sku_id)
            source_offer_id = str(row.get("offer_id") or offer_id).strip() or offer_id
            source_spec_id = str(row.get("spec_id") or "").strip()
            specification = _format_sku_specification(row, title)
            block_reason = ""
            if not source_sku_id.isascii() or not source_sku_id.isdigit():
                block_reason = "该规格缺少有效的数字 1688 SKU 编号，暂不能自动采购"
            elif source_offer_id != offer_id:
                block_reason = "该规格返回的商品编号与链接不一致，暂不能自动采购"
            elif not source_spec_id:
                block_reason = "1688 未返回该规格的下单标识（specId），暂不能自动采购"
            entry = {
                "source_sku_id": source_sku_id,
                "source_offer_id": source_offer_id,
                "source_spec_id": source_spec_id,
                "specification": specification,
                "price": str(row.get("price") or "").strip(),
                "stock": str(row.get("stock") or "").strip(),
                "image_url": str(row.get("image") or "").strip() or main_image,
                "can_auto_purchase": not block_reason,
                "block_reason": block_reason,
            }
            skus.append(entry)
            sku_map[source_sku_id] = entry
        if not skus:
            raise ValueError("该 1688 商品未返回可选 SKU 规格列表，暂无法通过接口绑定自动采购规格")
        resolve_id = uuid4().hex
        now = time.time()
        with _RESOLVE_LOCK:
            _prune_cache(now)
            _RESOLVE_CACHE[(scope_key, resolve_id)] = {
                "expires_at": now + _RESOLVE_TTL_SECONDS,
                "offer_id": offer_id,
                "product_url": product_url,
                "title": title,
                "supplier": supplier,
                "image_url": main_image,
                "skus": sku_map,
            }
        return {
            "ok": True,
            "resolve_id": resolve_id,
            "offer_id": offer_id,
            "product_url": product_url,
            "title": title,
            "supplier": supplier,
            "image_url": main_image,
            "skus": skus,
        }

    def query_sources(self, body: dict) -> dict:
        req = QuerySkuSourcesRequest.model_validate(body)
        grouped: dict[tuple[str, tuple[str, ...]], list] = {}
        target_identities: list[tuple[object, tuple[str, ...]]] = []
        for item in req.items:
            identities = tuple(self.read_identities_provider(item.platform, item.account_id))
            target_identities.append((item, identities))
            if identities:
                grouped.setdefault((item.platform, identities), []).append(item.seller_sku)
        cache: dict[tuple[str, tuple[str, ...]], dict[str, list[SalesSkuBinding]]] = {
            key: self.procurement.store.list_sku_bindings_batch(key[0], key[1], skus)
            for key, skus in grouped.items()
        }
        master_cache: dict[tuple[str, tuple[str, ...]], dict[str, tuple[str, str]]] = {
            key: self.procurement.store.resolve_master_skus_batch(key[0], key[1], skus)
            for key, skus in grouped.items()
        }
        results = []
        for item, identities in target_identities:
            raw_list = cache.get((item.platform, identities), {}).get(item.seller_sku, [])
            matched = [
                binding
                for binding in raw_list
                if self.procurement.store._matches_target(
                    binding, remote_id=item.remote_id, variant_id=item.variant_id
                )
            ]
            master_sku, linked_master_sku = master_cache.get((item.platform, identities), {}).get(
                item.seller_sku, (item.seller_sku, "")
            )
            default_binding = next((b for b in matched if b.is_default), matched[0] if len(matched) == 1 else None)
            results.append({
                "platform": item.platform,
                "account_id": item.account_id,
                "seller_sku": item.seller_sku,
                "remote_id": item.remote_id,
                "variant_id": item.variant_id,
                "master_sku": master_sku,
                "linked_master_sku": linked_master_sku,
                "default_binding_id": default_binding.id if default_binding else "",
                "bindings": [b.model_dump(mode="json") for b in matched],
            })
        return {"ok": True, "items": results}

    def _sync_order_line(self, order_id: str, line_key: str, revision: int | None, source: ProcurementSource) -> dict:
        accounts = self.procurement.accounts_provider()
        if revision is None:
            saved, _ = self.procurement.store.line_state(order_id, line_key, accounts)
            revision = saved["revision"] if saved else 0
        request = SelectSourceRequest(
            order_id=order_id,
            line_key=line_key,
            revision=revision,
            source=source,
        )
        self.procurement.store.select(request, source, accounts)
        return self.procurement.detail(order_id)

    def bind_source(self, body: dict) -> dict:
        req = BindSkuSourceRequest.model_validate(body)
        scope_key = self._scope_key()
        now = time.time()
        with _RESOLVE_LOCK:
            _prune_cache(now)
            resolved = _RESOLVE_CACHE.get((scope_key, req.resolve_id))
        if not resolved:
            raise ValueError("1688 商品规格解析已过期或授权已切换，请重新解析链接后再绑定")
        sku = resolved["skus"].get(req.source_sku_id)
        if not sku:
            raise ValueError("所选规格不属于本次解析的 1688 商品，请重新选择")
        if not sku["can_auto_purchase"]:
            raise ValueError(sku["block_reason"] or "该规格缺少有效的 1688 下单标识，暂不能绑定")

        write_identity = self.write_identity_provider(req.platform, req.account_id)
        if not write_identity:
            raise ValueError("当前店铺尚未完成授权，无法保存 SKU 采购来源")
        read_identities = list(self.read_identities_provider(req.platform, req.account_id))
        source = ProcurementSource(
            supplier=resolved["supplier"],
            source_platform="1688",
            product_url=resolved["product_url"],
            source_sku_id=sku["source_sku_id"],
            source_offer_id=sku["source_offer_id"],
            source_spec_id=sku["source_spec_id"],
            purchase_block_reason="",
            specification=sku["specification"],
        )
        effective_master, linked_master = self.procurement.store.resolve_master_sku(
            req.platform, read_identities, req.seller_sku
        )
        target_master = req.master_sku or effective_master
        if req.master_sku and req.master_sku != effective_master:
            target_master, linked_master = self.procurement.store.set_master_sku_link(
                req.platform,
                write_identity,
                req.seller_sku,
                req.master_sku,
                clear_identities=read_identities,
            )
        if req.shared_across_platforms:
            saved = self.procurement.store.upsert_master_source(
                target_master,
                source,
                image_url=sku["image_url"] or resolved["image_url"],
                origin="alibaba_api",
                set_default=req.set_default,
                platform=req.platform,
                store_identities=read_identities,
                seller_sku=req.seller_sku,
            )
        else:
            binding = SalesSkuBinding(
                platform=req.platform,
                store_identity=write_identity,
                seller_sku=req.seller_sku,
                remote_id="" if req.platform == "yandex" else req.remote_id,
                variant_id=req.variant_id,
                source=source,
                image_url=sku["image_url"] or resolved["image_url"],
                origin="alibaba_api",
                scope="store",
                master_sku=target_master,
                is_default=req.set_default,
            )
            saved = self.procurement.store.upsert_sku_binding(
                binding,
                set_default=req.set_default,
                clear_default_identities=read_identities,
            )
        bindings = self.procurement.store.list_sku_bindings(
            req.platform,
            read_identities,
            req.seller_sku,
            remote_id=req.remote_id,
            variant_id=req.variant_id,
        )
        response = {
            "ok": True,
            "master_sku": target_master,
            "linked_master_sku": linked_master,
            "binding": saved.model_dump(mode="json"),
            "bindings": [b.model_dump(mode="json") for b in bindings],
        }
        if req.order_id and req.line_key and req.set_default:
            response["order_detail"] = self._sync_order_line(req.order_id, req.line_key, req.revision, saved.source)
        return response

    def set_default(self, body: dict) -> dict:
        req = SetDefaultSkuSourceRequest.model_validate(body)
        read_identities = list(self.read_identities_provider(req.platform, req.account_id))
        if not read_identities:
            raise ValueError("当前店铺尚未完成授权，无法切换默认采购来源")
        effective_master, linked_master = self.procurement.store.resolve_master_sku(
            req.platform, read_identities, req.seller_sku
        )
        target_master = req.master_sku or effective_master
        if req.scope == "master" or req.binding_id.startswith("ms:"):
            updated = self.procurement.store.set_default_master_source(
                target_master,
                req.binding_id,
                platform=req.platform,
                store_identities=read_identities,
                seller_sku=req.seller_sku,
            )
        else:
            updated = self.procurement.store.set_default_sku_binding(
                req.platform,
                read_identities,
                req.seller_sku,
                req.binding_id,
                remote_id=req.remote_id,
                variant_id=req.variant_id,
            )
            if req.shared_across_platforms:
                updated = self.procurement.store.upsert_master_source(
                    target_master,
                    updated.source,
                    image_url=updated.image_url,
                    origin=updated.origin,
                    set_default=True,
                    platform=req.platform,
                    store_identities=read_identities,
                    seller_sku=req.seller_sku,
                )
        bindings = self.procurement.store.list_sku_bindings(
            req.platform,
            read_identities,
            req.seller_sku,
            remote_id=req.remote_id,
            variant_id=req.variant_id,
        )
        response = {
            "ok": True,
            "master_sku": target_master,
            "linked_master_sku": linked_master,
            "binding": updated.model_dump(mode="json"),
            "bindings": [b.model_dump(mode="json") for b in bindings],
        }
        if req.order_id and req.line_key:
            response["order_detail"] = self._sync_order_line(req.order_id, req.line_key, req.revision, updated.source)
        return response

    def remove_source(self, body: dict) -> dict:
        req = RemoveSkuSourceRequest.model_validate(body)
        read_identities = list(self.read_identities_provider(req.platform, req.account_id))
        if not read_identities:
            raise ValueError("当前店铺尚未完成授权，无法删除采购来源")
        effective_master, linked_master = self.procurement.store.resolve_master_sku(
            req.platform, read_identities, req.seller_sku
        )
        target_master = req.master_sku or effective_master
        if req.scope == "master" or req.binding_id.startswith("ms:"):
            removed = self.procurement.store.delete_master_source(target_master, req.binding_id)
        else:
            removed = self.procurement.store.delete_sku_binding(
                req.platform,
                read_identities,
                req.seller_sku,
                req.binding_id,
            )
        if not removed:
            raise ValueError("采购来源不存在或已被删除")
        bindings = self.procurement.store.list_sku_bindings(
            req.platform,
            read_identities,
            req.seller_sku,
        )
        return {
            "ok": True,
            "removed": True,
            "master_sku": target_master,
            "linked_master_sku": linked_master,
            "bindings": [b.model_dump(mode="json") for b in bindings],
        }

    def link_master_sku(self, body: dict) -> dict:
        req = LinkMasterSkuRequest.model_validate(body)
        write_identity = self.write_identity_provider(req.platform, req.account_id)
        if not write_identity:
            raise ValueError("当前店铺尚未完成授权，无法关联统一货号")
        read_identities = list(self.read_identities_provider(req.platform, req.account_id))
        master_sku, linked_master_sku = self.procurement.store.set_master_sku_link(
            req.platform,
            write_identity,
            req.seller_sku,
            req.master_sku,
            clear_identities=read_identities,
        )
        bindings = self.procurement.store.list_sku_bindings(
            req.platform,
            read_identities,
            req.seller_sku,
        )
        return {
            "ok": True,
            "master_sku": master_sku,
            "linked_master_sku": linked_master_sku,
            "bindings": [b.model_dump(mode="json") for b in bindings],
        }


__all__ = ["SkuProcurementService", "yandex_business_identity"]

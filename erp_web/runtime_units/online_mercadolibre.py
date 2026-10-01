"""Mercado 在线商品读取与字段级修改；按商品事实明确区分两种刊登模型。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.runtime_units.online_change_confirmation import mercado_change
from urllib.parse import quote

from erp_web.marketplaces.config_http import request_json
from erp_web.marketplaces.online_buyer_links import mercado_buyer_links
from erp_web.product_model import canonicalize_mercadolibre_siteless_user_product_id
from erp_web.marketplaces.online_sync import raise_if_access_blocked
from erp_web.runtime_units.online_mercadolibre_read import sync_mercado
from erp_web.runtime_units.online_product_status import mercado_status
from erp_web.schemas.online_products import Capability, MarketSnapshot, OnlineListing, PriceScope, StockScope, listing_identity
from erp_web.marketplaces.mercadolibre_mapping import validate_user_product_mapping
from erp_web.runtime_units.store_credentials import get_mercadolibre_access_token

API = "https://api.mercadolibre.com"


def amount(value: Any) -> str | None:
    return None if value is None else str(value)


class MercadoOnlineAdapter:
    platform = "mercadolibre"

    def __init__(self, config: dict[str, Any]):
        self.token = get_mercadolibre_access_token(config)
        self.config = config[self.platform]
        self.account_id = str(self.config.get("user_id") or "")
        if not self.account_id:
            raise ValueError("Mercado 授权缺少已验证的账号身份")

    def get(self, path: str, *, version: bool = False) -> Any:
        return request_json("GET", API+path, self.token, extra_headers={"X-API-Version": "2"} if version else None)

    def sync(self, ids=None):
        return sync_mercado(self, ids)

    def read_status(self, listing):
        return mercado_status(self, listing)

    def read(self, remote_id: str) -> OnlineListing:
        parent = self.get("/marketplace/items/"+quote(remote_id, safe=""))
        if not isinstance(parent, dict) or parent.get("id") != remote_id or str(parent.get("seller_id")) != self.account_id or parent.get("site_id") != "CBT":
            raise ValueError("Mercado 商品身份与当前全局账号不一致")
        refs = parent.get("marketplace_items") or []
        if not isinstance(refs, list):
            raise ValueError("Mercado 市场映射不是数组")
        up_id = canonicalize_mercadolibre_siteless_user_product_id(parent.get("user_product_id"))
        model = "user_products" if up_id else "traditional_global_items"
        up: dict[str, Any] = {}
        if up_id:
            mapping = validate_user_product_mapping(self.get(f"/marketplace/user-products/{quote(up_id, safe='')}/mapping"),
                {"siteless_user_product_id": up_id, "account_user_id": self.account_id})
            if mapping["parent_item_id"] != remote_id:
                raise ValueError("Mercado mapping 父 Item 与搜索结果不一致")
            refs = mapping["site_items"]
            up = self.get(f"/user-products/{quote(up_id, safe='')}", version=True)
            if not isinstance(up, dict) or canonicalize_mercadolibre_siteless_user_product_id(up.get("id")) != up_id:
                raise ValueError("Mercado User Product 详情身份不一致")
        markets, prices, children, errors = [], [], {}, []
        active = parent.get("status") in ("active", "paused")
        for ref in refs:
            item_id = str(ref.get("item_id") or "")
            if not item_id or ref.get("parent_id", remote_id) != remote_id or str(ref.get("parent_user_id", self.account_id)) != self.account_id:
                raise ValueError("Mercado 市场映射不属于当前父商品")
            try:
                child = self.get("/marketplace/items/"+quote(item_id, safe=""))
                if child.get("id") != item_id or child.get("site_id") != ref.get("site_id"):
                    raise ValueError("市场 Item 身份不一致")
                if ref.get("user_id") is not None and str(child.get("seller_id")) != str(ref["user_id"]):
                    raise ValueError("市场卖家身份不一致")
                if child.get("cbt_item_id") and child["cbt_item_id"] != remote_id:
                    raise ValueError("市场详情 CBT 父身份不一致")
                children[item_id] = child
                markets.append(MarketSnapshot(id=item_id, site_id=str(child["site_id"]), seller_id=str(child.get("seller_id") or ""),
                    logistic_type=str(ref.get("logistic_type") or ""), raw_status=str(child.get("status") or ""),
                    raw_sub_status=child.get("sub_status") or [], price=amount(child.get("price")), currency=str(child.get("currency_id") or "")))
                net_value = child.get("net_proceeds")
                net = net_value.get("amount") if isinstance(net_value, dict) else net_value
                currency = str(net_value.get("currency_id") or "") if isinstance(net_value, dict) else ("USD" if net is not None else str(child.get("currency_id") or ""))
                prices.append(PriceScope(id=item_id, label=f"{child['site_id']} · {ref.get('logistic_type', '')}",
                    amount=amount(net if net is not None else child.get("price")), currency=currency,
                    kind="net_proceeds" if net is not None else "sale_price",
                    writable=active and child.get("status") in ("active", "paused") and bool(currency) and (not up_id or net is not None),
                    reason="" if not up_id or net is not None else "尚未取得可核对的净收入报价"))
            except Exception as exc:
                raise_if_access_blocked(exc)
                errors.append(f"{item_id}：{exc}")
                markets.append(MarketSnapshot(id=item_id, site_id=str(ref.get("site_id") or ""), logistic_type=str(ref.get("logistic_type") or "")))
        base = up or parent
        stocks = []
        remote = any(m.logistic_type == "remote" for m in markets)
        # 传统 Global Item 的数量仅属于 Remote 共享库存，绝不拿来修改 FBO 实物库存。
        if not up_id:
            variations = parent.get("variations") or []
            for row in variations or [parent]:
                variation_id = str(row.get("id") or "") if variations else ""
                stocks.append(StockScope(id=variation_id or "shared", label="Remote 跨市场共享"+(f" · 变体 {variation_id}" if variation_id else ""),
                    quantity=row.get("available_quantity"), variation_id=variation_id, writable=active and remote,
                    reason="" if active and remote else "没有可修改的 Remote 库存；平台仓库存不可直接改数"))
        else:
            cross_docking = bool(children) and all(c.get("shipping", {}).get("logistic_type") == "cross_docking" for c in children.values())
            writable = active and cross_docking and type(base.get("available_quantity")) is int and not errors
            stocks.append(StockScope(id="shared", label="User Product 共享库存", quantity=base.get("available_quantity"), writable=writable,
                reason="" if writable else "此物流模式需通过 Stock Locations 管理；当前未取得可写库存位置"))
        if not up_id and parent.get("price") is not None:
            prices.insert(0, PriceScope(id="global", label="全局基础价（影响无独立价格的市场）", amount=amount(parent["price"]),
                currency=str(parent.get("currency_id") or ""), kind="base_price", writable=active and bool(parent.get("currency_id"))))
        pictures = [{"id": p["id"], "url": p.get("secure_url") or p.get("url") or ""} for p in base.get("pictures", []) if isinstance(p, dict) and p.get("id")]
        content = {"title": str(base.get("family_name") or base.get("title") or parent.get("title") or ""), "pictures": pictures,
                   "attributes": base.get("attributes") or []}
        content_fields = ["pictures", "attributes"]
        if not up_id and not parent.get("sold_quantity"):
            content_fields.insert(0, "title")
        return OnlineListing(id=listing_identity(self.platform, self.account_id, remote_id), platform=self.platform, account_id=self.account_id,
            remote_id=remote_id, model=model, title=content["title"], seller_sku=str(base.get("seller_custom_field") or next((a.get("value_name") for a in base.get("attributes", []) if a.get("id")=="SELLER_SKU"), "") or ""),
            thumbnail=str(parent.get("secure_thumbnail") or parent.get("thumbnail") or (pictures[0]["url"] if pictures else "")),
            sale_state=str(parent.get("status") or "unknown"), raw_status=str(parent.get("status") or ""), raw_sub_status=parent.get("sub_status") or [],
            markets=markets, prices=prices, stocks=stocks, content=content, errors=errors,
            buyer_links=mercado_buyer_links(children),
            snapshot={"parent": parent, "user_product": up, "children": children, "siteless_id": up_id},
            capabilities={
                "price": Capability(enabled=any(p.writable for p in prices), scope="所选价格范围"),
                "stock": Capability(enabled=any(s.writable for s in stocks), scope="Remote 共享库存", reason="User Products 需已核验的库存位置" if up_id and not any(s.writable for s in stocks) else ""),
                "content": Capability(enabled=active and not errors, fields=content_fields, scope="全局商品及关联市场", reason="User Products 的 family_name 会影响同族商品，本表单不修改族名" if up_id else "有销量后标题受限；描述写入路径未开放"),
                "sale_state": Capability(enabled=active and not errors, scope="全局商品及全部关联市场", reason="已关闭或受限商品不可直接恢复" if not active else ""),
            })

    def read_confirmation(self, listing, request):
        return mercado_change(self, listing, request)

    def write(self, listing: OnlineListing, operation: str, scope: str, changes: dict[str, Any]) -> dict[str, Any]:
        up_id = listing.snapshot.get("siteless_id")
        target = up_id or listing.remote_id
        path = "/global/user-products/" if up_id else "/global/items/"
        payload: dict[str, Any]
        if operation == "price":
            price = next(p for p in listing.prices if p.id == scope)
            field = "net_proceeds" if price.kind == "net_proceeds" else "price"
            payload = {field: float(changes["amount"])}
            if scope != "global":
                if up_id:
                    payload = {"listing_sites": [{"listing_id": scope, **payload}]}
                else:
                    target = scope
        elif operation == "stock":
            stock = next(s for s in listing.stocks if s.id == scope)
            if stock.variation_id:
                payload = {"variations": [{"id": v["id"], "available_quantity": changes["quantity"] if str(v["id"]) == stock.variation_id else v["available_quantity"]}
                    for v in listing.snapshot["parent"]["variations"]]}
            else:
                payload = {"available_quantity": changes["quantity"]}
        elif operation == "sale_state":
            payload = {"status": changes["state"]}
        else:
            payload = deepcopy(changes)
            if "pictures" in payload:
                payload["pictures"] = [{"id": p["id"]} for p in payload["pictures"]]
        result = request_json("PUT", API+path+quote(str(target), safe=""), self.token, payload)
        if not isinstance(result, dict):
            raise RuntimeError("Mercado 修改响应无法识别，需要只读对账")
        return result


    def confirmation_details(self, listing, operation, receipt, scope=""):
        """异步完成只以任务逐项结果为证据；finished 不等于全部成功。"""
        ids = set()
        def collect(value):
            if isinstance(value, dict):
                if value.get("task_id"):
                    ids.add(str(value["task_id"]))
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(receipt)
        tasks, errors, pending = [], [], False
        for task_id in sorted(ids):
            task = self.get("/user-products-families/tasks/" + quote(task_id, safe=""))
            if task.get("task_id") != task_id or not isinstance(task.get("user_products"), list):
                raise ValueError("Mercado 异步任务响应缺少可核验的身份或逐项结果")
            tasks.append(task)
            pending |= task.get("status") != "finished" or not task["user_products"]
            for row in task["user_products"]:
                if row.get("status") == "failed":
                    errors.append({"id": row.get("id"), "reasons": row.get("reasons")})
                elif row.get("status") != "succeeded":
                    pending = True
        return {"pending": pending, "errors": errors, "tasks": tasks}

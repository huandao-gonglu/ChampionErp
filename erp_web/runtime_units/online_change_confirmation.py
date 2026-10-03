"""按修改范围读取确认事实，不重新下载整个在线商品聚合。"""
from urllib.parse import quote

from erp_web.marketplaces.yandex_currency import yandex_internal_currency
from erp_web.runtime_units.online_yandex_read import catalog_pages, hidden_ids, index_rows, pages, stock_result
from erp_web.runtime_units.online_yandex_snapshot import build_stocks, card_attributes, card_issues


def mercado_change(adapter, listing, request):
    fresh = listing.model_copy(deep=True)
    operation, scope = request.operation, request.scope_id
    up_id = listing.snapshot.get("siteless_id")

    def item(remote_id, fields, *, parent=False):
        fields = sorted(set(fields) | {"id","seller_id","site_id","cbt_item_id"})
        row = adapter.get("/marketplace/items/"+quote(remote_id,safe="")+"?attributes="+",".join(fields))
        if row.get("id") != remote_id:
            raise ValueError("修改确认返回的商品身份不一致")
        if parent:
            if str(row.get("seller_id")) != adapter.account_id or row.get("site_id") != "CBT":
                raise ValueError("修改确认的父商品与账号不一致")
        else:
            market = next(m for m in listing.markets if m.id == remote_id)
            if str(row.get("seller_id")) != market.seller_id or row.get("site_id") != market.site_id or row.get("cbt_item_id") not in (None, "", listing.remote_id):
                raise ValueError("修改确认的市场身份不一致")
        return row

    if operation == "sale_state":
        parent = item(listing.remote_id,["status","sub_status"],parent=True)
        fresh.raw_status = fresh.sale_state = str(parent.get("status") or "unknown")
        fresh.raw_sub_status = parent.get("sub_status") or []
        for market in fresh.markets:
            child = item(market.id,["status","sub_status"])
            market.raw_status = str(child.get("status") or "unknown")
            market.raw_sub_status = child.get("sub_status") or []
        return fresh
    if operation == "price":
        price = next(p for p in fresh.prices if p.id == scope)
        row = item(listing.remote_id if scope == "global" else scope,["price","currency_id","net_proceeds"],parent=scope=="global")
        net = row.get("net_proceeds")
        value = (net.get("amount") if isinstance(net,dict) else net) if price.kind == "net_proceeds" else row.get("price")
        price.amount = str(value) if value is not None else None
        price.currency = str(net.get("currency_id") or "") if isinstance(net,dict) and price.kind == "net_proceeds" else ("USD" if price.kind == "net_proceeds" and net is not None else str(row.get("currency_id") or ""))
        return fresh
    if up_id:
        row = adapter.get("/user-products/"+quote(up_id,safe=""),version=True)
        if str(row.get("id")) != up_id:
            raise ValueError("User Product 修改确认身份不一致")
    else:
        fields = ["available_quantity","variations","status"] if operation == "stock" else list(request.changes)
        row = item(listing.remote_id,fields,parent=True)
    if operation == "stock":
        stock = next(s for s in fresh.stocks if s.id == scope)
        target = next((v for v in row.get("variations",[]) if str(v.get("id")) == stock.variation_id),{}) if stock.variation_id else row
        stock.quantity = target.get("available_quantity")
        if listing.desired_sale_state == "paused":
            status = item(listing.remote_id,["status"],parent=True) if up_id else row
            fresh.sale_state = str(status.get("status") or "unknown")
    else:
        for field in request.changes:
            if field not in row:
                raise ValueError(f"平台未返回确认字段：{field}")
            fresh.content[field] = ([{"id": picture["id"], "url": picture.get("secure_url") or picture.get("url") or ""}
                for picture in row[field] if isinstance(picture, dict) and picture.get("id")]
                if field == "pictures" else row[field])
        if "title" in request.changes:
            fresh.title = str(row["title"])
    return fresh


def yandex_change(adapter, listing, request):
    fresh = listing.model_copy(deep=True)
    remote_id, scope = listing.remote_id, request.scope_id
    body = {"offerIds": [remote_id]}

    def one(path,key):
        values = index_rows([r for page in pages(adapter,path,key,body) for r in page],allowed={remote_id})
        if remote_id not in values:
            raise ValueError("平台未返回待确认商品，已保留原快照")
        return values[remote_id]

    if request.operation == "sale_state":
        hidden = remote_id in hidden_ids(adapter,remote_id)
        fresh.snapshot["hidden"] = hidden
        # 隐藏解除只证明请求生效，不推断商品已通过平台审核。
        fresh.sale_state = "paused" if hidden else ("active" if fresh.raw_status == "PUBLISHED" else "unknown")
    elif request.operation == "price":
        if scope == "business":
            row = one(f"/v2/businesses/{adapter.business}/offer-prices","offers").get("price",{})
            fresh.snapshot["default_price"] = row
        else:
            campaign = one(f"/v2/campaigns/{adapter.campaign}/offers","offers")
            row = campaign.get("campaignPrice",{})
            fresh.snapshot["campaign"] = campaign
        price = next(p for p in fresh.prices if p.id == scope)
        price.amount = str(row["value"]) if row.get("value") is not None else None
        price.currency = yandex_internal_currency(str(row.get("currencyId") or ""))
    elif request.operation == "stock":
        if adapter.mode == "business":
            stock = next(s for s in fresh.stocks if s.id == scope)
            result = stock_result(adapter,f"/v3/businesses/{adapter.business}/offers/stocks",{**body,"partnerWarehouseId":int(scope)})
            warehouses = [{"id":scope,"name":stock.label,"models":[{"apiAvailability":"AVAILABLE"}]}]
            checked = build_stocks(adapter.mode,warehouses,{scope:result},remote_id)[0]
        else:
            result = stock_result(adapter,f"/v2/campaigns/{adapter.campaign}/offers/stocks",body)
            checked = build_stocks(adapter.mode,[],{"campaign":result},remote_id)[0]
        next(s for s in fresh.stocks if s.id == scope).quantity = checked.quantity
        if listing.desired_sale_state == "paused":
            fresh.sale_state = "paused" if remote_id in hidden_ids(adapter,remote_id) else "unknown"
    else:
        if set(request.changes)-{"attributes"}:
            mappings = [r for page in catalog_pages(adapter,[remote_id]) for r in page]
            if len(mappings) != 1 or mappings[0]["offer"]["offerId"] != remote_id:
                raise ValueError("商品内容确认身份不一致")
            offer = mappings[0]["offer"]
            for field in set(request.changes)-{"attributes"}:
                key = "name" if field == "title" else field
                if key not in offer:
                    raise ValueError(f"平台未返回确认字段：{field}")
                fresh.content[field] = offer[key]
            fresh.title = str(fresh.content.get("title") or "")
        card = one(f"/v2/businesses/{adapter.business}/offer-cards","offerCards")
        fresh.snapshot["card"] = card
        fresh.raw_sub_status = [str(card["cardStatus"])] if card.get("cardStatus") else []
        fresh.platform_issues = card_issues(card)
        if "attributes" in request.changes:
            names = {str(a["id"]): str(a.get("name") or "") for a in listing.content.get("attributes", [])}
            fresh.content["attributes"] = card_attributes(card, names)
    return fresh

"""在线管理的纯变更校验、预览与回读比较。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from erp_web.schemas.online_products import ChangeRequest, OnlineListing


def validate_changes(listing: OnlineListing, request: ChangeRequest) -> None:
    if listing.details_state != "ready" or listing.errors:
        raise ValueError("商品详情尚未完整同步，请完成同步后再修改")
    capability = listing.capabilities.get(request.operation)
    if not capability or not capability.enabled:
        raise ValueError(capability.reason if capability else "该商品不支持此操作")
    changes = request.changes
    if not changes:
        raise ValueError("未选择任何变更")
    if request.operation == "price":
        scope = next((p for p in listing.prices if p.id == request.scope_id and p.writable), None)
        if scope is None or set(changes) != {"amount", "currency"} or changes["currency"] != scope.currency:
            raise ValueError("价格范围或币种与平台快照不一致")
        try:
            value = Decimal(str(changes["amount"]))
            if not value.is_finite() or value <= 0 or value > Decimal("999999999") or value.as_tuple().exponent < -2:
                raise ValueError("价格必须是正数，最多两位小数")
        except InvalidOperation as exc:
            raise ValueError("价格不是有效金额") from exc
    elif request.operation == "stock":
        scope = next((s for s in listing.stocks if s.id == request.scope_id and s.writable), None)
        if scope is None or set(changes) != {"quantity"} or type(changes["quantity"]) is not int or not 0 <= changes["quantity"] <= 2_000_000_000:
            raise ValueError("库存范围无效，数量必须是非负整数")
        if listing.platform == "mercadolibre" and listing.desired_sale_state == "paused" and changes["quantity"] > 0:
            if listing.model != "traditional_global_items" or "paused_by_seller" not in listing.raw_sub_status:
                raise ValueError("尚未确认平台主动停售状态，请先重新提交停售并完成回读，再设置库存")
    elif request.operation == "sale_state":
        if request.scope_id not in ("", "global") or set(changes) != {"state"} or changes["state"] not in ("paused", "active"):
            raise ValueError("销售状态仅支持在声明范围内停售或恢复")
        if changes["state"] == "active" and listing.stocks and all(s.quantity == 0 for s in listing.stocks):
            raise ValueError("库存为零，不能请求恢复销售")
    else:
        if request.scope_id not in ("", "global"):
            raise ValueError("内容更新必须使用声明的全局或账号商品范围")
        if not set(changes).issubset(capability.fields):
            raise ValueError("变更包含不可编辑字段")
        if "title" in changes and (not isinstance(changes["title"], str) or not 1 <= len(changes["title"].strip()) <= 500):
            raise ValueError("标题不能为空且不得超过 500 字")
        if "description" in changes and not isinstance(changes["description"], str):
            raise ValueError("描述必须是文本")
        if "pictures" in changes:
            pictures = changes["pictures"]
            if not isinstance(pictures, list) or not 1 <= len(pictures) <= 30:
                raise ValueError("图片必须是完整的目标列表，保留 1–30 张")
            identities = []
            for picture in pictures:
                if isinstance(picture, dict) and "asset_id" in picture:
                    if (set(picture) != {"asset_id", "fingerprint"} or not isinstance(picture["asset_id"], str)
                            or not picture["asset_id"] or not isinstance(picture["fingerprint"], str)
                            or not re.fullmatch(r"[0-9a-f]{64}", picture["fingerprint"])):
                        raise ValueError("源图片必须包含有效的资产 ID 和内容版本")
                    identities.append(("asset", picture["asset_id"]))
                elif listing.platform == "mercadolibre":
                    allowed = {str(p.get("id")) for p in listing.content.get("pictures", []) if isinstance(p, dict)}
                    if not isinstance(picture, dict) or set(picture) - {"id", "url"} or str(picture.get("id")) not in allowed:
                        raise ValueError("新增图片必须从关联源草稿选择")
                    identities.append(("id", str(picture["id"])))
                else:
                    if not isinstance(picture, str) or picture not in listing.content.get("pictures", []):
                        raise ValueError("新增图片必须从关联源草稿选择")
                    identities.append(("url", picture))
            if len(set(identities)) != len(identities):
                raise ValueError("目标图集不能包含重复图片")
        if "attributes" in changes:
            rows = changes["attributes"]
            if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or not row.get("id") for row in rows):
                raise ValueError("属性必须包含有效的属性 ID 和值")
            old = {str(row.get("id")): row for row in listing.content.get("attributes", [])}
            if len({str(r["id"]) for r in rows}) != len(rows):
                raise ValueError("属性 ID 不得重复")
            for row in rows:
                key = str(row["id"])
                if key not in old or key in {"BRAND", "GTIN", "MODEL", "SELLER_SKU"} or old[key].get("tags", {}).get("read_only"):
                    raise ValueError(f"属性 {key} 不允许通过在线管理修改身份或只读字段")
                original = old[key]
                if original.get("value_id") or original.get("valueId"):
                    raise ValueError("枚举属性需要平台字典，当前表单仅修改已有自由文本属性")
                if listing.platform == "yandex":
                    if set(row) - {"id", "parameterId", "unitId", "value"} or row.get("parameterId") != original.get("parameterId") or row.get("unitId") != original.get("unitId"):
                        raise ValueError("属性身份和单位不得改变")
                    value = row.get("value")
                elif original.get("values"):
                    original_values, values = original["values"], row.get("values")
                    if len(original_values) != 1 or original_values[0].get("id") or set(row) != {"id", "values"} or not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict) or set(values[0]) != {"name"}:
                        raise ValueError("仅支持单个自由文本属性值，不接受复杂属性结构")
                    value = values[0]["name"]
                else:
                    if set(row) != {"id", "value_name"}:
                        raise ValueError("自由文本属性只允许提交 id 和 value_name")
                    value = row["value_name"]
                if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                    raise ValueError("属性值必须是 1–2000 字的文本")


def same_number(left: Any, right: Any) -> bool:
    try:
        return left is not None and right is not None and Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return False


def contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items() if k != "url")
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(contains(a, e) for a, e in zip(actual, expected))
    return actual == expected


def confirmation(listing: OnlineListing, request: ChangeRequest) -> dict[str, bool]:
    changes = request.changes
    if request.operation == "price":
        row = next((p for p in listing.prices if p.id == request.scope_id), None)
        return {"amount": bool(row and row.currency == changes["currency"] and same_number(row.amount, changes["amount"]))}
    if request.operation == "stock":
        row = next((s for s in listing.stocks if s.id == request.scope_id), None)
        result = {"quantity": bool(row and row.quantity == changes["quantity"])}
        if listing.desired_sale_state == "paused":
            result["preserved_pause"] = listing.sale_state == "paused"
        return result
    if request.operation == "sale_state":
        state = changes["state"]
        if listing.platform == "yandex":
            return {"state": listing.snapshot.get("hidden") is (state == "paused")}
        if listing.platform == "ozon":
            return {"state": listing.snapshot.get("info", {}).get("is_archived") is (state == "paused")}
        return {"state": listing.raw_status == state, **{f"market:{m.id}": m.raw_status == state for m in listing.markets}}
    result = {}
    for field, expected in changes.items():
        if field == "attributes":
            actual = {str(r.get("id")): r for r in listing.content.get(field, [])}
            result[field] = all(contains(actual.get(str(row["id"])), row) for row in expected)
        else:
            result[field] = contains(listing.content.get(field), expected)
    return result

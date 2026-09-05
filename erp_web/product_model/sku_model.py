"""商品 SKU、来源更新与草稿选品的纯数据契约。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import uuid
from typing import Any

PACKAGE_FIELDS = ("length_cm", "width_cm", "height_cm", "weight_kg")
SKU_FACT_FIELDS = ("name", "options", "cost_cny", "supplier_stock", "image", "barcode", "package_dimensions")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def sku_fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize_product_skus(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            raise ValueError("SKU 必须是对象")
        retired = {"spec1", "spec2", "price", "sale_price", "custom_stock", "selected"}.intersection(raw)
        if retired:
            raise ValueError("SKU 包含已退役字段：" + "、".join(sorted(retired)))
        sku_id = text(raw.get("id")) or uuid.uuid4().hex
        if sku_id in seen:
            raise ValueError("商品 SKU ID 重复：" + sku_id)
        seen.add(sku_id)
        options = {text(k): text(v) for k, v in record(raw.get("options")).items() if text(k)}
        result.append({
            "id": sku_id,
            "source_sku_id": text(raw.get("source_sku_id")),
            "name": text(raw.get("name")),
            "options": options,
            "cost_cny": text(raw.get("cost_cny")),
            "supplier_stock": text(raw.get("supplier_stock")),
            "image": text(raw.get("image")),
            "barcode": text(raw.get("barcode")),
            "package_dimensions": {k: text(record(raw.get("package_dimensions")).get(k)) for k in PACKAGE_FIELDS},
            "active": raw.get("active") is not False,
            "source_snapshot": deepcopy(record(raw.get("source_snapshot"))),
        })
    return result


def collected_skus(source: dict[str, Any]) -> list[dict[str, Any]]:
    """来源格式只在采集边界转换；无变体的商品也有一个真实销售规格。"""
    rows = source.get("skus") or [{
        "id": "single", "name": source.get("title"), "price": source.get("price"),
        "package_dimensions": {**record(source.get("dimensions")), "weight_kg": source.get("weight_kg")},
    }]
    result = []
    for raw in rows:
        supplier_id = text(raw.get("id"))
        if not supplier_id:
            raise ValueError("来源 SKU 缺少稳定编号，无法建立规格关联")
        options = deepcopy(record(raw.get("options")))
        facts = {
            "name": text(raw.get("name")), "options": options,
            "cost_cny": text(raw.get("price")) if text(source.get("currency")).upper() == "CNY" else "",
            "supplier_stock": text(raw.get("stock")), "image": text(raw.get("image")),
            "barcode": text(raw.get("barcode")),
            "package_dimensions": {k: text(record(raw.get("package_dimensions")).get(k)) for k in PACKAGE_FIELDS},
        }
        # URL 的查询参数不参与身份，刷新跟踪参数不会生成第二套 SKU。
        identity = text(source.get("source_url")).split("?")[0].rstrip("/") + "#" + supplier_id
        result.append({"id": uuid.uuid5(uuid.NAMESPACE_URL, identity).hex,
                       "source_sku_id": supplier_id, **facts, "active": True, "source_snapshot": facts})
    return normalize_product_skus(result)


def merge_collected_skus(existing: Any, source: dict[str, Any]) -> list[dict[str, Any]]:
    previous = {row["id"]: row for row in normalize_product_skus(existing)}
    incoming = collected_skus(source)
    for row in incoming:
        old = previous.pop(row["id"], None)
        if not old:
            continue
        snapshot = record(old.get("source_snapshot"))
        for field in SKU_FACT_FIELDS:
            # 用户编辑过的事实保留；新来源事实仍完整写入 source_snapshot 供核对。
            if field in snapshot and old.get(field) != snapshot.get(field):
                row[field] = deepcopy(old[field])
        row["active"] = old["active"]
    # 来源消失的 SKU 保留身份并停用，已发布关联仍可追溯。
    incoming.extend({**row, "active": False if row["source_sku_id"] else row["active"]} for row in previous.values())
    return incoming


def normalize_draft_skus(value: Any, product_skus: Any, draft_id: str) -> list[dict[str, Any]]:
    facts = {text(row.get("id")): row for row in product_skus if isinstance(row, dict)} if isinstance(product_skus, list) else {}
    result = []
    seen = set()
    for raw in value if isinstance(value, list) else []:
        sku_id = text(raw.get("sku_id"))
        if not sku_id or sku_id in seen:
            raise ValueError("草稿 SKU 引用为空或重复")
        if facts and sku_id not in facts:
            raise ValueError("草稿引用的 SKU 不属于当前商品：" + sku_id)
        seen.add(sku_id)
        result.append({
            "sku_id": sku_id, "selected": raw.get("selected") is True,
            "sku": text(raw.get("sku")) or "SKU-" + sku_fingerprint([draft_id, sku_id])[:20].upper(),
            "stock": text(raw.get("stock")),
            "overrides": deepcopy(record(raw.get("overrides"))),
            "attributes_by_target": deepcopy(record(raw.get("attributes_by_target"))),
            "pricing": deepcopy(record(raw.get("pricing"))),
            "pricing_overrides": deepcopy(record(raw.get("pricing_overrides"))),
            "publications": deepcopy(record(raw.get("publications"))),
        })
    return result


def effective_sku(product_sku: dict[str, Any], draft_sku: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(product_sku)
    for key, value in record(draft_sku.get("overrides")).items():
        if key not in SKU_FACT_FIELDS:
            raise ValueError("不支持覆盖的 SKU 字段：" + key)
        if isinstance(value, dict):
            result[key] = {**record(result.get(key)), **deepcopy(value)}
        else:
            result[key] = deepcopy(value)
    return result


def selected_skus(product: dict[str, Any], draft: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    facts = {row["id"]: row for row in product.get("sku_items", [])}
    result = []
    for row in draft.get("sku_items", []):
        if not row.get("selected"):
            continue
        fact = facts.get(row["sku_id"])
        if not fact or not fact.get("active", True):
            raise ValueError("已选择的 SKU 已停用或不存在：" + row["sku_id"])
        result.append((effective_sku(fact, row), row))
    return result


def retain_sku_publications(previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """普通内容保存不允许覆盖远端事实或重绑已使用的卖家编码。"""
    result = deepcopy(incoming)
    old = {row["sku_id"]: row for row in previous.get("sku_items", [])}
    rows = {row["sku_id"]: row for row in result.get("sku_items", [])}
    for sku_id, row in old.items():
        if row.get("publications") and (sku_id not in rows or rows[sku_id].get("sku") != row.get("sku")):
            raise ValueError("已发布或待确认的 SKU 不能删除或修改卖家编码，请取消勾选或创建新草稿")
    for sku_id, row in rows.items():
        row["publications"] = deepcopy(record(old.get(sku_id, {}).get("publications")))
    return result


__all__ = ["retain_sku_publications", "PACKAGE_FIELDS", "SKU_FACT_FIELDS", "collected_skus", "effective_sku", "merge_collected_skus", "normalize_draft_skus", "normalize_product_skus", "selected_skus", "sku_fingerprint"]

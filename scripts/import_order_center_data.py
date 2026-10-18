"""一次性将 SKU 采购/包裹分配库导入订单级模型；运行时不读取旧格式。

输入应为停服后的 SQLite 备份。只在全新输出目录生成两份新库及核对报告，
不修改源库，不调用任何远端接口。核对后由维护者替换正式数据库。
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import TypeAdapter

from erp_web.schemas.alibaba_orders import PurchaseProgressView
from erp_web.schemas.fulfillment import DomesticParcel
from erp_web.schemas.order_procurement import PurchaseItem, PurchaseRecord, order_line_key
from erp_web.schemas.orders import OrderSnapshot
from erp_web.stores.order_procurement_store import SCHEMA


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def platform(source):
    value = source.get("source_platform", "").strip().lower()
    return {"阿里巴巴": "1688", "淘宝": "taobao", "拼多多": "pinduoduo"}.get(value, value or "other")


def convert_purchases(rows):
    groups = defaultdict(list)
    for row in rows:
        record = json.loads(row["record_json"])
        groups[(row["order_id"], platform(record["source"]), record["purchase_order_number"])].append((row, record))
    records, mapping, requests = [], {}, []
    for (order_id, source_platform, number), group in groups.items():
        group.sort(key=lambda pair: (pair[1]["created_at"], pair[1]["id"]))
        active = [pair for pair in group if pair[1]["status"] == "purchased"]
        selected = active or group
        first = group[0][1]
        items = []
        for row, old in selected:
            source = old["source"]
            offer = source.get("source_offer_id") or ""
            if not offer:
                match = re.search(r"/offer/(\d+)\.html", source["product_url"])
                offer = match[1] if match else ""
            items.append(PurchaseItem(id=old["id"], line_key=old["line_key"],
                line_signature=row["line_signature"], quantity=old["quantity"], source=source,
                product_id=offer, sku=source["source_sku_id"], title=source["specification"]))
        # 旧模型允许作废同一采购单的一部分；新模型只保留整单状态。
        # 已作废部分进入可见历史备注，完整原始记录另有源库和导入报告保留。
        cancelled = [old for _, old in group if old["status"] == "cancelled"]
        notes = [f"旧登记 {old['id']} 已于 {old.get('cancelled_at', '')} 作废："
                 f"{old['source']['specification']}，{old['quantity']} 件。" for old in cancelled]
        warehouses = [old.get("receiving_warehouse") for _, old in selected if old.get("receiving_warehouse")]
        warehouse = warehouses[0] if warehouses and all(v == warehouses[0] for v in warehouses) else None
        if warehouses and warehouse is None:
            notes.append("旧登记的收货地址快照不一致，原快照保存在导入备份，请按实际收货安排核对。")
        record = PurchaseRecord(id=first["id"], request_id=first["request_id"],
            source_platform=source_platform, purchase_order_number=number, items=items,
            created_at=first["created_at"], status="purchased" if active else "cancelled",
            cancelled_at="" if active else max(old.get("cancelled_at", "") for _, old in group),
            receiving_warehouse=warehouse, note="\n".join(notes))
        records.append((order_id, record))
        for row, old in group:
            mapping[old["id"]] = record.id
            requests.append((order_id, old["request_id"], row["request_hash"], record.id))
    return records, mapping, requests


def convert_parcels(parcels, lines, purchases):
    groups = defaultdict(list)
    for parcel in parcels:
        groups[parcel["tracking_number"].strip().upper()].append(parcel)
    result = []
    for bill, parts in groups.items():
        descriptions, images, sources = [], [], []
        carriers = {p.get("carrier", "").strip() for p in parts if p.get("carrier", "").strip()}
        for part in parts:
            line = lines.get(part["line_key"], {})
            purchase = purchases.get(part.get("purchase_record_id"), {})
            source = purchase.get("source", {})
            origin = (platform(source), purchase.get("purchase_order_number", "")) if source else ("", "")
            sources.append(origin)
            label = " · ".join(str(v) for v in [line.get("title"), line.get("sku"), source.get("specification"),
                                  f"{part['quantity']} 件", *origin] if v)
            descriptions.append(label)
            if line.get("image_url"):
                images.append(line["image_url"])
        source_platform, number = sources[0] if all(s == sources[0] for s in sources) else ("", "")
        if len(carriers) > 1:
            descriptions.append("原登记快递公司不一致：" + "、".join(sorted(carriers)))
        descriptions.extend("商品图片：" + url for url in dict.fromkeys(images[1:]))
        result.append(DomesticParcel(id=parts[0]["id"], tracking_number=bill,
            carrier=next(iter(carriers)) if len(carriers) == 1 else "",
            quantity=sum(p["quantity"] for p in parts), description="\n".join(descriptions),
            image_url=images[0] if images else "", source_platform=source_platform,
            purchase_order_number=number).model_dump())
    return result


def convert_progress(rows, mapping):
    groups = defaultdict(list)
    for row in rows:
        groups[mapping[row["record_id"]]].append(row)
    result = []
    for ident, group in groups.items():
        snapshots = [json.loads(row["value"]) for row in group]
        latest = max(snapshots, key=lambda value: value.get("attempted_at", ""))
        value = {k: deepcopy(latest[k]) for k in ("state", "attempted_at", "message", "error", "data")}
        successful = [snapshot["data"] for snapshot in snapshots if snapshot.get("data")]
        if successful:
            value["data"] = deepcopy(max(successful, key=lambda data: data.get("checked_at", "")))
        data = value["data"]
        if data:
            data.pop("product", None)
            data.pop("product_warning", None)
            data["record_id"] = ident
            products = {}
            for snapshot in sorted(snapshots, key=lambda v: (v.get("data") or {}).get("checked_at", "")):
                product = (snapshot.get("data") or {}).get("product")
                if product:
                    key = (product["offer_id"], product["sku_id"], product["spec_id"])
                    products[key] = {**product, "item_id": "", "quantity": None}
            data["products"] = list(products.values())
        if value["state"] in {"conflict", "pending_assignment", "syncing"}:
            value.update(state="pending_review", message="已导入采购快照，请核对独立包裹清单。")
        elif value["state"] == "manual":
            value.update(state="synced", message="已保留手工录入的包裹。")
        TypeAdapter(PurchaseProgressView).validate_python(value)
        result.append((ident, max(r["generation"] for r in group) + 1, 0, dump(value)))
    return result


def copy_database(source, destination):
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(destination) as target:
        original.backup(target)
    destination.chmod(0o600)


def import_databases(source_dir: Path, output_dir: Path):
    source_dir, output_dir = source_dir.resolve(), output_dir.resolve()
    if output_dir.exists():
        raise ValueError("输出目录必须不存在，避免覆盖已导入数据")
    order_name, self_name = "order-notifications.sqlite3", "alibaba-self-purchases.sqlite3"
    if not all((source_dir / name).is_file() for name in (order_name, self_name)):
        raise ValueError("请提供完整的订单库与自动采购库备份目录")
    output_dir.mkdir(mode=0o700, parents=True)
    for name in (order_name, self_name):
        copy_database(source_dir / name, output_dir / name)
    with sqlite3.connect(output_dir / order_name) as conn:
        conn.row_factory = sqlite3.Row
        columns = {r[1] for r in conn.execute("PRAGMA table_info(order_purchases)")}
        if columns != {"id", "order_id", "line_key", "request_id", "request_hash", "line_signature", "quantity", "status", "record_json"}:
            raise ValueError("输入不是本次重构前的采购格式，禁止重复转换")
        rows = list(conn.execute("SELECT * FROM order_purchases"))
        purchases = {r["id"]: json.loads(r["record_json"]) for r in rows}
        records, mapping, requests = convert_purchases(rows)
        progress_rows = list(conn.execute("SELECT * FROM purchase_progress"))
        progress = convert_progress(progress_rows, mapping)
        orders = {r["id"]: OrderSnapshot.model_validate_json(r["snapshot"]) for r in conn.execute("SELECT * FROM orders")}
        before_parcels, after_parcels = 0, 0
        conn.execute("DROP TABLE order_purchases")
        conn.executescript(SCHEMA)
        for order_id, record in records:
            conn.execute("INSERT INTO order_purchases VALUES (?,?,?,?,?,?)", (record.id, order_id,
                record.source_platform, record.purchase_order_number, record.status, record.model_dump_json(exclude={"progress"})))
        conn.executemany("INSERT INTO order_purchase_requests VALUES (?,?,?,?)", requests)
        conn.execute("DELETE FROM purchase_progress")
        conn.executemany("INSERT INTO purchase_progress VALUES (?,?,?,?)", progress)
        for row in list(conn.execute("SELECT * FROM fulfillments")):
            value = json.loads(row["value"])
            if row["claim"] or value.get("operation"):
                raise ValueError("存在未结束的履约操作，请停服并核对操作结果后重新导入")
            lines = {order_line_key(line): line.model_dump() for line in orders[row["id"]].items}
            before_parcels += len(value["parcels"])
            value["parcels"] = convert_parcels(value["parcels"], lines, purchases)
            after_parcels += len(value["parcels"])
            observed = {p["tracking_number"] for p in value["parcels"]}
            for old in progress_rows:
                if next(r["order_id"] for r in rows if r["id"] == old["record_id"]) == row["id"]:
                    resolution = json.loads(old["value"]).get("resolution") or {}
                    observed.update(p["tracking_number"].strip().upper() for p in resolution.get("logistics", []) if p.get("tracking_number"))
            value.update(parcels_confirmed=False, observed_waybills=sorted(observed),
                         manual_submit=False, update_pending=False, editing_until=0)
            for attempt in value.get("attempt_history", []):
                attempt["parcels"] = convert_parcels(attempt.get("parcels", []), lines, purchases)
                attempt["parcels_confirmed"] = False
                attempt["observed_waybills"] = [p["tracking_number"] for p in attempt["parcels"]]
            conn.execute("UPDATE fulfillments SET value=?,revision=revision+1 WHERE id=?", (dump(value), row["id"]))
        conn.commit()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not list(conn.execute("PRAGMA foreign_key_check"))
    remapped = 0
    with sqlite3.connect(output_dir / self_name) as conn:
        for ident, raw in list(conn.execute("SELECT id,result FROM alibaba_self_purchases")):
            if not raw:
                continue
            result = json.loads(raw)
            if result.get("purchase_record_id"):
                result["purchase_record_id"] = mapping[result["purchase_record_id"]]
            if result.get("purchase_record_ids"):
                result["purchase_record_ids"] = list(dict.fromkeys(mapping[key] for key in result["purchase_record_ids"]))
            conn.execute("UPDATE alibaba_self_purchases SET result=? WHERE id=?", (dump(result), ident))
            remapped += 1
        conn.commit()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    report = {"orders": len(orders), "old_purchase_rows": len(rows), "purchase_orders": len(records),
              "old_parcel_rows": before_parcels, "parcels": after_parcels,
              "progress_snapshots": len(progress), "self_purchase_receipts": remapped,
              "record_mapping": mapping, "cancelled_registrations": [v for v in purchases.values() if v["status"] == "cancelled"]}
    report_path = output_dir / "import-report.json"
    report_path.write_text(dump(report) + "\n")
    report_path.chmod(0o600)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = import_databases(args.source_dir, args.output_dir)
    print(dump({key: value for key, value in report.items() if isinstance(value, int)}))

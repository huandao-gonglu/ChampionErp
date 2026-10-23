"""预览或迁移商品资料归属；--apply 前在独立目录保存完整数据库备份。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from erp_web.product_model.source_facts import migrate_source_facts, invalidate_source_changes


def migrate(connection: sqlite3.Connection, *, apply: bool = False) -> dict:
    counts = Counter()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for product_id, raw in connection.execute("SELECT product_id, product_json FROM products").fetchall():
        product = json.loads(raw)
        records = connection.execute("SELECT draft_id, draft_json FROM platform_drafts WHERE product_id = ?", (product_id,)).fetchall()
        drafts = [json.loads(row[1]) for row in records]
        updated, cleaned, actions = migrate_source_facts(product, drafts)
        counts.update(item["action"] for item in actions)
        if updated != product:
            counts["商品更新"] += 1
            if apply:
                connection.execute("UPDATE products SET product_json = ?, source_json = ?, updated_at = ? WHERE product_id = ?",
                    (json.dumps(updated, ensure_ascii=False), json.dumps(updated.get("source", {}), ensure_ascii=False), now, product_id))
        for (draft_id, _), before, after in zip(records, drafts, cleaned, strict=True):
            if before == after:
                continue
            counts["草稿清理"] += 1
            after = invalidate_source_changes({}, updated, after)
            # 保留核价明细和发布历史，仅撤销当前报价的应用状态。
            for row in after.get("sku_items", []):
                if row.get("pricing"):
                    row["pricing"]["applied"] = False
            after["last_precheck"] = {}
            after["last_precheck_target"] = {}
            if apply:
                connection.execute("UPDATE platform_drafts SET draft_json = ?, updated_at = ? WHERE draft_id = ?",
                    (json.dumps(after, ensure_ascii=False), now, draft_id))
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("erp.sqlite3"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    connection = sqlite3.connect(f"{database.as_uri()}?mode={'rw' if args.apply else 'ro'}", uri=True, timeout=30)
    report = {"apply": args.apply}
    try:
        connection.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        if args.apply:
            directory = database.parent / "data" / "audits" / "product-source-facts" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            directory.mkdir(parents=True, mode=0o700)
            backup = directory / database.name
            backup.touch(mode=0o600)
            with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source, sqlite3.connect(backup) as destination:
                source.backup(destination)
            report["backup"] = str(backup)
        report["changes"] = migrate(connection, apply=args.apply)
        if args.apply:
            connection.commit()
            (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()

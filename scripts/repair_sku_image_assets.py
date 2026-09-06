#!/usr/bin/env python3
"""补齐指定草稿所属商品的 SKU 图片；默认预览，--apply 下载并保存。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp_web.context import AppContext, AppPaths
from erp_web.db import ErpDatabase
from erp_web.product_model.sku_image_model import sku_image_asset
from erp_web.product_model.sku_model import selected_skus
from erp_web.services.image_service import materialize_image_values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-id", required=True, help="要修复的草稿编号")
    parser.add_argument("--apply", action="store_true", help="备份后下载并保存图片")
    args = parser.parse_args()
    paths = AppPaths.from_app_dir(Path(__file__).resolve().parents[1])
    if not paths.db_path.is_file():
        raise SystemExit("未找到商品数据库")
    app = AppContext(paths=paths, db=ErpDatabase(paths.db_path))
    try:
        draft = app.db.load_draft_model(args.draft_id)
        if not draft:
            raise SystemExit("草稿不存在")
        product_id = draft["product_id"]
        product = app.products.load_product_from_index(product_id, "")
        referenced = {sku.get("image_asset_id") for sku in product["sku_items"]}
        referenced.update((row.get("overrides") or {}).get("image_asset_id") for row in draft.get("sku_items", []))
        pending = [asset for asset in product["source"]["image_pool"]
                   if asset["id"] in referenced and (not asset.get("path") or not (paths.app_dir / asset["path"]).is_file())]
        print(json.dumps({"草稿": args.draft_id, "商品": product_id, "待下载图片": len(pending), "应用": args.apply}, ensure_ascii=False), flush=True)
        if not args.apply:
            return
        audit_dir = paths.data_dir / "audits" / "sku-image-repair" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        audit_dir.mkdir(parents=True, mode=0o700)
        backup = audit_dir / "before.sqlite3"
        backup.touch(mode=0o600)
        with sqlite3.connect(paths.db_path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        completed = {}
        for index, asset in enumerate(pending):
            # 旧本地路径已失效时只使用已保存的原图地址，不推测其它地址。
            request = dict(asset)
            if request.get("path") and not (paths.app_dir / request["path"]).is_file():
                request.pop("path")
                request["url"] = (request.get("raw") or {}).get("source_url") or request.get("url", "")
            materialized = materialize_image_values(paths.app_dir, [request], product_id, origin=asset.get("origin") or "source")
            if not materialized or materialized[0].get("status") != "ready":
                raise SystemExit(f"图片 {asset['id']} 下载失败，本次未保存商品：{(materialized or [{}])[0].get('note', '')}")
            completed[asset["id"]] = {**asset, **materialized[0], "order": asset.get("order", 0)}
            print(f"已下载 {index + 1}/{len(pending)}", flush=True)
        # 下载期间不持有数据库事务；提交前重读，保留最新的商品编辑和远端事实。
        latest = app.products.load_product_from_index(product_id, "")
        latest["source"]["image_pool"] = [completed.get(asset["id"], asset) for asset in latest["source"]["image_pool"]]
        saved = app.products.save_product_profile(latest)
        current_draft = app.db.load_draft_model(args.draft_id)
        missing = []
        for fact, row in selected_skus(saved, current_draft):
            try:
                asset = sku_image_asset(saved, fact)
                if asset and not (paths.app_dir / asset.get("path", "")).is_file():
                    missing.append(row["sku_id"])
            except ValueError:
                missing.append(row["sku_id"])
        report = {"draft_id": args.draft_id, "product_id": product_id, "downloaded": len(completed),
                  "image_pool_count": len(saved["source"]["image_pool"]), "missing_selected_skus": missing,
                  "backup": str(backup)}
        (audit_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if missing:
            raise SystemExit("仍有 SKU 图片缺失")
    finally:
        app.close()


if __name__ == "__main__":
    main()

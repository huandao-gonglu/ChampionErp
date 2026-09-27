"""补齐已同步商品的买家链接，不改变库存、价格、业务版本和同步时间。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from erp_web.context import AppContext, AppPaths
from erp_web.db import ErpDatabase
from erp_web.marketplaces.online_buyer_links import mercado_buyer_links, yandex_buyer_links
from erp_web.marketplaces.yandex_http import fetch_yandex_offer_mapping
from erp_web.services.online_product_service import account_identity
from erp_web.stores.online_product_store import OnlineProductStore


def backfill(context: AppContext) -> dict[str, int]:
    config = context.config.load_store_config()
    store = OnlineProductStore(context.db)
    counts = {"已更新": 0, "无需更新": 0, "并发跳过": 0, "未取得商品": 0}

    def save(listing, links):
        if listing.buyer_links == links:
            counts["无需更新"] += 1
        elif store.update_buyer_links(listing.id, links, expected_version=listing.version):
            counts["已更新"] += 1
        else:
            counts["并发跳过"] += 1

    account = account_identity("mercadolibre", config)
    for listing in store.listings("mercadolibre", account) if account else []:
        save(listing, mercado_buyer_links(listing.snapshot.get("children", {})))

    account = account_identity("yandex", config)
    listings = store.listings("yandex", account) if account else []
    for offset in range(0, len(listings), 50):
        batch = listings[offset:offset + 50]
        rows = fetch_yandex_offer_mapping(
            config["yandex"]["api_token"], str(config["yandex"]["business_id"]),
            [listing.remote_id for listing in batch],
        )
        by_id = {row.get("offer", {}).get("offerId"): row for row in rows}
        for listing in batch:
            if listing.remote_id not in by_id:
                counts["未取得商品"] += 1
                continue
            save(listing, yandex_buyer_links(by_id[listing.remote_id]))
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=ROOT, help="ERP 数据和配置所在目录")
    args = parser.parse_args()
    paths = AppPaths.from_app_dir(args.app_dir.resolve())
    context = AppContext(paths, ErpDatabase(paths.db_path))
    try:
        print(backfill(context))
    finally:
        context.close()


if __name__ == "__main__":
    main()

"""远端快照、同步进度和修改回执的持久化；不修改采集箱或草稿。"""
from __future__ import annotations

import json
import sqlite3
import time
from urllib.parse import urlsplit
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from erp_web.db import ErpDatabase, utc_now
from erp_web.product_model.online_listing_summary import summarize_yandex_status, yandex_issues
from erp_web.product_model.online_operation_model import UNRESOLVED_STATUSES, blocks_modification, effective_job_status, submission_status
from erp_web.schemas.online_products import BuyerLink, MarketSnapshot, OnlineListing, OnlineStatus, snapshot_version
from erp_web.services.external_request_context import current_task_origin


class OnlineConflict(ValueError):
    def __init__(self, message: str, *, job_id: str | None = None,
                 code: str = "ONLINE_CONFLICT", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.job_id = job_id
        self.code = code
        self.details = details or {}


class OnlineProductStore:
    def __init__(self, db: ErpDatabase):
        self.db = db

    def save(self, listing: OnlineListing, *, lease: dict[str, Any] | None = None, full_snapshot: bool = True) -> OnlineListing:
        if full_snapshot:
            listing.synced_at = utc_now()
            if listing.platform == "yandex":
                listing.sales_checked_at = listing.basic_synced_at = listing.synced_at
            if listing.details_state == "ready" and not listing.errors:
                listing.status_checked_at = datetime.now(timezone.utc).isoformat()
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if lease:
                self._assert_lease(conn, lease)
            self._write_listing(conn, listing)
            conn.commit()
        return self.get(listing.id)

    def save_many(self, listings: list[OnlineListing], *, lease: dict[str, Any] | None = None, full_snapshot: bool = True) -> list[OnlineListing]:
        """按批单事务落库目录或完整详情快照，避免三平台全量同步逐 SKU 触发数百次磁盘提交。"""
        if not listings:
            return []
        now = utc_now()
        status_now = datetime.now(timezone.utc).isoformat()
        for item in listings:
            if full_snapshot:
                item.synced_at = now
                if item.platform == "yandex":
                    item.sales_checked_at = item.basic_synced_at = now
                if item.details_state == "ready" and not item.errors:
                    item.status_checked_at = status_now
        for offset in range(0, len(listings), 500):
            chunk = listings[offset:offset + 500]
            with self.db._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if lease:
                    self._assert_lease(conn, lease)
                for item in chunk:
                    self._write_listing(conn, item)
                conn.commit()
        return listings

    def sync_state_many(self, listing_ids: list[str], state: str, errors: list[str], *, lease: dict[str, Any]) -> None:
        """按批单事务更新详情状态（如目录发现阶段批量标记 pending），保持既有业务快照与版本。"""
        if not listing_ids:
            return
        for offset in range(0, len(listing_ids), 500):
            chunk = listing_ids[offset:offset + 500]
            marks = ",".join("?" for _ in chunk)
            with self.db._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._assert_lease(conn, lease)
                rows = conn.execute(f"SELECT id,snapshot_json FROM online_listings WHERE id IN ({marks})", chunk).fetchall()
                for row in rows:
                    listing = OnlineListing.model_validate_json(row["snapshot_json"])
                    listing.details_state, listing.errors = state, list(errors)
                    conn.execute("UPDATE online_listings SET snapshot_json=? WHERE id=?", (listing.model_dump_json(), row["id"]))
                conn.commit()

    @staticmethod
    def _write_listing(conn, listing):
        listing.version = snapshot_version(listing)
        conn.execute("""INSERT INTO online_listings
            (id,platform,account_id,remote_id,version,snapshot_json,synced_at) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET version=excluded.version,
            snapshot_json=excluded.snapshot_json,synced_at=excluded.synced_at""",
            (listing.id, listing.platform, listing.account_id, listing.remote_id,
             listing.version, listing.model_dump_json(), listing.synced_at))

    @staticmethod
    def _retain_price_permissions(listing, previous):
        prices = {p.id: p for p in previous.prices}
        for price in listing.prices:
            if old := prices.get(price.id):
                price.writable, price.reason = old.writable, old.reason

    @staticmethod
    def _strip_unused_campaign_price_errors(listing, previous):
        """未启用独立价且无历史独立价的店铺，不在 HTTP 200 未返回记录时重复挂载噪音错误。"""
        old_prices = {p.id: p for p in previous.prices}
        for price in listing.prices:
            if price.id == "business" or price.read_error != "平台未返回该 SKU 的店铺商品记录":
                continue
            old = old_prices.get(price.id)
            if not price.writable and (old is None or old.amount is None):
                price.read_error = ""

    @staticmethod
    def _preserve_missing_sales(listing, previous):
        """仅替换本次缺失范围的展示值，旧值始终携带当时的成功读取时间。"""
        last_read = previous.sales_checked_at or previous.synced_at
        prices = {p.id: p for p in previous.prices}
        for price in listing.prices:
            old = prices.get(price.id)
            if price.read_error and old and old.amount is not None and last_read:
                price.amount, price.currency = old.amount, old.currency
                price.cached_at = old.cached_at or old.observed_at or last_read
        stocks = {s.id: s for s in previous.stocks}
        for stock in listing.stocks:
            old = stocks.get(stock.id)
            if stock.read_error and old and old.quantity is not None and last_read:
                stock.quantity = old.quantity
                stock.cached_at = old.cached_at or old.observed_at or last_read
        if listing.platform == "yandex":
            failed_cids = {
                price.id.removeprefix("campaign:")
                for price in listing.prices
                if price.read_error and price.id.startswith("campaign:")
                and not listing.snapshot.get("campaigns", {}).get(price.id.removeprefix("campaign:"))
            }
            if failed_cids:
                old_campaigns = previous.snapshot.get("campaigns", {})
                old_markets = {market.id: market for market in previous.markets}
                restored = False
                for cid in failed_cids:
                    old_camp = old_campaigns.get(cid)
                    if old_camp:
                        listing.snapshot.setdefault("campaigns", {})[cid] = old_camp
                        listing.platform_issues.extend(yandex_issues(old_camp, source="campaign"))
                        restored = True
                    old_market = old_markets.get(cid)
                    if old_market:
                        for market in listing.markets:
                            if market.id == cid:
                                market.raw_status = old_market.raw_status
                                market.raw_sub_status = list(old_market.raw_sub_status)
                                if market.price is None and old_market.price is not None:
                                    market.price, market.currency = old_market.price, old_market.currency
                                restored = True
                    for stock in listing.stocks:
                        if stock.campaign_id == cid and (old_stock := stocks.get(stock.id)):
                            stock.raw_status = old_stock.raw_status
                            stock.sale_state = old_stock.sale_state
                            restored = True
                if restored:
                    summarize_yandex_status(listing)

    def _merge_basic_in_conn(self, conn, listing: OnlineListing, existing_row, now: str, status_now: str) -> OnlineListing:
        if existing_row:
            previous = OnlineListing.model_validate_json(existing_row["snapshot_json"])
            if previous.details_state == "ready" and previous.synced_at:
                self._retain_price_permissions(listing, previous)
            self._strip_unused_campaign_price_errors(listing, previous)
            self._preserve_missing_sales(listing, previous)
            listing.synced_at = previous.synced_at
            listing.snapshot = {**previous.snapshot, **listing.snapshot}
            if previous.details_state == "ready" and previous.synced_at:
                listing.details_state = "ready"
                for operation in ("price", "stock", "content"):
                    if operation in previous.capabilities:
                        listing.capabilities[operation] = previous.capabilities[operation]
            previous_category = previous.snapshot.get("offer", {}).get("marketCategoryId") or previous.snapshot.get("mapping", {}).get("marketCategoryId")
            category = listing.snapshot.get("offer", {}).get("marketCategoryId") or listing.snapshot.get("mapping", {}).get("marketCategoryId")
            if category == previous_category:
                names = {str(a.get("id")): a.get("name") for a in previous.content.get("attributes", []) if a.get("name")}
                for attribute in listing.content.get("attributes", []):
                    if name := names.get(str(attribute.get("id"))):
                        attribute["name"] = name
        listing.sales_checked_at = listing.basic_synced_at = now
        listing.status_checked_at = status_now
        self._write_listing(conn, listing)
        return listing

    def save_basic(self, listing: OnlineListing, *, lease: dict[str, Any]) -> OnlineListing:
        """合并列表读取与已有完整详情，独立保存销售数据检查时间。"""
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_lease(conn, lease)
            row = conn.execute("SELECT snapshot_json FROM online_listings WHERE id=?", (listing.id,)).fetchone()
            self._merge_basic_in_conn(conn, listing, row, utc_now(), datetime.now(timezone.utc).isoformat())
            conn.commit()
        return self.get(listing.id)

    def save_basic_many(self, listings: list[OnlineListing], *, lease: dict[str, Any]) -> list[OnlineListing]:
        """按批单事务合并基础同步快照，避免逐 SKU 提交触发数百次磁盘同步。"""
        if not listings:
            return []
        saved: list[OnlineListing] = []
        now = utc_now()
        status_now = datetime.now(timezone.utc).isoformat()
        for offset in range(0, len(listings), 500):
            chunk = listings[offset:offset + 500]
            ids = [row.id for row in chunk]
            marks = ",".join("?" for _ in ids)
            with self.db._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._assert_lease(conn, lease)
                existing = {
                    row["id"]: row
                    for row in conn.execute(f"SELECT id,snapshot_json FROM online_listings WHERE id IN ({marks})", ids).fetchall()
                }
                for item in chunk:
                    merged = self._merge_basic_in_conn(conn, item, existing.get(item.id), now, status_now)
                    saved.append(merged)
                conn.commit()
        return saved

    def _merge_hot_in_conn(self, conn, hot: OnlineListing, existing_row, now: str, status_now: str) -> OnlineListing | None:
        if existing_row is None:
            return None
        if hot.platform != "yandex":
            # Ozon / Mercado Libre 热刷跳过目录翻页，直接拉取最新完整商品快照；保留本地停售意图。
            hot.desired_sale_state = existing_row["desired_sale_state"]
            hot.details_state = "ready"
            hot.synced_at = now
            hot.sales_checked_at = now
            if not hot.errors:
                hot.status_checked_at = status_now
            self._write_listing(conn, hot)
            return hot
        previous = OnlineListing.model_validate_json(existing_row["snapshot_json"])
        previous.desired_sale_state = existing_row["desired_sale_state"]
        has_fresh_campaign = (
            bool(hot.snapshot.get("campaign"))
            or any(bool(camp) for camp in hot.snapshot.get("campaigns", {}).values())
        )
        has_fresh_sales = (
            has_fresh_campaign
            or any(p.amount is not None for p in hot.prices)
            or any(s.quantity is not None for s in hot.stocks)
        )
        self._retain_price_permissions(hot, previous)
        self._strip_unused_campaign_price_errors(hot, previous)
        self._preserve_missing_sales(hot, previous)
        if not has_fresh_sales:
            previous.prices = hot.prices
            previous.sales_checked_at = now
            self._write_listing(conn, previous)
            return previous
        previous.prices = hot.prices
        previous.stocks = hot.stocks
        if hot.markets:
            previous.markets = hot.markets
        for key in ("warehouse_catalog", "campaigns", "campaign", "hidden_campaigns", "hidden"):
            if key in hot.snapshot:
                previous.snapshot[key] = hot.snapshot[key]
        if hot.snapshot.get("default_price"):
            previous.snapshot["default_price"] = hot.snapshot["default_price"]
        card_issues = [issue for issue in previous.platform_issues if issue.source != "campaign"]
        campaign_issues = [issue for issue in hot.platform_issues if issue.source == "campaign"]
        previous.platform_issues = card_issues + campaign_issues
        summarize_yandex_status(previous)
        previous.sales_checked_at = now
        previous.status_checked_at = status_now
        self._write_listing(conn, previous)
        return previous

    def save_hot(self, hot: OnlineListing, *, lease: dict[str, Any]) -> OnlineListing | None:
        """热数据快刷：只更新价格、可售库存、销售/隐藏状态与店铺级反馈，保留既有目录图文、卡片反馈与详情状态。"""
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_lease(conn, lease)
            row = conn.execute("SELECT snapshot_json,desired_sale_state FROM online_listings WHERE id=?", (hot.id,)).fetchone()
            merged = self._merge_hot_in_conn(conn, hot, row, utc_now(), datetime.now(timezone.utc).isoformat())
            if merged is None:
                return None
            conn.commit()
        return self.get(hot.id)

    def save_hot_many(self, hots: list[OnlineListing], *, lease: dict[str, Any]) -> dict[str, OnlineListing]:
        """按批单事务落库热数据快刷，保持短锁同时将数百次逐条提交压为 1 次提交。"""
        if not hots:
            return {}
        saved: dict[str, OnlineListing] = {}
        now = utc_now()
        status_now = datetime.now(timezone.utc).isoformat()
        for offset in range(0, len(hots), 500):
            chunk = hots[offset:offset + 500]
            ids = [row.id for row in chunk]
            marks = ",".join("?" for _ in ids)
            with self.db._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._assert_lease(conn, lease)
                existing = {
                    row["id"]: row
                    for row in conn.execute(
                        f"SELECT id,snapshot_json,desired_sale_state FROM online_listings WHERE id IN ({marks})",
                        ids,
                    ).fetchall()
                }
                for item in chunk:
                    merged = self._merge_hot_in_conn(conn, item, existing.get(item.id), now, status_now)
                    if merged is not None:
                        saved[merged.id] = merged
                conn.commit()
        return saved

    def update_detail(self, before: OnlineListing, detail: OnlineListing) -> OnlineListing:
        """单件详情读取后以版本及读取时间核对并发，网络请求不占用写事务。"""
        if (detail.id, detail.platform, detail.account_id, detail.remote_id) != (before.id, before.platform, before.account_id, before.remote_id):
            raise ValueError("详情响应商品身份不一致，已保留原记录")
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_status_refreshable(conn, before)
            row = conn.execute("SELECT snapshot_json FROM online_listings WHERE id=?", (before.id,)).fetchone()
            if row is None:
                raise OnlineConflict("商品记录已变化，请重新打开详情")
            current = OnlineListing.model_validate_json(row[0])
            if (current.version, current.synced_at, current.basic_synced_at, current.status_checked_at, current.sales_checked_at, current.prices, current.stocks) != (before.version, before.synced_at, before.basic_synced_at, before.status_checked_at, before.sales_checked_at, before.prices, before.stocks):
                raise OnlineConflict("查询期间商品已更新，请重新打开详情")
            if detail.platform == "yandex":
                self._preserve_missing_sales(detail, current)
            detail.desired_sale_state = current.desired_sale_state
            detail.details_state = "ready"
            detail.synced_at = utc_now()
            detail.basic_synced_at = detail.synced_at if detail.platform == "yandex" else current.basic_synced_at
            if detail.platform == "yandex":
                detail.sales_checked_at = detail.synced_at
            detail.status_checked_at = datetime.now(timezone.utc).isoformat()
            self._write_listing(conn, detail)
            conn.commit()
        return self.get(before.id)

    def sales_targets(self, account, ids):
        marks = ",".join("?" for _ in ids)
        with self.db._connect() as conn:
            rows = conn.execute(f"SELECT snapshot_json FROM online_listings WHERE platform='yandex' AND account_id=? AND id IN ({marks})",
                                (account, *ids)).fetchall()
            if len(rows) != len(ids):
                raise ValueError("所选商品不属于当前在线店铺")
            records = [OnlineListing.model_validate_json(row[0]) for row in rows]
            if any(row["status"] in ("queued", "running") for row in self._resource_jobs(conn, "yandex", account, "*", "status")):
                raise OnlineConflict("当前店铺有同步或修改进行中，列表将保留已保存的数据")
        return records

    def update_sales(self, before, sales):
        """仅合并售价和库存，读取期间的详情、状态或修改回执优先。"""
        if (before.id, before.remote_id, before.account_id, before.platform) != (sales.id, sales.remote_id, sales.account_id, sales.platform):
            raise ValueError("销售数据与当前商品身份不一致")
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_status_refreshable(conn, before)
            row = conn.execute("SELECT snapshot_json FROM online_listings WHERE id=?", (before.id,)).fetchone()
            if not row:
                return
            current = OnlineListing.model_validate_json(row[0])
            if (current.version, current.basic_synced_at, current.status_checked_at, current.sales_checked_at) != (
                    before.version, before.basic_synced_at, before.status_checked_at, before.sales_checked_at):
                return
            # 同值库存写回也有新回执时间，旧的网络响应不得倒灌。
            if current.stocks != before.stocks or current.prices != before.prices:
                return
            has_fresh_campaign = (
                bool(sales.snapshot.get("campaign"))
                or any(bool(camp) for camp in sales.snapshot.get("campaigns", {}).values())
            )
            has_fresh_sales = (
                has_fresh_campaign
                or any(p.amount is not None for p in sales.prices)
                or any(s.quantity is not None for s in sales.stocks)
            )
            self._retain_price_permissions(sales, current)
            self._strip_unused_campaign_price_errors(sales, current)
            self._preserve_missing_sales(sales, current)
            if not has_fresh_sales:
                # 平台对全部店铺、价格和仓库均未返回该 SKU（如已在平台删除或目录已变动）：
                # 保留原有库存结构与销售状态，仅在基础价上保留读取异常与旧值时间，避免把既有状态降级为“待核实”。
                current.prices = sales.prices
                current.sales_checked_at = utc_now()
                self._write_listing(conn, current)
                conn.commit()
                return
            old_stocks = {stock.id: stock for stock in current.stocks}
            known_markets = {market.id for market in current.markets}
            fresh_campaigns = sales.snapshot.get("campaigns", {})
            merged_stocks = []
            for stock in sales.stocks:
                old = old_stocks.get(stock.id)
                if old and old.campaign_id == stock.campaign_id:
                    stock.raw_status, stock.sale_state = old.raw_status, old.sale_state
                    merged_stocks.append(stock)
                elif not stock.campaign_id or stock.campaign_id in known_markets or stock.quantity is not None or fresh_campaigns.get(stock.campaign_id):
                    stock.raw_status, stock.sale_state = "", "unknown"
                    merged_stocks.append(stock)
            if merged_stocks:
                current.stocks = merged_stocks
            current.prices = sales.prices
            prices = {p.id: p for p in sales.prices}
            for market in current.markets:
                price = prices.get("campaign:" + market.id)
                if price:
                    market.price, market.currency = price.amount, price.currency
            current.sales_checked_at = utc_now()
            self._write_listing(conn, current)
            conn.commit()

    def get(self, listing_id: str) -> OnlineListing:
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM online_listings WHERE id=?", (listing_id,)).fetchone()
        if row is None:
            raise ValueError("在线商品不存在，请先同步店铺")
        result = OnlineListing.model_validate_json(row["snapshot_json"])
        result.desired_sale_state = row["desired_sale_state"]
        return result

    def sync_state(self, listing_id: str, state: str, errors: list[str], *, lease: dict[str, Any]) -> None:
        """详情进度独立于业务快照；读取失败不覆盖旧价格、库存、版本和同步时间。"""
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_lease(conn, lease)
            conn.execute("""UPDATE online_listings SET snapshot_json=json_set(
                snapshot_json,'$.details_state',?,'$.errors',json(?)) WHERE id=?""",
                (state, json.dumps(errors, ensure_ascii=False), listing_id))
            conn.commit()

    def prune_missing_ids(self, platform: str, account: str, listing_ids: list[str], *, lease: dict[str, Any]) -> list[str]:
        """全量目录完整发现或热刷幽灵复核后清理已移出目录的旧快照；存在未结束任务时保留，保护回读与人工结单。"""
        if not listing_ids:
            return []
        removed: list[str] = []
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_lease(conn, lease)
            for listing_id in listing_ids:
                unresolved = [
                    row for row in self._resource_jobs(conn, platform, account, listing_id, "sync")
                    if row["id"] != lease["id"]
                ]
                if unresolved:
                    continue
                cursor = conn.execute(
                    "DELETE FROM online_listings WHERE id=? AND platform=? AND account_id=?",
                    (listing_id, platform, account),
                )
                if cursor.rowcount:
                    removed.append(listing_id)
            conn.commit()
        return removed

    def prune_missing(self, platform: str, account: str, listing_ids: list[str], *, lease: dict[str, Any]) -> int:
        return len(self.prune_missing_ids(platform, account, listing_ids, lease=lease))

    @staticmethod
    def _assert_status_refreshable(conn, listing: OnlineListing):
        rows = OnlineProductStore._resource_jobs(conn, listing.platform, listing.account_id, listing.id, "status")
        if any(row["status"] in ("queued", "running") for row in rows):
            raise OnlineConflict("该商品正在同步或执行修改，请完成后再刷新状态")

    def assert_status_refreshable(self, listing: OnlineListing) -> None:
        with self.db._connect() as conn:
            self._assert_status_refreshable(conn, listing)

    def update_status(self, before: OnlineListing, status: OnlineStatus) -> OnlineListing:
        """短事务内核对版本后合并状态；平台等待在事务外，完整同步时间不变。"""
        if status.remote_id != before.remote_id:
            raise ValueError("刷新结果与目标商品身份不一致")
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_status_refreshable(conn, before)
            row = conn.execute("SELECT snapshot_json FROM online_listings WHERE id=?", (before.id,)).fetchone()
            if row is None:
                raise OnlineConflict("商品记录已变化，请重新打开商品")
            current = OnlineListing.model_validate_json(row[0])
            if (current.version, current.synced_at, current.status_checked_at) != (before.version, before.synced_at, before.status_checked_at):
                raise OnlineConflict("查询期间商品数据已更新，请重新刷新状态")
            markets = {item.id: item for item in status.markets}
            allowed = {item.id for item in current.markets}
            if current.platform == 'yandex':
                allowed |= {warehouse.campaign_id for warehouse in status.warehouses}
            if len(markets) != len(status.markets) or set(markets) - allowed:
                raise OnlineConflict("商品市场关系已变化，请同步店铺商品")
            current.raw_status = status.raw_status
            current.raw_sub_status = status.raw_sub_status
            current.platform_issues = status.platform_issues
            current.sale_state = status.sale_state
            if status.archived is not None:
                current.archived = status.archived
                if current.platform == "yandex":
                    current.snapshot.setdefault("offer", {})["archived"] = status.archived
            for market in current.markets:
                if market.id in markets:
                    market.raw_status = markets[market.id].raw_status
                    market.raw_sub_status = markets[market.id].raw_sub_status
            if current.platform == 'yandex' and status.warehouses:
                current.snapshot['warehouse_catalog'] = [warehouse.model_dump() for warehouse in status.warehouses]
                current.snapshot['hidden_campaigns'] = status.hidden_campaigns
                current.snapshot['hidden'] = status.hidden_campaigns.get(current.account_id.split(':', 1)[1], False)
                current.markets = [next((market for market in current.markets if market.id == item.id),
                    MarketSnapshot(id=item.id, site_id=item.id, seller_id=current.account_id.split(':', 1)[0], name=item.name))
                    for item in status.markets]
                for market in current.markets:
                    market.raw_status, market.raw_sub_status = markets[market.id].raw_status, markets[market.id].raw_sub_status
                for stock in current.stocks:
                    warehouse = next((warehouse for warehouse in status.warehouses if warehouse.id == stock.id), None)
                    if warehouse:
                        stock.campaign_id = warehouse.campaign_id
                        market = markets.get(stock.campaign_id)
                        stock.raw_status = market.raw_status if market else ''
                        stock.sale_state = ('paused' if status.hidden_campaigns.get(stock.campaign_id)
                                            else 'active' if stock.raw_status == 'PUBLISHED' and not current.archived else 'unknown')
            current.status_checked_at = datetime.now(timezone.utc).isoformat()
            current.version = snapshot_version(current)
            conn.execute("UPDATE online_listings SET version=?,snapshot_json=? WHERE id=?",
                (current.version, current.model_dump_json(), before.id))
            conn.commit()
        return self.get(before.id)

    def update_buyer_links(self, listing_id: str, links: list[BuyerLink], *, expected_version: str) -> bool:
        """补齐导航元数据，保留业务版本和原同步时间，拒绝覆盖已变化的商品。"""
        encoded = json.dumps([link.model_dump(mode="json") for link in links], ensure_ascii=False)
        with self.db._connect() as conn:
            cursor = conn.execute(
                "UPDATE online_listings SET snapshot_json=json_set(snapshot_json,'$.buyer_links',json(?)) WHERE id=? AND version=?",
                (encoded, listing_id, expected_version),
            )
            conn.commit()
        return cursor.rowcount == 1

    def listings(self, platform: str, account: str, *, list_only: bool = False) -> list[OnlineListing]:
        # 组合身份、旧归档事实及仓库状态来自原始快照，只投影列表实际使用的路径。
        projection = """json_set(json_remove(snapshot_json, '$.content', '$.snapshot',
                     '$.capabilities.content', '$.capabilities.stock', '$.capabilities.price'), '$.snapshot',
            json_patch('{}', json_object(
                'offer', json_object('groupId', snapshot_json -> '$.snapshot.offer.groupId',
                                     'archived', snapshot_json -> '$.snapshot.offer.archived'),
                'user_product', json_object('family_id', snapshot_json -> '$.snapshot.user_product.family_id'),
                'warehouse_catalog', snapshot_json -> '$.snapshot.warehouse_catalog',
                'hidden_campaigns', snapshot_json -> '$.snapshot.hidden_campaigns',
                'hidden', snapshot_json -> '$.snapshot.hidden'
            )))""" if list_only else "snapshot_json"
        with self.db._connect() as conn:
            rows = conn.execute(f"SELECT {projection} AS snapshot_json,desired_sale_state FROM online_listings WHERE platform=? AND account_id=? ORDER BY synced_at DESC,id", (platform, account)).fetchall()
        return [OnlineListing.model_validate({**json.loads(row["snapshot_json"]), "desired_sale_state": row["desired_sale_state"]}) for row in rows]

    def thumbnails(self, platform: str, account: str, remote_ids: set[str]) -> dict[str, str]:
        """按完整账号和远端商品身份批量读取平台缩略图；不使用本地资产或采购图片。"""
        ids = sorted(remote_id for remote_id in remote_ids if remote_id)
        result = {}
        if not ids or not account:
            return result
        with self.db._connect() as conn:
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                marks = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"""SELECT remote_id,json_extract(snapshot_json,'$.thumbnail') AS thumbnail
                        FROM online_listings WHERE platform=? AND account_id=?
                        AND remote_id IN ({marks})""", (platform, account, *batch),
                ).fetchall()
                for row in rows:
                    url = str(row["thumbnail"] or "").strip()
                    try:
                        parsed = urlsplit(url)
                        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                            result[row["remote_id"]] = url
                    except ValueError:
                        continue
        return result

    def sale_intent(self, listing_id: str, state: str, *, lease: dict[str, Any]) -> None:
        if lease['platform'] == 'yandex' and lease['request'].get('scope_id', '').startswith('campaign:'):
            # Yandex 按店铺隐藏；单仓操作不写入全局停售意图，结果由隐藏状态回读保存。
            return
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_lease(conn, lease)
            newer = conn.execute("""SELECT 1 FROM online_jobs WHERE target_id=? AND operation='sale_state'
                AND dispatched=1 AND created_at>(SELECT created_at FROM online_jobs WHERE id=?) LIMIT 1""",
                (listing_id, lease["id"])).fetchone()
            if newer:
                return
            conn.execute("UPDATE online_listings SET desired_sale_state=? WHERE id=?", (state, listing_id))
            conn.commit()

    def archive_state(self, listing_id: str, archived: bool, *, lease: dict[str, Any]) -> None:
        """平台逐项回执已确认归档结果，更新该账号各店铺的目录归属，不修改库存或隐藏意图。"""
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_lease(conn, lease)
            target = conn.execute("SELECT * FROM online_listings WHERE id=? AND platform='yandex'", (listing_id,)).fetchone()
            if target is None:
                raise ValueError("归档目标不存在或平台不支持")
            prefix = target["account_id"].split(":", 1)[0] + ":"
            rows = conn.execute("SELECT * FROM online_listings WHERE platform='yandex' AND substr(account_id,1,?)=? AND remote_id=?",
                                (len(prefix), prefix, target["remote_id"])).fetchall()
            for row in rows:
                listing = OnlineListing.model_validate_json(row["snapshot_json"])
                listing.archived = archived
                listing.snapshot.setdefault("offer", {})["archived"] = archived
                # 恢复归档不保证已恢复销售；原隐藏设置与真实库存保持不变。
                if listing.sale_state == "active":
                    listing.sale_state = "unknown"
                listing.version = snapshot_version(listing)
                conn.execute("UPDATE online_listings SET snapshot_json=?,version=? WHERE id=?",
                             (listing.model_dump_json(), listing.version, listing.id))
            conn.commit()

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["result"] = json.loads(result.pop("result_json"))
        result["status"] = effective_job_status(result)
        result.pop("owner", None)
        result.pop("lease_until", None)
        return result

    def job(self, job_id: str) -> dict[str, Any]:
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM online_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("操作记录不存在")
        return self._job(row)

    def jobs(self, platform: str, account: str) -> list[dict[str, Any]]:
        with self.db._connect() as conn:
            rows = conn.execute("SELECT * FROM online_jobs WHERE platform=? AND account_id=? ORDER BY created_at DESC LIMIT 100", (platform, account)).fetchall()
        return [self._job(row) for row in rows]

    def list_page_jobs(self, platform: str, account: str, latest_sync_id: str) -> list[dict[str, Any]]:
        """列表只显示执行中的操作和最近同步的计数，逐项回执按任务 ID 读取。"""
        with self.db._connect() as conn:
            rows = conn.execute("""SELECT id,operation,platform,status,target_id,created_at,updated_at,
                json_extract(request_json,'$.catalog') AS catalog,
                json_extract(request_json,'$.mode') AS mode,
                json_extract(request_json,'$.source') AS source,
                CASE WHEN json_type(request_json,'$.ids')='array' AND COALESCE(json_extract(request_json,'$.source'),'')!='publish'
                     THEN json_array_length(request_json,'$.ids') END AS retry_count,
                CASE WHEN json_type(request_json,'$.ids')='array' AND json_extract(request_json,'$.source')='publish'
                     THEN json_array_length(request_json,'$.ids') END AS ingest_count,
                json_extract(result_json,'$.phase') AS phase,
                json_extract(result_json,'$.data_scope') AS data_scope,
                json_extract(result_json,'$.discovered') AS discovered,
                json_extract(result_json,'$.discovery_complete') AS discovery_complete,
                json_extract(result_json,'$.completed') AS completed,
                json_extract(result_json,'$.created') AS created,
                json_extract(result_json,'$.updated') AS updated,
                json_extract(result_json,'$.removed') AS removed,
                json_extract(result_json,'$.moved') AS moved,
                json_extract(result_json,'$.failed') AS failed,
                json_extract(result_json,'$.error') AS error
                FROM online_jobs WHERE platform=? AND account_id=?
                AND (status IN ('queued','running') OR id=?) ORDER BY created_at DESC,id DESC""",
                (platform, account, latest_sync_id)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["request"] = {key: value for key in ("catalog", "mode", "source", "retry_count", "ingest_count") if (value := item.pop(key)) is not None}
            result = {key: value for key in ("phase", "data_scope", "discovered", "discovery_complete", "completed", "created", "updated", "removed", "moved", "failed", "error")
                      if (value := item.pop(key)) is not None}
            if "discovery_complete" in result:
                result["discovery_complete"] = bool(result["discovery_complete"])
            items.append({**item, "result": result})
        return items

    def latest_sync_summary(self, platform: str, account: str, *, catalog: str = "current") -> dict[str, Any] | None:
        """查询最新同步的固定进度字段，避免为商品读取加载历史任务及逐项回执。"""
        with self.db._connect() as conn:
            row = conn.execute("""SELECT id,status,updated_at,
                json_extract(result_json,'$.discovered') AS discovered,
                json_extract(result_json,'$.completed') AS completed,
                json_extract(result_json,'$.failed') AS failed,
                json_extract(result_json,'$.discovery_complete') AS discovery_complete,
                COALESCE(json_extract(result_json,'$.error_code'),'') AS error_code
                FROM online_jobs WHERE platform=? AND account_id=? AND operation='sync'
                AND COALESCE(json_extract(request_json,'$.source'),'')!='publish'
                AND (?='all' OR COALESCE(json_extract(request_json,'$.catalog'),'all') IN (?, 'all'))
                ORDER BY created_at DESC,id DESC LIMIT 1""", (platform, account, catalog, catalog)).fetchone()
        return dict(row) if row is not None else None

    def idempotent_job(self, key: str) -> dict[str, Any] | None:
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM online_jobs WHERE idempotency_key=?", (key,)).fetchone()
        return self._job(row) if row else None

    @staticmethod
    def _resource_jobs(conn, platform: str, account: str, target: str, operation: str):
        """归档为 business 级操作，同一 SKU 在其他店铺的执行也属于同一资源。"""
        prefix = account.split(":", 1)[0] + ":"
        return conn.execute("""SELECT j.* FROM online_jobs j LEFT JOIN online_listings l ON l.id=j.target_id
            WHERE j.platform=? AND j.status IN ('queued','running','submitted','waiting_confirmation','outcome_unknown','needs_attention')
            AND ((j.account_id=? AND (?='*' OR j.target_id IN (?, '*')))
                OR (?='yandex' AND substr(j.account_id,1,?)=? AND (j.operation='archive' OR ?='archive')
                    AND (?='*' OR j.target_id='*' OR l.remote_id=(SELECT remote_id FROM online_listings WHERE id=?))))
            ORDER BY j.created_at""", (platform, account, target, target, platform, len(prefix), prefix, operation, target, target)).fetchall()

    def enqueue(self, platform: str, account: str, operation: str, target: str, request: dict[str, Any], key: str) -> dict[str, Any]:
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = self._enqueue(conn, platform, account, operation, target, request, key)
            conn.commit()
        return job

    def _enqueue(self, conn, platform, account, operation, target, request, key, result=None):
        facts = json.dumps(request, ensure_ascii=False, sort_keys=True)
        now, job_id = utc_now(), uuid4().hex
        old = conn.execute("SELECT * FROM online_jobs WHERE idempotency_key=?", (key,)).fetchone()
        if old:
            if (old["platform"], old["account_id"], old["operation"], old["target_id"], old["request_json"]) != (platform, account, operation, target, facts):
                raise OnlineConflict("重复提交键对应不同操作，请重新确认")
            return self._job(old)
        if operation != "sync":
            current = conn.execute("SELECT version FROM online_listings WHERE id=?", (target,)).fetchone()
            if request.get("version") and (not current or current["version"] != request["version"]):
                raise OnlineConflict("商品快照已更新，请重新读取目标字段和版本后提交，原修改未入队", code="ONLINE_VERSION_STALE")
        # 明确受理的修改不占用商品；异步处理中/未知结果只保护相关变更。
        rows = self._resource_jobs(conn, platform, account, target, operation)
        jobs = [self._job(row) for row in rows]
        conflict = next((job for job in jobs
                         if (job["status"] in ("queued", "running") if operation == "sync"
                              else blocks_modification(job, request))), None)
        if conflict:
            if conflict["operation"] == "sync":
                progress = conflict["result"]
                count = int(progress.get("completed", 0)) + int(progress.get("failed", 0))
                raise OnlineConflict(f"当前店铺正在同步，已处理 {count} 件；请查看同步进度，无需重复启动", job_id=conflict["id"])
            if operation == "sync":
                raise OnlineConflict("当前店铺有排队或执行中的商品修改，请完成后再同步", job_id=conflict["id"])
            raise OnlineConflict("该商品正在执行修改，或相关变更仍在平台处理/结果未知，请在任务队列查看对应任务", job_id=conflict["id"])
        conn.execute("INSERT INTO online_jobs(id,platform,account_id,operation,target_id,status,idempotency_key,request_json,result_json,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?,?,?)",
                     (job_id, platform, account, operation, target, key, facts, json.dumps({"task_origin": current_task_origin(), **(result or {})}, ensure_ascii=False), now, now))
        return self._job(conn.execute("SELECT * FROM online_jobs WHERE id=?", (job_id,)).fetchone())

    def batch_jobs(self, batch_id: str) -> list[dict[str, Any]]:
        prefix = "batch:" + batch_id + ":"
        with self.db._connect() as conn:
            rows = conn.execute("SELECT * FROM online_jobs WHERE idempotency_key>=? AND idempotency_key<? ORDER BY idempotency_key",
                                (prefix, prefix + "~")).fetchall()
        return [self._job(row) for row in rows]

    def enqueue_batch(self, platform, account, requests, *, batch_id, fingerprint, limit):
        jobs = []
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prefix = "batch:" + batch_id + ":"
            existing = conn.execute("SELECT * FROM online_jobs WHERE idempotency_key>=? AND idempotency_key<? ORDER BY idempotency_key",
                                    (prefix, prefix + "~")).fetchall()
            if existing:
                if len(existing) != len(requests) or any(json.loads(row["result_json"]).get("batch_fingerprint") != fingerprint for row in existing):
                    raise OnlineConflict("批量提交键对应不同的商品或修改，请重新确认")
                return [self._job(row) for row in existing]
            for index, request in enumerate(requests):
                jobs.append(self._enqueue(conn, platform, account, request["operation"], request["listing_id"], request,
                    f"batch:{batch_id}:{index:05d}", {"batch_id": batch_id, "batch_fingerprint": fingerprint, "batch_limit": limit}))
            conn.commit()
        return jobs

    def dispatch_batch(self, entries):
        """一次远端请求前原子保存全部写前日志；不让部分 SKU 在崩溃后被重放。"""
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for job, listing, result in entries:
                self._assert_lease(conn, job)
                row = conn.execute("SELECT version FROM online_listings WHERE id=?", (listing.id,)).fetchone()
                if not row or row["version"] != job["request"]["version"]:
                    raise OnlineConflict("批量执行前商品版本已变化，请重新确认")
                result["submitted_at"] = utc_now()
                conn.execute("UPDATE online_jobs SET dispatched=1,result_json=?,lease_until=?,updated_at=? WHERE id=?",
                             (json.dumps(result, ensure_ascii=False), time.time()+900, utc_now(), job["id"]))
                if job["operation"] == "sale_state" and job["request"]["changes"]["state"] == "paused":
                    conn.execute("UPDATE online_listings SET desired_sale_state='paused' WHERE id=?", (listing.id,))
            conn.commit()

    def claim(self, owner: str) -> dict[str, Any] | None:
        token = owner + ":" + uuid4().hex
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # 未发出的任务可恢复；查询中断不撤销已保存的受理回执，已发出的修改永不重放。
            expired = conn.execute("SELECT * FROM online_jobs WHERE status='running' AND lease_until<?", (time.time(),)).fetchall()
            for stale in expired:
                result = json.loads(stale["result_json"])
                status = submission_status(stale["platform"], stale["operation"], result.get("receipt"),
                    model=result.get("before", {}).get("model", ""), target_id=result.get("receipt_target", ""), scope_id=result.get("scope_id", "")) if stale["dispatched"] else "queued"
                if stale["operation"] == "archive" and status == "confirmed":
                    # 批量回执与本地各店铺目录更新可能在不同事务，中断时仍需回读补齐投影。
                    status = "outcome_unknown"
                conn.execute("UPDATE online_jobs SET status=?,owner='',lease_until=0,updated_at=? WHERE id=?",
                             (status, utc_now(), stale["id"]))
            row = conn.execute("""SELECT * FROM online_jobs WHERE status='queued'
                ORDER BY created_at LIMIT 1""").fetchone()
            if row is None:
                conn.commit()
                return None
            metadata = json.loads(row["result_json"])
            rows = [row]
            if metadata.get("batch_id"):
                rows = conn.execute("""SELECT * FROM online_jobs WHERE status='queued' AND platform=? AND account_id=?
                    AND operation=? AND json_extract(result_json,'$.batch_id')=? ORDER BY idempotency_key LIMIT ?""",
                    (row["platform"], row["account_id"], row["operation"], metadata["batch_id"], metadata["batch_limit"])).fetchall()
            for claimed in rows:
                conn.execute("UPDATE online_jobs SET status='running',owner=?,lease_until=?,updated_at=? WHERE id=?", (token, time.time()+900, utc_now(), claimed["id"]))
            conn.commit()
        jobs = [{**self._job(claimed), "lease_token": token} for claimed in rows]
        return {**jobs[0], "batch_jobs": jobs} if metadata.get("batch_id") else jobs[0]

    @staticmethod
    def _assert_lease(conn, job):
        row = conn.execute("SELECT owner,lease_until FROM online_jobs WHERE id=?", (job["id"],)).fetchone()
        if not row or row["owner"] != job["lease_token"] or row["lease_until"] <= time.time():
            raise OnlineConflict("任务执行租约已失效，禁止过期执行者提交结果")

    def update_job(self, job_id: str, status: str, result: dict[str, Any], *, owner: str, dispatched: bool = False, lease_until: float | None = None,
                   confirmed_stock: tuple[str, str, int] | None = None) -> None:
        with self.db._connect() as conn:
            cursor = conn.execute("UPDATE online_jobs SET status=?,result_json=?,dispatched=MAX(dispatched,?),lease_until=?,updated_at=? WHERE id=? AND owner=? AND lease_until>?",
                         (status, json.dumps(result, ensure_ascii=False), int(dispatched), 0 if status in {"submitted", "waiting_confirmation"} else lease_until or time.time()+900, utc_now(), job_id, owner, time.time()))
            if cursor.rowcount != 1:
                raise OnlineConflict("任务执行租约已失效，禁止过期执行者提交结果")
            if confirmed_stock:
                # 目标仓库存与平台成功回执在同一事务提交。
                listing_id, scope_id, quantity = confirmed_stock
                row = conn.execute("SELECT snapshot_json FROM online_listings WHERE id=?", (listing_id,)).fetchone()
                current = OnlineListing.model_validate_json(row[0])
                stock = next(s for s in current.stocks if s.id == scope_id)
                stock.quantity, stock.observed_at = quantity, utc_now()
                stock.read_error = stock.cached_at = ""
                self._write_listing(conn, current)
            conn.commit()

    def claim_reconcile(self, job_id: str, *, lease_until: float | None = None) -> dict[str, Any]:
        token = uuid4().hex
        lease_until = lease_until or time.time()+900
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM online_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("操作记录不存在")
            if self._job(row)["status"] not in ("submitted", "waiting_confirmation", "outcome_unknown", "needs_attention"):
                raise OnlineConflict("当前状态不需要回读原任务；请读取本地任务状态及商品版本，不要重复回读或重放旧请求",
                                     code="ONLINE_RECONCILE_NOT_APPLICABLE", job_id=job_id,
                                     details={"status": self._job(row)["status"], "listing_id": row["target_id"]})
            active = next((other for other in self._resource_jobs(conn, row["platform"], row["account_id"], row["target_id"], row["operation"])
                           if other["status"] in ("queued", "running")), None)
            if active:
                message = "当前店铺正在同步" if active["operation"] == "sync" else "该商品正在执行修改"
                raise OnlineConflict(message + "，请完成后再查询平台结果", job_id=active["id"])
            conn.execute("UPDATE online_jobs SET status='running',owner=?,lease_until=?,updated_at=? WHERE id=?", (token, lease_until, utc_now(), job_id))
            conn.commit()
        return {**self._job(row), "lease_token": token, "confirmation_lease_until": lease_until}

    def close_operation(self, job_id: str, expected_updated_at: str) -> dict[str, Any]:
        """短事务内核验证据并解除目标互斥；旧提交键和外部请求阻断始终保留。"""
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM online_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("操作记录不存在")
            result = json.loads(row["result_json"])
            if row["status"] == "closed_unconfirmed" and result.get("resolution", {}).get("based_on_updated_at") == expected_updated_at:
                return self._job(row)
            if row["updated_at"] != expected_updated_at:
                raise OnlineConflict("操作记录版本已变化，请重新读取任务状态后选择操作", code="ONLINE_JOB_STALE", job_id=job_id)
            if row["operation"] == "sync" or row["status"] not in UNRESOLVED_STATUSES:
                raise OnlineConflict("当前状态不适用人工结束；partial、failed、confirmed、submitted、closed_unconfirmed 无需解锁。新修改仍须核对当前商品和其他活动任务",
                                     code="ONLINE_CLOSE_NOT_APPLICABLE", job_id=job_id,
                                     details={"status": row["status"], "listing_id": row["target_id"]})
            evidence = result.get("evidence") or {}
            try:
                checked_at = datetime.fromisoformat(evidence.get("checked_at", "")).timestamp()
            except (TypeError, ValueError):
                checked_at = 0
            observed = evidence.get("observed") or {}
            checks = result.get("confirmation") or {}
            if (not 0 <= time.time() - checked_at <= 300 or checked_at < float(result.get("last_confirmation_at") or 0)
                    or result.get("read_errors") or not checks or all(checks.values())
                    or not observed or any(value is None for value in observed.values())):
                raise OnlineConflict("请先成功查询平台当前值，并在五分钟内核对后结束操作")
            if result.get("platform_confirmation", {}).get("pending"):
                raise OnlineConflict("平台仍在处理原请求，请等待处理结束后重新查询")
            active = any(other["status"] in ("queued", "running") for other in
                         self._resource_jobs(conn, row["platform"], row["account_id"], row["target_id"], row["operation"]))
            listing = conn.execute("SELECT version FROM online_listings WHERE id=? AND platform=? AND account_id=?",
                                   (row["target_id"], row["platform"], row["account_id"])).fetchone()
            if active or not listing or listing["version"] != evidence.get("version"):
                raise OnlineConflict("商品正在处理或核对后已变化，请重新查询平台结果")
            now = utc_now()
            result["resolution"] = {"decision": "accept_current_state", "previous_status": row["status"],
                "closed_at": now, "based_on_updated_at": expected_updated_at,
                "acknowledged_unconfirmed": True, "evidence": evidence}
            conn.execute("""UPDATE online_jobs SET status='closed_unconfirmed',result_json=?,
                owner='',lease_until=0,updated_at=? WHERE id=?""", (json.dumps(result, ensure_ascii=False), now, job_id))
            conn.commit()
        return self.job(job_id)

"""运营大屏只读聚合分析服务：仅读取本地 SQLite 快照，不触发远端刷新或凭据读取。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from erp_web.schemas.orders import PLATFORMS, account_pairs

ORDER_STATES = (
    "pending_shipment",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "unknown",
)

READY_DRAFT_STATUSES = frozenset({"ready_to_publish", "ready", "published"})
PUBLISHED_DRAFT_STATUSES = frozenset({"published"})


def _resolve_timezone(name: str):
    cleaned = str(name or "").strip()
    if not cleaned or cleaned.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(cleaned)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _parse_iso_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        if len(text) >= 10:
            try:
                parsed = datetime.fromisoformat(text[:10])
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_amount(value: Any) -> Decimal:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return Decimal("0")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return Decimal("0")
    return amount if amount.is_finite() else Decimal("0")


def _percent_delta(current: int, previous: int) -> float | None:
    if previous <= 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100.0, 1)


def _is_urgent_deadline(deadline_text: str, now_utc: datetime, tz) -> tuple[bool, bool]:
    """Return (is_overdue, is_urgent_24h) for an order shipment deadline."""
    parsed = _parse_iso_datetime(deadline_text)
    if parsed is None:
        return False, False
    # Date-only deadlines (YYYY-MM-DD) are treated as end of that local day.
    raw = str(deadline_text or "").strip()
    if len(raw) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, tzinfo=tz)
    delta_seconds = (parsed.astimezone(timezone.utc) - now_utc).total_seconds()
    if delta_seconds < 0:
        return True, True
    return False, delta_seconds <= 24 * 3600


def _collect_order_analytics(
    context,
    *,
    days: int,
    tz,
    now_utc: datetime,
    date_keys: list[str],
    prev_start_date,
    start_date,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    order_service = context.order_notifications
    accounts = order_service.accounts()
    scopes = account_pairs(accounts)
    sync_status = order_service.store.sync_status(accounts)
    sync_map = {row["platform"]: row for row in sync_status}

    counts = {state: 0 for state in ORDER_STATES}
    platform_stats: dict[str, dict[str, Any]] = {
        platform: {
            "platform": platform,
            "total": 0,
            "pending_shipment": 0,
            "processing": 0,
            "shipped": 0,
            "delivered": 0,
            "cancelled": 0,
            "unknown": 0,
            "window_orders": 0,
            "gmv_by_currency": {},
            "sync_status": sync_map.get(platform, {
                "platform": platform,
                "status": "idle",
                "error": "",
                "last_success_at": "",
                "next_attempt": 0,
            }),
        }
        for platform in PLATFORMS
    }

    trend_buckets: dict[str, dict[str, Any]] = {
        key: {
            "date": key,
            "label": key[5:],
            "orders": 0,
            "pending": 0,
            "shipped": 0,
            "cancelled": 0,
            "by_platform": {platform: 0 for platform in PLATFORMS},
            "gmv_by_currency": {},
        }
        for key in date_keys
    }

    window_gmv: dict[str, Decimal] = {}
    all_gmv: dict[str, Decimal] = {}
    platform_gmv: dict[str, dict[str, Decimal]] = {platform: {} for platform in PLATFORMS}
    trend_gmv: dict[str, dict[str, Decimal]] = {key: {} for key in date_keys}

    window_orders = 0
    previous_window_orders = 0
    overdue_count = 0
    urgent_count = 0
    unread_alerts = 0
    attention_count = 0
    recent_candidates: list[tuple[float, str, dict[str, Any]]] = []

    if scopes:
        where = " OR ".join("(platform=? AND account_id=?)" for _ in scopes)
        args = tuple(value for pair in scopes for value in pair)
        with order_service.store.connect() as conn:
            rows = conn.execute(
                f"SELECT id, platform, account_id, state, snapshot, remote_time, checked_at FROM orders WHERE {where}",
                args,
            ).fetchall()
            unread_alerts = conn.execute(
                f"SELECT COUNT(*) FROM alerts WHERE ({where}) AND read_at=''",
                args,
            ).fetchone()[0]
            attention_count = conn.execute(
                f"SELECT COUNT(*) FROM inbox WHERE ({where}) AND status IN ('failed','retry')",
                args,
            ).fetchone()[0]

        for row in rows:
            platform = str(row["platform"] or "")
            state = str(row["state"] or "unknown")
            if state not in counts:
                state = "unknown"
            counts[state] += 1

            try:
                snapshot = json.loads(row["snapshot"] or "{}")
            except (TypeError, ValueError):
                snapshot = {}

            amount = _parse_amount(snapshot.get("amount"))
            currency = str(snapshot.get("currency") or "").strip().upper()
            if currency and amount > 0 and state != "cancelled":
                all_gmv[currency] = all_gmv.get(currency, Decimal("0")) + amount

            p_entry = platform_stats.get(platform)
            if p_entry is not None:
                p_entry["total"] += 1
                p_entry[state] += 1
                if currency and amount > 0 and state != "cancelled":
                    pg = platform_gmv[platform]
                    pg[currency] = pg.get(currency, Decimal("0")) + amount

            if state in ("pending_shipment", "processing"):
                is_overdue, is_urgent = _is_urgent_deadline(snapshot.get("shipment_deadline", ""), now_utc, tz)
                if is_overdue:
                    overdue_count += 1
                if is_urgent:
                    urgent_count += 1

            event_dt = _parse_iso_datetime(snapshot.get("updated_at")) or _parse_iso_datetime(row["checked_at"])
            if event_dt is not None:
                local_date = event_dt.astimezone(tz).date()
                date_key = local_date.isoformat()
                if date_key in trend_buckets:
                    window_orders += 1
                    bucket = trend_buckets[date_key]
                    bucket["orders"] += 1
                    if state == "pending_shipment":
                        bucket["pending"] += 1
                    elif state in ("shipped", "delivered"):
                        bucket["shipped"] += 1
                    elif state == "cancelled":
                        bucket["cancelled"] += 1
                    if platform in bucket["by_platform"]:
                        bucket["by_platform"][platform] += 1
                    if p_entry is not None:
                        p_entry["window_orders"] += 1
                    if currency and amount > 0 and state != "cancelled":
                        window_gmv[currency] = window_gmv.get(currency, Decimal("0")) + amount
                        tg = trend_gmv[date_key]
                        tg[currency] = tg.get(currency, Decimal("0")) + amount
                elif prev_start_date <= local_date < start_date:
                    previous_window_orders += 1

            sort_ts = float(row["remote_time"] or 0.0)
            if sort_ts <= 0 and event_dt is not None:
                sort_ts = event_dt.timestamp()
            recent_candidates.append((
                sort_ts,
                str(row["checked_at"] or ""),
                {
                    "id": str(row["id"] or ""),
                    "platform": platform,
                    "order_id": str(snapshot.get("order_id") or ""),
                    "title": str(snapshot.get("title") or ""),
                    "state": state,
                    "amount": str(snapshot.get("amount") or ""),
                    "currency": currency,
                    "updated_at": str(snapshot.get("updated_at") or row["checked_at"] or ""),
                    "shipment_deadline": str(snapshot.get("shipment_deadline") or ""),
                    "shop_name": str(snapshot.get("shop_name") or ""),
                },
            ))

    for platform, gmv_map in platform_gmv.items():
        platform_stats[platform]["gmv_by_currency"] = {
            cur: round(float(val), 2) for cur, val in sorted(gmv_map.items())
        }

    for key, gmv_map in trend_gmv.items():
        trend_buckets[key]["gmv_by_currency"] = {
            cur: round(float(val), 2) for cur, val in sorted(gmv_map.items())
        }

    recent_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    recent_orders = [item[2] for item in recent_candidates[:15]]

    total_orders = sum(counts.values())
    fulfilled_count = counts["shipped"] + counts["delivered"]
    active_denominator = max(total_orders - counts["cancelled"], 0)
    fulfillment_rate = round((fulfilled_count / active_denominator) * 100.0, 1) if active_denominator else 0.0

    order_summary = {
        "total": total_orders,
        "window_orders": window_orders,
        "previous_window_orders": previous_window_orders,
        "window_orders_delta_pct": _percent_delta(window_orders, previous_window_orders),
        "counts": counts,
        "pending_shipment": counts["pending_shipment"],
        "processing": counts["processing"],
        "shipped": counts["shipped"],
        "delivered": counts["delivered"],
        "cancelled": counts["cancelled"],
        "fulfillment_rate": fulfillment_rate,
        "overdue_count": overdue_count,
        "urgent_count": urgent_count,
        "unread_alerts": int(unread_alerts),
        "attention_count": int(attention_count),
        "window_gmv": {cur: round(float(val), 2) for cur, val in sorted(window_gmv.items())},
        "total_gmv": {cur: round(float(val), 2) for cur, val in sorted(all_gmv.items())},
        "sync_status": sync_status,
    }

    return order_summary, [platform_stats[p] for p in PLATFORMS], trend_buckets, recent_orders


def _collect_catalog_and_ops_analytics(
    context,
    *,
    tz,
    date_keys: list[str],
    prev_start_date,
    start_date,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, dict[str, int]]]:
    product_trend: dict[str, dict[str, int]] = {
        key: {"collected": 0, "drafts": 0, "published": 0}
        for key in date_keys
    }

    products_total = 0
    window_collected = 0
    prev_collected = 0

    drafts_total = 0
    ready_drafts = 0
    published_drafts = 0
    drafts_by_platform = {platform: 0 for platform in PLATFORMS}

    online_total = 0
    online_active = 0
    online_paused = 0
    online_attention = 0
    online_by_platform = {
        platform: {"total": 0, "active": 0, "paused": 0, "attention": 0}
        for platform in PLATFORMS
    }

    task_counts = {"running": 0, "queued": 0, "completed": 0, "failed": 0, "attention": 0}

    with context.db._connect() as conn:
        for row in conn.execute("SELECT created_at, updated_at FROM products").fetchall():
            products_total += 1
            dt = _parse_iso_datetime(row["created_at"]) or _parse_iso_datetime(row["updated_at"])
            if dt is not None:
                local_date = dt.astimezone(tz).date()
                key = local_date.isoformat()
                if key in product_trend:
                    product_trend[key]["collected"] += 1
                    window_collected += 1
                elif prev_start_date <= local_date < start_date:
                    prev_collected += 1

        for row in conn.execute("SELECT platform, status, created_at, updated_at FROM platform_drafts").fetchall():
            drafts_total += 1
            platform = str(row["platform"] or "").lower()
            status = str(row["status"] or "").lower()
            if platform in drafts_by_platform:
                drafts_by_platform[platform] += 1
            if status in READY_DRAFT_STATUSES:
                ready_drafts += 1
            if status in PUBLISHED_DRAFT_STATUSES:
                published_drafts += 1

            created_dt = _parse_iso_datetime(row["created_at"]) or _parse_iso_datetime(row["updated_at"])
            if created_dt is not None:
                key = created_dt.astimezone(tz).date().isoformat()
                if key in product_trend:
                    product_trend[key]["drafts"] += 1

            if status in PUBLISHED_DRAFT_STATUSES:
                pub_dt = _parse_iso_datetime(row["updated_at"]) or created_dt
                if pub_dt is not None:
                    key = pub_dt.astimezone(tz).date().isoformat()
                    if key in product_trend:
                        product_trend[key]["published"] += 1

        for row in conn.execute("SELECT platform, snapshot_json FROM online_listings").fetchall():
            online_total += 1
            platform = str(row["platform"] or "").lower()
            try:
                snap = json.loads(row["snapshot_json"] or "{}")
            except (TypeError, ValueError):
                snap = {}
            sale_state = str(snap.get("sale_state") or "").lower()
            errors = snap.get("errors") or []
            issues = snap.get("platform_issues") or []
            has_attention = bool(errors) or any(
                isinstance(issue, dict) and issue.get("severity") == "error" for issue in issues
            )
            p_online = online_by_platform.get(platform)
            if p_online is not None:
                p_online["total"] += 1
            if sale_state == "active":
                online_active += 1
                if p_online is not None:
                    p_online["active"] += 1
            elif sale_state in ("paused", "inactive", "archived"):
                online_paused += 1
                if p_online is not None:
                    p_online["paused"] += 1
            if has_attention:
                online_attention += 1
                if p_online is not None:
                    p_online["attention"] += 1

        for table in ("publish_jobs", "online_jobs"):
            for row in conn.execute(f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status").fetchall():
                status = str(row["status"] or "").lower()
                count = int(row["n"] or 0)
                if status in ("running", "processing", "in_progress"):
                    task_counts["running"] += count
                elif status in ("queued", "pending"):
                    task_counts["queued"] += count
                elif status in ("completed", "done", "success", "published"):
                    task_counts["completed"] += count
                elif status in ("failed", "error"):
                    task_counts["failed"] += count
                elif status in ("attention", "interrupted", "partial", "needs_attention"):
                    task_counts["attention"] += count

    catalog_summary = {
        "products_total": products_total,
        "window_collected": window_collected,
        "previous_window_collected": prev_collected,
        "window_collected_delta_pct": _percent_delta(window_collected, prev_collected),
        "drafts_total": drafts_total,
        "ready_drafts": ready_drafts,
        "published_drafts": published_drafts,
        "drafts_by_platform": drafts_by_platform,
    }
    online_summary = {
        "total": online_total,
        "active": online_active,
        "paused": online_paused,
        "attention": online_attention,
        "by_platform": online_by_platform,
    }
    return catalog_summary, online_summary, task_counts, product_trend


def build_dashboard_analytics(
    context,
    *,
    days: int = 14,
    time_zone: str = "UTC",
) -> dict[str, Any]:
    """聚合运营大屏所需的订单、趋势、商品、在线刊登与任务概览。"""
    window_days = max(7, min(int(days or 14), 30))
    tz = _resolve_timezone(time_zone)
    now_utc = datetime.now(timezone.utc)
    today_local = now_utc.astimezone(tz).date()
    start_date = today_local - timedelta(days=window_days - 1)
    prev_start_date = start_date - timedelta(days=window_days)
    date_keys = [(start_date + timedelta(days=offset)).isoformat() for offset in range(window_days)]

    order_summary, platform_breakdown, order_trend_map, recent_orders = _collect_order_analytics(
        context,
        days=window_days,
        tz=tz,
        now_utc=now_utc,
        date_keys=date_keys,
        prev_start_date=prev_start_date,
        start_date=start_date,
    )
    catalog_summary, online_summary, task_summary, product_trend_map = _collect_catalog_and_ops_analytics(
        context,
        tz=tz,
        date_keys=date_keys,
        prev_start_date=prev_start_date,
        start_date=start_date,
    )

    trends = [
        {
            **order_trend_map[key],
            **product_trend_map[key],
        }
        for key in date_keys
    ]

    return {
        "ok": True,
        "generated_at": now_utc.isoformat(),
        "window_days": window_days,
        "time_zone": str(time_zone or "UTC"),
        "order_summary": order_summary,
        "platform_breakdown": platform_breakdown,
        "catalog_summary": catalog_summary,
        "online_summary": online_summary,
        "task_summary": task_summary,
        "trends": trends,
        "recent_orders": recent_orders,
    }


__all__ = ["build_dashboard_analytics"]

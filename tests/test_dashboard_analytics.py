"""运营大屏聚合分析查询（dashboard.analytics）的后端契约与业务聚合测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import time

from erp_web.business_query_composition import BUSINESS_QUERIES, build_business_data_catalog
from erp_web.context import get_context
from erp_web.schemas.orders import OrderSnapshot, timestamp
from erp_web.services.dashboard_analytics import build_dashboard_analytics


def _save_order(context, *, platform: str, account_id: str, order_id: str, state: str, amount: str, currency: str, updated_at: str, deadline: str = ""):
    store = context.order_notifications.store
    snapshot = OrderSnapshot(
        platform=platform,
        account_id=account_id,
        order_id=order_id,
        title=f"商品 {order_id}",
        status=state,
        state=state,
        amount=amount,
        currency=currency,
        updated_at=updated_at,
        shipment_deadline=deadline,
    )
    with store.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?)",
            (
                snapshot.identity,
                snapshot.platform,
                snapshot.account_id,
                snapshot.state,
                snapshot.model_dump_json(),
                timestamp(updated_at),
                updated_at,
            ),
        )
        conn.commit()


def test_dashboard_analytics_registered_in_business_queries():
    query = next(row for row in BUSINESS_QUERIES.definitions if row.name == "dashboard.analytics")
    assert query.path == "/api/business-queries/dashboard.analytics"
    assert query.permission == "order.read"


def test_build_dashboard_analytics_aggregates_orders_trends_catalog_online_and_tasks(monkeypatch):
    app_context = get_context()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today_iso = now.isoformat()
    yesterday_iso = (now - timedelta(days=1)).isoformat()
    prev_window_iso = (now - timedelta(days=10)).isoformat()
    overdue_deadline = (now - timedelta(hours=3)).isoformat()

    monkeypatch.setattr(
        app_context.order_notifications,
        "accounts",
        lambda: {"yandex": ("ya-1",), "ozon": ("oz-1",), "mercadolibre": ("ml-1",)},
    )

    _save_order(
        app_context,
        platform="yandex",
        account_id="ya-1",
        order_id="YA-1001",
        state="pending_shipment",
        amount="2500.50",
        currency="RUB",
        updated_at=today_iso,
        deadline=overdue_deadline,
    )
    _save_order(
        app_context,
        platform="ozon",
        account_id="oz-1",
        order_id="OZ-2001",
        state="shipped",
        amount="1200.00",
        currency="RUB",
        updated_at=yesterday_iso,
    )
    _save_order(
        app_context,
        platform="mercadolibre",
        account_id="ml-1",
        order_id="ML-3001",
        state="cancelled",
        amount="99.00",
        currency="USD",
        updated_at=yesterday_iso,
    )
    _save_order(
        app_context,
        platform="yandex",
        account_id="ya-1",
        order_id="YA-0999",
        state="delivered",
        amount="800.00",
        currency="RUB",
        updated_at=prev_window_iso,
    )

    with app_context.db._connect() as conn:
        conn.execute(
            "INSERT INTO products (product_id, title, created_at, updated_at) VALUES (?,?,?,?)",
            ("prod-1", "测试商品 1", today_iso, today_iso),
        )
        conn.execute(
            "INSERT INTO platform_drafts (draft_id, product_id, platform, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("draft-1", "prod-1", "yandex", "published", today_iso, today_iso),
        )
        conn.execute(
            "INSERT INTO online_listings (id, platform, account_id, remote_id, version, snapshot_json, synced_at) VALUES (?,?,?,?,?,?,?)",
            (
                "ol-1",
                "yandex",
                "ya-1",
                "rem-1",
                "v1",
                json.dumps({"sale_state": "active", "errors": [], "platform_issues": []}),
                today_iso,
            ),
        )
        conn.execute(
            "INSERT INTO online_listings (id, platform, account_id, remote_id, version, snapshot_json, synced_at) VALUES (?,?,?,?,?,?,?)",
            (
                "ol-2",
                "ozon",
                "oz-1",
                "rem-2",
                "v1",
                json.dumps({"sale_state": "paused", "errors": ["stock_error"], "platform_issues": []}),
                today_iso,
            ),
        )
        conn.execute(
            "INSERT INTO online_jobs (id, platform, account_id, operation, target_id, status, idempotency_key, request_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("oj-1", "yandex", "ya-1", "price", "ol-1", "running", "idem-1", "{}", today_iso, today_iso),
        )
        conn.commit()

    payload = build_dashboard_analytics(app_context, days=7, time_zone="UTC")
    assert payload["ok"] is True
    assert payload["window_days"] == 7
    assert len(payload["trends"]) == 7

    order_summary = payload["order_summary"]
    assert order_summary["total"] == 4
    assert order_summary["window_orders"] == 3
    assert order_summary["previous_window_orders"] == 1
    assert order_summary["window_orders_delta_pct"] == 200.0
    assert order_summary["pending_shipment"] == 1
    assert order_summary["shipped"] == 1
    assert order_summary["delivered"] == 1
    assert order_summary["cancelled"] == 1
    assert order_summary["overdue_count"] == 1
    assert order_summary["window_gmv"] == {"RUB": 3700.5}
    assert order_summary["total_gmv"] == {"RUB": 4500.5}

    catalog = payload["catalog_summary"]
    assert catalog["products_total"] == 1
    assert catalog["window_collected"] == 1
    assert catalog["drafts_total"] == 1
    assert catalog["published_drafts"] == 1

    online = payload["online_summary"]
    assert online["total"] == 2
    assert online["active"] == 1
    assert online["paused"] == 1
    assert online["attention"] == 1

    tasks = payload["task_summary"]
    assert tasks["running"] == 1

    catalog_result = build_business_data_catalog(app_context).load("dashboard.analytics", {"days": 7, "time_zone": "Asia/Shanghai"})
    assert catalog_result["ok"] is True
    assert catalog_result["window_days"] == 7

"""单次读取主库历史通知；新通知只写入订单领域库，不建立双写路径。"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from erp_web.schemas.orders import OrderEvent
from erp_web.stores.order_notification_store import OrderNotificationStore


def import_historical_notifications(
    store: OrderNotificationStore, source: Path
) -> None:
    if store.setting("historical_import") == "done" or not source.exists():
        return
    conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_notifications'"
        ).fetchone()
        if exists:
            for topic, resource, raw_json in conn.execute(
                "SELECT topic,resource,raw_json FROM order_notifications ORDER BY id"
            ):
                record = json.loads(raw_json)
                account = str(record.get("user_id") or "")
                if (
                    not account
                    or topic not in {"orders_v2", "shipments"}
                    or not re.fullmatch(r"/(orders|shipments)/[0-9]+", resource)
                ):
                    # 无可靠账号身份的历史记录保留在原库，不能猜测归属后接入新账号。
                    continue
                store.enqueue(
                    OrderEvent(
                        platform="mercadolibre",
                        account_id=account,
                        topic=topic,
                        resource=resource,
                        occurred_at=str(record.get("sent") or ""),
                        payload=record.get("raw") or record,
                    )
                )
    finally:
        conn.close()
    store.set_setting("historical_import", "done")

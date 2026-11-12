"""全部店铺合批同步、筛选与下游权限及采购来源隔离。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from erp_web.context import get_context
from erp_web.marketplaces.yandex_http import fetch_yandex_shop_names
from erp_web.runtime_units.order_notifications import parse_notification, order_request_scopes
from erp_web.runtime_units.orders_yandex import YandexOrderAdapter
from erp_web.schemas.platform_query_capabilities import PlatformOrdersQueryRequest
from erp_web.services.order_center_query_service import OrderCenterQueryService
from erp_web.services.order_notification_service import OrderNotificationService
from erp_web.services.order_query_service import OrderQueryService
from erp_web.stores.order_notification_store import OrderNotificationStore
from tests.test_order_procurement import domain, job
from erp_web.runtime_units.order_source_bindings import bindings_from_publish_job


CONFIG = {"yandex": {"campaign_id": "4", "business_id": "5", "api_token": "测试凭据", "shop_name": "经济店"}}


def remote_order(number, campaign):
    return {"orderId": number, "campaignId": campaign, "programType": "FBS", "status": "PROCESSING",
            "substatus": "STARTED", "items": [{"offerId": "SALE-1", "count": 1, "offerName": "同名商品"}]}


def test_directory_reads_all_pages_and_excludes_other_business():
    calls = []

    def request(method, path, token, **kwargs):
        calls.append(kwargs["query"].copy())
        if len(calls) == 1:
            return {"campaigns": [{"id": 4, "business": {"id": 5}, "domain": "经济店"},
                                  {"id": 99, "business": {"id": 999}, "domain": "其他企业"}],
                    "paging": {"nextPageToken": "next"}}
        return {"campaigns": [{"id": 6, "business": {"id": 5}, "domain": "快线店"}]}

    assert fetch_yandex_shop_names("凭据", "5", request=request) == {"4": "经济店", "6": "快线店"}
    assert calls == [{"limit": 100}, {"limit": 100, "pageToken": "next"}]


def test_sync_persists_all_shops_and_callbacks_keep_true_campaign(tmp_path, monkeypatch):
    config = deepcopy(CONFIG)
    store = OrderNotificationStore(tmp_path / "orders.sqlite3")
    batches, handovers = [], []

    def request(method, path, token, body=None, **kwargs):
        if method == "GET":
            return {"campaigns": [{"id": n, "business": {"id": 5}, "domain": name}
                                  for n, name in [(4, "经济店"), (6, "快线店")]]}
        batches.append(body)
        if body.get("orderIds"):
            return {"orders": [remote_order(456, 6)]}
        return {"orders": [remote_order(123, 4), remote_order(456, 6)]}

    def enrich(rows, *, account, **kwargs):
        handovers.append((account, [row.account_id for row in rows]))
        return rows

    monkeypatch.setattr("erp_web.runtime_units.orders_yandex.request_yandex_json", request)
    monkeypatch.setattr("erp_web.runtime_units.orders_yandex.enrich_yandex_handover", enrich)
    service = OrderNotificationService(store, lambda: config, config_store=get_context().config,
        adapters={"yandex": lambda cfg: YandexOrderAdapter(cfg,
            shops={row.account_id: row.name for row in service.shops() if row.platform == "yandex"},
            on_shops=lambda names: service.save_yandex_shops(cfg, names))},
        parser=parse_notification, start_worker=False)
    service.sync("yandex")
    assert service.process_one("yandex")
    assert batches == [{"campaignIds": [4, 6]}]
    assert handovers == [("4", ["4"]), ("6", ["6"])]
    assert service.accounts() == {"yandex": ("4", "6")}
    assert store.summary(service.accounts())["counts"] == {"pending_shipment": 2}
    assert service.sync_status()[0]["status"] == "done"

    # 重开服务不触发网络，店铺目录与全量订单仍可读取。
    reopened = OrderNotificationService(OrderNotificationStore(store.path), lambda: config,
        config_store=get_context().config, adapters=service.adapters, parser=parse_notification, start_worker=False)
    assert [shop.name for shop in reopened.shops()] == ["经济店", "快线店"]
    assert store.read(reopened.accounts(), limit=1, offset=1)["total"] == 2
    callback = {"notificationType": "ORDER_UPDATED", "campaignId": 6, "orderId": 456}
    event, _ = parse_notification("yandex", callback, config, accounts=reopened.accounts())
    store.enqueue(event)
    assert service.process_one("yandex")
    assert batches[-1] == {"campaignIds": [6], "orderIds": [456]}
    with pytest.raises(ValueError, match="不一致"):
        parse_notification("yandex", {**callback, "campaignId": 99}, config, accounts=service.accounts())
    # 凭据切换不会把上一次授权发现的店铺继续作为可信范围。
    config["yandex"]["api_token"] = "另一凭据"
    assert service.accounts() == {"yandex": ("4",)}
    assert store.read(service.accounts())["total"] == 1


def test_sync_batches_fifty_campaigns_and_rejects_unrequested_rows(monkeypatch):
    calls = []

    def request(method, path, token, body=None, **kwargs):
        if method == "GET":
            return {"campaigns": [{"id": n, "business": {"id": 5}} for n in range(1, 53)]}
        calls.append(body["campaignIds"])
        return {"orders": []}

    monkeypatch.setattr("erp_web.runtime_units.orders_yandex.request_yandex_json", request)
    assert list(YandexOrderAdapter(CONFIG).sync()) == []
    assert calls == [list(range(1, 51)), [51, 52]]
    adapter = YandexOrderAdapter(CONFIG, shops={"4": "经济店", "6": "快线店"})
    with pytest.raises(ValueError, match="不属于"):
        adapter.normalize(remote_order(789, 99))


def test_all_shop_filtering_pagination_and_sources_stay_isolated(domain):
    procurement, first, _ = domain
    second = first.model_copy(update={"account_id": "6", "order_id": "456"})
    outsider = first.model_copy(update={"account_id": "99", "order_id": "789"})
    with procurement.store.orders.connect() as conn:
        for order in [second, outsider]:
            conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?)",
                         (order.identity, order.platform, order.account_id, order.state,
                          order.model_dump_json(), 0, ""))
        conn.commit()
    procurement.accounts_provider = lambda: {"yandex": ("4", "6")}
    procurement.identity_provider = lambda platform, account_id="": "shop-a" if account_id == "4" else "shop-b"
    procurement.store.add_bindings(bindings_from_publish_job(job(identity="shop-a", source_id="red")))
    procurement.store.add_bindings(bindings_from_publish_job(job(identity="shop-b", source_id="blue")))
    notifications = SimpleNamespace(accounts=procurement.accounts_provider, store=procurement.store.orders, sync_status=lambda: [])
    center = OrderCenterQueryService(procurement, notifications, SimpleNamespace(query_records=lambda orders: {}))
    assert center.read(limit=1)["total"] == 2
    selected = center.read(shop="yandex:6", state="pending_shipment")
    assert [row["id"] for row in selected["items"]] == [second.identity]
    assert selected["counts"] == {"pending_shipment": 1}
    assert selected["alerts"] == center.read()["alerts"]
    assert selected["notifications"] == center.read()["notifications"]
    assert selected["latest_alert_id"] == center.read()["latest_alert_id"]
    assert center.read(shop="yandex:6", q="blue")["total"] == 1
    assert center.read(shop="yandex:6", q="red")["total"] == 0
    with pytest.raises(ValueError, match="不属于"):
        center.read(shop="yandex:99")
    assert procurement.detail(second.identity)["lines"][0]["selection"]["candidates"][0]["source"]["source_sku_id"] == "blue"
    assert procurement.detail(first.identity)["lines"][0]["selection"]["candidates"][0]["source"]["source_sku_id"] == "red"
    with pytest.raises(ValueError, match="不属于"):
        procurement.detail(outsider.identity)
    query = OrderQueryService(procurement, notifications)
    result = query.query(PlatformOrdersQueryRequest(q="blue"))
    assert [row["id"] for row in result["items"]] == [second.identity]


def test_request_blocks_cover_every_shop_handover_scope():
    scopes = order_request_scopes(CONFIG, accounts={"yandex": ("4", "6")})["yandex"]
    assert {row.quota_key for row in scopes if "first-mile" in row.interface} == {"campaigns:4", "campaigns:6"}
    assert any(row.interface == "/v2/campaigns" for row in scopes)

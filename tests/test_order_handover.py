"""交货信息只读查询：订单关联、地址方向、分页完整性和账号隔离。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from erp_web.marketplaces.yandex_http import YandexApiError
from erp_web.schemas.orders import OrderView, OrderEvent
from erp_web.runtime_units.orders_yandex import YandexOrderAdapter
from erp_web.runtime_units.order_notifications import parse_notification, order_request_scopes
from erp_web.services.order_notification_service import OrderNotificationService
from erp_web.stores.order_notification_store import OrderNotificationStore
from erp_web.stores.order_procurement_store import OrderProcurementStore
from erp_web.services import order_handover_service as module
from erp_web.services.external_request_context import request_context


def order(**values):
    return OrderView.model_validate({
        "id": "yandex:4:FBS:123", "platform": "yandex", "account_id": "4",
        "order_id": "123", "fulfillment": "FBS", "status": "PROCESSING",
        "checked_at": "", "shipment_deadline": "2026-10-13", **values,
    })


def shipment(**values):
    return {
        "id": 789, "orderIds": [123, 456], "shipmentType": "IMPORT",
        "status": "OUTBOUND_CREATED",
        "warehouse": {"id": 1, "name": "卖家仓", "address": "始发地址"},
        "warehouseTo": {"id": 2, "name": "测试交货点", "address": "测试市交货路23号"},
        "planIntervalFrom": "2026-10-13T04:30:00+03:00",
        "planIntervalTo": "2026-10-13T15:00:00+03:00", **values,
    }


def response(rows, cursor=""):
    return {"result": {"shipments": rows, "paging": {"nextPageToken": cursor}}}


@pytest.fixture
def setup(monkeypatch):
    config = {"yandex": {"campaign_id": "4", "api_token": "test-token"}}
    current = order()
    calls = []
    replies = [response([shipment()])]

    def request(*args, **kwargs):
        calls.append((deepcopy(args), deepcopy(kwargs)))
        value = replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def read(orders=None):
        return module.enrich_yandex_handover(orders or [current], account="4", api_token="test-token", request=request)
    service = SimpleNamespace(read=lambda _: read()[0].handover)
    return SimpleNamespace(service=service, read=read, config=config, current=current, calls=calls, replies=replies)


def test_read_matches_order_and_keeps_destination_separate_from_origin(setup):
    result = setup.service.read(setup.current.id)
    assert result.state == "ready" and result.checked_at
    assert result.shipments[0].destination.address == "测试市交货路23号"
    assert result.shipments[0].origin.address == "始发地址"
    assert result.shipments[0].planned_to == "2026-10-13T15:00:00+03:00"
    args, kwargs = setup.calls[0]
    assert args == ("PUT", "/v2/campaigns/4/first-mile/shipments", "test-token", {
        "dateFrom": "2026-10-06", "dateTo": "2026-10-20", "orderIds": [123],
    })
    assert kwargs["query"] == {"limit": 30}
    assert "test-token" not in result.model_dump_json()


def test_missing_destination_cannot_be_replaced_with_seller_warehouse(setup):
    setup.replies[:] = [response([shipment(warehouseTo=None)])]
    result = setup.service.read(setup.current.id)
    assert result.shipments[0].destination is None
    assert result.shipments[0].origin.address == "始发地址"


def test_pagination_keeps_all_shipments_for_order_and_deduplicates(setup):
    setup.replies[:] = [response([shipment()], "next"), response([shipment(), shipment(id=790)])]
    result = setup.service.read(setup.current.id)
    assert [row.shipment_id for row in result.shipments] == ["789", "790"]
    assert setup.calls[1][1]["query"] == {"limit": 30, "pageToken": "next"}


@pytest.mark.parametrize("patch,reason", [
    ({"orderIds": [456]}, "不一致"),
    ({"orderIds": None}, "订单归属"),
    ({"id": True}, "标识无效"),
    ({"planIntervalTo": "2026-10-13T15:00:00"}, "时区"),
    ({"planIntervalTo": "2026-10-12T15:00:00+03:00"}, "时间范围"),
])
def test_untrusted_shipment_is_rejected_without_displaying_address(setup, patch, reason):
    setup.replies[:] = [response([shipment(**patch)])]
    with pytest.raises(ValueError, match=reason):
        setup.service.read(setup.current.id)


def test_repeated_cursor_does_not_return_partial_success(setup):
    setup.replies[:] = [response([shipment()], "loop"), response([shipment()], "loop")]
    with pytest.raises(ValueError, match="分页未前进"):
        setup.service.read(setup.current.id)


def test_conflicting_duplicate_shipment_is_not_silently_overwritten(setup):
    setup.replies[:] = [response([shipment()], "next"), response([shipment(warehouseTo=None)])]
    with pytest.raises(ValueError, match="分页期间发生变化"):
        setup.service.read(setup.current.id)


@pytest.mark.parametrize("model", ["FBY", "DBS"])
def test_unsupported_orders_do_not_query_yandex(setup, model):
    assert setup.read([order(fulfillment=model)])[0].handover is None
    assert not setup.calls


def test_missing_date_and_empty_search_are_explicit_unavailable(setup):
    setup.replies[:] = [response([])]
    assert setup.service.read("order").state == "unavailable"
    setup.calls.clear()
    assert "尚未提供发货日期" in setup.read([order(shipment_deadline="")])[0].handover.message
    assert not setup.calls


def test_cross_account_snapshot_is_rejected_before_request(setup):
    with pytest.raises(ValueError, match="当前店铺范围"):
        setup.read([order(account_id="other")])
    assert not setup.calls


def test_one_request_associates_multiple_orders_without_crossing_membership(setup):
    setup.replies[:] = [response([shipment(), shipment(id=790, orderIds=[456])])]
    result = setup.read([order(), order(order_id="456"), order(order_id="999")])
    assert len(setup.calls) == 1
    assert setup.calls[0][0][3]["orderIds"] == [123, 456, 999]
    assert [s.shipment_id for s in result[0].handover.shipments] == ["789"]
    assert [s.shipment_id for s in result[1].handover.shipments] == ["789", "790"]
    assert result[2].handover.state == "unavailable"


def test_remote_failure_preserves_native_retry_information(setup):
    failure = YandexApiError("SERVER_ERROR", "远端读取失败", http_status=500, retryable=True)
    setup.replies[:] = [failure]
    with pytest.raises(YandexApiError) as error:
        setup.service.read("order")
    assert error.value is failure and error.value.retryable


def test_read_only_put_semantics_do_not_apply_to_shipment_writes():
    base = "https://api.partner.market.yandex.ru/v2/campaigns/4/first-mile/shipments"
    assert request_context(base, method="PUT").semantics == "read"
    assert request_context(base + "/789/pallets", method="PUT").semantics == "write"
    assert request_context(base + "/789/confirm", method="PUT").semantics == "write"


def test_shipment_request_scope_matches_campaign_quota():
    config = {"yandex": {"campaign_id": "4", "business_id": "5", "api_token": "test-token"}}
    scope = next(row for row in order_request_scopes(config)["yandex"] if "first-mile" in row.interface)
    assert scope.account_id == "5" and scope.quota_key == "campaigns:4"


def test_sync_and_notifications_persist_handover_and_detail_reads_stay_local(tmp_path, monkeypatch):
    config = {"yandex": {"campaign_id": "4", "business_id": "5", "api_token": "test-token"}}
    store = OrderNotificationStore(tmp_path / "orders.sqlite3")
    service = OrderNotificationService(store, lambda: config, adapters={"yandex": YandexOrderAdapter},
                                       parser=parse_notification, start_worker=False)
    row = {"orderId": 123, "campaignId": 4, "programType": "FBS", "status": "PROCESSING",
           "substatus": "READY_TO_SHIP", "updateDate": "2026-10-05T11:00:00+03:00",
           "delivery": {"shipment": {"shipmentDate": "2026-10-13"}}}
    calls = []
    batch = shipment()
    failure = None

    def request(method, path, token, body, **kwargs):
        calls.append(path)
        if method == "POST":
            return {"orders": [deepcopy(row)]}
        if failure:
            raise failure
        return response([deepcopy(batch)])

    monkeypatch.setattr("erp_web.runtime_units.orders_yandex.request_yandex_json", request)
    service.sync("yandex")
    assert service.process_one("yandex")
    procurement = OrderProcurementStore(store)
    saved = procurement.order("yandex:4:FBS:123", {"yandex": "4"})
    assert saved.handover.shipments[0].destination.address == "测试市交货路23号"
    assert len(calls) == 2
    for _ in range(3):
        assert procurement.order(saved.id, {"yandex": "4"}).handover == saved.handover
    assert len(calls) == 2
    with pytest.raises(ValueError):
        procurement.order(saved.id, {"yandex": "999"})

    # 平台订单版本未变时，后续通知仍可更新交货批次状态。
    batch["status"] = "OUTBOUND_CONFIRMED"
    store.enqueue(OrderEvent(platform="yandex", account_id="4", topic="ORDER_UPDATED", resource="123"))
    assert service.process_one("yandex")
    updated = procurement.order(saved.id, {"yandex": "4"})
    assert updated.handover.shipments[0].status == "OUTBOUND_CONFIRMED"

    failure = YandexApiError("SERVER_ERROR", "不可向用户暴露的远端正文", http_status=500, retryable=True)
    service.sync("yandex")
    assert service.process_one("yandex")
    assert procurement.order(saved.id, {"yandex": "4"}).handover == updated.handover
    notification = store.read({"yandex": "4"})["notifications"][0]
    assert notification["status"] == "retry"
    assert "不可向用户暴露" not in notification["error"]


def test_old_persisted_order_without_handover_remains_readable(tmp_path):
    store = OrderNotificationStore(tmp_path / "orders.sqlite3")
    snapshot = order().model_dump(exclude={"id", "checked_at", "fulfillment_summary", "handover"})
    import json
    with store.connect() as conn:
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", (
            "yandex:4:FBS:123", "yandex", "4", "unknown", json.dumps(snapshot), 0, "2026-10-07T00:00:00Z",
        ))
        conn.commit()
    assert store.read({"yandex": "4"})["items"][0]["handover"] is None

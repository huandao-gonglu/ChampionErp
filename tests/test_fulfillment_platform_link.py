"""平台订单关联：只发唯一报单 ID，关联失败不重复创建或重放未知写入。"""

import pytest

from erp_web.schemas.fulfillment import FulfillmentError
from erp_web.services.crossborderbus_client import READ_PATHS
from tests.test_fulfillment import domain, ready, command

PATH = "/erpapi/orderassociation/association_order_account"


def created(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    return service, bus, order


def test_new_yandex_order_uses_platform_number_and_associates_once(domain):
    service, bus, order = created(domain)
    assert bus.calls[0][1]["order_data"][0]["order_number"] == order.order_id
    assert service.detail(order.identity)["platform_link_state"] == "pending"
    before = len(bus.calls)
    service.detail(order.identity)
    assert len(bus.calls) == before
    assert service.process_one(order)
    assert service.detail(order.identity)["platform_link_state"] == "linked"
    assert [(path, body) for path, body in bus.calls if path == PATH] == [(PATH, {"order_id": 99})]
    assert PATH not in READ_PATHS
    before = len(bus.calls)
    command(service, order, "associate")
    assert not service.process_one(order)
    assert len(bus.calls) == before


@pytest.mark.parametrize("unknown", [False, True])
def test_failed_association_preserves_created_order_and_never_auto_retries(domain, monkeypatch, unknown):
    service, bus, order = created(domain)
    original = bus.request

    def request(path, body, **kwargs):
        if path == PATH:
            bus.calls.append((path, body))
            raise FulfillmentError("关联失败", unknown=unknown)
        return original(path, body, **kwargs)

    monkeypatch.setattr(bus, "request", request)
    service.process_one(order)
    value = service.detail(order.identity)
    assert value["platform_link_state"] == ("unknown" if unknown else "failed")
    assert value["platform_link_error"] == "关联失败"
    assert value["crossborderbus_order_id"] == 99
    assert value["fulfillment_status"] == "FULFILLMENT_CREATED"
    assert not value["create_unknown"] and not value["error_message"]
    before = len(bus.calls)
    for _ in range(3):
        assert not service.process_one(order)
    assert len(bus.calls) == before
    monkeypatch.setattr(bus, "request", original)
    if unknown:
        with pytest.raises(FulfillmentError, match="不能重复"):
            command(service, order, "associate")
    else:
        assert command(service, order, "associate")["platform_link_state"] == "linked"
    assert len([path for path, _ in bus.calls if path.endswith("createOrder")]) == 1


@pytest.mark.parametrize("case", ["packed", "deleted", "wrong-id", "wrong-warehouse", "wrong-section"])
def test_association_preflight_rejects_wrong_or_locked_remote_order(domain, case):
    service, bus, order = created(domain)
    if case == "packed": bus.remote["order_status"] = 1
    if case == "deleted": bus.remote["is_delete"] = 1
    if case == "wrong-id": bus.lookup["id"] = 100
    if case == "wrong-warehouse": bus.lookup["sid"] = 20
    if case == "wrong-section": bus.lookup["sheet_info"]["section"] = 2
    service.process_one(order)
    assert service.detail(order.identity)["platform_link_state"] == "failed"
    assert not any(path == PATH for path, _ in bus.calls)


@pytest.mark.parametrize("change", ["cancel", "account"])
def test_cancel_or_account_change_during_preflight_stops_association(domain, monkeypatch, change):
    service, bus, order = created(domain)

    def search(*args, **kwargs):
        if change == "cancel":
            command(service, order, "cancel")
        else:
            domain[-1]["yandex"] = "another-account"
        return bus.lookup

    monkeypatch.setattr(bus, "search", search)
    if change == "account":
        with pytest.raises(ValueError, match="订单不属于当前店铺"):
            service.process_one(order)
    else:
        service.process_one(order)
    assert service.store.get(order.identity)["platform_link_state"] == "failed"
    assert not any(path == PATH for path, _ in bus.calls)


def test_interrupted_association_keeps_unknown_and_original_order_id(domain):
    service, bus, order = created(domain)
    value = service.store.get(order.identity)
    claimed, _ = service.store.claim(order.identity, "associate", value["revision"])
    assert claimed["platform_link_state"] == "unknown"
    with service.store.orders.connect() as conn:
        conn.execute("UPDATE fulfillments SET lease_until=0 WHERE id=?", (order.identity,))
        conn.commit()
    assert not service.process_one(order)
    with pytest.raises(FulfillmentError, match="不能重复"):
        command(service, order, "associate")
    assert service.store.get(order.identity)["crossborderbus_order_id"] == 99


def test_historical_remote_order_number_is_preserved_during_association(domain):
    service, bus, order = created(domain)
    value = service.store.get(order.identity)
    service.store.change(order.identity, value["revision"], {"order_number": "ERP-old", "platform_link_state": ""})
    bus.lookup["sheet_info"]["section_order"] = "ERP-old"
    assert command(service, order, "associate")["platform_link_state"] == "linked"
    assert service.store.get(order.identity)["order_number"] == "ERP-old"
    assert not any(path.endswith("updateOrder") for path, _ in bus.calls)


def test_association_endpoint_is_validated_and_ui_only():
    from erp_web.http_route_units.fulfillment_routes import POST_HANDLERS
    from erp_web.schemas.requests import validate_request_payload

    endpoint = "/api/orders/fulfillment/associate"
    assert endpoint in POST_HANDLERS
    assert validate_request_payload({"order_id": "order-1", "revision": 1}, endpoint=endpoint)["revision"] == 1


def test_unsubmitted_old_number_changes_before_create_but_unknown_number_does_not(domain):
    service, bus, order = ready(domain)
    value = service.store.get(order.identity)
    value = service.store.change(order.identity, value["revision"], {"order_number": "ERP-old", "create_unknown": True})
    assert not service.process_one(order)
    assert service.store.get(order.identity)["order_number"] == "ERP-old"
    assert not bus.calls
    service.store.change(order.identity, value["revision"], {"create_unknown": False})
    assert service.process_one(order)
    assert bus.calls[0][1]["order_data"][0]["order_number"] == order.order_id


def test_missing_platform_order_error_is_definitive_and_sanitized(domain, monkeypatch):
    import io
    import json
    from erp_web.services import crossborderbus_client as module

    response = {"code": 0, "message": "关联失败,未查询到关联订单 secret-value"}
    monkeypatch.setattr(module, "managed_urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()))
    with pytest.raises(FulfillmentError, match="未找到可关联的平台订单") as error:
        module.CrossborderBusClient(domain[0].store)._request(PATH, {"order_id": 99})
    assert error.value.definitive and not error.value.unknown
    assert "secret-value" not in str(error.value)

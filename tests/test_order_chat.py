"""订单聊天只由人工命令驱动，验证平台契约、隔离、分页和写入语义。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from erp_web.schemas.orders import OrderView
from erp_web.schemas.platform_errors import PublishAdapterError
from erp_web.schemas.requests import validate_request_payload
from erp_web.services.order_chat_service import OrderChatService
from erp_web.services.external_request_context import request_context


CONTEXT = {"type": "ORDER", "orderId": 123, "campaignId": 4}


def result(**values):
    return {"status": "OK", "result": values}


def chat():
    return {"chatId": 17, "type": "CHAT", "context": dict(CONTEXT)}


def message(number, **values):
    return {"messageId": number, "message": f"消息 {number}", "sender": "CUSTOMER", "createdAt": "2026-10-13T10:00:00+03:00", **values}


@pytest.fixture
def domain():
    order = OrderView(id="yandex:4:FBS:123", platform="yandex", account_id="4", order_id="123", fulfillment="FBS", status="PROCESSING", checked_at="")
    reader = Mock(return_value=order)
    request = Mock()
    config = {"yandex": {"campaign_id": "4", "business_id": "8", "api_token": "secret"}}
    service = OrderChatService(reader, config, request=request)
    return SimpleNamespace(service=service, request=request, reader=reader, order=order, config=config)


def test_history_reads_all_pages_sorts_deduplicates_and_never_creates_chat(domain):
    domain.request.side_effect = [result(chats=[chat()]),
        result(context=CONTEXT, messages=[message(3), message(2)], paging={"nextPageToken": "next"}),
        result(context=CONTEXT, messages=[message(2), message(1)])]
    history = domain.service.history(domain.order.id)
    assert [row["id"] for row in history["messages"]] == ["1", "2", "3"]
    assert history["chat_id"] == "17"
    domain.reader.assert_called_once_with(domain.order.id, {"yandex": "4"})
    calls = domain.request.call_args_list
    assert calls[0].args[3] == {"contexts": [{"type": "ORDER", "id": 123}], "types": ["CHAT"]}
    assert calls[1].kwargs["query"] == {"chatId": "17", "limit": 100}
    assert calls[2].kwargs["query"] == {"chatId": "17", "limit": 100, "pageToken": "next"}
    assert all(call.args[1].endswith(("/chats", "/history")) for call in calls)


def test_no_chat_returns_empty_without_creation(domain):
    domain.request.return_value = result(chats=[])
    assert domain.service.history(domain.order.id) == {"ok": True, "chat_id": "", "messages": []}
    assert domain.request.call_count == 1


def test_second_shop_chat_uses_true_campaign_without_changing_selected_shop(domain):
    accounts = {"yandex": ("4", "6")}
    second = domain.order.model_copy(update={"account_id": "6", "id": "yandex:6:FBS:123"})
    domain.reader.return_value = second
    context = {**CONTEXT, "campaignId": 6}
    domain.request.side_effect = [result(chats=[{**chat(), "context": context}]),
                                  result(context=context, messages=[message(1)])]
    service = OrderChatService(domain.reader, domain.config, accounts=accounts, request=domain.request)
    assert service.history(second.id)["messages"][0]["id"] == "1"
    domain.reader.assert_called_once_with(second.id, accounts)
    assert domain.config["yandex"]["campaign_id"] == "4"
    domain.request.reset_mock()
    domain.reader.return_value = second.model_copy(update={"account_id": "99"})
    with pytest.raises(ValueError, match="不属于"):
        service.history(second.id)
    domain.request.assert_not_called()


def test_send_creates_order_context_and_sends_once_without_history(domain):
    domain.request.side_effect = [result(chatId=17), {"status": "OK"}]
    assert domain.service.send(domain.order.id, "您好\n订单说明") == {"ok": True, "chat_id": "17"}
    calls = domain.request.call_args_list
    assert calls[0].args[1:] == ("/v2/businesses/8/chats/new", "secret", {"context": {"type": "ORDER", "id": 123}})
    assert calls[1].args[1:] == ("/v2/businesses/8/chats/message", "secret", {"message": "您好\n订单说明"})
    assert calls[1].kwargs["query"] == {"chatId": "17"}
    assert len(calls) == 2


@pytest.mark.parametrize("text", ["", " \n", "字" * 4097, None])
def test_invalid_message_never_contacts_platform(domain, text):
    with pytest.raises(ValueError, match="消息不能为空"):
        domain.service.send(domain.order.id, text)
    domain.request.assert_not_called()


@pytest.mark.parametrize("patch", [{"account_id": "5"}, {"platform": "ozon"}])
def test_wrong_account_or_platform_rejected_before_request(domain, patch):
    domain.reader.return_value = domain.order.model_copy(update=patch)
    with pytest.raises(ValueError, match="不属于"):
        domain.service.history(domain.order.id)
    domain.request.assert_not_called()


@pytest.mark.parametrize("context", [{**CONTEXT, "orderId": 456}, {**CONTEXT, "campaignId": 5}, None])
def test_remote_chat_and_history_must_match_order(domain, context):
    domain.request.side_effect = [result(chats=[{**chat(), "context": context}])]
    with pytest.raises(ValueError, match="归属"):
        domain.service.history(domain.order.id)
    domain.request.side_effect = [result(chats=[chat()]), result(context=context, messages=[])]
    with pytest.raises(ValueError, match="归属"):
        domain.service.history(domain.order.id)


def test_pagination_loop_and_partial_failure_do_not_return_partial_history(domain):
    page = result(context=CONTEXT, messages=[message(1)], paging={"nextPageToken": "same"})
    domain.request.side_effect = [result(chats=[chat()]), deepcopy(page), deepcopy(page)]
    with pytest.raises(ValueError, match="分页未前进"):
        domain.service.history(domain.order.id)
    domain.request.side_effect = [result(chats=[chat()]), page, PublishAdapterError("FAILED", "读取失败")]
    with pytest.raises(PublishAdapterError):
        domain.service.history(domain.order.id)


def test_unknown_write_is_not_retried(domain):
    failure = PublishAdapterError("EXTERNAL_WRITE_OUTCOME_UNKNOWN", "发送结果未知")
    domain.request.side_effect = [result(chatId=17), failure]
    with pytest.raises(PublishAdapterError):
        domain.service.send(domain.order.id, "订单说明")
    assert domain.request.call_count == 2


def test_attachments_only_expose_safe_https_links(domain):
    domain.request.side_effect = [result(chats=[chat()]), result(context=CONTEXT, messages=[message(1, payload=[
        {"name": "图片", "url": "https://example.com/a.jpg"},
        {"name": "不安全", "url": "javascript:alert(1)"},
    ])])]
    assert domain.service.history(domain.order.id)["messages"][0]["attachments"] == [{"name": "图片", "url": "https://example.com/a.jpg"}]


@pytest.mark.parametrize("suffix,semantics", [("", "read"), ("/history", "read"), ("/new", "write"), ("/message", "write"), ("/file/send", "write")])
def test_external_manager_classifies_chat_commands(monkeypatch, domain, suffix, semantics):
    monkeypatch.setattr("erp_web.services.external_request_context.get_context", lambda: SimpleNamespace(config=SimpleNamespace(load_store_config=lambda: domain.config)))
    ctx = request_context("https://api.partner.market.yandex.ru/v2/businesses/8/chats" + suffix, {"Api-Key": "secret"}, method="POST")
    assert ctx.semantics == semantics
    assert ctx.max_attempts == 1
    assert ctx.account_id == "8" and ctx.quota_key == "businesses:8"


def test_routes_validate_commands_and_report_platform_failures(monkeypatch):
    from erp_web.http_route_units import order_routes
    for action in ("history", "send"):
        path = "/api/orders/chat/" + action
        assert path in order_routes.POST_HANDLERS and path not in order_routes.GET_HANDLERS
        body = {"order_id": "local-id", **({"message": "你好"} if action == "send" else {})}
        assert validate_request_payload(body, endpoint=path) == body
        command = Mock(side_effect=PublishAdapterError("DENIED", "请开通买家沟通权限"))
        monkeypatch.setattr(order_routes.chat, "command", command)
        handler = SimpleNamespace(path=path, read_body=lambda: body, send_json=Mock())
        order_routes.POST_HANDLERS[path](handler)
        command.assert_called_once_with(action, body)
        assert handler.send_json.call_args.args == ({"ok": False, "error": "请开通买家沟通权限", "code": "DENIED"}, 502)


def test_chat_has_explicit_agent_capabilities_but_no_background_sync(domain):
    from erp_web.ai_capability_coverage import AI_CAPABILITY_COVERAGE_MANIFEST
    from erp_web.runtime_units.order_notifications import parse_notification
    for entry in AI_CAPABILITY_COVERAGE_MANIFEST:
        if entry.path.startswith("/api/orders/chat/"):
            assert entry.disposition == "capability"
            assert entry.capability_names == (("order_buyer_chat_read",) if entry.path.endswith("/history") else ("order_buyer_chat_send",))
    # 即使收到聊天通知，也不得入队触发读取。
    for topic in ("CHAT_CREATED", "CHAT_MESSAGE_SENT"):
        event, _ = parse_notification("yandex", {"notificationType": topic, "campaignId": 4}, domain.config)
        assert event is None


def test_real_order_store_is_unchanged_and_rejects_other_shop(tmp_path, domain):
    from erp_web.schemas.orders import OrderEvent, OrderSnapshot
    from erp_web.stores.order_notification_store import OrderNotificationStore
    from erp_web.stores.order_procurement_store import OrderProcurementStore

    orders = OrderNotificationStore(tmp_path / "orders.sqlite3")
    procurement = OrderProcurementStore(orders)
    snapshot = OrderSnapshot.model_validate(domain.order.model_dump(include=set(OrderSnapshot.model_fields)))
    orders.enqueue(OrderEvent(platform="yandex", account_id="4", topic="test", resource="123"))
    claim = orders.claim({"yandex": "4"}, now=1)
    orders.save_snapshot(claim, snapshot, now=1)
    orders.finish(claim, now=1)
    with orders.connect() as conn:
        before = list(conn.iterdump())
    service = OrderChatService(procurement.order, domain.config, request=domain.request)
    domain.request.side_effect = [result(chats=[chat()]), result(context=CONTEXT, messages=[message(1)]), result(chatId=17), {"status": "OK"}]
    assert service.history(snapshot.identity)["messages"]
    service.send(snapshot.identity, "测试消息正文不应落库")
    with orders.connect() as conn:
        assert list(conn.iterdump()) == before
    domain.config["yandex"]["campaign_id"] = "5"
    domain.request.reset_mock()
    with pytest.raises(ValueError, match="不属于当前店铺"):
        service.send(snapshot.identity, "不应发送")
    domain.request.assert_not_called()

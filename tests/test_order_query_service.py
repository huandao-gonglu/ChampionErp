"""按业务货号检索订单：冻结证据、覆盖范围、账号隔离与原生工具入口。"""

import asyncio
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units.order_source_bindings import bindings_from_publish_job
from erp_web.schemas.platform_query_capabilities import PlatformOrdersQueryRequest
from erp_web.schemas.orders import OrderEvent
from erp_web.services.order_query_service import OrderQueryService
from tests.test_order_procurement import domain, job, manual_source, purchase_body
from tests.test_native_agent_integration import body, service as chat_service


def save_order(procurement, order, *, checked_at="2026-10-12T12:58:07+00:00"):
    with procurement.store.orders.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?)",
            (order.identity, order.platform, order.account_id, order.state, order.model_dump_json(), 0, checked_at))
        conn.commit()


def bind(procurement, code="EE010", *, seller_sku="SALE-1", **changes):
    value = bindings_from_publish_job(job())[0]
    value = value.model_copy(update={"seller_sku": seller_sku, "remote_id": seller_sku,
        "source": value.source.model_copy(update={"specification": f"尺寸：{code} 20X20CM / 规格：20X20CM"}), **changes})
    procurement.store.add_bindings([value])
    return value


@pytest.fixture
def query_domain(domain):
    procurement, order, key = domain
    save_order(procurement, order)
    notifications = SimpleNamespace(store=procurement.store.orders, accounts=procurement.accounts_provider,
        sync_status=lambda: [{"platform": "yandex", "status": "done", "last_success_at": "2026-10-12T12:58:23+00:00"}],
        close=lambda: None)
    return OrderQueryService(procurement, notifications), order, key


def query(service, **kwargs):
    return service.query(PlatformOrdersQueryRequest(**kwargs))


def hits(result):
    return [hit for order in result["items"] for line in order["items"] for hit in line["matches"]]


def test_multiple_articles_find_frozen_sources_without_drafts(query_domain):
    service, order, _ = query_domain
    frozen = bind(service.procurement)
    second_line = order.items[0].model_copy(update={"sku": "OPAQUE-2", "remote_id": "OPAQUE-2", "quantity": 1})
    second = order.model_copy(update={"order_id": "456", "items": [second_line]})
    save_order(service.procurement, second)
    bind(service.procurement, "EE052", seller_sku="OPAQUE-2")
    frozen.source.specification = "可编辑对象后来已改变"
    result = query(service, article_numbers=["EE052", "ee010"])
    assert {row["order_id"] for row in result["items"]} == {"123", "456"}
    assert result["pagination"]["total"] == 2
    assert {hit["term"] for hit in hits(result)} == {"EE052", "ee010"}
    assert all(hit["kind"] == "published_source" and hit["publication_id"] == "job-1" for hit in hits(result))
    assert result["coverage"]["unresolved_source_lines"] == 0
    assert result["coverage"]["scanned_orders"] == 2
    assert query(service, q="EE052")["pagination"]["total"] == 1


def test_article_matching_does_not_guess_from_titles_or_prefixes(query_domain):
    service, order, _ = query_domain
    bind(service.procurement, "EE0100")
    line = order.items[0].model_copy(update={"title": "EE010 合集"})
    save_order(service.procurement, order.model_copy(update={"title": "EE010 合集", "items": [line]}))
    assert query(service, article_numbers=["EE010"])["pagination"]["total"] == 0
    assert query(service, article_numbers=["SALE-1"])["pagination"]["total"] == 1
    assert query(service, q="EE010")["pagination"]["total"] == 1


def test_ambiguous_bindings_are_candidates_not_verified_matches(query_domain):
    service, _, _ = query_domain
    bind(service.procurement, "EE010")
    bind(service.procurement, "EE052")
    result = query(service, article_numbers=["EE010"])
    assert result["pagination"]["total"] == 1
    assert {hit["certainty"] for hit in hits(result)} == {"ambiguous"}
    assert result["coverage"]["ambiguous_source_lines"] == 1
    assert result["coverage"]["unresolved_source_lines"] == 1


def test_account_and_publication_identities_are_isolated(query_domain):
    service, order, _ = query_domain
    bind(service.procurement, store_identity="another-shop")
    bind(service.procurement, remote_id="different-remote")
    bind(service.procurement, variant_id="different-variant")
    line = order.items[0].model_copy(update={"variant_id": "actual-variant"})
    save_order(service.procurement, order.model_copy(update={"items": [line]}))
    # 另一账号订单即使 SKU 及货号一致也不能进入当前结果。
    other = order.model_copy(update={"account_id": "another-account", "items": [line.model_copy(update={"sku": "EE010"})]})
    save_order(service.procurement, other)
    result = query(service, article_numbers=["EE010"])
    assert result["items"] == []
    assert result["coverage"]["scanned_orders"] == 1
    assert result["coverage"]["unresolved_source_lines"] == 1


def test_confirmed_and_purchase_sources_retain_separate_evidence(query_domain):
    service, order, key = query_domain
    procurement = service.procurement
    procurement.select_source({"order_id": order.identity, "line_key": key, "revision": 0,
        "source": {**manual_source(), "specification": "EE010 20X20CM"}})
    request = purchase_body(order, key)
    request['items'][0]['source']['specification'] = 'EE010 20X20CM'
    record = procurement.record_purchase(request)["purchases"][0]
    procurement.select_source({"order_id": order.identity, "line_key": key, "revision": 1,
        "source": {**manual_source(), "specification": "EE052 20X20CM"}})
    result = query(service, article_numbers=["EE010", "EE052"])
    assert {(hit["term"], hit["kind"]) for hit in hits(result)} == {
        ("EE010", "purchase_record"), ("EE052", "confirmed_source")}
    assert next(hit for hit in hits(result) if hit["kind"] == "purchase_record")["purchase_quantity"] == 1
    procurement.cancel_purchase({"order_id": order.identity, "record_id": record["id"]})
    assert query(service, article_numbers=["EE010"])["items"] == []


def test_changed_line_signature_excludes_saved_sources_and_purchases(query_domain):
    service, order, key = query_domain
    service.procurement.select_source({"order_id": order.identity, "line_key": key, "revision": 0,
        "source": {**manual_source(), "specification": "EE010"}})
    service.procurement.record_purchase(purchase_body(order, key))
    line = order.items[0].model_copy(update={"remote_id": "new-remote"})
    save_order(service.procurement, order.model_copy(update={"items": [line]}))
    assert query(service, article_numbers=["EE010"])["items"] == []


def test_duplicate_order_line_identity_does_not_guess_source(query_domain):
    service, order, _ = query_domain
    bind(service.procurement)
    save_order(service.procurement, order.model_copy(update={"items": [order.items[0], order.items[0]]}))
    result = query(service, article_numbers=["EE010"])
    assert result["items"] == []
    assert result["coverage"]["unresolved_source_lines"] == 2


def test_pagination_searches_beyond_first_batch_and_has_bounded_sql(query_domain, monkeypatch):
    service, order, _ = query_domain
    for index in range(205):
        line = order.items[0].model_copy(update={"sku": f"OPAQUE-{index}", "remote_id": f"OPAQUE-{index}"})
        save_order(service.procurement, order.model_copy(update={"order_id": f"batch-{index:03}", "items": [line]}))
    for index in [150, 200]:
        bind(service.procurement, seller_sku=f"OPAQUE-{index}")
    connect, statements = service.procurement.store.orders.connect, []
    @contextmanager
    def traced():
        with connect() as conn:
            conn.set_trace_callback(statements.append)
            yield conn
    monkeypatch.setattr(service.procurement.store.orders, "connect", traced)
    first = query(service, article_numbers=["EE010"], limit=1)
    assert first["pagination"]["total"] == 2 and first["coverage"]["has_more"]
    assert first["coverage"]["scanned_orders"] == 206
    assert len([sql for sql in statements if sql.startswith("SELECT")]) == 10
    second = query(service, article_numbers=["EE010"], limit=1, offset=1)
    assert not second["coverage"]["has_more"]
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert query(service, state="shipped")["items"] == []


def test_notifications_are_opt_in_platform_scoped_and_freshness_is_explicit(query_domain, monkeypatch):
    service, order, _ = query_domain
    service.notifications.store.enqueue(OrderEvent(platform="ozon", account_id="9", topic="test", resource="other"))
    service.notifications.accounts = lambda: {"yandex": "4", "ozon": "9"}
    service.notifications.sync_status = lambda: [
        {"platform": "yandex", "status": "done", "last_success_at": "2026-10-12T12:58:23+00:00"},
        {"platform": "ozon", "status": "blocked", "last_success_at": "", "error": "授权已停用"}]
    read = service.notifications.store.read_notifications
    monkeypatch.setattr(service.notifications.store, "read_notifications", lambda _: pytest.fail("默认查询不读取通知"))
    result = query(service, platform="yandex")
    assert not result["notifications"] and len(result["sync_status"]) == 1
    coverage = result["coverage"]
    assert coverage["oldest_snapshot_at"] == coverage["newest_snapshot_at"] == "2026-10-12T12:58:07+00:00"
    assert not coverage["realtime_verified"] and not coverage["platform_history_complete"]
    assert coverage["queried_at"] != coverage["newest_snapshot_at"]
    assert query(service, platform="mercadolibre")["coverage"]["unavailable_platforms"] == ["mercadolibre"]
    monkeypatch.setattr(service.notifications.store, "read_notifications", read)
    result = query(service, platform="yandex", include_notifications=True)
    assert result["notifications"] and {row["platform"] for row in result["notifications"]} == {"yandex"}
    assert len(query(service)["sync_status"]) == 2


@pytest.mark.parametrize("payload", [{"article_numbers": [" "]}, {"article_numbers": ["EE010"] * 21},
    {"q": "EE010", "article_numbers": ["EE052"]}, {"state": "typo"}])
def test_invalid_queries_fail_before_reading(payload):
    with pytest.raises(ValidationError):
        PlatformOrdersQueryRequest(**payload)


@pytest.mark.parametrize("entry", ["direct", "python"])
def test_main_agent_queries_articles_through_native_toolset(query_domain, tmp_path, entry):
    service, order, _ = query_domain
    bind(service.procurement)
    app = get_context()
    app._order_procurement = service.procurement
    app._order_notifications = service.notifications
    outputs = []
    async def model(messages, info):
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returned:
            outputs.extend(part.content for part in returned)
            yield "本地快照匹配完成"
        else:
            args = {"article_numbers": ["EE052", "EE010"]}
            if entry == "python":
                args = {"code": "r = await platform_orders_query(article_numbers=['EE052', 'EE010'])\nr"}
            yield {0: DeltaToolCall(name="run_code" if entry == "python" else "platform_orders_query",
                tool_call_id="query-orders", json_args=json.dumps(args))}
    ui = chat_service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body("查看 EE052、EE010 在哪些订单中下单了")).stream(chunks.append))
    assert "TOOL_OUTPUT_SCHEMA_INVALID" not in b"".join(chunks).decode()
    assert len(outputs) == 1 and outputs[0]["items"][0]["id"] == order.identity
    assert outputs[0]["coverage"]["scope"] == "local_snapshot" and not outputs[0]["notifications"]

"""在线接口复用、Code Mode 聚合、原生审批和任务回执的隔离验收。"""
import asyncio
from dataclasses import replace
import json
import time

import pytest
from pydantic_ai.messages import RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset, build_job_status_readers
from erp_web.runtime_units.online_product_capabilities import (
    OnlineProductCapabilityScope,
    _change_snapshot, _retry_snapshot, online_products_change, online_products_read,
    online_products_sync, online_products_retry, online_products_reconcile,
)
from erp_web.runtime_units.online_product_job_reader import OnlineProductJobReader
from erp_web.schemas.online_product_capabilities import OnlineReadRequest, OnlineJobRequest, OnlineSyncRequest
from erp_web.schemas.online_products import BuyerLink, OnlineChange
from erp_web.schemas.ai_tools import AiToolCommand, AiToolExecutionError
from erp_web.services.ai_tool_runtime import AiToolRuntime
from erp_web.services.agent_job_service import AgentJobService
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.online_product_service import OnlineProductService
from tests.test_online_products import RemoteFixture, listing
from tests.approval_support import _approved_execution, _execution
from tests.test_native_agent_integration import CONVERSATION, body, service


@pytest.fixture
def online(monkeypatch):
    app = get_context()
    config = {"mercadolibre": {"user_id": "seller"}}
    monkeypatch.setattr(app.config, "load_store_config", lambda: config)
    remote = RemoteFixture()
    domain = OnlineProductService(app, adapter_factories={"mercadolibre": lambda _: remote}, start_worker=False)
    app._online_products = domain
    domain.store.save(remote.rows["CBT1"])
    yield app, domain, remote, OnlineProductCapabilityScope(lambda: domain), config
    domain.close()


def change(domain, **kwargs):
    return OnlineChange(listing_id="CBT1", version=domain.store.get("CBT1").version,
                        operation="price", scope_id="global", changes={"amount": "15.25", "currency": "USD"}, **kwargs)


def test_reads_share_page_order_and_detail_payload_and_enforce_current_account(online):
    _, domain, _, scope, config = online
    page = domain.list("mercadolibre")
    result = online_products_read(OnlineReadRequest(), scope).model_dump()
    assert result["items"][0]["id"] == page["items"][0]["id"]
    assert result["groups"][0]["id"] == page["groups"][0]["id"]
    assert "jobs" not in result and "content" not in result["items"][0]
    detail = online_products_read(OnlineReadRequest(id="CBT1"), scope).model_dump()["item"]
    assert detail == domain.detail("CBT1")["item"]
    assert "snapshot" not in detail
    config["mercadolibre"]["user_id"] = "another-store"
    assert online_products_read(OnlineReadRequest(), scope).total == 0
    with pytest.raises(BusinessCapabilityError, match="店铺身份"):
        online_products_read(OnlineReadRequest(id="CBT1"), scope)


def test_projected_detail_reuses_account_check_and_keeps_business_context(online):
    _, domain, _, scope, config = online
    result = online_products_read(OnlineReadRequest(id="CBT1", fields=["content.title", "stocks"]), scope)
    assert result.item is None and result.items == [] and len(result.records) == 1
    record = result.records[0]
    assert record.id == "CBT1" and record.account_id == "seller"
    assert record.values["content.title"] == "测试商品"
    assert record.values["stocks"][0]["id"] == "shared"
    assert record.version == domain.store.get("CBT1").version
    assert record.missing_fields == []
    assert "pictures" not in record.model_dump_json() and "snapshot" not in record.model_dump_json()
    config["mercadolibre"]["user_id"] = "another-store"
    assert online_products_read(OnlineReadRequest(fields=["stocks"]), scope).records == []
    with pytest.raises(BusinessCapabilityError, match="店铺身份"):
        online_products_read(OnlineReadRequest(id="CBT1", fields=["stocks"]), scope)


@pytest.fixture
def large_online_group(online):
    """重现 198 个变体、长详情和大同步回执，不接触真实店铺。"""
    app, domain, remote, scope, config = online
    config["yandex"] = {"business_id": "business", "campaign_id": "campaign"}
    for number in range(198):
        row = listing(f"YDX{number:03}")
        row.platform, row.account_id, row.model = "yandex", "business:campaign", "business_offer"
        row.snapshot = {"offer": {"groupId": "真实组合"}}
        row.content = {"description": "长描述不应随列表返回。" * 1000, "attributes": [{"name": "尺寸", "value": "20×20"}]}
        row.stocks[0].quantity = None if number == 0 else number % 3
        domain.store.save(row)
    job = domain.store.enqueue("yandex", "business:campaign", "sync", "*", {}, "test-large-sync")
    with domain.store.db._connect() as conn:
        conn.execute("UPDATE online_jobs SET status='confirmed',result_json=? WHERE id=?", (json.dumps({
            "discovered": 198, "completed": 198, "failed": 0, "discovery_complete": True,
            "items": [{"remote_id": "大回执" * 10000}] * 3,
        }), job["id"]))
        conn.commit()
    return online


@pytest.mark.parametrize("entry", ["direct", "python"])
def test_first_online_product_needs_one_bounded_read(large_online_group, tmp_path, monkeypatch, entry):
    app, domain, _, scope, _ = large_online_group
    http_page = domain.list("yandex")
    assert len(json.dumps(http_page, ensure_ascii=False).encode()) > 262144
    expected = http_page["items"][0]["id"]

    def forbid_full_payload(*args, **kwargs):
        pytest.fail("AI 列表不得调用页面完整载荷或加载历史任务")
    monkeypatch.setattr(domain, "list", forbid_full_payload)
    monkeypatch.setattr(domain.store, "jobs", forbid_full_payload)
    compact = online_products_read(OnlineReadRequest(platform="yandex", limit=1), scope)
    assert compact.items[0].id == expected
    assert compact.groups[0].total_count == compact.groups[0].matched_count == 198
    assert compact.latest_sync.completed == 198 and compact.latest_sync.discovery_complete is True
    assert len(compact.model_dump_json().encode()) < 5000
    empty = online_products_read(OnlineReadRequest(platform="yandex", q="没有此商品"), scope)
    assert empty.total == 0 and empty.summary["total"] == 198 and empty.latest_sync.completed == 198
    assert len(empty.model_dump_json().encode()) < 2000
    requests, outputs = [], []

    async def model(messages, info):
        requests.append(1)
        assert not any(isinstance(p, RetryPromptPart) for m in messages for p in m.parts)
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if returned:
            outputs.append(returned[-1].content)
            yield "第一个在线商品是测试商品，组合包含 198 个 SKU。"
        elif entry == "direct":
            yield {0: DeltaToolCall(name="online_products_read", tool_call_id="first", json_args=json.dumps({
                "platform": "yandex", "limit": 1,
            }))}
        else:
            yield {0: DeltaToolCall(name="run_code", tool_call_id="first", json_args=json.dumps({"code":
                "r = await online_products_read(platform='yandex', limit=1)\n"
                "{'id': r['items'][0]['id'], 'count': r['groups'][0]['total_count']}"
            }))}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body("当前在线商品的第一个是什么")).stream(chunks.append))
    assert len(requests) == 2 and len(outputs) == 1, b"".join(chunks).decode()
    assert (outputs[0]["items"][0]["id"] if entry == "direct" else outputs[0]["id"]) == expected
    assert not ui.call_store.current_turn_receipts(CONVERSATION)


def test_code_mode_pages_all_group_members_under_normal_output_limit(large_online_group, tmp_path):
    app, _, _, _, _ = large_online_group
    outputs = []

    async def model(messages, info):
        assert not any(isinstance(p, RetryPromptPart) for m in messages for p in m.parts)
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if returned:
            outputs.append(returned[-1].content)
            yield "已读取组合全部 198 个 SKU。"
        else:
            yield {0: DeltaToolCall(name="run_code", tool_call_id="members", json_args=json.dumps({"code":
                "first = await online_products_read(platform='yandex', limit=1)\n"
                "group_id = first['groups'][0]['id']\n"
                "number = 1\nrows = []\n"
                "while number is not None:\n"
                "    result = await online_products_read(platform='yandex', view='listings', group_id=group_id, page=number, limit=25)\n"
                "    rows.extend(result['items'])\n"
                "    number = result['next_page']\n"
                "{'count': len(rows), 'unique': len(set(row['id'] for row in rows)), "
                "'unknown': sum(1 for row in rows if row['stocks'][0]['quantity'] is None), "
                "'zero': sum(1 for row in rows if row['stocks'][0]['quantity'] == 0)}"
            }))}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body("统计第一个组合的库存")).stream(chunks.append))
    assert outputs == [{"count": 198, "unique": 198, "unknown": 1, "zero": 65}], b"".join(chunks).decode()


def test_code_mode_locates_identifier_with_four_projected_pages_and_no_detail_reads(
        large_online_group, tmp_path, monkeypatch):
    """按字段取数后由原生 CodeMode 完成匹配，不为具体编号增加服务端搜索。"""
    app, domain, remote, scope, _ = large_online_group
    target = "229411801149030400"
    matched_id = "YDX188"
    for row in domain.store.listings("yandex", "business:campaign"):
        number = int(row.id[3:])
        row.seller_sku = f"SKU-{number}"
        row.buyer_links = [BuyerLink(label="Yandex Market", site_id="B2C",
                            url=f"https://market.yandex.ru/card/slug/{target if row.id == matched_id else str(number)}?businessId=business")]
        row.content["attributes"] = [{"name": "款号", "value": "EE001" if row.id == matched_id else "其他款号"},
                                     {"name": "尺寸", "value": "25", "unit": "cm"}]
        domain.store.save(row)
    group_id = online_products_read(OnlineReadRequest(platform="yandex", limit=1), scope).groups[0].id
    calls, outputs, requests = [], [], []
    read_page = domain.read_page

    def track_pages(platform, **kwargs):
        calls.append(kwargs)
        return read_page(platform, **kwargs)

    def forbid_details(*args, **kwargs):
        pytest.fail("批量字段取数不能逐件读取详情或整组页面载荷")

    monkeypatch.setattr(domain, "read_page", track_pages)
    monkeypatch.setattr(domain, "detail", forbid_details)
    monkeypatch.setattr(domain, "list", forbid_details)
    script = (
        f"group_id = {group_id!r}\n"
        f"target = {target!r}\n"
        "number = 1\ncount = 0\nmatches = []\nmissing = []\n"
        "while number is not None:\n"
        "    page = await online_products_read(platform='yandex', view='listings', group_id=group_id, "
        "page=number, limit=50, fields=['buyer_links', 'content.attributes'])\n"
        "    for record in page['records']:\n"
        "        count += 1\n"
        "        if record['missing_fields']:\n"
        "            missing.append(record['id'])\n"
        "        for link in record['values']['buyer_links']:\n"
        "            card_id = link['url'].split('?')[0].rstrip('/').split('/')[-1]\n"
        "            if card_id == target:\n"
        "                matches.append({'id': record['id'], 'sku': record['seller_sku'], "
        "'attributes': record['values']['content.attributes']})\n"
        "    number = page['next_page']\n"
        "{'count': count, 'matches': matches, 'missing': missing}"
    )

    async def model(messages, info):
        requests.append(1)
        assert not any(isinstance(p, RetryPromptPart) for m in messages for p in m.parts)
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if returned:
            outputs.append(returned[-1].content)
            yield "编号唯一对应 EE001、25 cm 的 SKU。"
        else:
            yield {0: DeltaToolCall(name="run_code", tool_call_id="find-card", json_args=json.dumps({"code": script}))}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body(f"当前组合哪个 SKU 对应编号 {target}")).stream(chunks.append))
    assert outputs == [{"count": 198, "matches": [{"id": matched_id, "sku": "SKU-188", "attributes": [
        {"name": "款号", "value": "EE001"}, {"name": "尺寸", "value": "25", "unit": "cm"}]}], "missing": []}], b"".join(chunks).decode()
    assert len(requests) == 2 and len(calls) == 4
    assert [call["page"] for call in calls] == [1, 2, 3, 4]
    assert all(call["group_id"] == group_id and call["fields"] == ["buyer_links", "content.attributes"] for call in calls)
    assert len(json.dumps(outputs, ensure_ascii=False)) < 500
    assert not remote.writes and not ui.call_store.current_turn_receipts(CONVERSATION)


@pytest.mark.parametrize("entry", ["direct", "python", "python_retry"])
@pytest.mark.parametrize("include_platform", [False, True])
def test_yandex_details_pass_native_tool_output_validation(online, tmp_path, entry, include_platform):
    """复现状态追问：详情省略顶层平台时，直接调用和 Python 并发读取都能返回。"""
    app, domain, remote, _, config = online
    config["yandex"] = {"business_id": "business", "campaign_id": "campaign"}
    for listing_id in ("YDX1", "YDX2"):
        row = listing(listing_id)
        row.platform = "yandex"
        row.account_id = "business:campaign"
        row.raw_status = "DISABLED_AUTOMATICALLY"
        domain.store.save(row)
    platform_arg = ", platform='yandex'" if include_platform else ""
    code = (
        "lst = await online_products_read(platform='yandex', status='DISABLED_AUTOMATICALLY')\n"
        "ids = [i['id'] for i in lst['items']]\n"
        f"details = await asyncio.gather(*[online_products_read(id=x{platform_arg}) for x in ids])\n"
        "out = []\n"
        "for d in details:\n"
        "    item = d.get('item') or {}\n"
        "    out.append({'platform': d.get('platform'), 'id': item.get('id'), 'status': item.get('raw_status')})\n"
        "out"
    )
    outputs = []
    attempts = 0

    async def model(messages, info):
        nonlocal attempts
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returned:
            if entry == "direct":
                outputs.extend({"platform": p.content["platform"], "id": p.content["item"]["id"],
                                "status": p.content["item"]["raw_status"]} for p in returned)
            else:
                outputs.extend(returned[-1].content)
            yield "详情已读取。"
            return
        attempts += 1
        if entry == "direct":
            yield {n: DeltaToolCall(name="online_products_read", tool_call_id=f"detail-{n}",
                json_args=json.dumps({"id": listing_id, **({"platform": "yandex"} if include_platform else {})}))
                for n, listing_id in enumerate(("YDX1", "YDX2"))}
        else:
            script = code if entry == "python_retry" and attempts == 1 else "import asyncio\n" + code
            yield {0: DeltaToolCall(name="run_code", tool_call_id=f"details-{attempts}",
                                   json_args=json.dumps({"code": script}))}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body("查看这两个停售商品的状态详情")).stream(chunks.append))
    assert sorted(outputs, key=lambda item: item["id"]) == [
        {"platform": None, "id": listing_id, "status": "DISABLED_AUTOMATICALLY"}
        for listing_id in ("YDX1", "YDX2")
    ], b"".join(chunks).decode()
    retries = [part for message in ui.chat_service.trusted_history(CONVERSATION)
               for part in message.parts if isinstance(part, RetryPromptPart)]
    assert len(retries) == (1 if entry == "python_retry" else 0)
    assert attempts == (2 if entry == "python_retry" else 1)
    assert not remote.writes and not domain.store.jobs("yandex", "business:campaign")


def test_code_mode_can_compute_over_all_pages_without_new_query_functions(online, tmp_path):
    app, domain, _, _, _ = online
    for number in range(31):
        row = listing(f"CBT{number}")
        row.stocks[0].quantity = [0, 8, None][number % 3]
        domain.store.save(row)
    outputs = []

    async def model(messages, info):
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returned:
            outputs.append(returned[-1].content)
            yield "统计完成，未知库存单独列出。"
        else:
            yield {0: DeltaToolCall(name="run_code", tool_call_id="count", json_args=json.dumps({"code":
                "page = await online_products_read(platform='mercadolibre', view='listings')\n"
                "items = page['items']\n"
                "for number in range(2, (page['total'] + page['per_page'] - 1) // page['per_page'] + 1):\n"
                "    next_page = await online_products_read(platform='mercadolibre', view='listings', page=number)\n"
                "    items.extend(next_page['items'])\n"
                "{'total': len(items), 'zero': sum(1 for item in items if item['stocks'][0]['quantity'] == 0), "
                "'unknown': sum(1 for item in items if item['stocks'][0]['quantity'] is None)}"
            }))}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body("统计在线商品库存")).stream(chunks.append))
    assert outputs == [{"total": 31, "zero": 11, "unknown": 10}], b"".join(chunks).decode()


@pytest.mark.parametrize("operation,scope_id,changes", [
    ("price", "global", {"amount": "18.25", "currency": "USD"}),
    ("stock", "shared", {"quantity": 100}),
    ("content", "global", {"title": "新标题"}),
    ("sale_state", "", {"state": "paused"}),
])
def test_one_change_tool_reuses_all_existing_mutations(online, operation, scope_id, changes):
    _, domain, remote, scope, _ = online
    request = OnlineChange(listing_id="CBT1", version=domain.store.get("CBT1").version,
                           operation=operation, scope_id=scope_id, changes=changes)
    snapshot = _change_snapshot(request, scope)
    if operation == "content":
        assert "基础价" not in snapshot.summary
    execution = _approved_execution(snapshot, "online_products_change")
    result = online_products_change(request, scope, execution)
    repeated = online_products_change(request, scope, execution)
    assert repeated.job_id == result.job_id
    job = domain.store.job(result.job_id)
    assert {key: job["request"][key] for key in type(request).model_fields} == request.model_dump()
    assert result.accepted is True and result.status == "queued"
    assert result.listing_id == request.listing_id
    assert "job_type" not in result.model_dump()
    assert not remote.writes


def test_approval_rejects_parameter_changes_and_stale_business_versions(online):
    _, domain, _, scope, _ = online
    request = change(domain)
    execution = _approved_execution(_change_snapshot(request, scope), "online_products_change")
    modified = request.model_copy(update={"changes": {"amount": "99", "currency": "USD"}})
    with pytest.raises(AiToolExecutionError) as exc:
        online_products_change(modified, scope, execution)
    assert exc.value.code == "ONLINE_APPROVAL_STALE"
    current = domain.store.get("CBT1")
    current.title = "已在其他地方修改"
    domain.store.save(current)
    with pytest.raises(BusinessCapabilityError, match="快照已更新"):
        online_products_change(request, scope, execution)
    assert domain.store.jobs("mercadolibre", "seller") == []


@pytest.mark.parametrize("permissions,allow_write", [((), True), (("online_product.write",), False), (("online_product.write",), True)])
def test_runtime_permissions_and_approval_cannot_be_bypassed(online, permissions, allow_write):
    app, domain, remote, _, _ = online
    execution = replace(_execution(), permissions=frozenset(permissions), allow_write=allow_write)
    runtime = AiToolRuntime(toolset=build_global_chat_toolset(app), execution_context=execution)
    result = runtime.execute(AiToolCommand(call_id="unapproved", tool_name="online_products_change", tool_version="1",
                                           arguments=change(domain).model_dump(), round=1))
    assert not result.ok
    assert not remote.writes and not domain.store.jobs("mercadolibre", "seller")


def test_sync_and_failure_retry_reuse_business_jobs(online):
    _, domain, remote, scope, _ = online
    remote.rows["CBT2"] = listing("CBT2")
    remote.rows["CBT2"].errors = ["平台详情读取失败"]
    result = online_products_sync(OnlineSyncRequest(platform="mercadolibre"), scope, _execution())
    domain.run_once()
    state = OnlineProductJobReader(scope.job).read_job_state(result.job_id)
    assert state.status == "failed" and state.last_external_status == "partial"
    retry = OnlineJobRequest(job_id=result.job_id)
    execution = _approved_execution(_retry_snapshot(retry, scope), "online_products_retry", operation_key="retry")
    retried = online_products_retry(retry, scope, execution)
    assert retried.accepted is True and "job_type" not in retried.model_dump()
    assert domain.store.job(retried.job_id)["request"]["ids"] == ["CBT2"]
    assert not remote.writes


def test_unknown_is_reported_and_reconcile_never_resends_the_change(online):
    _, domain, remote, scope, _ = online
    request = change(domain)
    execution = _approved_execution(_change_snapshot(request, scope), "online_products_change")
    job = online_products_change(request, scope, execution)
    remote.write_error = TimeoutError("提交后连接断开")
    domain.run_once()
    reader = OnlineProductJobReader(scope.job)
    assert reader.read_job_state(job.job_id).last_external_status == "outcome_unknown"
    with pytest.raises(BusinessCapabilityError, match="未知结果"):
        _retry_snapshot(OnlineJobRequest(job_id=job.job_id), scope)
    remote.rows["CBT1"].prices[0].amount = "15.25"
    result = online_products_reconcile(OnlineJobRequest(job_id=job.job_id), scope, _execution())
    assert result.job.status == "confirmed"
    assert reader.read_job_state(job.job_id).status == "success"
    assert len(remote.writes) == 1


@pytest.mark.parametrize("status,polls,expected", [
    ("queued", 0, "running"), ("running", 0, "running"), ("waiting_confirmation", 19, "running"),
    ("waiting_confirmation", 20, "failed"), ("submitted", 20, "failed"), ("confirmed", 1, "success"),
    ("outcome_unknown", 1, "failed"), ("partial", 1, "failed"), ("failed", 1, "failed"),
])
def test_job_reader_preserves_platform_state_without_infinite_wait(status, polls, expected):
    reader = OnlineProductJobReader(lambda _: {"platform": "yandex", "operation": "stock", "status": status, "result": {"polls": polls}})
    state = reader.read_job_state("job")
    assert state.status == expected
    assert state.last_external_status == status


@pytest.mark.parametrize("mode", ["ask", "full"])
@pytest.mark.parametrize("count", [1, 3])
def test_native_approval_finishes_batch_after_enqueue_without_platform_wait(online, tmp_path, mode, count):
    app, domain, remote, _, _ = online
    requests = []
    for index in range(count):
        row = listing(f"CBT{index + 1}")
        domain.store.save(row)
        remote.rows[row.id] = row
        requests.append(change(domain).model_copy(update={"listing_id": row.id,
                        "version": domain.store.get(row.id).version}).model_dump())
    results = []

    async def model(messages, info):
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returned:
            results.extend(part.content for part in returned)
            yield "修改请求已提交，远端处理结果请在操作记录查看。"
        else:
            yield {index: DeltaToolCall(name="online_products_change", json_args=json.dumps(request),
                                       tool_call_id=f"online-change-{index}") for index, request in enumerate(requests)}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    ui.chat_service.approval_mode_reader = lambda: mode
    asyncio.run(ui.prepare_run(body("修改在线商品价格为 15.25 美元")).stream(lambda _: None))
    assert not remote.writes and not domain.store.jobs("mercadolibre", "seller")
    if mode == "ask":
        parts = [part for message in ui.dump_ui_messages(CONVERSATION)["messages"] for part in message["parts"]
                 if part.get("state") == "approval-requested"]
        assert len(parts) == count
        for part in parts:
            part["state"] = "approval-responded"
            part["approval"]["approved"] = True
        payload = json.dumps({"id": CONVERSATION, "trigger": "submit-message",
                              "messages": [{"id": "approval", "role": "assistant", "parts": parts}]}).encode()
        asyncio.run(ui.prepare_run(payload, approval_token="test-token").stream(lambda _: None))
    worker = AgentJobService(ui_service=ui, job_readers=build_job_status_readers(app))
    try:
        for row in ui.call_store.work():
            worker.execute_or_reconcile(row)
        assert not remote.writes
        for index in range(count):
            receipt = ui.call_store.receipt(CONVERSATION, f"online-change-{index}")
            assert receipt["status"] == "completed"
        assert len(domain.store.jobs("mercadolibre", "seller")) == count
        assert all(job["status"] == "queued" for job in domain.store.jobs("mercadolibre", "seller"))
        worker.scan()
        for _ in range(200):
            if not ui.run_registry.is_active(CONVERSATION):
                break
            time.sleep(.01)
        assert not remote.writes
        assert len(results) == count and all(result["accepted"] for result in results)
        assert all("evidence" not in result and "job_type" not in result for result in results)
        assert not ui.call_store.pending(CONVERSATION)
        # Agent 已结束后，领域任务仍可独立执行；远端失败不会重新唤醒模型。
        remote.write_error = TimeoutError("平台响应时间不确定")
        domain.run_once()
        assert len(remote.writes) == 1
        assert any(job["status"] == "outcome_unknown" for job in domain.store.jobs("mercadolibre", "seller"))
        worker.scan()
        assert len(results) == count and not ui.run_registry.is_active(CONVERSATION)
    finally:
        worker.close()

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
    ONLINE_PRODUCT_JOB_TYPE, OnlineProductCapabilityScope,
    _change_snapshot, _retry_snapshot, online_products_change, online_products_read,
    online_products_sync, online_products_retry, online_products_reconcile,
)
from erp_web.runtime_units.online_product_job_reader import OnlineProductJobReader
from erp_web.schemas.online_product_capabilities import OnlineReadRequest, OnlineJobRequest, OnlineSyncRequest
from erp_web.schemas.online_products import OnlineChange
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


def test_reads_reuse_page_and_detail_payload_and_enforce_current_account(online):
    _, domain, _, scope, config = online
    page = domain.list("mercadolibre")
    result = online_products_read(OnlineReadRequest(), scope).model_dump()
    assert {key: result[key] for key in page} == page
    detail = online_products_read(OnlineReadRequest(id="CBT1"), scope).model_dump()["item"]
    assert detail == domain.detail("CBT1")["item"]
    assert "snapshot" not in detail
    config["mercadolibre"]["user_id"] = "another-store"
    assert online_products_read(OnlineReadRequest(), scope).total == 0
    with pytest.raises(BusinessCapabilityError, match="店铺身份"):
        online_products_read(OnlineReadRequest(id="CBT1"), scope)


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
                "page = await online_products_read(platform='mercadolibre')\n"
                "items = page['items']\n"
                "for number in range(2, (page['total'] + page['per_page'] - 1) // page['per_page'] + 1):\n"
                "    next_page = await online_products_read(platform='mercadolibre', page=number)\n"
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
    assert result.job_type == ONLINE_PRODUCT_JOB_TYPE
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
def test_native_approval_deferred_job_and_platform_readback(online, tmp_path, mode):
    app, domain, remote, _, _ = online
    request = change(domain).model_dump()
    results = []

    async def model(messages, info):
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returned:
            results.append(returned[-1].content)
            yield "价格已由平台回读确认。"
        else:
            yield {0: DeltaToolCall(name="online_products_change", json_args=json.dumps(request), tool_call_id="online-change")}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    ui.chat_service.approval_mode_reader = lambda: mode
    asyncio.run(ui.prepare_run(body("修改在线商品价格为 15.25 美元")).stream(lambda _: None))
    assert not remote.writes and not domain.store.jobs("mercadolibre", "seller")
    if mode == "ask":
        parts = [part for message in ui.dump_ui_messages(CONVERSATION)["messages"] for part in message["parts"]
                 if part.get("state") == "approval-requested"]
        assert len(parts) == 1
        parts[0]["state"] = "approval-responded"
        parts[0]["approval"]["approved"] = True
        payload = json.dumps({"id": CONVERSATION, "trigger": "submit-message",
                              "messages": [{"id": "approval", "role": "assistant", "parts": parts}]}).encode()
        asyncio.run(ui.prepare_run(payload, approval_token="test-token").stream(lambda _: None))
    worker = AgentJobService(ui_service=ui, job_readers=build_job_status_readers(app))
    try:
        for row in ui.call_store.work():
            worker.execute_or_reconcile(row)
        assert not remote.writes
        receipt = ui.call_store.receipt(CONVERSATION, "online-change")
        assert receipt["status"] == "waiting_job"
        domain.run_once()
        from test_online_products import expire
        pending = domain.store.jobs("mercadolibre","seller")[0]
        assert pending["status"] == "submitted"
        expire(domain,pending["id"])
        domain.run_once()
        for row in ui.call_store.work():
            worker.execute_or_reconcile(row)
        worker.scan()
        for _ in range(200):
            if not ui.run_registry.is_active(CONVERSATION):
                break
            time.sleep(.01)
        assert len(remote.writes) == 1
        assert results and results[-1]["ok"] is True
        assert results[-1]["evidence"]["last_external_status"] == "confirmed"
    finally:
        worker.close()

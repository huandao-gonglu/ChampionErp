"""在线批量提交、任务状态与原生结束校验的回归。平台 I/O 使用隔离替身。"""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry, UsageLimits
from pydantic_ai.exceptions import UnexpectedModelBehavior, RunCancelled
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from erp_web.runtime_units.online_product_capabilities import (
    _changes_snapshot, _close_snapshot, online_products_changes_apply, online_products_job_read,
)
from erp_web.schemas.online_product_capabilities import OnlineChangesRequest, OnlineJobRequest
from erp_web.schemas.online_products import CloseOnlineOperationRequest
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.online_product_completion import recoverable_online_changes, validate_online_completion
from erp_web.services.online_product_tool_results import compact_online_job
from tests.approval_support import _approved_execution, _execution
from tests.test_online_product_capabilities import online, change  # noqa: F401
from tests.test_online_products import listing


def batch(domain, count=3):
    items = []
    for index in range(count):
        row = listing(f"CBT{index + 1}")
        row.model = "user_products"
        row.snapshot = {"user_product": {"family_id": "family-1"}}
        domain.store.save(row)
        items.append(change(domain).model_copy(update={"listing_id": row.id, "version": domain.store.get(row.id).version}))
    group = domain.read_page("mercadolibre")["groups"][0]["id"]
    return OnlineChangesRequest(items=items, group_id=group)


def test_batch_covers_74_targets_and_keeps_rejected_item_without_losing_others(online):
    _, domain, remote, scope, _ = online
    request = batch(domain, 74)
    blocking = domain.change({**request.items[17].model_dump(), "idempotency_key": "other-operation"})["job"]
    snapshot = _changes_snapshot(request, scope)
    approved = _approved_execution(snapshot, "online_products_changes_apply")
    result = online_products_changes_apply(request, scope, approved)
    assert (result.total, result.accepted_count, result.failed_count) == (74, 73, 1)
    failure = [item for item in result.items if not item.accepted][0]
    assert failure.listing_id == "CBT18" and failure.details["conflict_job_id"] == blocking["id"]
    assert len({item.listing_id for item in result.items}) == 74
    assert not remote.writes
    # 同一操作的恢复不重复入队已提交项。
    again = online_products_changes_apply(request, scope, approved)
    assert again == result
    with domain.store.db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM online_jobs").fetchone()[0] == 74


def test_batch_approval_and_complete_group_are_bound_to_actual_targets(online):
    _, domain, _, scope, _ = online
    request = batch(domain)
    with pytest.raises(BusinessCapabilityError) as exc:
        _changes_snapshot(request.model_copy(update={"items": request.items[:2]}), scope)
    assert exc.value.code == "ONLINE_BATCH_SCOPE_MISMATCH"
    assert exc.value.details["missing_ids"] == ["CBT3"]
    approved = _approved_execution(_changes_snapshot(request, scope), "online_products_changes_apply")
    changed = request.model_copy(update={"items": [item.model_copy(update={"changes": {"amount": "20", "currency": "USD"}}) for item in request.items]})
    with pytest.raises(AiToolExecutionError):
        online_products_changes_apply(changed, scope, approved)
    assert not domain.store.jobs("mercadolibre", "seller")


def test_batch_cannot_mix_warehouses_or_duplicate_skus(online):
    _, domain, _, _, _ = online
    request = batch(domain)
    with pytest.raises(ValidationError, match="仓库范围"):
        OnlineChangesRequest(items=[request.items[0], request.items[1].model_copy(update={"scope_id": "another"})])
    with pytest.raises(ValidationError, match="重复"):
        OnlineChangesRequest(items=[request.items[0], request.items[0]])


def test_batch_cancel_keeps_already_submitted_and_stops_remaining(online):
    _, domain, _, scope, _ = online
    request = batch(domain)
    approved = _approved_execution(_changes_snapshot(request, scope), "online_products_changes_apply")
    checks = []

    def cancel():
        checks.append(1)
        if len(checks) == 2:
            raise RunCancelled("用户停止")

    with pytest.raises(RunCancelled):
        online_products_changes_apply(request, scope, replace(approved, cancellation_check=cancel))
    assert len(domain.store.jobs("mercadolibre", "seller")) == 1


def test_partial_result_explains_no_unlock_and_removes_repeated_product_payload(online):
    _, domain, remote, scope, _ = online
    job = domain.change({**change(domain).model_dump(), "idempotency_key": "original-operation"})["job"]
    with domain.store.db._connect() as conn:
        conn.execute("UPDATE online_jobs SET status='partial', result_json=? WHERE id=?",
            (json.dumps({"before": {"description": "冗余内容" * 10000}, "platform_errors": ["版本值过长"]}), job["id"]))
        conn.commit()
    result = online_products_job_read(OnlineJobRequest(job_id=job["id"]), scope)
    assert result.job.status == "partial"
    assert not result.recovery.requires_resolution and not result.recovery.can_close
    assert "无需人工解锁" in result.recovery.explanation
    assert "before" not in result.job.result and result.job.result["platform_errors"] == ["版本值过长"]
    assert len(result.model_dump_json()) < 2500
    assert "before" in domain.store.job(job["id"])["result"]
    request = CloseOnlineOperationRequest(job_id=job["id"], expected_updated_at=result.job.updated_at, acknowledge=True)
    with pytest.raises(BusinessCapabilityError) as exc:
        _close_snapshot(request, scope)
    assert exc.value.code == "ONLINE_CLOSE_NOT_APPLICABLE" and exc.value.details["status"] == "partial"
    # 不执行 close，也能按当前版本提交新的明确操作。
    domain.change({**change(domain).model_dump(), "idempotency_key": "replacement-operation"})
    assert len(domain.store.jobs("mercadolibre", "seller")) == 2 and not remote.writes


def transcript(code="ONLINE_CONFLICT", status="partial"):
    command = {"listing_id": "sku-1", "operation": "content", "scope_id": "", "changes": {"title": "新标题"}, "version": "old"}
    return [
        ModelRequest(parts=[UserPromptPart("修改商品")]),
        ModelResponse(parts=[ToolCallPart("online_products_change", command, "change")]),
        ModelRequest(parts=[ToolReturnPart("online_products_change", {"ok": False, "error": {"code": code, "details": {"conflict_job_id": "job-1"}}}, "change")]),
        ModelResponse(parts=[ToolCallPart("online_products_reconcile", {"job_id": "job-1"}, "reconcile")]),
        ModelRequest(parts=[ToolReturnPart("online_products_reconcile", {"ok": True, "job": {"id": "job-1", "status": status}}, "reconcile")]),
    ]


@pytest.mark.parametrize("status", ["partial", "failed", "confirmed", "submitted", "closed_unconfirmed"])
def test_completion_recognizes_resolved_old_blocker(status):
    pending = recoverable_online_changes(transcript(status=status))
    assert len(pending) == 1 and pending[0]["listing_id"] == "sku-1"


@pytest.mark.parametrize("operation,code", [
    ("online_products_close", "ONLINE_CLOSE_NOT_APPLICABLE"),
    ("online_products_reconcile", "ONLINE_RECONCILE_NOT_APPLICABLE"),
])
def test_completion_uses_terminal_status_from_inapplicable_recovery(operation, code):
    messages = transcript()[:3]
    messages.extend([
        ModelResponse(parts=[ToolCallPart(operation, {"job_id": "job-1"}, "recover")]),
        ModelRequest(parts=[ToolReturnPart(operation, {"ok": False, "error": {
            "code": code, "details": {"status": "partial"}}}, "recover")]),
    ])
    assert len(recoverable_online_changes(messages)) == 1


@pytest.mark.parametrize("status", ["queued", "running", "waiting_confirmation", "outcome_unknown"])
def test_completion_does_not_force_replay_or_unlock_pending_writes(status):
    assert not recoverable_online_changes(transcript(status=status))


def test_completion_only_uses_current_user_turn_and_actual_write_attempts():
    messages = transcript()
    messages.append(ModelRequest(parts=[UserPromptPart("不用继续，解释失败原因")]))
    assert not recoverable_online_changes(messages)
    assert not recoverable_online_changes([ModelRequest(parts=[UserPromptPart("查看全部商品")])])
    messages = transcript()
    args = messages[1].parts[0].args
    messages.extend([ModelResponse(parts=[ToolCallPart("online_products_change", args, "new-change")]),
                     ModelRequest(parts=[ToolReturnPart("online_products_change", {"accepted": True}, "new-change")])])
    assert not recoverable_online_changes(messages)


def test_completion_preserves_declined_approval_and_real_new_blocker():
    for response in ("The tool call was denied.", {"ok": False, "error": {"code": "ONLINE_INVALID_REQUEST"}}):
        messages = transcript()
        messages.extend([ModelResponse(parts=[ToolCallPart("online_products_change", messages[1].parts[0].args, "new-change")]),
                         ModelRequest(parts=[ToolReturnPart("online_products_change", response, "new-change")])])
        assert not recoverable_online_changes(messages)


def test_fresh_read_already_matching_target_does_not_force_duplicate_write():
    messages = transcript(code="ONLINE_VERSION_STALE")
    messages.extend([ModelResponse(parts=[ToolCallPart("online_products_read", {"id": "sku-1", "fields": ["content.title"]}, "read")]),
                     ModelRequest(parts=[ToolReturnPart("online_products_read", {"records": [
                         {"id": "sku-1", "version": "new", "values": {"content.title": "新标题"}}]}, "read")])])
    assert not recoverable_online_changes(messages)
    messages[-1].parts[0].content["records"][0]["values"] = {}
    assert len(recoverable_online_changes(messages)) == 1


@pytest.mark.parametrize("serialized", [False, True])
def test_completion_reads_native_python_receipts_without_treating_script_text_as_evidence(serialized):
    from dataclasses import asdict
    messages = transcript(code="ONLINE_VERSION_STALE")
    call = ToolCallPart("online_products_read", {"id": "sku-1", "fields": ["content.title"]}, "nested-read")
    result = ToolReturnPart("online_products_read", {"records": [
        {"id": "sku-1", "values": {"content.title": "新标题"}}]}, "nested-read")
    if serialized:
        call, result = asdict(call), asdict(result)
    messages.append(ModelRequest(parts=[ToolReturnPart("run_code", "已核对", "script", metadata={
        "code_mode": True, "tool_calls": {"nested-read": call}, "tool_returns": {"nested-read": result}})]))
    assert not recoverable_online_changes(messages)
    messages[-1].parts[0].metadata = None
    assert len(recoverable_online_changes(messages)) == 1


def test_invalid_tool_arguments_in_history_do_not_break_completion():
    messages = [ModelRequest(parts=[UserPromptPart("读取商品")]),
                ModelResponse(parts=[ToolCallPart("online_products_read", '{"id":', "invalid")])]
    assert not recoverable_online_changes(messages)


@pytest.mark.parametrize("mode", ["ask", "full"])
def test_batch_tool_through_production_native_approval_and_deferred(online, tmp_path, mode):
    from pydantic_ai.models.function import DeltaToolCall
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset, build_job_status_readers
    from erp_web.services.agent_job_service import AgentJobService
    from tests.ai_code_mode_helpers import discovering_tools
    from tests.test_native_agent_integration import service, body, CONVERSATION

    app, domain, remote, _, _ = online
    request = batch(domain)
    replies = []

    @discovering_tools("online_products_changes_apply")
    async def model(messages, info):
        results = [part for message in messages for part in message.parts
                   if isinstance(part, ToolReturnPart) and part.tool_name == "online_products_changes_apply"]
        if results:
            replies.append(results[-1].content)
            yield "3 个目标均已入队，平台结果由后台跟踪。"
        else:
            yield {0: DeltaToolCall(name="online_products_changes_apply", tool_call_id="batch", json_args=request.model_dump_json())}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    ui.chat_service.approval_mode_reader = lambda: mode
    asyncio.run(ui.prepare_run(body("将整组商品价格设置为 15.25 美元")).stream(lambda _: None))
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
        worker.scan()
        for _ in range(200):
            if not ui.run_registry.is_active(CONVERSATION):
                break
            time.sleep(.01)
        assert not ui.run_registry.is_active(CONVERSATION)
        assert len(replies) == 1 and replies[0]["accepted_count"] == 3
        assert len(domain.store.jobs("mercadolibre", "seller")) == 3
        assert not remote.writes and not ui.call_store.pending(CONVERSATION)
    finally:
        worker.close()


def deps(seconds=600, limit=240):
    return SimpleNamespace(tool_runtime=SimpleNamespace(max_tool_calls=limit),
        execution_context=replace(_execution(), deadline_at=datetime.now(timezone.utc) + timedelta(seconds=seconds)))


def test_completion_respects_budget_deadline_and_deferred_output():
    for seconds, limit, usage in [(600, 2, 0), (20, 240, 0), (600, 240, 239)]:
        ctx = SimpleNamespace(deps=deps(seconds, limit), usage=RunUsage(tool_calls=usage), messages=transcript())
        assert validate_online_completion(ctx, "任务尚未完成") == "任务尚未完成"
    ctx = SimpleNamespace(deps=deps(), usage=RunUsage(), messages=transcript())
    deferred = DeferredToolRequests()
    assert validate_online_completion(ctx, deferred) is deferred
    with pytest.raises(ModelRetry, match="1 个"):
        validate_online_completion(ctx, "仍需解锁，请下次继续")


def test_native_output_retry_continues_tools_instead_of_ending_after_partial():
    calls = []
    history = transcript()

    def model(messages, info):
        calls.append(1)
        if len(calls) == 1:
            return ModelResponse(parts=[TextPart("解锁失败，下次再继续")])
        if len(calls) == 2:
            assert "旧任务已不再阻塞" in str(messages)
            return ModelResponse(parts=[ToolCallPart("online_products_change", history[1].parts[0].args, "new-change")])
        return ModelResponse(parts=[TextPart("已提交")])

    agent = Agent(FunctionModel(model), deps_type=object, retries=2)
    agent.output_validator(validate_online_completion)

    @agent.tool_plain
    def online_products_change(listing_id: str, operation: str, scope_id: str, changes: dict, version: str) -> dict:
        return {"accepted": True}

    result = agent.run_sync(None, message_history=history, deps=deps())
    assert result.output == "已提交" and len(calls) == 3


def test_native_output_retry_is_bounded_for_model_ignoring_completion():
    calls = []

    def model(messages, info):
        calls.append(1)
        return ModelResponse(parts=[TextPart("下次再继续")])

    agent = Agent(FunctionModel(model), retries=2)
    agent.output_validator(validate_online_completion)
    with pytest.raises(UnexpectedModelBehavior):
        agent.run_sync(None, message_history=transcript(), deps=deps(), usage_limits=UsageLimits(request_limit=5))
    assert len(calls) == 3


def test_production_chat_recovers_stale_version_after_premature_final(online, tmp_path):
    """真实主对话装配中，结束校验必须跨流式输出和 Deferred 恢复继续有效。"""
    from pydantic_ai.models.function import DeltaToolCall
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset, build_job_status_readers
    from erp_web.services.agent_job_service import AgentJobService
    from tests.ai_code_mode_helpers import discovering_tools
    from tests.test_native_agent_integration import service, body, CONVERSATION

    app, domain, remote, _, _ = online
    stages = []
    request = change(domain).model_dump()

    @discovering_tools("online_products_read", "online_products_change")
    async def model(messages, info):
        stages.append(1)
        step = len(stages)
        if step == 1:
            args = {**request, "version": "outdated"}
            yield {0: DeltaToolCall(name="online_products_change", tool_call_id="stale", json_args=json.dumps(args))}
        elif step == 2:
            yield "版本已过期，本轮先结束。"
        elif step == 3:
            assert "已尝试的新修改未入队" in str(messages)
            yield {0: DeltaToolCall(name="online_products_read", tool_call_id="fresh",
                json_args=json.dumps({"id": "CBT1", "fields": ["prices"]}))}
        elif step == 4:
            yield {0: DeltaToolCall(name="online_products_change", tool_call_id="corrected", json_args=json.dumps(request))}
        else:
            yield "已使用当前版本提交。"

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    ui.chat_service.approval_mode_reader = lambda: "full"
    chunks = []
    asyncio.run(ui.prepare_run(body("将价格改为 15.25 美元")).stream(chunks.append))
    worker = AgentJobService(ui_service=ui, job_readers=build_job_status_readers(app))
    try:
        for row in ui.call_store.work():
            worker.execute_or_reconcile(row)
        worker.scan()
        for _ in range(200):
            if not ui.run_registry.is_active(CONVERSATION):
                break
            time.sleep(.01)
        assert len(stages) == 5, b"".join(chunks).decode()
        assert not ui.run_registry.is_active(CONVERSATION)
        assert len(domain.store.jobs("mercadolibre", "seller")) == 1
        assert not remote.writes and not ui.call_store.pending(CONVERSATION)
    finally:
        worker.close()

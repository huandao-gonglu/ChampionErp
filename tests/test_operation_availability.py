"""网页、桌面和后台按同一操作来源执行；不启动外部浏览器或调用真实模型。"""
import asyncio
from dataclasses import replace
import json
from threading import Event

import pytest
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.messages import ToolReturnPart

from erp_shared.desktop import ACTION_PARAMS
from erp_web.context import get_context
from erp_web.facades import image_facade
from erp_web.facades.auth_config_facade import save_ai_config_payload, save_settings_payload
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.schemas.requests import RequestValidationError
from erp_web.services.desktop_connection_service import (
    DesktopUnavailable, bind_desktop_connection, current_desktop_connection, desktop_unavailable_reason,
    server_worker_context,
)
from erp_web.services.operation_availability import operation_availability
from erp_web.services.agent_job_service import AgentJobService
from erp_web.services.agent_run_storage import AgentRunStorage
from erp_web.services.config_service import merge_ai_config
from tests.test_draft_image_workbench import finish, seeded, request
from tests.test_native_agent_integration import CONVERSATION, binding, body, service, tools
from tests.ai_code_mode_helpers import business_returns, python_call


def local_config():
    return {"ai_models": [
        {"id": "canvas", "name": "Gemini Canvas", "connection_type": "browser",
         "browser_provider": "gemini", "capabilities": ["image_edit"]},
        {"id": "text", "name": "Codex CLI", "connection_type": "cli", "capabilities": ["chat", "json"]},
    ]}


def test_every_local_action_rejects_missing_origin_even_if_another_desktop_is_online():
    broker = get_context().desktop_connections
    broker.connect()
    for operation in ACTION_PARAMS:
        with pytest.raises(DesktopUnavailable, match="桌面端") as caught:
            broker.call(operation, {})
        assert caught.value.code == 'DESKTOP_UNAVAILABLE' and not caught.value.outcome_unknown


def test_a_server_worker_keeps_business_context_without_inheriting_the_launch_device():
    from contextvars import ContextVar
    tenant = ContextVar('test_tenant')
    token = tenant.set('workspace-a')
    try:
        with bind_desktop_connection('device-a'):
            worker = server_worker_context()
            assert worker.run(tenant.get) == 'workspace-a'
            assert worker.run(current_desktop_connection) == ''
            assert current_desktop_connection() == 'device-a'
    finally:
        tenant.reset(token)


def test_model_and_collection_rules_follow_current_connection_and_actual_binding(monkeypatch):
    context = get_context()
    config = local_config()
    monkeypatch.setattr(context.config, 'load_app_config', lambda: config)
    catalog = build_global_chat_toolset(context)
    key = context.desktop_connections.connect()['connection_id']
    blocked = operation_availability(config)
    assert blocked['desktop_reason'] and 'Gemini Canvas' in blocked['blocked_use_cases']['image.translate']
    assert 'Codex CLI' in blocked['blocked_use_cases']['text.translate']
    assert catalog.get('image_edit').unavailable_reason(None) == blocked['blocked_use_cases']['image.translate']
    assert catalog.get('collect_from_browser_tab').unavailable_reason(None) == blocked['desktop_reason']
    batch = catalog.get('collect_batch')
    assert not batch.unavailable_reason(None)
    assert not batch.unavailable_reason({'mode': 'http', 'urls': ['https://example.org/1']})
    assert batch.unavailable_reason({'urls': ['https://example.org/1']}) == blocked['desktop_reason']
    mixed = catalog.get('collect_1688')
    assert not mixed.unavailable_reason({'text': '商品资料'})
    assert mixed.unavailable_reason({'url': 'https://detail.1688.com/offer/1'}) == blocked['desktop_reason']
    with bind_desktop_connection(key):
        assert not catalog.get('image_edit').unavailable_reason(None)
        assert not catalog.get('text_translate').unavailable_reason(None)
        assert not catalog.get('collect_from_browser_tab').unavailable_reason(None)
        assert 'API 模型' in operation_availability(config)['blocked_use_cases']['image.plan']
        context.desktop_connections.disconnect(key)
        assert '断开' in catalog.get('image_edit').unavailable_reason(None)
    config['ai_models'].insert(0, {'id': 'api_image', 'connection_type': 'api', 'capabilities': ['image_edit']})
    assert not catalog.get('image_edit').unavailable_reason(None)
    config['ai_use_case_bindings'] = {'image.translate': {'model_id': 'canvas'}}
    assert catalog.get('image_edit').unavailable_reason(None)


def test_image_workbench_rejects_before_batch_registration_and_keeps_saved_result(monkeypatch):
    context = get_context()
    product, draft = seeded()
    submission = request(product, draft, ['common'])
    monkeypatch.setattr(image_facade, 'load_app_config', local_config)
    broker = context.desktop_connections
    key = broker.connect()['connection_id']
    with pytest.raises(RequestValidationError) as denied:
        image_facade.process_draft_images_payload(submission)
    assert denied.value.status_code == 409 and denied.value.error_code == 'OPERATION_UNAVAILABLE'
    assert not context.image_batches.get(submission['request_id'])
    assert context.products.draft_record(draft['draft_id']) == draft
    with bind_desktop_connection(key):
        broker.disconnect(key)
        with pytest.raises(RequestValidationError, match='断开'):
            image_facade.process_draft_images_payload(submission)
    assert not context.image_batches.get(submission['request_id'])
    assert image_facade.draft_image_state_payload(submission)[1] == 200
    calls = []
    def process(*_, **kwargs):
        calls.append(current_desktop_connection())
        raise DesktopUnavailable('已发送，结果待核对。', outcome_unknown=True)
    monkeypatch.setattr(image_facade.image_translate_service, 'edit_images', process)
    new_key = broker.connect()['connection_id']
    with bind_desktop_connection(new_key):
        image_facade.process_draft_images_payload(submission)
    result = finish(draft['draft_id'])
    assert result['image_batch']['items'][0]['outcome_unknown']
    restored, status = image_facade.process_draft_images_payload(submission)
    assert status == 200 and restored['image_batch'] == result['image_batch']
    assert calls == [new_key]


def test_web_configuration_save_preserves_local_connection_and_cannot_register_a_new_one(tmp_path):
    original = {**local_config()['ai_models'][0], 'local_connection_id': 'canvas', 'local_config_fingerprint': 'original'}
    config = {'ai_models': [original]}
    incoming = {'ai_models': [{**original, 'local_config_fingerprint': 'foreign', 'capabilities': []},
                              {'id': 'api', 'connection_type': 'api', 'provider_id': 'deepseek', 'name': '网页 API'}]}
    saved = merge_ai_config(tmp_path, config, incoming, preserve_local_models=True)
    assert saved['ai_models'][0]['local_config_fingerprint'] == 'original'
    assert saved['ai_models'][0]['capabilities'] == ['image_edit']
    assert saved['ai_models'][1]['name'] == '网页 API'
    with pytest.raises(RequestValidationError):
        merge_ai_config(tmp_path, {}, incoming, preserve_local_models=True)
    with pytest.raises(RequestValidationError):
        merge_ai_config(tmp_path, config, {'ai_models': []}, preserve_local_models=True)
    with pytest.raises(RequestValidationError):
        merge_ai_config(tmp_path, {}, {'ai_models': [{**original, 'connection_type': ' Browser '}]}, preserve_local_models=True)
    with pytest.raises(RequestValidationError):
        merge_ai_config(tmp_path, config, {'ai_models': [original, original]}, preserve_local_models=True)


def test_both_settings_routes_enforce_the_same_platform_and_recheck_concurrent_edits(monkeypatch):
    store = get_context().config
    config = {**store.load_app_config(), **local_config()}
    store.save_app_config(config)
    for route, key in [(save_ai_config_payload, 'config'), (save_settings_payload, 'appConfig')]:
        with pytest.raises(RequestValidationError) as denied:
            route({key: {'ai_use_case_bindings': {'image.translate': {'model_id': 'canvas'}}}})
        assert denied.value.error_code == 'OPERATION_UNAVAILABLE'
        with pytest.raises(RequestValidationError):
            route({key: {'ai_models': []}})
    updated = {**config, 'ai_models': [{**row, 'name': '桌面修改'} for row in config['ai_models']]}
    merge = store._merge_client_ai_config
    def concurrent_save(current, incoming):
        merged = merge(current, incoming)
        store.save_app_config(updated)
        return merged
    monkeypatch.setattr(store, '_merge_client_ai_config', concurrent_save)
    with pytest.raises(RequestValidationError) as conflict:
        store.save_client_app_config({}, ai_only=True)
    assert conflict.value.error_code == 'APP_CONFIG_CHANGED'
    assert store.load_app_config()['ai_models'][0]['name'] == '桌面修改'


def test_chat_discovers_local_tools_only_for_the_current_input_and_keeps_connection_private(tmp_path):
    observed = []
    async def model(messages, info):
        observed.append({definition.name for definition in info.function_tools})
        yield "完成"

    local = replace(binding('open_local', lambda *_: {'ok': True}),
                    unavailable_reason=lambda _: desktop_unavailable_reason())
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(local))
    broker = get_context().desktop_connections
    key = broker.connect()['connection_id']
    # 场景一：桌面发送、后台运行；场景二：同一会话由网页继续。
    with bind_desktop_connection(key):
        run = ui.prepare_run(body(message_id='desktop'))
    chunks = []
    asyncio.run(run.stream(chunks.append))
    history = ui.chat_service.trusted_history(CONVERSATION)
    assert AgentRunStorage(ui.call_store, CONVERSATION, history, 1).desktop_connection == ''
    asyncio.run(ui.prepare_run(body(message_id='web')).stream(chunks.append))
    assert 'open_local' in observed[0] and 'open_local' not in observed[1]
    assert key not in str(ui.chat_service.trusted_history(CONVERSATION))
    assert key.encode() not in b''.join(chunks)
    # 同批消息来自不同端，整个新决策不能借用其中一个设备。
    run = ui.prepare_run(body(message_id='web-queued'))
    with bind_desktop_connection(key):
        ui.prepare_run(body(message_id='desktop-queued'))
    asyncio.run(run.stream(chunks.append))
    assert 'open_local' not in observed[-1]


def test_python_tool_keeps_server_branch_and_returns_reason_for_local_branch(tmp_path):
    executed = []
    async def model(messages, info):
        results = business_returns(messages)
        if len(results) < 2:
            branch = 'browser' if not results else 'http'
            yield {0: python_call(name='collect_item', json_args=json.dumps({'draft_id': branch}), tool_call_id=branch)}
        else:
            yield "网页已使用 HTTP 完成采集"
    mixed = replace(binding('collect_item', lambda args, _: executed.append(args['draft_id']) or {'ok': True}, write=True),
                    unavailable_reason=lambda args: desktop_unavailable_reason() if args and args['draft_id'] == 'browser' else '')
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(mixed))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    results = business_returns(ui.chat_service.trusted_history(CONVERSATION))
    assert results[0].content['error']['code'] == 'OPERATION_UNAVAILABLE'
    assert '桌面端' in results[0].content['error']['message']
    assert executed == ['http']


@pytest.mark.parametrize('disconnect', [False, True])
def test_approval_from_another_desktop_keeps_the_original_device(tmp_path, disconnect):
    broker = get_context().desktop_connections
    original = broker.connect()['connection_id']
    approver = broker.connect()['connection_id']
    executed = []
    async def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            yield "已处理原调用"
        else:
            yield {0: DeltaToolCall(name="open_local", json_args='{"draft_id":"draft"}', tool_call_id="original")}

    local = replace(binding('open_local', lambda *_: executed.append(current_desktop_connection()) or {'ok': True},
                            approval=True, write=True), unavailable_reason=lambda _: desktop_unavailable_reason())
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(local))
    with bind_desktop_connection(original):
        run = ui.prepare_run(body())
    asyncio.run(run.stream(lambda _: None))
    pending = ui.call_store.pending(CONVERSATION)[0]
    assert pending.metadata['original']['desktop_connection'] == original
    parts = [p for m in ui.dump_ui_messages(CONVERSATION)['messages'] for p in m['parts']
             if p.get('state') == 'approval-requested']
    assert len(parts) == 1
    parts[0].update(state="approval-responded")
    parts[0]['approval']['approved'] = True
    if disconnect:
        broker.disconnect(original)
    decision = json.dumps({'id': CONVERSATION, 'trigger': 'submit-message',
                           'messages': [{'id': 'decision', 'role': 'assistant', 'parts': parts}]}).encode()
    with bind_desktop_connection(approver):
        run = ui.prepare_run(decision, approval_token='test-token')
        asyncio.run(run.stream(lambda _: None))
    assert executed == ([] if disconnect else [original])
    assert not ui.call_store.pending(CONVERSATION)
    history = str(ui.chat_service.trusted_history(CONVERSATION))
    assert original not in history and approver not in history
    if disconnect:
        returns = [part.content for message in ui.chat_service.trusted_history(CONVERSATION) for part in message.parts
                   if isinstance(part, ToolReturnPart) and part.tool_call_id == "original"]
        assert any(isinstance(content, dict) and content.get("error", {}).get("code") == "OPERATION_UNAVAILABLE"
                   for content in returns)


def test_background_job_and_next_decision_keep_origin_across_detached_runs(tmp_path):
    broker = get_context().desktop_connections
    original, other = (broker.connect()['connection_id'] for _ in range(2))
    executed = []
    visible_on_resume = []
    resumed = Event()
    async def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            visible_on_resume.append({tool.name for tool in info.function_tools})
            resumed.set()
            yield "完成"
        else:
            yield {0: DeltaToolCall(name="local_job", json_args='{"draft_id":"draft"}', tool_call_id="job")}
    local = replace(binding('local_job', lambda *_: executed.append(current_desktop_connection()) or {'ok': True},
                            external=True, write=True), unavailable_reason=lambda _: desktop_unavailable_reason())
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(local))
    with bind_desktop_connection(original):
        run = ui.prepare_run(body())
    asyncio.run(run.stream(lambda _: None))
    worker = AgentJobService(ui_service=ui, job_readers={})
    try:
        with bind_desktop_connection(other):
            for row in ui.call_store.work():
                worker.execute_or_reconcile(row)
            # 完整结果由正常后台入口恢复，不借用调度线程上的连接。
            worker.scan()
            assert resumed.wait(5)
        assert executed == [original]
        assert len(visible_on_resume) == 1 and 'local_job' in visible_on_resume[0]
    finally:
        worker.close()


def test_chat_image_input_and_read_tool_follow_selected_chat_model(monkeypatch):
    context = get_context()
    config = {"ai_models": [
        {"id": "text", "capabilities": ["chat", "tool_calling"]},
        {"id": "visual", "capabilities": ["chat", "tool_calling", "image_understanding"]},
    ]}
    monkeypatch.setattr(context.config, "load_app_config", lambda: config)
    tool = build_global_chat_toolset(context).get("product_image_read")
    blocked = operation_availability(config)["blocked_use_cases"]
    assert "global.chat" not in blocked and blocked["global.chat.images"] == tool.unavailable_reason(None)
    config["ai_use_case_bindings"] = {"global.chat": {"model_id": "visual"}}
    assert "global.chat.images" not in operation_availability(config)["blocked_use_cases"]
    assert not tool.unavailable_reason(None)

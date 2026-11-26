"""通用 Python 通过生产 Agent 和业务边界执行，不接触真实数据。"""

import asyncio
import json
import time
from dataclasses import replace
from copy import deepcopy

import pytest

from pydantic_ai.messages import ToolReturnPart, RetryPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tests.test_native_agent_integration import (
    CONVERSATION, binding, body, service, tools,
)
from tests.test_main_chat_attributes import subject
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.services import global_agent_chat_service
from tests.ai_code_mode_helpers import business_returns, discover_tools
from tests.native_domain_fixture import _source_product


def code_delta(code, call_id="script"):
    return DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id=call_id)


@pytest.mark.parametrize("entry", ["direct", "python"])
def test_both_entries_retain_constraints_and_native_return_types(tmp_path, entry):
    executed, results, descriptions = [], [], []
    read = binding("bounded_read", lambda args, ctx: executed.append(args["limit"]) or {"count": args["limit"]})
    read = replace(read, definition=replace(
        read.definition,
        input_schema={"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 3},
        }, "required": ["limit"], "additionalProperties": False},
        output_schema={"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]},
    ))

    async def model(messages, info):
        native = next(t for t in info.function_tools if t.name == "bounded_read")
        assert native.parameters_json_schema["properties"]["limit"]["maximum"] == 3
        description = next(t.description for t in info.function_tools if t.name == "run_code")
        descriptions.append(description)
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        retries = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
        if returns:
            results.append(returns[-1].content)
            yield "已完成。"
        elif retries:
            yield {0: (code_delta("result = await bounded_read(limit=3)\nresult['count']", "valid")
                       if entry == "python" else DeltaToolCall(name="bounded_read", json_args='{"limit":3}', tool_call_id="valid"))}
        else:
            yield {0: (code_delta("await bounded_read(limit=4)", "invalid")
                       if entry == "python" else DeltaToolCall(name="bounded_read", json_args='{"limit":4}', tool_call_id="invalid"))}

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(read))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert "minimum=1, maximum=3" in descriptions[0]
    assert "count: int" in descriptions[0]
    assert executed == [3]
    assert results == ([3] if entry == "python" else [{"count": 3}])


def test_direct_read_and_single_save_use_business_tools_without_python(subject, tmp_path, monkeypatch):
    app, draft_id, _ = subject
    saved_calls, requests = [], []
    original_save = app.products.save_draft_content

    def counted_save(*args, **kwargs):
        saved_calls.append(1)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(app.products, "save_draft_content", counted_save)

    async def model(messages, info):
        if discovery := discover_tools(info, "draft_attributes_read", "draft_sku_attributes_update"):
            yield {0: discovery}
            return
        requests.append(1)
        names = {tool.name for tool in info.function_tools}
        assert {"draft_attributes_read", "draft_sku_attributes_update", "run_code"} <= names
        if len(requests) == 1:
            yield {0: DeltaToolCall(name="draft_attributes_read", json_args=json.dumps({
                "draft_id": draft_id, "platform": "ozon", "site": "global", "scope": "sku",
            }), tool_call_id="read")}
        elif len(requests) == 2:
            returned = next(p for p in business_returns(messages) if p.tool_name == "draft_attributes_read")
            assert returned.tool_name == "draft_attributes_read" and returned.content["sku_count"] == 2
            yield {0: DeltaToolCall(name="draft_sku_attributes_update", json_args=json.dumps({
                "draft_id": draft_id, "platform": "ozon", "site": "global", "category_id": "94765",
                "sku_id": "s0", "updates": {"color": "Черный"},
            }), tool_call_id="save")}
        else:
            yield "已保存。"

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body(target_draft_ids=[draft_id])).stream(chunks.append))
    assert saved_calls == [1], b"".join(chunks).decode()
    assert len(requests) == 3
    assert app.products.draft_record(draft_id)["sku_items"][0]["attributes_by_target"]["ozon:global"]["color"] == "Черный"
    returned = business_returns(ui.chat_service.trusted_history(CONVERSATION))
    assert [p.tool_name for p in returned] == ["draft_attributes_read", "draft_sku_attributes_update"]
    receipts = ui.call_store.current_turn_receipts(CONVERSATION)
    assert len(receipts) == 1 and receipts[0]["tool_call_id"] == "save" and receipts[0]["status"] == "completed"
    assert b'"type":"tool-output-available"' in b"".join(chunks)


def test_direct_call_between_scripts_preserves_repl_and_write_receipts(tmp_path):
    writes, outputs = [], []
    steps = iter([
        code_delta("ids = ['first', 'last']\nawait update_row(draft_id=ids[0])", "first-script"),
        DeltaToolCall(name="update_row", json_args='{"draft_id":"middle"}', tool_call_id="direct"),
        code_delta("await update_row(draft_id=ids[1])", "last-script"),
    ])

    async def model(messages, info):
        step = next(steps, None)
        if step is not None:
            yield {0: step}
        else:
            outputs.extend(business_returns(messages))
            yield "三项已保存。"

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(
        binding("update_row", lambda args, ctx: writes.append(args["draft_id"]) or {"ok": True}, write=True),
    ))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert writes == ["first", "middle", "last"]
    assert len(outputs) == 3
    assert [row["tool_call_id"] for row in ui.call_store.current_turn_receipts(CONVERSATION)] == [
        "first-script__1", "direct", "last-script__1",
    ]


def test_script_uses_existing_tools_and_persists_individual_write_receipts(tmp_path):
    writes, results = [], []

    async def model(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            assert {t.name for t in info.function_tools} == {"read_rows", "update_row", "run_code", "request_user_input"}
            yield {0: code_delta(
                "rows = await read_rows(draft_id='selected')\n"
                "saved = []\n"
                "for row in rows['items']:\n"
                "    receipt = await update_row(draft_id=row['id'])\n"
                "    if receipt.get('ok') is False:\n"
                "        raise ValueError(receipt)\n"
                "    saved.append(row['id'])\n"
                "{'saved_count': len(saved)}"
            )}
        else:
            results.append(returns[-1])
            yield "已保存 198 项。"

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(
        binding("read_rows", lambda args, ctx: {"items": [{"id": f"item-{i}"} for i in range(198)]}),
        binding("update_row", lambda args, ctx: writes.append(args["draft_id"]) or {"ok": True}, write=True),
    ))
    chunks = []
    asyncio.run(ui.prepare_run(body()).stream(chunks.append))
    assert len(writes) == 198, b"".join(chunks).decode()
    assert results[0].content == {"saved_count": 198}
    assert len(results[0].metadata["tool_returns"]) == 199
    receipts = ui.call_store.current_turn_receipts(CONVERSATION)
    assert len(receipts) == 198
    assert all(r["status"] == "completed" for r in receipts)
    stored = ui.chat_service.trusted_history(CONVERSATION)
    returned = [p for m in stored for p in m.parts if isinstance(p, ToolReturnPart)]
    assert len(returned[-1].metadata["tool_returns"]) == 199
    ui_receipts = [row for message in ui.dump_ui_messages(CONVERSATION)["messages"]
                   for row in message.get("metadata", {}).get("business_write_receipts", [])]
    assert len(ui_receipts) == 198
    assert ui_receipts[0]["tool_call_id"] == "script__2"


def test_large_read_can_retry_with_smaller_page(tmp_path, monkeypatch):
    monkeypatch.setattr(global_agent_chat_service, "GLOBAL_CHAT_PROFILE", replace(
        global_agent_chat_service.GLOBAL_CHAT_PROFILE, max_tool_output_bytes=300,
    ))
    counts, results = [], []
    def read_rows(arguments, execution):
        limit = arguments.get("limit", 198)
        counts.append(limit)
        return {"items": ["规格说明" * 10] * limit}
    read = binding("read_rows", read_rows)
    schema = read.definition.to_dict()["input_schema"]
    schema["properties"]["limit"] = {"type": "integer", "minimum": 1}
    read = replace(read, definition=replace(read.definition, input_schema=schema))

    async def model(messages, info):
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        retries = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
        if returned:
            results.append(returned[-1].content)
            yield "已改为分段读取。"
        elif retries:
            assert "limit" in str(retries[-1].content)
            yield {0: code_delta("page = await read_rows(draft_id='selected', limit=1)\nlen(page['items'])", "small")}
        else:
            yield {0: code_delta("page = await read_rows(draft_id='selected')\nlen(page['items'])", "large")}

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(read))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert counts == [198, 1]
    assert results == [1]


def test_script_cannot_call_approval_or_job_tools(tmp_path):
    executed, observed = [], []

    async def model(messages, info):
        names = {t.name for t in info.function_tools}
        assert names == {"run_code", "publish", "job", "request_user_input"}
        code_tool = next(t for t in info.function_tools if t.name == "run_code")
        assert "async def request_user_input" not in (info.instructions or "")
        assert "async def publish" not in code_tool.description
        assert "async def job" not in code_tool.description
        retries = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
        if not retries:
            yield {0: code_delta("await publish(draft_id='a')")}
        else:
            observed.extend(retries)
            yield "发布需要使用原生审批工具。"

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(
        binding("publish", lambda args, ctx: executed.append(1) or {}, approval=True, write=True),
        binding("job", lambda args, ctx: executed.append(1) or {}, external=True, write=True),
    ))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert observed
    assert not executed
    assert ui.call_store.pending(CONVERSATION) is None


def test_python_reads_all_extracts_codes_and_saves_198_skus(subject, tmp_path, record_property, monkeypatch):
    app, draft_id, _ = subject
    product = app.products.load_product_from_index("product-native-0")
    product["sku_items"] = [
        {"id": f"s{i}", "active": True, "name": "钻石画", "options": {
            "尺寸": f"EE{i:03d}{'不带钻' if i == 47 else ''} 20X20CM", "规格": "20X20CM",
        }} for i in range(198)
    ]
    pricing = _source_product()["drafts"]["ozon"]["pricing"]
    product["drafts"]["ozon"]["sku_items"] = [
        {"sku_id": f"s{i}", "selected": True, "attributes_by_target": {
            "yandex:global": {"old": "保留"}, "ozon:global": {
                "other": "保留", **({"color": "EE047不带钻"} if i == 47 else {}),
            },
        }, "pricing": deepcopy(pricing)} for i in range(198)
    ]
    app.products.save_product(product)
    before = app.products.draft_record(draft_id)
    def forbid_page_response(*args, **kwargs):
        pytest.fail("业务逐项读写不应生成页面上下文或扫描全库列表")
    for name in ("draft_product_context", "load_products_index", "load_drafts_index"):
        monkeypatch.setattr(app.products, name, forbid_page_response)
    results, requests = [], []
    code = f"""import re
data = await draft_attributes_read(draft_id={draft_id!r}, platform='ozon', site='global', scope='sku')
rows = data['skus']
assert len(rows) == data['sku_count'] and data['next_offset'] is None
updates = []
for row in rows:
    match = re.match(r'[A-Za-z]+[0-9]+', row['options']['尺寸'])
    if match is None:
        raise ValueError(row['sku_id'])
    updates.append((row['sku_id'], match.group(0)))
saved = []
for sku_id, value in updates:
    receipt = await draft_sku_attributes_update(draft_id={draft_id!r}, platform='ozon', site='global', category_id='94765', sku_id=sku_id, updates={{'color': value}})
    if receipt.get('ok') is False:
        raise ValueError(receipt)
    saved.append(sku_id)
{{'saved_count': len(saved)}}
"""

    async def model(messages, info):
        if discovery := discover_tools(info, "draft_attributes_read", "draft_sku_attributes_update"):
            yield {0: discovery}
            return
        requests.append(1)
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name != "search_tools"]
        if not returns:
            yield {0: code_delta(code)}
        else:
            results.append(returns[-1].content)
            yield "198 个 SKU 的颜色已按规格前缀保存。"

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    started = time.monotonic()
    asyncio.run(ui.prepare_run(body("用尺寸字段开头的编码填写全部 SKU 颜色", target_draft_ids=[draft_id])).stream(chunks.append))
    record_property("isolated_execution_seconds", round(time.monotonic() - started, 3))
    assert results == [{"saved_count": 198}], b"".join(chunks).decode()
    assert len(requests) == 2
    saved = app.products.draft_record(draft_id)
    for row in saved["sku_items"]:
        index = int(row["sku_id"][1:])
        assert row["attributes_by_target"]["ozon:global"] == {"other": "保留", "color": f"EE{index:03d}"}
        assert row["attributes_by_target"]["yandex:global"] == {"old": "保留"}
        assert row["pricing"] == before["sku_items"][index]["pricing"]
    receipts = ui.call_store.current_turn_receipts(CONVERSATION)
    assert len(receipts) == 198 and all(row["status"] == "completed" for row in receipts)
    returns = business_returns(ui.chat_service.trusted_history(CONVERSATION))
    assert len(returns) == 199  # 一次完整读取、198 次原有写入。
    from erp_web.runtime_units.product_capabilities import draft_attributes_read, ProductCapabilityScope
    from erp_web.schemas.product_capabilities import DraftAttributesReadRequest
    pages = [draft_attributes_read(DraftAttributesReadRequest(
        draft_id=draft_id, platform="ozon", site="global", scope="sku", offset=offset, limit=100,
    ), ProductCapabilityScope(app.products)) for offset in (0, 100)]
    assert [len(page.skus) for page in pages] == [100, 98]
    assert [page.next_offset for page in pages] == [100, None]
    assert [row["sku_id"] for page in pages for row in page.skus] == [row["sku_id"] for row in saved["sku_items"]]


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize("first_entry", ["direct", "python"])
def test_direct_and_nested_calls_share_run_budget(tmp_path, monkeypatch, mode, first_entry):
    monkeypatch.setattr(global_agent_chat_service, "GLOBAL_CHAT_PROFILE", replace(
        global_agent_chat_service.GLOBAL_CHAT_PROFILE, max_tool_calls=6,
    ))
    executed = []
    steps = iter([
        (code_delta("await update_row(draft_id='first')", "first-script") if first_entry == "python" else
         DeltaToolCall(name="update_row", json_args='{"draft_id":"first"}', tool_call_id="first-direct")),
        code_delta("for i in range(10):\n    await update_row(draft_id=str(i))" if mode == "sequential" else
                   "import asyncio\nawait asyncio.gather(*[update_row(draft_id=str(i)) for i in range(5)])", "last-script"),
    ])

    async def model(messages, info):
        step = next(steps, None)
        if step is None:
            yield "额度不足，部分完成。"
        else:
            yield {0: step}

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(
        binding("update_row", lambda args, ctx: executed.append(args["draft_id"]) or {}, write=True),
    ))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert len(executed) == (4 if first_entry == "python" else 5)  # 每个 run_code 也占额度。


@pytest.mark.parametrize("code", [
    "import pathlib\npathlib.Path('/etc/hosts').read_text()",
    "import os\nos.environ['PATH']",
    "import subprocess\nsubprocess.run(['echo', 'unexpected'])",
    "import sqlite3\nsqlite3.connect('erp.sqlite3')",
])
def test_python_has_no_host_file_environment_or_process_access(tmp_path, code):
    retries = []

    async def model(messages, info):
        failures = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
        if not failures:
            yield {0: code_delta(code)}
        else:
            retries.extend(failures)
            yield "环境未开放该能力。"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert retries


@pytest.mark.parametrize("outside_selection", [False, True])
@pytest.mark.parametrize("entry", ["direct", "python"])
def test_both_entries_preserve_business_validation_and_selected_draft_scope(subject, tmp_path, outside_selection, entry):
    app, draft_id, _ = subject
    before = app.products.draft_record(draft_id)
    results = []

    async def model(messages, info):
        if discovery := discover_tools(info, "draft_sku_attributes_update"):
            yield {0: discovery}
            return
        returns = [p for m in messages for p in m.parts if isinstance(p, (ToolReturnPart, RetryPromptPart)) and p.tool_name != "search_tools"]
        if not returns:
            field = "color" if outside_selection else "readonly"
            arguments = {"draft_id": draft_id, "platform": "ozon", "site": "global", "category_id": "94765",
                         "sku_id": "s0", "updates": {field: "wrong"}}
            keywords = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
            yield {0: (code_delta(f"await draft_sku_attributes_update({keywords})") if entry == "python" else
                       DeltaToolCall(name="draft_sku_attributes_update", json_args=json.dumps(arguments), tool_call_id="invalid"))}
        else:
            results.append(str(returns[-1].content))
            yield "该修改已被拒绝。"

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    asyncio.run(ui.prepare_run(body(target_draft_ids=["another-draft" if outside_selection else draft_id])).stream(lambda _: None))
    assert results
    assert "ATTRIBUTE_OUTSIDE_SCOPE" in results[0] if not outside_selection else "所选" in results[0]
    assert app.products.draft_record(draft_id) == before


def test_script_error_exposes_saved_receipts_before_retry_without_replaying(tmp_path):
    executed, requests = [], []

    async def model(messages, info):
        requests.append(1)
        if len(requests) == 1:
            yield {0: code_delta("await update_row(draft_id='already-saved')\n1 / 0", "partial")}
        elif len(requests) == 2:
            assert "already-saved" in info.instructions
            assert "partial__1" in info.instructions
            assert "禁止整段重放" in info.instructions
            yield {0: code_delta("await update_row(draft_id='remaining')", "continue")}
        else:
            yield "两项已保存。"

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(
        binding("update_row", lambda args, ctx: executed.append(args["draft_id"]) or {"ok": True}, write=True),
    ))
    chunks = []
    asyncio.run(ui.prepare_run(body()).stream(chunks.append))
    assert executed == ["already-saved", "remaining"], b"".join(chunks).decode()
    assert len(requests) == 3
    ui_receipts = [row for message in ui.dump_ui_messages(CONVERSATION)["messages"]
                   for row in message.get("metadata", {}).get("business_write_receipts", [])]
    assert [row["tool_call_id"] for row in ui_receipts] == ["partial__1", "continue__1"]


def test_large_script_summary_is_bounded_without_losing_nested_receipts(tmp_path):
    outputs = []

    async def model(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            yield {0: code_delta("await update_row(draft_id='a')\n'x' * 300000")}
        else:
            outputs.append(returns[-1])
            yield "已完成，汇总过长已截断。"

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(
        binding("update_row", lambda args, ctx: {"ok": True}, write=True),
    ))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert outputs and len(outputs[0].model_response_str()) < 70000
    assert len(outputs[0].metadata["tool_returns"]) == 1
    assert len(ui.call_store.current_turn_receipts(CONVERSATION)) == 1


def test_run_code_type_errors_get_more_corrections_than_other_tools(tmp_path):
    assert global_agent_chat_service.GLOBAL_CHAT_PROFILE.retries == 2
    assert global_agent_chat_service.GLOBAL_CHAT_PROFILE.python_retries == 4
    finals = []

    async def model(messages, info):
        failures = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "run_code"]
        if returns:
            finals.append(returns[-1].content)
            yield "已完成。"
        elif len(failures) < 3:
            # 线上曾连续三次引用上一轮变量或未导入的类型，原上限 2 会中止整轮。
            yield {0: code_delta(f"value: Any = missing_{len(failures)}\nvalue", f"bad{len(failures)}")}
        else:
            yield {0: code_delta("1 + 1", "good")}

    ui = service(tmp_path, FunctionModel(stream_function=model))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert finals == [2]


def test_run_code_stops_unbounded_computation(tmp_path, monkeypatch):
    from erp_web.services import ai_agent_factory

    original = ai_agent_factory.build_python_capabilities

    def small_cpu_budget(*args, **kwargs):
        capabilities = original(*args, **kwargs)
        capabilities[0].resource_limits = {**capabilities[0].resource_limits, "max_duration_secs": 0.03}
        return capabilities

    monkeypatch.setattr(ai_agent_factory, "build_python_capabilities", small_cpu_budget)
    retries = []

    async def model(messages, info):
        errors = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
        if not errors:
            yield {0: code_delta("while True:\n    pass")}
        else:
            retries.extend(errors)
            yield "计算已被时限终止。"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    started = time.monotonic()
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert time.monotonic() - started < 3
    assert retries and "max_duration_secs" in str(retries[0].content)


def test_script_error_then_literal_tool_text_recovers_with_existing_evidence(tmp_path):
    executions = []
    final = "三个候选方案中，经济陆运的总运费最低。"
    invalid = "<run_code>\n<code>\nawait quote_read(draft_id='d1')\n</｜｜DSML｜｜ invoke>"

    async def model(messages, info):
        retries = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
        if not retries:
            # 跳过导入的静态诊断，复现线上已经输出报价后才发生的运行时错误。
            yield {0: code_delta("r = await quote_read(draft_id='d1')\nprint(r['totals'])\nimport statistics  # type: ignore")}
        elif retries[-1].tool_name == "run_code":
            assert "ModuleNotFoundError" in str(retries[-1].content)
            assert "2928" in str(retries[-1].content)
            assert "标准输出" in info.instructions
            yield invalid
        else:
            assert "未执行" in retries[-1].content
            yield final

    read = binding("quote_read", lambda args, ctx: executions.append(args) or {"totals": [2928, 4114, 5703]})
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(read))
    asyncio.run(ui.prepare_run(body("为什么选择这个物流？")).stream(lambda _: None))
    native = ui.chat_service.trusted_history(CONVERSATION)
    assert len(executions) == 1
    finals = [m for m in native if (m.metadata or {}).get("chat_final")]
    assert len(finals) == 1 and finals[0].parts[-1].content == final
    visible = ui.dump_ui_messages(CONVERSATION)["messages"]
    assert [m["role"] for m in visible].count("user") == 1
    texts = [p["text"] for m in visible for p in m["parts"] if p["type"] == "text"]
    assert invalid not in texts and texts[-1] == final
    assert any(getattr(part, "content", None) == invalid for m in native for part in m.parts)
    branch = ui.fork_conversation(CONVERSATION, visible[-1]["id"])
    assert final in str(branch["messages"])


@pytest.mark.parametrize("text", [
    "用 <run_code> 示意调用。",
    "```xml\n<run_code><code>示例</code></run_code>\n```",
])
def test_tool_markup_in_explanation_is_displayable(text):
    from erp_web.services.chat_reply_text import is_tool_call_text
    assert not is_tool_call_text(text)
    assert is_tool_call_text("<think>计划</think>\n<｜DSML｜function_calls>")


def test_repeated_tool_markup_exhausts_native_retries_without_executing(tmp_path):
    calls, executions = [], []

    async def model(messages, info):
        calls.append(1)
        yield "<｜DSML｜function_calls><｜DSML｜invoke name=\"update_row\">"

    update = binding("update_row", lambda args, ctx: executions.append(args), write=True)
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(update))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert len(calls) == global_agent_chat_service.GLOBAL_CHAT_PROFILE.retries + 1
    assert ui.dump_ui_messages(CONVERSATION)["run_status"] == "failed"
    assert not executions
    assert not any((m.metadata or {}).get("chat_final") for m in ui.chat_service.trusted_history(CONVERSATION))


def test_run_code_emits_transient_code_progress_chunks_without_polluting_history(tmp_path):
    """run_code 内嵌套调用发出瞬态 data-code-progress SSE 事件，不写入持久化消息历史。"""
    seen = []

    async def model(messages, info):
        if not business_returns(messages):
            yield {0: code_delta(
                "import asyncio\n"
                "res = await asyncio.gather(lookup_row(draft_id='d1'), lookup_row(draft_id='d2'))\n"
                "res",
                "code-progress-call",
            )}
        else:
            yield "两项查询完成。"

    lookup = binding("lookup_row", lambda args, ctx: seen.append(args) or {"ok": True, "id": args["draft_id"]})
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(lookup))
    raw_chunks = []
    asyncio.run(ui.prepare_run(body("批量查询两条草稿")).stream(raw_chunks.append))

    progress_events = []
    for raw in raw_chunks:
        text = raw.decode("utf-8")
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            if event.get("type") == "data-code-progress":
                progress_events.append(event)

    assert len(seen) == 2
    assert progress_events, "应在 SSE 流中收到瞬态 data-code-progress 事件"
    assert all(event.get("transient") is True for event in progress_events)
    final_progress = progress_events[-1]["data"]
    assert final_progress["tool_call_id"] == "code-progress-call"
    assert final_progress["tool_name"] == "lookup_row"
    assert final_progress["started"] == 2
    assert final_progress["completed"] == 2
    assert final_progress["failed"] == 0
    assert final_progress["active"] == 0

    dumped = ui.dump_ui_messages(CONVERSATION)
    all_part_types = [part["type"] for msg in dumped["messages"] for part in msg["parts"]]
    assert "data-code-progress" not in all_part_types


def test_run_code_and_search_tools_close_when_tool_budget_exhausted(tmp_path, monkeypatch):
    """工具调用额度耗尽后，run_code 与 search_tools 必须一并隐藏并强制最终答复。"""
    from pydantic_ai.settings import ToolOrOutput

    monkeypatch.setattr(
        global_agent_chat_service,
        "GLOBAL_CHAT_PROFILE",
        replace(global_agent_chat_service.GLOBAL_CHAT_PROFILE, max_tool_calls=2),
    )
    seen_tools: list[list[str]] = []
    seen_tool_choices: list[object] = []

    async def model(messages, info):
        names = [tool.name for tool in info.function_tools]
        seen_tools.append(names)
        seen_tool_choices.append((info.model_settings or {}).get("tool_choice"))
        if len(seen_tools) == 1:
            discovery = discover_tools(info, "lookup_row")
            assert discovery is not None
            yield {0: discovery}
        elif len(seen_tools) == 2:
            yield {0: DeltaToolCall(name="lookup_row", json_args='{"draft_id":"d1"}', tool_call_id="c1")}
        else:
            yield "工具额度已用尽，直接汇总已完成结果。"

    ts = replace(tools(binding("lookup_row", lambda args, ctx: {"ok": True})), defer_loading=True)
    ui = service(tmp_path, FunctionModel(stream_function=model), ts)
    asyncio.run(ui.prepare_run(body("检查一条草稿")).stream(lambda _: None))

    assert len(seen_tools) == 3
    assert "run_code" in seen_tools[0] and "search_tools" in seen_tools[0]
    assert seen_tools[2] == []
    assert seen_tool_choices[2] == ToolOrOutput(function_tools=[])
    assert ui.dump_ui_messages(CONVERSATION)["run_status"] == "completed"


def test_run_code_closes_when_deadline_reserve_reached(tmp_path, monkeypatch):
    """进入最后收尾保留时间窗口后，即使仍有工具额度也必须关闭 run_code 并设置 tool_choice。"""
    from pydantic_ai.settings import ToolOrOutput
    from erp_web.schemas.ai_trace import AiExecutionContext

    phase = {"closing": False}
    monkeypatch.setattr(
        AiExecutionContext,
        "remaining_seconds",
        lambda self, *, now=None: 45.0 if phase["closing"] else 900.0,
    )
    seen_tools: list[list[str]] = []
    seen_tool_choices: list[object] = []

    def lookup(args, ctx):
        del args, ctx
        phase["closing"] = True
        return {"ok": True}

    async def model(messages, info):
        seen_tools.append([tool.name for tool in info.function_tools])
        seen_tool_choices.append((info.model_settings or {}).get("tool_choice"))
        if len(seen_tools) == 1:
            yield {0: DeltaToolCall(name="lookup_row", json_args='{"draft_id":"d1"}', tool_call_id="c1")}
        else:
            yield "进入收尾窗口，直接输出当前进度。"

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(binding("lookup_row", lookup)))
    asyncio.run(ui.prepare_run(body("检查一条草稿")).stream(lambda _: None))

    assert len(seen_tools) == 2
    assert "run_code" in seen_tools[0] and "lookup_row" in seen_tools[0]
    assert seen_tools[1] == []
    assert seen_tool_choices[1] == ToolOrOutput(function_tools=[])
    assert ui.dump_ui_messages(CONVERSATION)["run_status"] == "completed"


def test_run_code_emits_deduped_fatal_model_errors_in_code_progress(tmp_path):
    """run_code 内嵌套调用遭遇 402 等致命模型故障时，实时进度事件立即携带去重告警摘要。"""
    fatal_output = {
        "ok": False,
        "error": {
            "code": "AI_PROVIDER_PAYMENT_REQUIRED",
            "message": "「商品类目候选匹配」绑定的模型「deepSeekv4.1flash」余额不足或计费不可用（HTTP 402），请充值或在「授权配置 → AI 模型」切换绑定。",
            "retryable": False,
            "details": {
                "status_code": 402,
                "use_case_id": "category.product_match",
                "use_case_label": "商品类目候选匹配",
                "model_id": "ai_model_ds41",
                "model_name": "deepSeekv4.1flash",
            },
        },
    }

    async def model(messages, info):
        if not business_returns(messages):
            yield {0: code_delta(
                "r1 = await match_row(draft_id='d1')\n"
                "r2 = await match_row(draft_id='d2')\n"
                "[r1['ok'], r2['ok']]",
                "fatal-progress-call",
            )}
        else:
            yield "子模型余额不足，请切换模型后重试。"

    matcher = binding("match_row", lambda args, ctx: fatal_output, write=True)
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(matcher))
    raw_chunks = []
    asyncio.run(ui.prepare_run(body("批量匹配类目")).stream(raw_chunks.append))

    progress_events = []
    for raw in raw_chunks:
        for line in raw.decode("utf-8").splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            if event.get("type") == "data-code-progress":
                progress_events.append(event["data"])

    with_fatal = [item for item in progress_events if item.get("fatal_errors")]
    assert with_fatal, "首个 402 发生时应立即在 data-code-progress 浮出致命故障摘要"
    last_fatal = with_fatal[-1]["fatal_errors"]
    assert len(last_fatal) == 1
    assert last_fatal[0]["code"] == "AI_PROVIDER_PAYMENT_REQUIRED"
    assert last_fatal[0]["use_case_label"] == "商品类目候选匹配"
    assert last_fatal[0]["model_name"] == "deepSeekv4.1flash"
    assert last_fatal[0]["count"] == 2

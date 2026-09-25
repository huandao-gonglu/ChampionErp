"""可控主模型 + 真实草稿准备、Store、审批和发布 Job 的产品验收。"""

import asyncio
import json
import threading
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel, DeltaToolCall

from erp_web.context import get_context
from erp_web.facades import agent_capability_facade as capabilities
from erp_web.runtime_units import category_catalog, category_store
from erp_web.runtime_units.category_catalog import CategoryCatalog
from erp_web.services.ai_tool_registry import AiToolSet, deadline_aware_tool_executor
from erp_web.services.pricing_service import pricing_calculation_fingerprint
from erp_web.services.agent_job_service import AgentJobService
from erp_web.runtime_units.publish_bus import persist_publish_bus_terminal_results
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter
from tests.native_domain_fixture import (
    _source_product,
    _category_record,
    _FakeCategoryProvider,
    _PlatformNetworkBoundary,
)
from tests.runtime_test_utils import seed_store_currency
from tests.ai_code_mode_helpers import python_call, business_returns
from tests.test_native_agent_integration import service, body, CONVERSATION




def setup_domain(monkeypatch, count=1):
    context = get_context()
    seed_store_currency(
        "ozon",
        "RUB",
        identity={"client_id": "vertical-client", "api_key": "isolated-test-key"},
    )
    ids = []
    for i in range(count):
        product = _source_product()
        product["product_id"] = f"product-native-{i}"
        product["source"].setdefault("attributes", {})["测试缺资料"] = i == 14
        draft = product["drafts"]["ozon"]
        product["sku_items"] = [
            {
                "id": f"sku-{i}",
                "active": True,
                "name": "风扇",
                "cost_cny": "100",
                "barcode": draft["upc"],
                "package_dimensions": deepcopy(draft["package_dimensions"]),
            }
        ]
        pricing = deepcopy(draft["pricing"])
        quote = pricing["targets"]["ozon:global"]
        quote["calculation_basis"].update(
            {
                "domestic_freight_cny": "0",
                "packaging_cost_cny": "0",
                "other_cost_cny": "0",
            }
        )
        quote["calculation_fingerprint"] = pricing_calculation_fingerprint(
            quote["calculation_basis"]
        )
        draft["sku_items"] = [
            {
                "sku_id": f"sku-{i}",
                "sku": f"NATIVE-{i}",
                "stock": "5",
                "selected": True,
                "pricing": {**pricing, "applied": True},
            }
        ]
        saved = context.products.save_product(product)
        ids.append(saved["drafts"]["ozon"]["draft_id"])
    catalog = CategoryCatalog(
        {"ozon": _FakeCategoryProvider({"94765": _category_record()})}
    )
    monkeypatch.setattr(category_store, "get_category_catalog", lambda: catalog)
    monkeypatch.setattr(category_catalog, "get_category_catalog", lambda: catalog)
    monkeypatch.setattr(
        capabilities,
        "generate_ai_copy_bundle",
        lambda *args, **kwargs: {
            "ok": True,
            "language": "ru-RU",
            "copy": {
                "title": "Портативный вентилятор",
                "description": "Описание вентилятора",
            },
        },
    )
    monkeypatch.setattr(
        capabilities,
        "run_category_match",
        lambda *args, **kwargs: {
            "ok": True,
            "status": "completed",
            "selected_category_id": "94765",
            "candidates": [{"category_id": "94765", "name": "Вентиляторы"}],
        },
    )
    return context, ids


def test_fifteen_selected_drafts_prepare_concurrently_and_report_real_remaining_gap(
    tmp_path, monkeypatch
):
    context, ids = setup_domain(monkeypatch, 16)
    selected = ids[:15]
    toolset = capabilities.build_global_chat_toolset(context)
    original = toolset.bindings["draft_prepare_for_market"]
    gate = threading.Barrier(2)
    intervals = {}

    @deadline_aware_tool_executor
    def timed(arguments, execution):
        draft_id = arguments["draft_id"]
        start = time.monotonic()
        if draft_id in selected[:2]:
            gate.wait(timeout=5)
        result = original.executor(arguments, execution)
        intervals[draft_id] = (start, time.monotonic())
        return result

    toolset = AiToolSet(
        toolset.toolset_id,
        {
            **toolset.bindings,
            "draft_prepare_for_market": replace(original, executor=timed),
        },
    )

    async def model(messages, info):
        results = business_returns(messages)
        if not results:
            yield {0: DeltaToolCall(
                name="run_code",
                json_args=json.dumps({"code": (
                    "import asyncio\n"
                    f"await asyncio.gather(*[draft_prepare_for_market(draft_id=draft_id, target_platform='ozon', regenerate_copy=True) for draft_id in {selected!r}])"
                )}),
                tool_call_id="prepare",
            )}
        else:
            assert len(results) == 15
            errors = [
                p
                for p in results
                if isinstance(p.content, dict) and p.content.get("ok") is False
            ]
            assert len(errors) == 0, [(p.tool_call_id, p.content) for p in errors]
            yield "已完成 15 条草稿的文案、图片、类目与定价准备；属性需主对话单独填写。"

    ui = service(tmp_path, FunctionModel(stream_function=model), toolset)
    asyncio.run(ui.prepare_run(body(target_draft_ids=selected)).stream(lambda _: None))
    text = json.dumps(ui.dump_ui_messages(CONVERSATION), ensure_ascii=False)
    assert "已完成 15 条" in text, text[-2500:]
    first, second = intervals[selected[0]], intervals[selected[1]]
    assert max(first[0], second[0]) < min(first[1], second[1])
    for draft_id in selected[:14]:
        draft = context.products.draft_record(draft_id)
        assert draft["title"] == "Портативный вентилятор"
        assert draft["category_id"] == "94765"
        assert "85" not in draft["attributes"]
        assert draft["images"]
    assert context.products.draft_record(ids[15])["title"] == "Portable fan"
    assert context.db.list_publish_jobs()[0] == []


@pytest.mark.parametrize("remote_success", [True, False])
def test_native_approval_to_real_publish_job_and_model_reconciliation(
    tmp_path, monkeypatch, remote_success
):
    context, ids = setup_domain(monkeypatch)
    adapter = _PlatformNetworkBoundary(succeed=remote_success)
    bus = PublishingBus(
        context.db,
        adapters={"ozon": SkuGroupPublishingAdapter(adapter)},
        config_provider=context.config.load_store_config,
        terminal_callback=lambda state: persist_publish_bus_terminal_results(
            state, context=context
        ),
        max_retries=0,
        auto_resume_pending=False,
    )
    context._publishing_bus = bus

    async def model(messages, info):
        results = business_returns(messages)
        if not results:
            yield {
                0: python_call(
                    name="draft_prepare_for_market",
                    json_args=json.dumps(
                        {
                            "draft_id": ids[0],
                            "target_platform": "ozon",
                            "regenerate_copy": True,
                        }
                    ),
                    tool_call_id="prepare",
                )
            }
        elif len(results) == 1:
            assert "error" not in results[-1].content, results[-1].content
            yield {0: python_call(name="product_attributes_update", json_args=json.dumps({
                "draft_id": ids[0], "platform": "ozon", "site": "global", "category_id": "94765",
                "updates": {"85": "Champion", "4191": "Описание вентилятора"},
            }), tool_call_id="attributes")}
        elif len(results) == 2:
            assert "error" not in results[-1].content, results[-1].content
            yield {
                0: DeltaToolCall(
                    name="product_publish_request",
                    json_args=json.dumps({"draft_id": ids[0], "platform": "ozon"}),
                    tool_call_id="publish",
                )
            }
        else:
            yield "发布成功" if results[-1].content.get("ok") else "平台拒绝，发布失败"

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        capabilities.build_global_chat_toolset(context),
    )
    worker = AgentJobService(
        ui_service=ui, job_readers=capabilities.build_job_status_readers(context)
    )
    try:
        asyncio.run(ui.prepare_run(body(target_draft_ids=ids)).stream(lambda _: None))
        parts = [
            p
            for m in ui.dump_ui_messages(CONVERSATION)["messages"]
            for p in m["parts"]
            if p.get("state") == "approval-requested"
        ]
        assert len(parts) == 1 and adapter.publish_calls == 0, ui.dump_ui_messages(
            CONVERSATION
        )
        parts[0]["state"] = "approval-responded"
        parts[0]["approval"]["approved"] = True
        approval = json.dumps(
            {
                "id": CONVERSATION,
                "trigger": "submit-message",
                "messages": [{"id": "approval", "role": "assistant", "parts": parts}],
            }
        ).encode()
        asyncio.run(
            ui.prepare_run(approval, approval_token="test-token").stream(lambda _: None)
        )
        assert adapter.publish_calls == 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            worker.scan()
            if ui.call_store.pending(
                CONVERSATION
            ) is None and not ui.run_registry.is_active(CONVERSATION):
                break
            time.sleep(0.02)
        assert adapter.publish_calls == 1
        assert ui.call_store.pending(CONVERSATION) is None
        text = json.dumps(ui.dump_ui_messages(CONVERSATION), ensure_ascii=False)
        assert ("发布成功" if remote_success else "平台拒绝，发布失败") in text
    finally:
        worker.close()
        bus.executor.shutdown(wait=True)

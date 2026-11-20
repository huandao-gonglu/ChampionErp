"""网页对话翻译草稿：从发现工具到 API 出图或同一操作交接桌面。"""

import asyncio
import base64
import json

import pytest
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.facades.image_facade import continue_image_operation
from erp_web.runtime_units.draft_image_prepare import prepare_draft_images
from erp_web.schemas.image_capabilities import DraftImagesPrepareRequest
from erp_web.schemas.requests import RequestValidationError
from erp_web.services import ai_direct_request_service, image_set_service
from erp_web.services.desktop_connection_service import bind_desktop_connection
from erp_web.services.operation_availability import ImageDesktopRequired
from tests.ai_code_mode_helpers import business_returns
from tests.test_ai_direct_request_service import _binding
from tests.test_draft_image_prepare import wait_result
from tests.test_draft_image_workbench import finish
from tests.test_image_sets import image_model, plan, setup_sources
from tests.test_native_agent_integration import CONVERSATION, body, service
from tests.test_operation_availability import local_config


@pytest.mark.parametrize("desktop", [False, True], ids=["网页使用-API", "网页交接桌面"])
def test_web_chat_translates_only_requested_draft_images(monkeypatch, app_dir, desktop):
    product, draft, raw = setup_sources(app_dir)
    context = get_context()
    (app_dir / "reference-common.png").unlink()
    inputs = []
    config = context.config.load_app_config()
    if desktop:
        config["ai_models"].insert(0, local_config()["ai_models"][0])
        config["ai_use_case_bindings"]["image.translate"] = {"model_id": "canvas"}
        monkeypatch.setattr(context.config, "load_app_config", lambda: config)
    else:
        monkeypatch.setattr(ai_direct_request_service, "create_pydantic_model_binding",
                            lambda *args, **kwargs: _binding(required=("image_edit",)))

        def generate(**kwargs):
            inputs.append(kwargs["source_id"])
            return [{"source_id": kwargs["source_id"], "bytes": raw, "suffix": ".png"}]

        monkeypatch.setattr(ai_direct_request_service, "_image_request", generate)

    async def chat_model(messages, info):
        tools = {tool.name for tool in info.function_tools}
        if "image_translate" not in tools:
            yield {0: DeltaToolCall(name="search_tools", json_args='{"queries":["image_translate"]}', tool_call_id="find")}
        elif not business_returns(messages):
            yield {0: DeltaToolCall(name="image_translate", tool_call_id="translate",
                                    json_args=json.dumps({"product_id": product["product_id"], "draft_id": draft["draft_id"],
                                                          "source_image_ids": ["common", "red"]}))}
        else:
            yield "按图片处理回执报告实际结果。"

    ui = service(app_dir, FunctionModel(stream_function=chat_model), build_global_chat_toolset(context))
    chunks = []
    asyncio.run(ui.prepare_run(body("翻译当前草稿中包含中文的图片")).stream(chunks.append))
    assert b'"type":"error"' not in b"".join(chunks)
    output = business_returns(ui.chat_service.trusted_history(CONVERSATION))[-1].content
    assert output == json.loads(context.agent_calls.receipt(CONVERSATION, "translate")["output_json"])
    if desktop:
        assert b"IMAGE_DESKTOP_REQUIRED" in b"".join(chunks)
        error = output["error"]
        assert error["code"] == "IMAGE_DESKTOP_REQUIRED"
        action = error["details"]["desktop_image_action"]
        request = error["details"]["request"]
        assert request["target_language"] == draft["language"]
        assert request["draft_image_strategy"] == "replace_selected"
        assert context.image_batches.latest_for_draft(draft["draft_id"]) is None
        with pytest.raises(RequestValidationError, match="桌面端"):
            continue_image_operation(action)
        broker = context.desktop_connections
        key = broker.connect()["connection_id"]
        with bind_desktop_connection(key):
            initial, status = continue_image_operation(action)
        assert status == 200
        check, = broker.poll(key, wait_seconds=2)["calls"]
        assert check["operation"] == "canvas.check"
        broker.complete(key, {"id": check["id"], "ok": True, "result": {"ok": True}})
        edit, = broker.poll(key, wait_seconds=2)["calls"]
        assert edit["operation"] == "canvas.edit"
        inputs.extend(image["id"] for image in edit["params"]["images"])
        broker.complete(key, {"id": edit["id"], "ok": True, "result": {"images": [
            {"source_id": "red", "data": base64.b64encode(raw).decode(), "mime": "image/png"}
        ]}})
        batch = finish(draft["draft_id"])["image_batch"]
        assert initial["image_batch"]["conversation_id"] == batch["conversation_id"]
        with bind_desktop_connection(key):
            restored, status = continue_image_operation(action)
        assert status == 200 and restored["image_batch"] == batch
        assert not broker.poll(key, wait_seconds=0)["calls"]
    else:
        assert output["status"] == "partial"
        assert (output["success_count"], output["failed_count"]) == (1, 1)
        batch = context.image_batches.get(output["batch_id"]).snapshot()

    assert inputs == ["red"]
    assert [(row["source_id"], row["status"]) for row in batch["items"]] == [("common", "failed"), ("red", "success")]
    replacement, = batch["items"][1]["result_asset_ids"]
    saved = context.products.draft_record(draft["draft_id"])
    assert saved["images"] == draft["images"]
    assert saved["sku_items"][0]["overrides"]["image_asset_id"] == replacement
    pool = context.products.load_product_from_index(product["product_id"], "")["source"]["image_pool"]
    generated = next(item for item in pool if item["id"] == replacement)
    assert generated["derived_from_id"] == "red" and generated["target_language"] == draft["language"]


def test_automatic_image_preparation_checks_endpoint_before_creating_job(monkeypatch, app_dir):
    _, draft, raw = setup_sources(app_dir)
    context = get_context()
    config = context.config.load_app_config()
    config["ai_models"].insert(0, local_config()["ai_models"][0])
    config["ai_use_case_bindings"]["image.translate"] = {"model_id": "canvas"}
    monkeypatch.setattr(context.config, "load_app_config", lambda: config)
    request = DraftImagesPrepareRequest(draft_id=draft["draft_id"], count=3)
    with pytest.raises(ImageDesktopRequired) as blocked:
        prepare_draft_images(request, context=context, operation_key="prepare")
    assert not context.image_batches.latest_preparation(draft["draft_id"])

    # 执行条件恢复后继续原范围，旧阻塞不占用或污染生成记录。
    config["ai_use_case_bindings"]["image.translate"] = {"model_id": "vision-test"}
    monkeypatch.setattr(image_set_service, "plan_image_set",
                        lambda **_: image_set_service.ImageSetPlan.model_validate(plan()))
    image_model(monkeypatch, raw)
    queued = prepare_draft_images(request, context=context, operation_key="prepare",
                                  handoff_fingerprint=blocked.value.fingerprint)
    assert queued.status == "queued"
    assert wait_result(queued).status == "generated"

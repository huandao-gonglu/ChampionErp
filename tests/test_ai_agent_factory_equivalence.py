"""AiAgentFactory.run_sync() 等价契约黄金测试（重构计划 §11 阶段 1 / §12.1）。

统一 native event 内核替换后，这些测试固定重构前后的关键语义：

- output validator 在同步 native stream 路径生效（ModelRetry → 模型修正）。
- validator 持续失败映射为 validator.error_code 的安全错误；部分历史保存。
- 工具执行失败映射为脱敏的 TOOL_EXECUTION_FAILED；工具只执行一次。

成功 / Provider 失败 / deferred / 部分历史保存的等价性由
``test_ai_agent_message_persistence.py`` 与
``test_ai_agent_deferred_runtime.py`` 覆盖（均已切换到统一内核）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from erp_web.db import ErpDatabase
from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
    AiAgentFactory,
)
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.ai_model_errors import map_pydantic_model_error
from erp_web.services.ai_tool_registry import AiToolSet, deadline_aware_tool_executor
from erp_web.stores.pydantic_message_store import PydanticMessageStore
from tests.ai_function_model_streaming import streaming_function_model


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


def _profile(*, retries: int, toolset_id: str, use_case_id: str) -> AiAgentExecutionProfile[Answer]:
    return AiAgentExecutionProfile(
        use_case_id=use_case_id,
        output_type=Answer,
        toolset_id=toolset_id,
        budget_profile="equivalence.golden.v1",
        permissions=frozenset(),
        timeout_seconds=10,
        max_model_requests=4,
        max_tool_calls=1,
        max_tool_output_bytes=4096,
        retries=retries,
    )


EMPTY_TOOLSET_ID = "equivalence.golden.empty"
EMPTY_TOOLSET = AiToolSet.bind(EMPTY_TOOLSET_ID, [], {})


def _factory(tmp_path: Path, model: FunctionModel) -> AiAgentFactory:
    streaming_model = streaming_function_model(model)

    def binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del args, kwargs
        return PydanticModelBinding(
            model=streaming_model,
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    return AiAgentFactory(
        app_dir=tmp_path,
        app_config={},

        model_binding_factory=binding,
    )


class _GoldenValidator:
    """前 ``reject_times`` 次拒绝输出；携带 error_code 供安全错误映射。"""

    def __init__(self, reject_times: int, code: str) -> None:
        self.error_code = ""
        self._reject_times = reject_times
        self._code = code
        self.calls = 0

    def __call__(
        self,
        ctx: RunContext[Any],
        output: Answer,
    ) -> Answer:
        del ctx
        self.calls += 1
        if self.calls <= self._reject_times:
            self.error_code = self._code
            raise ModelRetry("输出不符合业务约束，请重新生成。")
        self.error_code = ""
        return output


def test_output_validator_runs_on_native_stream_path(tmp_path: Path) -> None:
    """validator 拒绝后模型在同步 native stream 路径收到重试并修正。"""

    model_calls = 0

    def model(_messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        answer = "bad" if model_calls == 1 else "good"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"answer": answer},
                    tool_call_id=f"final_{model_calls}",
                )
            ]
        )

    factory = _factory(tmp_path, FunctionModel(model))
    validator = _GoldenValidator(reject_times=1, code="GOLDEN_OUTPUT_INVALID")
    profile = _profile(
        retries=1,
        toolset_id=EMPTY_TOOLSET_ID,
        use_case_id="equivalence.golden.validator",
    )

    outcome = factory.run_sync(
        profile=profile,
        instructions="返回结构化答案。",
        user_prompt="执行。",
        toolset=EMPTY_TOOLSET,
        output_validator=validator,
    )

    assert outcome.output == Answer(answer="good")
    assert model_calls == 2
    assert validator.calls == 2

    assert len([message for message in outcome.messages if isinstance(message, ModelResponse)]) == 2


def test_persistent_validator_failure_maps_to_validator_error_code(
    tmp_path: Path,
) -> None:
    """validator 持续失败：映射 validator.error_code，部分历史保存。"""

    def model(_messages: list[Any], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"answer": "bad"},
                    tool_call_id="final_bad",
                )
            ]
        )

    factory = _factory(tmp_path, FunctionModel(model))
    validator = _GoldenValidator(reject_times=99, code="GOLDEN_OUTPUT_INVALID")
    profile = _profile(
        retries=1,
        toolset_id=EMPTY_TOOLSET_ID,
        use_case_id="equivalence.golden.validator.fail",
    )

    with pytest.raises(AiAgentExecutionError) as caught:
        factory.run_sync(
            profile=profile,
            instructions="返回结构化答案。",
            user_prompt="执行。",
            toolset=EMPTY_TOOLSET,
            output_validator=validator,
        )

    assert caught.value.code == "GOLDEN_OUTPUT_INVALID"
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable"),
    [(402, "AI_PROVIDER_PAYMENT_REQUIRED", False), (401, "HTTP_401", False),
     (429, "HTTP_429", True), (503, "HTTP_503", True),
     (None, "AIModelRequestError", True)],
)
def test_direct_model_error_in_validator_preserves_cause_and_history(
    tmp_path, status, expected_code, retryable,
):
    """翻译等嵌套请求失败时保留原因，不触发输出纠错或吞掉原生历史。"""
    provider_message = "Insufficient Balance; authorization=Bearer sk-testsecret12345"
    original = (
        ModelHTTPError(status, "translation", {"error": {
            "code": "invalid_request_error", "message": provider_message,
        }}) if status else ModelAPIError("translation", provider_message)
    )
    request_error = map_pydantic_model_error(
        original, model_id="translation", model_name="translation",
        api_style="openai_compatible", base_url="https://provider.example/v1",
    )
    calls = 0

    def model(_messages, info):
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[ToolCallPart(
            info.output_tools[0].name, {"answer": "等待翻译"}, "final-translation",
        )])

    def validator(ctx, output):
        raise request_error

    # 之前的业务纠错状态不能盖过这次明确的 Provider 错误。
    validator.error_code = "STALE_VALIDATION_ERROR"
    factory = _factory(tmp_path, FunctionModel(model))
    with pytest.raises(AiAgentExecutionError) as caught:
        factory.run_sync(
            profile=_profile(retries=2, toolset_id=EMPTY_TOOLSET_ID,
                             use_case_id="equivalence.translation.fail"),
            instructions="返回结构化答案。", user_prompt="翻译外观属性。",
            toolset=EMPTY_TOOLSET, output_validator=validator,
        )
    error = caught.value
    assert error.code == expected_code
    assert error.retryable is retryable
    assert "Insufficient Balance" in str(error)
    assert "sk-testsecret12345" not in str(error)
    if status:
        assert f"HTTP {status}" in str(error)
        assert "invalid_request_error" in str(error)
        assert error.details["status_code"] == status
        assert error.details["model_id"] == "translation"
        assert error.details["model_name"] == "translation"
        assert error.details["use_case_id"] == "equivalence.translation.fail"
    assert calls == 1


@pytest.mark.parametrize(
    ("status_code", "expected_code", "expected_phrase"),
    [
        (402, "AI_PROVIDER_PAYMENT_REQUIRED", "余额不足或计费不可用（HTTP 402）"),
        (401, "authentication_error", "鉴权失败或无访问权限（HTTP 401）"),
        (403, "forbidden_error", "鉴权失败或无访问权限（HTTP 403）"),
    ],
)
def test_agent_model_http_fatal_errors_include_use_case_and_model_attribution(
    tmp_path: Path,
    status_code: int,
    expected_code: str,
    expected_phrase: str,
) -> None:
    """子 Agent 遭遇 402/401/403 时，文案和 details 均明确指认功能与模型。"""

    def failing_model(_messages, _info):
        raise ModelHTTPError(
            status_code,
            "deepseek-chat",
            {"error": {"code": expected_code, "message": "Account billing/auth rejected"}},
        )

    def fake_binding(*_args, **_kwargs) -> PydanticModelBinding:
        return PydanticModelBinding(
            model=streaming_function_model(FunctionModel(failing_model)),
            model_settings={},
            model_id="ai_model_ds41",
            model_name="deepseek-chat",
            provider_id="openai",
            provider_family="openai",
            api_style="openai_compatible",
            model_config={"id": "ai_model_ds41", "name": "deepSeekv4.1flash", "model": "deepseek-chat"},
        )

    factory = AiAgentFactory(
        app_dir=tmp_path,
        app_config={},
        model_binding_factory=fake_binding,
    )
    profile = _profile(
        retries=1,
        toolset_id=EMPTY_TOOLSET_ID,
        use_case_id="category.product_match",
    )
    with pytest.raises(AiAgentExecutionError) as caught:
        factory.run_sync(
            profile=profile,
            instructions="匹配类目。",
            user_prompt="执行。",
            toolset=EMPTY_TOOLSET,
        )

    error = caught.value
    assert error.code == expected_code
    assert error.retryable is False
    assert "「商品类目候选匹配」绑定的模型「deepSeekv4.1flash」" in str(error)
    assert expected_phrase in str(error)
    assert "授权配置 → AI 模型" in str(error)
    assert error.details["status_code"] == status_code
    assert error.details["use_case_id"] == "category.product_match"
    assert error.details["use_case_label"] == "商品类目候选匹配"
    assert error.details["model_id"] == "ai_model_ds41"
    assert error.details["model_name"] == "deepSeekv4.1flash"


def test_direct_model_error_payload_preserves_use_case_and_display_name() -> None:
    from erp_web.services.ai_model_errors import direct_model_error_payload

    original = ModelHTTPError(
        402,
        "deepseek-chat",
        {"error": {"code": "insufficient_balance", "message": "Insufficient Balance"}},
    )
    mapped = map_pydantic_model_error(
        original,
        model_id="model_copy_1",
        model_name="deepseek-chat",
        api_style="openai_compatible",
        base_url="https://api.deepseek.com/v1",
        use_case_id="copy.generate",
        model_display_name="文案模型A",
    )
    payload = direct_model_error_payload(mapped)
    assert payload is not None
    assert payload["code"] == "AI_PROVIDER_PAYMENT_REQUIRED"
    assert payload["retryable"] is False
    assert "「商品 AI 文案」绑定的模型「文案模型A」" in payload["message"]
    assert payload["details"] == {
        "status_code": 402,
        "use_case_id": "copy.generate",
        "use_case_label": "商品 AI 文案",
        "model_id": "model_copy_1",
        "model_name": "文案模型A",
    }


def test_tool_executor_failure_maps_to_safe_error_once(tmp_path: Path) -> None:
    """工具执行失败：脱敏 TOOL_EXECUTION_FAILED；工具只执行一次。"""

    toolset_id = "equivalence.golden.tool"
    profile = AiAgentExecutionProfile(
        use_case_id="equivalence.golden.tool.fail",
        output_type=Answer,
        toolset_id=toolset_id,
        budget_profile="equivalence.golden.v1",
        permissions=frozenset({"equivalence.run"}),
        timeout_seconds=10,
        max_model_requests=4,
        max_tool_calls=1,
        max_tool_output_bytes=4096,
        retries=0,
    )
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        raise RuntimeError("internal-runtime-secret-token")

    definition = AiToolDefinition(
        name="flaky_tool",
        version="1",
        description="总是失败的工具",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "additionalProperties": False,
        },
        required_permission="equivalence.run",
        side_effect="none",
    )
    toolset = AiToolSet.bind(
        toolset_id,
        [definition],
        {"flaky_tool": deadline_aware_tool_executor(executor)},
    )

    def model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart("flaky_tool", {}, tool_call_id="flaky-1"),
            ]
        )

    factory = _factory(tmp_path, FunctionModel(model))

    with pytest.raises(AiAgentExecutionError) as caught:
        factory.run_sync(
            profile=profile,
            instructions="调用工具。",
            user_prompt="执行。",
            toolset=toolset,
        )

    assert caught.value.code == "TOOL_EXECUTION_FAILED"
    assert str(caught.value) == "工具执行失败，请稍后重试。"
    assert "internal-runtime-secret-token" not in str(caught.value)
    assert executions == 1

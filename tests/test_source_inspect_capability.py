"""来源检查能力的真实存储读取与 Agent 工具绑定回归。"""

from datetime import datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from erp_web.ai_capability_composition import (
    application_capability_permissions,
    bind_global_chat_toolset,
)
from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_capability_binding_scope
from erp_web.runtime_units.product_capabilities import ProductCapabilityScope
from erp_web.runtime_units.source_inspect_capability import inspect_source_facts
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.source_inspect import InspectSourceFactsRequest
from erp_web.services.capability_errors import BusinessCapabilityError


def _seed_draft(attributes: dict, logs: list) -> str:
    product = get_context().products.save_product(
        {
            "product_id": "source-product",
            "name": "来源检查测试商品",
            "source": {
                "source_platform": "1688",
                "source_url": "https://example.com/source-product",
                "attributes": attributes,
                "collect_logs": logs,
            },
            "drafts": {
                "mercadolibre": {
                    "enabled": True,
                    "platform": "mercadolibre",
                    "title": "来源检查测试草稿",
                }
            },
        }
    )
    return product["drafts"]["mercadolibre"]["draft_id"]


def test_source_inspect_executes_from_global_toolset_with_original_json() -> None:
    attributes = {
        "材质": "橡胶",
        "件数": 2,
        "压力": 0.1,
        "可拆卸": False,
        "颜色": ["红色", "蓝色"],
        "尺寸": {"长度": 12, "单位": "cm"},
        "认证": None,
        "完整原文" * 60: "  " + "原始说明" * 150 + "  ",
    }
    collected_at = "2026-09-12T10:00:00+08:00"
    draft_id = _seed_draft(attributes, [{"finished_at": collected_at}])
    context = get_context()
    before = context.db.load_product_model("source-product")
    toolset = bind_global_chat_toolset(
        scope=build_capability_binding_scope(context),
        declared_permissions=application_capability_permissions(),
    )
    binding = toolset.bindings["inspect_source_facts"]
    assert binding.definition.side_effect == "none"
    assert binding.definition.required_permission == "source.read"

    result = binding.executor(
        {"draft_id": draft_id},
        AiExecutionContext(
            task_run_id="source-inspect-test",
            attempt_id="attempt-1",
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            budget_profile="test",
        ),
    )

    assert result["draft_id"] == draft_id
    assert result["source_raw_dict"] == attributes
    entries = {entry["key"]: entry for entry in result["source_attributes"]}
    for key, value in attributes.items():
        text = entries[key]["value"]
        assert (text if isinstance(value, str) else json.loads(text)) == value
    assert entries["材质"]["field_type"] == "material"
    assert entries["压力"]["field_type"] == "specification"
    assert result["collected_at"] == collected_at
    assert result["source_platform"] == "1688"
    assert result["source_url"] == "https://example.com/source-product"
    assert "parser_confidence" not in result
    assert context.db.load_product_model("source-product") == before


@pytest.mark.parametrize(
    ("logs", "expected_time"),
    [
        ([], ""),
        (["采集日志文本"], ""),
        (
            [{"finished_at": "2026-09-12T10:00:00Z"}, None, "采集结束"],
            "2026-09-12T10:00:00Z",
        ),
    ],
)
def test_source_inspect_handles_empty_attributes_and_unstructured_logs(
    logs: list, expected_time: str
) -> None:
    draft_id = _seed_draft({}, logs)
    result = inspect_source_facts(
        InspectSourceFactsRequest(draft_id=draft_id),
        ProductCapabilityScope(get_context().products),
    )
    assert result.source_attributes == []
    assert result.source_raw_dict == {}
    assert result.collected_at == expected_time


def test_source_inspect_missing_draft_returns_business_error() -> None:
    _seed_draft({"材质": "不得返回其他草稿的资料"}, [])
    with pytest.raises(BusinessCapabilityError) as error:
        inspect_source_facts(
            InspectSourceFactsRequest(draft_id="missing-draft"),
            ProductCapabilityScope(get_context().products),
        )
    assert error.value.code == "DRAFT_NOT_FOUND"


def test_source_inspect_request_rejects_blank_identity() -> None:
    with pytest.raises(ValidationError):
        InspectSourceFactsRequest(draft_id="   ")

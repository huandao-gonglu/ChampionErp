"""global.chat 单主 Agent 纵向集成测试。

覆盖：
- 主 Agent ToolSet 组合：Direct 只读能力 + 任务控制能力，且 Direct 不含写工具；
- ``global_task_start`` 的模型可见契约由真实 Task Capability Request Schema
  机械投影，任务计划以类型化参数进入 Controller（无第二次计划模型调用）；
- prepare → validate → 审批 → 发布长任务 → worker 轮询终态的完整纵向流程
  （真实 Capability + 真实 Ozon 确定性逻辑 + 只替代最终外部网络边界）；
  任务创建走 Deferred 受理 + 首次 history ready 屏障 + worker 唯一执行；
- HTTP 受信门面与 Controller/AI 路径读取到等价任务状态。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from erp_web.context import get_context
from erp_web.facades import global_task_facade
from erp_web.marketplaces.category_provider import CategoryProvider
from erp_web.runtime_units import (
    category_attribute_ai_fill,
    category_catalog,
    category_store,
)
from erp_web.runtime_units.category_catalog import CategoryCatalog
from erp_web.runtime_units.category_definition_support import (
    definition_from_legacy_attributes,
    paginate_value_candidates,
)
from erp_web.runtime_units.global_ai_control_tools import (
    GlobalTaskStartControlRequest,
    TASK_CONTROL_PERMISSION,
)
from erp_web.runtime_units.publish_adapter import OzonPublishingAdapter
from erp_web.runtime_units.publish_bus import (
    persist_publish_bus_terminal_results,
)
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.schemas.category_definition import (
    CategoryAttributeValuePage,
    CategoryDefinition,
    CategoryDetail,
)
from erp_web.schemas.global_tasks import (
    GlobalTaskApproveRequest,
    GlobalTaskInputRequest,
)
from erp_web.ai_capability_composition import (
    APPLICATION_CAPABILITY_CATALOG,
    GLOBAL_CHAT_DIRECT_CAPABILITIES,
    GLOBAL_TASK_CAPABILITIES,
)
from erp_web.services.category_attribute_fill_agent_service import (
    CategoryAttributeFillAgentRun,
)
from erp_web.services.global_task_controller import GlobalTaskControllerError
from erp_web.services.listing_currency_service import compute_currency_fingerprint
from erp_web.services.pricing_service import pricing_calculation_fingerprint
from tests.runtime_test_utils import seed_store_currency

#: 纵向流程 Ozon 店铺的发布币种指纹（身份 client_id=vertical-client）。
_VERTICAL_STORE_CURRENCY_FINGERPRINT = compute_currency_fingerprint(
    "ozon", "vertical-client", "RUB", ["RUB"], "locked", "account_api"
)


class _PlatformNetworkBoundary(OzonPublishingAdapter):
    """保留真实 Ozon 确定性逻辑，只替代最终外部网络提交。"""

    def __init__(self, *, succeed: bool) -> None:
        self.succeed = succeed
        self.publish_calls = 0

    def publish(
        self,
        product: dict[str, Any],
        platform: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        raise AssertionError("确认发布不得重新从 product 构建外发 payload")

    def publish_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.publish_calls += 1
        assert payload["items"][0]["description_category_id"] == 17027949
        assert config["ozon"]["client_id"] == "vertical-client"
        if self.succeed:
            return {
                "ok": True,
                "status": "real_publish_success",
                "external_id": "ozon-vertical-item",
            }
        return {
            "ok": False,
            "status": "real_publish_failed",
            "error": "平台拒绝纵向测试商品",
            "error_code": "VERTICAL_REMOTE_REJECTED",
        }


class _FakeCategoryProvider(CategoryProvider):
    """类目明细边界的可信替代：只覆盖 detail 与定义读取。"""

    platform = "ozon"

    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self.records = records
        self.detail_calls: list[tuple[str, str, bool]] = []

    def _record(self, category_id: str) -> dict[str, Any]:
        record = self.records.get(str(category_id))
        if record is None:
            raise ValueError(f"未找到类目 {category_id}")
        return deepcopy(record)

    def resolve_site(self, site: str = "") -> str:
        return "global"

    def category_detail(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDetail:
        self.detail_calls.append((category_id, site, False))
        record = self._record(category_id)
        return CategoryDetail(
            platform=self.platform,
            site="global",
            category_id=str(record.get("category_id") or category_id),
            path=str(record.get("category_path") or ""),
            is_leaf=True,
        )

    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDefinition:
        self.detail_calls.append((category_id, site, True))
        record = self._record(category_id)
        attributes = (
            record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
        )
        return definition_from_legacy_attributes(
            platform=self.platform,
            site="global",
            category_id=str(record.get("category_id") or category_id),
            category_path=str(record.get("category_path") or ""),
            description_category_id=str(record.get("description_category_id") or ""),
            required=list(attributes.get("required") or []),
            optional=list(attributes.get("optional") or []),
        )

    def attribute_values(
        self,
        category_id: str,
        attribute_id: str,
        *,
        site: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> CategoryAttributeValuePage:
        return paginate_value_candidates(
            [],
            platform=self.platform,
            site="global",
            category_id=str(category_id),
            attribute_id=str(attribute_id),
            query=query,
            cursor=cursor,
            limit=limit,
        )


def _category_record() -> dict[str, Any]:
    return {
        "category_id": "94765",
        "description_category_id": "17027949",
        "category_path": "Электроника / Вентиляторы",
        "platform": "ozon",
        "site": "global",
        "source": "vertical-test-boundary",
        "attributes": {
            "required": [
                {
                    "id": "85",
                    "name": "Бренд",
                    "required": True,
                    "dictionary_id": 0,
                    "raw": {"dictionary_id": 0},
                },
                {
                    "id": "4191",
                    "name": "Аннотация",
                    "required": True,
                },
            ],
            "optional": [],
        },
    }


def _source_product() -> dict[str, Any]:
    basis = {
        "cost_cny": "100",
        "listing_currency": "RUB",
        "currency_fingerprint": _VERTICAL_STORE_CURRENCY_FINGERPRINT,
        "length_cm": "12.3",
        "width_cm": "4.5",
        "height_cm": "6.7",
        "weight_kg": "0.25",
    }
    return {
        "product_id": "product-vertical-ozon",
        "name": "Portable fan",
        "brand": "Champion",
        "model": "V1",
        "sku": "OZON-VERTICAL-1",
        "cost": "100",
        "stock": "5",
        "upc": "123456789012",
        "source": {
            "title": "Portable fan",
            "description": "Source description",
            "source_platform": "1688",
            "source_url": "https://example.com/vertical-product",
            "currency": "CNY",
            "price": "100",
            "weight_kg": "0.25",
            "dimensions": {
                "length_cm": "12.3",
                "width_cm": "4.5",
                "height_cm": "6.7",
            },
            "image_pool": [
                {
                    "id": "image-vertical-1",
                    "url": "https://cdn.example.com/vertical-ozon.jpg",
                    "origin": "source",
                    "status": "ready",
                    "selected": True,
                    "is_main": True,
                    "order": 0,
                    "platforms": ["ozon"],
                }
            ],
        },
        "drafts": {
            "ozon": {
                "enabled": True,
                "platform": "ozon",
                "platforms": ["ozon"],
                "site": "global",
                "language": "ru-RU",
                "target_sites": [
                    {
                        "platform": "ozon",
                        "site": "global",
                        "language": "ru-RU",
                        "listing_currency": "RUB",
                    }
                ],
                "title": "Portable fan",
                "description": "Source description",
                "brand": "Champion",
                "model": "V1",
                "sku": "OZON-VERTICAL-1",
                "upc": "123456789012",
                "stock": "5",
                "vat": "0",
                "images": [],
                "attributes": {},
                "package_dimensions": {
                    "length_cm": "12.3",
                    "width_cm": "4.5",
                    "height_cm": "6.7",
                    "weight_kg": "0.25",
                },
                "pricing": {
                    "targets": {
                        "ozon:global": {
                            "listing_currency": "RUB",
                            "suggested_price": {
                                "amount": "1999.90",
                                "currency": "RUB",
                            },
                            "applied_price": {
                                "amount": "1999.90",
                                "currency": "RUB",
                            },
                            "calculation_basis": basis,
                            "calculation_fingerprint": (
                                pricing_calculation_fingerprint(basis)
                            ),
                        }
                    }
                },
                "status": "claimed",
            }
        },
    }


def test_global_chat_toolset_is_direct_read_only_plus_task_control() -> None:
    context = get_context()
    toolset = global_task_facade.build_global_chat_toolset(context)

    assert toolset.toolset_id == "global.chat"
    expected_direct = set(GLOBAL_CHAT_DIRECT_CAPABILITIES)
    # 审批/拒绝不进入模型可绑定 ToolSet：只能走受信 UI/API（P1-1）。
    expected_control = {
        "global_task_start",
        "global_task_get",
        "global_task_submit_input",
        "global_task_cancel",
    }
    assert set(toolset.bindings) == expected_direct | expected_control
    assert "global_task_approve" not in toolset.bindings
    assert "global_task_reject" not in toolset.bindings

    # Direct 能力必须是只读：主 Agent 不允许在对话里直接执行业务写操作。
    for name in expected_direct:
        definition = toolset.bindings[name].definition
        assert definition.side_effect == "none", name
        assert definition.approval_required is False, name

    # 任务控制工具集 ID 与权限来自组合根，而不是逐处硬编码。
    controller = global_task_facade.build_global_task_controller(context)
    assert set(controller.task_toolset.bindings) == set(GLOBAL_TASK_CAPABILITIES)
    assert controller.task_toolset.toolset_id == "global.task"
    permissions = global_task_facade.global_chat_permissions()
    assert TASK_CONTROL_PERMISSION in permissions
    assert permissions == {
        tool.definition.required_permission
        for tool in APPLICATION_CAPABILITY_CATALOG.tools.values()
        if tool.definition.required_permission
    } | {TASK_CONTROL_PERMISSION}


@pytest.mark.parametrize("remote_success", [True, False])
def test_global_chat_typed_task_vertical_publish_flow(
    monkeypatch: pytest.MonkeyPatch,
    remote_success: bool,
) -> None:
    context = get_context()
    context.config.update_store_config_fields(
        "ozon",
        {
            "client_id": "vertical-client",
            "api_key": "vertical-api-key",
            "auth_status": "success",
            "auth_masked_account": "vertical-seller",
            "shop_name": "纵向测试店铺",
        },
        preserve_empty_sensitive=False,
    )
    # 发布币种唯一事实源：显式创建 ready 店铺授权币种配置。
    seed_store_currency("ozon", "RUB", identity={"client_id": "vertical-client"})
    saved_product = context.products.save_product(_source_product())
    draft_id = str(saved_product["drafts"]["ozon"]["draft_id"])

    boundary_calls = {"copy": 0, "category": 0, "attributes": 0}

    def direct_copy_boundary(
        product: dict[str, Any],
        source_platform: str,
        target_market: str,
        language: str,
        mode: str,
        app_config: dict[str, Any],
        *,
        app_dir,
    ) -> dict[str, Any]:
        boundary_calls["copy"] += 1
        assert product["product_id"] == saved_product["product_id"]
        assert (source_platform, target_market, language, mode) == (
            "1688",
            "ozon",
            "ru-RU",
            "rewrite",
        )
        assert isinstance(app_config, dict)
        assert app_dir == context.paths.app_dir
        return {
            "ok": True,
            "language": "ru-RU",
            "copy": {
                "title": "Портативный вентилятор",
                "description": "Подробное описание портативного вентилятора.",
                "bullets": ["Компактный"],
            },
        }

    def focused_category_boundary(*_args, **kwargs) -> dict[str, Any]:
        boundary_calls["category"] += 1
        del kwargs
        return {
            "ok": True,
            "status": "unresolved",
            "selected_category_id": None,
            "query": "portable fan",
            "candidates": [{"category_id": "94765", "name": "Вентиляторы"}],
            "decision": {"model_confidence": 0.4},
            "failure": {
                "code": "CATEGORY_MATCH_UNRESOLVED",
                "message": "请选择最终 Ozon 类目。",
                "retryable": False,
            },
            "trace": {"conversation_id": "focused-category-vertical"},
        }

    def deterministic_attribute_agent(
        payload,
        toolset,
        ledger,
        **kwargs,
    ) -> CategoryAttributeFillAgentRun:
        boundary_calls["attributes"] += 1
        del toolset, ledger, kwargs
        product_context = payload.get("product_context")
        draft_context = (
            product_context.get("draft")
            if isinstance(product_context, dict)
            else {}
        )
        description = (
            draft_context.get("description")
            if isinstance(draft_context, dict)
            else ""
        )
        return CategoryAttributeFillAgentRun(
            {
                "assignments": [
                    {"attribute_id": "4191", "value": str(description or "")}
                ]
            }
        )

    monkeypatch.setattr(
        global_task_facade,
        "generate_ai_copy_bundle",
        direct_copy_boundary,
    )
    monkeypatch.setattr(
        global_task_facade,
        "run_category_match",
        focused_category_boundary,
    )
    provider = _FakeCategoryProvider({"94765": _category_record()})
    fake_catalog = CategoryCatalog({"ozon": provider})
    monkeypatch.setattr(
        category_store,
        "get_category_catalog",
        lambda: fake_catalog,
    )
    # 发布预检/payload 编译经 prepare_publish_context 读取同一 Catalog。
    monkeypatch.setattr(
        category_catalog,
        "get_category_catalog",
        lambda: fake_catalog,
    )
    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        deterministic_attribute_agent,
    )

    network_adapter = _PlatformNetworkBoundary(succeed=remote_success)
    bus = PublishingBus(
        context.db,
        adapters={"ozon": network_adapter},
        config_provider=context.config.load_store_config,
        terminal_callback=lambda state: persist_publish_bus_terminal_results(
            state,
            context=context,
        ),
        max_retries=0,
        auto_resume_pending=False,
    )
    context._publishing_bus = bus
    links = context.deferred_task_links
    call_counter = {"value": 0}

    def _accept_and_run(controller, request, *, conversation_id: str):
        """完整 Deferred 生命周期：受理 → 首次 history 提交 → worker 推进。"""

        call_counter["value"] += 1
        acceptance = controller.accept_deferred_task(
            request,
            conversation_id=conversation_id,
            request_run_id=f"run-vertical-{call_counter['value']}",
            tool_call_id=f"call-vertical-{call_counter['value']}",
            message_id=f"message-vertical-{call_counter['value']}",
        )
        links.commit_initial_deferred_history(
            conversation_id,
            [ModelRequest(parts=[UserPromptPart("创建任务")])],
            link_id=acceptance.link_id,
            request_run_id=f"run-vertical-{call_counter['value']}",
            encoded_chunks=[],
        )
        return controller.resume_task(acceptance.task_id)

    try:
        controller = global_task_facade.build_global_task_controller(context)

        # 阶段一：类型化任务准备 + 确定性校验；补资料经统一 submit_input 合并，
        # 执行始终由 worker（resume_task）推进。
        prepare_task = _accept_and_run(
            controller,
            GlobalTaskStartControlRequest.model_validate(
                {
                    "goal": "把第一个草稿准备到 Ozon 并完成发布校验",
                    "product_id": saved_product["product_id"],
                    "platform": "ozon",
                    "steps": [
                        {
                            "capability_name": "draft_prepare_for_market",
                            "arguments": {
                                "draft_id": draft_id,
                                "target_platform": "ozon",
                                "regenerate_copy": True,
                            },
                        },
                        {
                            "capability_name": "product_publish_validate",
                            "arguments": {"draft_id": draft_id},
                        },
                    ],
                }
            ),
            conversation_id="conversation_global_chat_" + "1" * 32,
        )
        assert prepare_task.status == "needs_input"
        assert [item.key for item in prepare_task.pending_inputs] == [
            "category_id"
        ]
        assert boundary_calls == {"copy": 1, "category": 1, "attributes": 0}

        # 每次补资料都重建 Controller，证明暂停状态来自 SQLite owner。
        controller = global_task_facade.build_global_task_controller(context)
        attribute_pause = controller.submit_input(
            GlobalTaskInputRequest(
                task_id=prepare_task.task_id,
                arguments={"category_id": "94765"},
            )
        ).task
        assert attribute_pause.status == "running"
        attribute_pause = controller.resume_task(prepare_task.task_id)
        assert attribute_pause.status == "needs_input"
        assert [item.key for item in attribute_pause.pending_inputs] == ["85"]
        assert boundary_calls == {"copy": 1, "category": 1, "attributes": 1}

        controller = global_task_facade.build_global_task_controller(context)
        prepared = controller.submit_input(
            GlobalTaskInputRequest(
                task_id=attribute_pause.task_id,
                arguments={"provided_attributes": {"85": "Champion"}},
            )
        ).task
        assert prepared.status == "running"
        prepared = controller.resume_task(attribute_pause.task_id)
        assert prepared.status == "completed"
        # 第二次重放时 4191 已持久化、85 由补充资料提供，无需再次 AI 填充。
        assert boundary_calls == {"copy": 1, "category": 1, "attributes": 1}
        prepared_draft = context.db.load_draft_model(draft_id)
        assert prepared_draft["title"] == "Портативный вентилятор"
        assert prepared_draft["category_id"] == "94765"
        assert prepared_draft["description_category_id"] == "17027949"
        assert prepared_draft["attributes"]["85"] == "Champion"
        assert prepared_draft["attributes"]["4191"] == prepared_draft[
            "description"
        ]
        assert prepared_draft["images"] == [
            {"asset_id": "image-vertical-1", "role": "main", "order": 0}
        ]

        # 校验结果（含 digest）是任务状态里的可信事实，供下一步审批引用。
        validate_result = prepared.steps[1].result
        assert validate_result is not None
        assert validate_result["passed"] is True
        validation_digest = str(validate_result["validation_digest"])
        assert len(validation_digest) == 64

        # 阶段二：发布步骤进入任务；审批快照/摘要由服务端生成，模型不提交 approval。
        publish_task = _accept_and_run(
            controller,
            GlobalTaskStartControlRequest.model_validate(
                {
                    "goal": "提交 Ozon 真实发布",
                    "product_id": saved_product["product_id"],
                    "platform": "ozon",
                    "steps": [
                        {
                            "capability_name": "product_publish_request",
                            "arguments": {
                                "draft_id": draft_id,
                                "platform": "ozon",
                            },
                        }
                    ],
                }
            ),
            conversation_id="conversation_global_chat_" + "2" * 32,
        )
        assert publish_task.status == "pending_approval"
        assert publish_task.pending_approval is not None
        # 审批展示与执行参数都来自服务端冻结快照（P1-2）。
        approval_payload = publish_task.pending_approval.payload
        assert approval_payload["summary"]
        assert approval_payload["canonical_payload"]["validation_digest"] == (
            validation_digest
        )
        assert approval_payload["canonical_payload"]["draft_id"] == draft_id
        assert network_adapter.publish_calls == 0
        assert boundary_calls["copy"] == 1

        # 阶段三：受信审批者确认只改变业务状态；worker 领取后以原
        # operation_key 执行，长任务进入通用 in_progress。
        submitted = controller.approve_task(
            GlobalTaskApproveRequest(task_id=publish_task.task_id),
            conversation_id="conversation_global_chat_" + "2" * 32,
            message_id="message-vertical-3",
            approver="local-ui:vertical-test",
        ).task
        assert submitted.status == "running"
        submitted = controller.resume_task(publish_task.task_id)
        assert submitted.status == "in_progress"
        assert submitted.active_job is not None
        job_id = submitted.active_job.job_id
        assert submitted.steps[0].status == "running"

        # 审批已消费：重复确认被稳定拒绝，且不会二次提交。
        with pytest.raises(GlobalTaskControllerError) as error:
            controller.approve_task(
                GlobalTaskApproveRequest(task_id=publish_task.task_id),
                approver="local-ui:vertical-test",
            )
        assert error.value.code == "GLOBAL_TASK_APPROVAL_NOT_EXPECTED"

        bus.wait(job_id, timeout=5)
        bus.executor.shutdown(wait=True)
        assert network_adapter.publish_calls == 1

        # 阶段四：worker 轮询把平台真实终态映射为任务终态。
        terminal = controller.resume_task(publish_task.task_id)
        expected_task_status = "completed" if remote_success else "failed"
        assert terminal.status == expected_task_status
        assert terminal.active_job is None
        assert terminal.steps[0].status == (
            "completed" if remote_success else "failed"
        )
        if remote_success:
            assert terminal.steps[0].result == {
                "job_id": job_id,
                "job_status": "success",
            }
        else:
            assert terminal.error_code == "GLOBAL_TASK_JOB_FAILED"
            assert terminal.error_message == "平台拒绝纵向测试商品"

        persisted_draft = context.db.load_draft_model(draft_id)
        assert persisted_draft["publish_status"] == (
            "published" if remote_success else "failed"
        )
        assert persisted_draft["last_publish_task"]["job_id"] == job_id
        assert context.db.publish_log_exists(job_id, "ozon")

        # HTTP 受信门面与 Controller 读取到等价任务状态。
        payload, status = global_task_facade.read_global_task_state_payload(
            publish_task.task_id
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["task"] == controller.get_state(
            publish_task.task_id
        ).model_dump(mode="json")
        state_payload, state_status = (
            global_task_facade.read_global_task_state_payload(
                prepare_task.task_id
            )
        )
        assert state_status == 200
        assert state_payload["task"]["status"] == "completed"
    finally:
        bus.executor.shutdown(wait=True)

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest
from pydantic_ai import ModelRetry

from erp_web.context import get_context
from erp_web.runtime_units.global_task_tools import (
    DRAFTS_QUERY_TOOL,
    GLOBAL_TASK_PLAN_TOOLSET_ID,
    GLOBAL_TASK_READ_PERMISSION,
    build_global_task_planning_toolset,
)
from erp_web.schemas.ai_tools import AiToolCommand
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_capabilities import (
    DraftQueryCriteria,
    DraftQueryRequest,
    DraftQuerySnapshot,
)
from erp_web.schemas.global_tasks import (
    AnswerResolutionScope,
    GlobalPlanningDecision,
)
from erp_web.services.ai_tool_runtime import AiToolRuntime
from erp_web.services.draft_query_service import query_drafts
from erp_web.services.global_agent_service import (
    GlobalAgentService,
    GlobalPlanningOutputValidator,
)


class _Drafts:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def load_drafts_index(self, scope: str = "active") -> list[dict[str, Any]]:
        records = deepcopy(self.records)
        if scope == "published":
            return [item for item in records if item["status"] == "published"]
        if scope == "active":
            return [item for item in records if item["status"] != "published"]
        return records

    def iter_drafts_index(self, scope: str = "active") -> Iterator[dict[str, Any]]:
        yield from self.load_drafts_index(scope)


class _Snapshots:
    def __init__(self, *snapshots: DraftQuerySnapshot) -> None:
        self.items = {snapshot.snapshot_id: snapshot for snapshot in snapshots}

    def save_draft_query_snapshot(
        self,
        snapshot: DraftQuerySnapshot,
    ) -> DraftQuerySnapshot:
        self.items[snapshot.snapshot_id] = snapshot
        return snapshot

    def load_draft_query_snapshot(
        self,
        snapshot_id: str,
    ) -> DraftQuerySnapshot | None:
        return self.items.get(snapshot_id)


class _StubAgentOutcome:
    def __init__(
        self,
        output: GlobalPlanningDecision,
        *,
        conversation_id: str,
    ) -> None:
        self.output = output
        self.conversation_id = conversation_id
        self.completed_result: dict[str, Any] | None = None

    def complete(self, result: dict[str, Any]) -> None:
        self.completed_result = result


class _DraftQueryAnswerFactory:
    """模拟主 Agent 先调用 drafts_query，再引用其真实快照回答。"""

    def __init__(
        self,
        *,
        tool_arguments: dict[str, Any],
        answer_kind: str,
        answer_draft_position: int | None = None,
        conversation_id: str = "planning-tool-answer",
    ) -> None:
        self.tool_arguments = dict(tool_arguments)
        self.answer_kind = answer_kind
        self.answer_draft_position = answer_draft_position
        self.conversation_id = conversation_id
        self.calls: list[dict[str, Any]] = []
        self.tool_outputs: list[dict[str, Any]] = []

    def run_sync(self, **kwargs: Any) -> _StubAgentOutcome:
        self.calls.append(kwargs)
        runtime = AiToolRuntime(
            toolset=kwargs["toolset"],
            execution_context=AiExecutionContext.create(
                timeout_seconds=10,
                budget_profile="global.task.plan.test",
                permissions={GLOBAL_TASK_READ_PERMISSION},
            ),
            max_tool_calls=1,
        )
        result = runtime.execute(
            AiToolCommand(
                call_id=f"call-drafts-query-{len(self.calls)}",
                tool_name=DRAFTS_QUERY_TOOL,
                tool_version="1",
                arguments=self.tool_arguments,
                round=1,
            )
        )
        assert result.ok is True
        self.tool_outputs.append(result.output)
        decision = GlobalPlanningDecision(
            action="answer",
            query_snapshot_id=result.output["snapshot_id"],
            answer_kind=self.answer_kind,
            answer_draft_position=self.answer_draft_position,
        )
        validated = kwargs["output_validator"](None, decision)
        return _StubAgentOutcome(
            validated,
            conversation_id=self.conversation_id,
        )


def _draft_record(draft_id: str) -> dict[str, Any]:
    return {
        "draft_id": draft_id,
        "product_id": f"product-{draft_id}",
        "platform": "ozon",
        "platforms": ["ozon"],
        "site": "global",
        "status": "claimed",
        "publish_status": "",
        "title": f"Title {draft_id}",
        "product_title": f"Product {draft_id}",
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
        "raw": {
            "description": "Description",
            "attributes": {},
            "images": [],
            "last_precheck": {"ok": False, "errors": [], "warnings": []},
        },
    }


def _snapshot(
    snapshot_id: str = "snapshot-real",
    *,
    draft_ids: list[str] | None = None,
) -> DraftQuerySnapshot:
    ids = list(draft_ids or ["draft-1"])
    return DraftQuerySnapshot(
        snapshot_id=snapshot_id,
        draft_ids=ids,
        total=len(ids),
        count_by_platform={"ozon": len(ids)},
        count_by_status={"claimed": len(ids)},
        query=DraftQueryCriteria(scope="active"),
        created_at=datetime.now(timezone.utc),
    )


def _plan_decision(
    capabilities: list[str],
    *,
    snapshot_id: str = "",
    draft_position: int | None = None,
) -> GlobalPlanningDecision:
    return GlobalPlanningDecision(
        action="plan",
        query_snapshot_id=snapshot_id,
        plan={
            "steps": [
                {
                    "local_key": f"step-{index}",
                    "capability": capability,
                    "objective": f"执行 {capability}",
                }
                for index, capability in enumerate(capabilities, start=1)
            ],
            "draft_position": draft_position,
        },
    )


def _validate(
    validator: GlobalPlanningOutputValidator,
    decision: GlobalPlanningDecision,
) -> GlobalPlanningDecision:
    return validator(None, decision)  # type: ignore[arg-type]


def test_global_planning_toolset_only_exposes_drafts_query_and_runs_via_runtime() -> None:
    drafts = _Drafts([_draft_record("draft-1")])
    snapshots = _Snapshots()
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )

    assert toolset.toolset_id == GLOBAL_TASK_PLAN_TOOLSET_ID
    assert tuple(toolset.bindings) == (DRAFTS_QUERY_TOOL,)
    definition = toolset.bindings[DRAFTS_QUERY_TOOL].definition
    assert definition.required_permission == GLOBAL_TASK_READ_PERMISSION
    assert definition.side_effect == "none"

    runtime = AiToolRuntime(
        toolset=toolset,
        execution_context=AiExecutionContext.create(
            timeout_seconds=10,
            budget_profile="global.task.plan.test",
            permissions={GLOBAL_TASK_READ_PERMISSION},
        ),
        max_tool_calls=2,
    )
    result = runtime.execute(
        AiToolCommand(
            call_id="call-drafts-query",
            tool_name=DRAFTS_QUERY_TOOL,
            tool_version="1",
            arguments={"scope": "active", "target_platform": "ozon"},
            round=1,
        )
    )

    assert result.ok is True
    assert result.output["total"] == 1
    assert result.output["items"][0]["draft_id"] == "draft-1"
    assert result.output["snapshot_id"] in snapshots.items

    forbidden = runtime.execute(
        AiToolCommand(
            call_id="call-product-read",
            tool_name="product_read",
            tool_version="1",
            arguments={},
            round=1,
        )
    )
    assert forbidden.ok is False
    assert forbidden.error["code"] == "TOOL_NOT_ALLOWED"


@pytest.mark.parametrize(
    "goal",
    [
        "查看当前草稿箱有多少个草稿",
        "查看当前草稿箱子有多少个草稿",
    ],
)
def test_natural_language_draft_count_runs_main_agent_and_trusted_resolver(
    goal: str,
) -> None:
    drafts = _Drafts([_draft_record("draft-1")])
    snapshots = _Snapshots()
    factory = _DraftQueryAnswerFactory(
        tool_arguments={"scope": "active"},
        answer_kind="active_draft_count",
        conversation_id="planning-count-answer",
    )
    service = GlobalAgentService(
        app_dir=get_context().paths.app_dir,
        app_config=get_context().config.load_app_config(),
        message_store=get_context().pydantic_messages,
        snapshot_reader=snapshots,
        product_store=drafts,
        factory=factory,  # type: ignore[arg-type]
    )
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )

    run = service.plan(goal=goal, toolset=toolset)

    assert len(factory.calls) == 1
    assert factory.calls[0]["toolset"] is toolset
    assert run.outcome is not None
    assert run.decision.action == "answer"
    assert run.decision.answer_kind == "active_draft_count"
    assert run.decision.query_snapshot_id == factory.tool_outputs[0]["snapshot_id"]
    assert run.trusted_answer is not None
    assert run.trusted_answer.message == "当前共有 1 个活跃草稿。"
    assert run.trusted_answer.facts["active_draft_count"] == 1
    assert run.trusted_answer.query_snapshot_id in snapshots.items
    assert run.trusted_answer.query_snapshot_id != run.decision.query_snapshot_id


def test_model_count_answer_refreshes_an_old_snapshot_before_answering() -> None:
    drafts = _Drafts([_draft_record("draft-1")])
    snapshots = _Snapshots()
    old_query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    drafts.records.append(_draft_record("draft-2"))

    class _AnswerOutcome:
        def __init__(self) -> None:
            self.output = GlobalPlanningDecision(
                action="answer",
                query_snapshot_id=old_query.snapshot_id,
                answer_kind="active_draft_count",
            )
            self.conversation_id = "planning-count"
            self.completed_result: dict[str, Any] | None = None

        def complete(self, result: dict[str, Any]) -> None:
            self.completed_result = result

    outcome = _AnswerOutcome()

    class _AnswerFactory:
        def run_sync(self, **_kwargs: Any) -> Any:
            return outcome

    service = GlobalAgentService(
        app_dir=get_context().paths.app_dir,
        app_config=get_context().config.load_app_config(),
        message_store=get_context().pydantic_messages,
        snapshot_reader=snapshots,
        product_store=drafts,
        factory=_AnswerFactory(),  # type: ignore[arg-type]
    )
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )

    run = service.plan(
        goal="根据已有草稿查询回答活跃草稿总量",
        toolset=toolset,
    )

    assert old_query.total == 1
    assert run.decision.query_snapshot_id == old_query.snapshot_id
    assert run.trusted_answer is not None
    assert run.trusted_answer.facts["active_draft_count"] == 2
    assert run.trusted_answer.query_snapshot_id != old_query.snapshot_id
    assert run.trusted_answer.evidence_refs == [
        f"draft_query_snapshot:{run.trusted_answer.query_snapshot_id}"
    ]
    assert snapshots.items[run.trusted_answer.query_snapshot_id].total == 2
    run.finish()
    assert outcome.completed_result is not None
    assert (
        outcome.completed_result["query_snapshot_id"]
        == run.trusted_answer.query_snapshot_id
    )
    assert outcome.completed_result["trusted_answer"] == (
        run.trusted_answer.model_dump(mode="json")
    )


@pytest.mark.parametrize(
    ("goal", "product_id", "platform"),
    [
        ("Ozon 草稿有几个", "", ""),
        ("这个商品有多少个草稿", "", ""),
        ("ready 状态的草稿数量", "", ""),
        ("商品 p123 有多少个草稿", "", ""),
        ("今天新增多少个草稿", "", ""),
        ("缺图的草稿有几个", "", ""),
        ("现在有多少个草稿", "product-1", ""),
        ("现在有多少个草稿", "", "ozon"),
    ],
)
def test_qualified_draft_count_runs_main_agent_without_forcing_global_answer(
    goal: str,
    product_id: str,
    platform: str,
) -> None:
    drafts = _Drafts([_draft_record("draft-1")])
    snapshots = _Snapshots()
    calls: list[dict[str, Any]] = []

    class _AskFactory:
        def run_sync(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return type(
                "Outcome",
                (),
                {
                    "output": GlobalPlanningDecision(
                        action="ask_user",
                        question="请确认草稿查询条件。",
                    ),
                    "conversation_id": "planning-qualified-count",
                },
            )()

    service = GlobalAgentService(
        app_dir=get_context().paths.app_dir,
        app_config=get_context().config.load_app_config(),
        message_store=get_context().pydantic_messages,
        snapshot_reader=snapshots,
        product_store=drafts,
        factory=_AskFactory(),  # type: ignore[arg-type]
    )
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )

    run = service.plan(
        goal=goal,
        toolset=toolset,
        product_id=product_id,
        platform=platform,
    )

    assert len(calls) == 1
    assert run.decision.action == "ask_user"


def test_draft_market_question_runs_main_agent_and_trusted_resolver() -> None:
    record = _draft_record("draft-1")
    record.update(
        {
            "source_platform": "1688",
            "platform": "mercadolibre",
            "platforms": ["mercadolibre"],
            "site": "CBT",
        }
    )
    drafts = _Drafts([record])
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    factory = _DraftQueryAnswerFactory(
        tool_arguments={"positions": [1]},
        answer_kind="draft_market_context",
        answer_draft_position=1,
        conversation_id="planning-market-answer",
    )
    service = GlobalAgentService(
        app_dir=get_context().paths.app_dir,
        app_config=get_context().config.load_app_config(),
        message_store=get_context().pydantic_messages,
        snapshot_reader=snapshots,
        product_store=drafts,
        factory=factory,  # type: ignore[arg-type]
    )
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
        recent_snapshot_id=query.snapshot_id,
    )

    run = service.plan(
        goal="这个草稿是来自哪个市场",
        toolset=toolset,
        recent_snapshot_id=query.snapshot_id,
    )

    assert len(factory.calls) == 1
    assert factory.calls[0]["toolset"] is toolset
    assert factory.tool_outputs[0]["snapshot_id"] == query.snapshot_id
    assert factory.tool_outputs[0]["selected_items"][0]["draft_id"] == "draft-1"
    assert run.outcome is not None
    assert run.decision.action == "answer"
    assert run.decision.answer_kind == "draft_market_context"
    assert run.decision.query_snapshot_id == query.snapshot_id
    assert run.trusted_answer is not None
    assert run.trusted_answer.facts["source_platform"] == "1688"
    assert run.trusted_answer.facts["target_platform"] == "mercadolibre"
    assert run.trusted_answer.facts["target_site"] == "CBT"
    assert "Cross-Border" not in run.trusted_answer.message


@pytest.mark.parametrize(
    ("product_id", "platform"),
    [
        ("product-p1", ""),
        ("", "mercadolibre"),
    ],
)
def test_market_question_with_bound_scope_runs_main_agent(
    product_id: str,
    platform: str,
) -> None:
    drafts = _Drafts([_draft_record("p2")])
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    calls: list[dict[str, Any]] = []

    class _AskFactory:
        def run_sync(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return type(
                "Outcome",
                (),
                {
                    "output": GlobalPlanningDecision(
                        action="ask_user",
                        question="当前草稿与任务上下文不一致，请重新选择。",
                    ),
                    "conversation_id": "planning-scope-mismatch",
                },
            )()

    service = GlobalAgentService(
        app_dir=get_context().paths.app_dir,
        app_config=get_context().config.load_app_config(),
        message_store=get_context().pydantic_messages,
        snapshot_reader=snapshots,
        product_store=drafts,
        factory=_AskFactory(),  # type: ignore[arg-type]
    )
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )

    run = service.plan(
        goal="这个草稿的目标平台是什么",
        toolset=toolset,
        product_id=product_id,
        platform=platform,
        recent_snapshot_id=query.snapshot_id,
    )

    assert len(calls) == 1
    assert run.decision.action == "ask_user"
    assert run.trusted_answer is None


def test_ambiguous_market_reference_is_left_to_main_agent_without_guessing() -> None:
    drafts = _Drafts([_draft_record("draft-1"), _draft_record("draft-2")])
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    calls: list[dict[str, Any]] = []

    class _AskFactory:
        def run_sync(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return type(
                "Outcome",
                (),
                {
                    "output": GlobalPlanningDecision(
                        action="ask_user",
                        question="请说明要查看第几个草稿。",
                    ),
                    "conversation_id": "planning-ambiguous",
                },
            )()

    service = GlobalAgentService(
        app_dir=get_context().paths.app_dir,
        app_config=get_context().config.load_app_config(),
        message_store=get_context().pydantic_messages,
        snapshot_reader=snapshots,
        product_store=drafts,
        factory=_AskFactory(),  # type: ignore[arg-type]
    )
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )

    run = service.plan(
        goal="这个草稿是来自哪个市场",
        toolset=toolset,
        recent_snapshot_id=query.snapshot_id,
    )

    assert len(calls) == 1
    assert run.decision.action == "ask_user"
    assert run.trusted_answer is None


@pytest.mark.parametrize(
    "goal",
    [
        "把这个草稿的目标平台改成 Ozon",
        "更新这个草稿的来源平台字段",
        "准备这个草稿到目标平台",
        "发布这个草稿到 Ozon",
    ],
)
def test_market_mutation_goal_runs_main_agent(goal: str) -> None:
    drafts = _Drafts([_draft_record("draft-1")])
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    calls: list[dict[str, Any]] = []

    class _AskFactory:
        def run_sync(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return type(
                "Outcome",
                (),
                {
                    "output": GlobalPlanningDecision(
                        action="ask_user",
                        question="请确认写操作参数。",
                    ),
                    "conversation_id": "planning-market-write",
                },
            )()

    service = GlobalAgentService(
        app_dir=get_context().paths.app_dir,
        app_config=get_context().config.load_app_config(),
        message_store=get_context().pydantic_messages,
        snapshot_reader=snapshots,
        product_store=drafts,
        factory=_AskFactory(),  # type: ignore[arg-type]
    )
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )

    run = service.plan(
        goal=goal,
        toolset=toolset,
        recent_snapshot_id=query.snapshot_id,
    )

    assert len(calls) == 1


@pytest.mark.parametrize(
    "goal",
    [
        "这个草稿的目标平台是什么？然后删除这个草稿",
        "这个草稿的来源平台是什么，并导出这个草稿",
        "这个草稿的目标平台是什么，取消草稿",
    ],
)
def test_compound_market_goal_runs_main_agent(goal: str) -> None:
    drafts = _Drafts([_draft_record("draft-1")])
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    calls: list[dict[str, Any]] = []

    class _AskFactory:
        def run_sync(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return type(
                "Outcome",
                (),
                {
                    "output": GlobalPlanningDecision(
                        action="ask_user",
                        question="请确认复合操作。",
                    ),
                    "conversation_id": "planning-compound-market",
                },
            )()

    service = GlobalAgentService(
        app_dir=get_context().paths.app_dir,
        app_config=get_context().config.load_app_config(),
        message_store=get_context().pydantic_messages,
        snapshot_reader=snapshots,
        product_store=drafts,
        factory=_AskFactory(),  # type: ignore[arg-type]
    )
    toolset = build_global_task_planning_toolset(
        products=drafts,  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )

    run = service.plan(
        goal=goal,
        toolset=toolset,
        recent_snapshot_id=query.snapshot_id,
    )

    assert len(calls) == 1
    assert run.decision.action == "ask_user"


def test_global_plan_validator_rejects_unknown_capability() -> None:
    validator = GlobalPlanningOutputValidator(_Snapshots())

    with pytest.raises(ModelRetry):
        _validate(validator, _plan_decision(["system.shell.execute"]))

    assert validator.error_code == "GLOBAL_PLAN_CAPABILITY_FORBIDDEN"


@pytest.mark.parametrize(
    "capabilities",
    [
        ["product.publish.request"],
        ["product.publish.request", "product.publish.validate"],
    ],
)
def test_global_plan_validator_rejects_invalid_publish_order(
    capabilities: list[str],
) -> None:
    validator = GlobalPlanningOutputValidator(_Snapshots())

    with pytest.raises(ModelRetry):
        _validate(validator, _plan_decision(capabilities))

    assert validator.error_code == "GLOBAL_PLAN_PUBLISH_ORDER_INVALID"


def test_global_plan_validator_rejects_unknown_snapshot_and_out_of_range_position() -> None:
    snapshots = _Snapshots(_snapshot())
    validator = GlobalPlanningOutputValidator(snapshots)
    fake_answer = GlobalPlanningDecision(
        action="answer",
        query_snapshot_id="snapshot-invented-by-model",
        answer_kind="active_draft_count",
    )

    with pytest.raises(ModelRetry):
        _validate(validator, fake_answer)
    assert validator.error_code == "GLOBAL_PLAN_SNAPSHOT_UNKNOWN"

    with pytest.raises(ModelRetry):
        _validate(
            validator,
            _plan_decision(
                ["draft.prepare_for_market"],
                snapshot_id="snapshot-real",
                draft_position=2,
            ),
        )
    assert validator.error_code == "GLOBAL_PLAN_DRAFT_POSITION_INVALID"


def test_global_plan_answer_must_reference_a_real_snapshot() -> None:
    snapshots = _Snapshots(_snapshot())
    validator = GlobalPlanningOutputValidator(snapshots)
    answer = GlobalPlanningDecision(
        action="answer",
        query_snapshot_id="snapshot-real",
        answer_kind="active_draft_count",
    )

    assert _validate(validator, answer) is answer
    assert validator.error_code == ""


def test_global_plan_count_rejects_filtered_active_snapshot() -> None:
    snapshot = _snapshot()
    snapshot = snapshot.model_copy(
        update={
            "query": snapshot.query.model_copy(
                update={"target_platform": "mercadolibre"}
            )
        }
    )
    validator = GlobalPlanningOutputValidator(_Snapshots(snapshot))
    decision = GlobalPlanningDecision(
        action="answer",
        query_snapshot_id=snapshot.snapshot_id,
        answer_kind="active_draft_count",
    )

    with pytest.raises(ModelRetry):
        _validate(validator, decision)

    assert validator.error_code == "GLOBAL_PLAN_ACTIVE_SNAPSHOT_REQUIRED"


@pytest.mark.parametrize(
    "scope",
    [
        AnswerResolutionScope(expected_product_id="product-1"),
        AnswerResolutionScope(expected_target_platform="ozon"),
    ],
)
def test_global_plan_count_rejects_bound_business_scope(
    scope: AnswerResolutionScope,
) -> None:
    snapshot = _snapshot()
    validator = GlobalPlanningOutputValidator(
        _Snapshots(snapshot),
        resolution_scope=scope,
    )
    decision = GlobalPlanningDecision(
        action="answer",
        query_snapshot_id=snapshot.snapshot_id,
        answer_kind="active_draft_count",
    )

    with pytest.raises(ModelRetry):
        _validate(validator, decision)

    assert validator.error_code == "GLOBAL_PLAN_COUNT_SCOPE_CONFLICT"


def test_global_plan_rejects_target_platform_overriding_bound_scope() -> None:
    validator = GlobalPlanningOutputValidator(
        _Snapshots(),
        resolution_scope=AnswerResolutionScope(
            expected_target_platform="ozon",
        ),
    )
    decision = _plan_decision(["product.read"])
    assert decision.plan is not None
    decision = decision.model_copy(
        update={
            "plan": decision.plan.model_copy(
                update={"target_platform": "mercadolibre"}
            )
        }
    )

    with pytest.raises(ModelRetry):
        _validate(validator, decision)

    assert validator.error_code == "GLOBAL_PLAN_PLATFORM_SCOPE_MISMATCH"


def test_global_plan_allows_target_platform_when_scope_is_unbound() -> None:
    validator = GlobalPlanningOutputValidator(_Snapshots())
    decision = _plan_decision(["product.read"])
    assert decision.plan is not None
    decision = decision.model_copy(
        update={
            "plan": decision.plan.model_copy(update={"target_platform": "ozon"})
        }
    )

    assert _validate(validator, decision) is decision

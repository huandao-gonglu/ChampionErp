from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

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
    DraftQuerySnapshot,
)
from erp_web.schemas.global_tasks import GlobalPlanningDecision
from erp_web.services.ai_tool_runtime import AiToolRuntime
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


class _Recorder:
    conversation_id = "aic_global_plan_test"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record(self, event_type: str, **payload: Any) -> None:
        self.events.append((event_type, payload))


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
        recorder=_Recorder(),
        max_tool_calls=2,
    )
    result = runtime.execute(
        AiToolCommand(
            call_id="call-drafts-query",
            tool_name=DRAFTS_QUERY_TOOL,
            tool_version="1",
            arguments={"scope": "active", "platform": "ozon"},
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


def test_global_planner_passes_parent_before_agent_conversation_starts() -> None:
    snapshots = _Snapshots()
    toolset = build_global_task_planning_toolset(
        products=_Drafts([]),  # type: ignore[arg-type]
        global_tasks=snapshots,  # type: ignore[arg-type]
    )
    calls: list[dict[str, Any]] = []

    class _Factory:
        def run_sync(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return type(
                "Outcome",
                (),
                {
                    "output": GlobalPlanningDecision(
                        action="ask_user",
                        question="请说明要处理哪个草稿。",
                    ),
                    "conversation_id": "planning-child",
                },
            )()

    context = get_context()
    service = GlobalAgentService(
        app_dir=context.paths.app_dir,
        app_config=context.config.load_app_config(),
        journal=context.ai_journal,
        snapshot_reader=snapshots,
        factory=_Factory(),  # type: ignore[arg-type]
    )

    run = service.plan(
        goal="查看草稿",
        toolset=toolset,
        parent_conversation_id="global-parent",
    )

    assert run.conversation_id == "planning-child"
    assert calls[0]["parent_conversation_id"] == "global-parent"


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
        answer="当前有一个草稿。",
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
        answer="根据查询快照生成确定性回答。",
    )

    assert _validate(validator, answer) is answer
    assert validator.error_code == ""

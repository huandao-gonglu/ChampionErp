from __future__ import annotations

from datetime import datetime, timezone

import pytest

from erp_web.db import ErpDatabase
from erp_web.schemas.global_tasks import (
    LocalGlobalTaskState,
    LocalTaskStep,
    PublishConfirmation,
)
from erp_web.stores.global_task_store import (
    GlobalTaskStoreError,
    LocalGlobalTaskStore,
)


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)


def _task(
    task_id: str,
    conversation_id: str,
    *,
    status: str = "planning",
) -> LocalGlobalTaskState:
    return LocalGlobalTaskState(
        task_id=task_id,
        task_kind="global.agent.chat",
        goal="准备第二个草稿并发布到 Ozon",
        status=status,
        ai_work_conversation_id=conversation_id,
        assistant_message="正在规划。",
        created_at=NOW,
        updated_at=NOW,
    )


def test_sqlite_roundtrip_preserves_complete_global_task_state(tmp_path) -> None:
    database_path = tmp_path / "erp.sqlite3"
    store = LocalGlobalTaskStore(ErpDatabase(database_path))
    state = LocalGlobalTaskState(
        task_id="task-roundtrip",
        task_kind="global.agent.chat",
        goal="发布第二个草稿",
        product_id="product-1",
        platform="ozon",
        status="waiting_publish_result",
        steps=[
            LocalTaskStep(
                step_id="step_1_validate",
                capability="product.publish.validate",
                objective="执行确定性发布校验",
                status="completed",
                inputs={
                    "draft_id": "draft-2",
                    "draft_position": 2,
                    "nested": {"site": "global", "enabled": True},
                },
                result_summary="校验通过",
                result_ref="draft-2",
            ),
            LocalTaskStep(
                step_id="step_2_publish",
                capability="product.publish.request",
                objective="提交发布",
                status="running",
                inputs={"draft_id": "draft-2", "platform": "ozon"},
                result_summary="已提交发布队列",
                result_ref="publish-job-1",
            ),
        ],
        current_step_index=1,
        publish_confirmation=PublishConfirmation(
            status="confirmed",
            validation_digest="a" * 64,
            summary={
                "platform": "ozon",
                "price": {"amount": "199.00", "currency": "RUB"},
            },
            confirmed_at=NOW,
        ),
        publish_idempotency_key="global-task:task-roundtrip:step:step_2_publish",
        publish_job_id="publish-job-1",
        draft_query_snapshot_id="snapshot-1",
        ai_work_conversation_id="conversation-1",
        agent_execution_conversation_ids=["execution-1", "execution-2"],
        assistant_message="平台正在处理发布。",
        plan_explanation="先校验，再确认并提交。",
        created_at=NOW,
        updated_at=NOW,
    )

    store.create_task(state)

    # 使用新的 Database/Store 实例读取，确保断言覆盖真实 SQLite 序列化边界。
    loaded = LocalGlobalTaskStore(ErpDatabase(database_path)).require_task(
        state.task_id
    )

    assert loaded == state
    assert loaded.steps[0].inputs["nested"] == {
        "site": "global",
        "enabled": True,
    }
    assert loaded.publish_confirmation.confirmed_at == NOW


def test_only_one_active_task_is_allowed_per_conversation(tmp_path) -> None:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    first = _task("task-1", "conversation-1")
    second = _task("task-2", "conversation-1")

    store.create_task(first)

    with pytest.raises(GlobalTaskStoreError) as error:
        store.create_task(second)

    assert error.value.code == "GLOBAL_TASK_CONVERSATION_BUSY"
    assert store.find_active_task("conversation-1") == first
    assert store.list_unfinished_tasks() == [first]

    store.save_task(
        first.model_copy(
            update={
                "status": "completed",
                "assistant_message": "任务已完成。",
            }
        ),
        touch=False,
    )
    store.create_task(second)

    assert store.find_active_task("conversation-1") == second
    assert store.list_unfinished_tasks() == [second]

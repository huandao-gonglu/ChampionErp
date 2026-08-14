from __future__ import annotations

from datetime import datetime, timezone
import time

import pytest

from erp_web.db import ErpDatabase
from erp_web.schemas.global_tasks import (
    LocalGlobalTaskState,
    LocalTaskStep,
    PublishConfirmation,
    RequiredInput,
)
from erp_web.stores.global_task_store import (
    GlobalTaskStoreError,
    LocalGlobalTaskStore,
)


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)


def _task(
    task_id: str,
    *,
    status: str = "planning",
) -> LocalGlobalTaskState:
    return LocalGlobalTaskState(
        task_id=task_id,
        goal="准备第二个草稿并发布到 Ozon",
        status=status,
        assistant_message="正在规划。",
        created_at=NOW,
        updated_at=NOW,
    )


def test_sqlite_roundtrip_preserves_complete_global_task_state(tmp_path) -> None:
    database_path = tmp_path / "erp.sqlite3"
    store = LocalGlobalTaskStore(ErpDatabase(database_path))
    state = LocalGlobalTaskState(
        task_id="task-roundtrip",
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


def test_multiple_active_tasks_are_stored_independently(tmp_path) -> None:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    first = _task("task-1")
    second = _task("task-2")

    store.create_task(first)
    store.create_task(second)

    assert store.list_unfinished_tasks() == [first, second]


def test_stale_revision_save_is_rejected_without_lost_update(tmp_path) -> None:
    database_path = tmp_path / "erp.sqlite3"
    first_store = LocalGlobalTaskStore(ErpDatabase(database_path))
    second_store = LocalGlobalTaskStore(ErpDatabase(database_path))
    created = first_store.create_task(_task("task-cas"))
    stale = second_store.require_task(created.task_id)

    saved = first_store.save_task(
        created.model_copy(update={"assistant_message": "第一个写入成功。"})
    )
    with pytest.raises(GlobalTaskStoreError) as error:
        second_store.save_task(
            stale.model_copy(update={"assistant_message": "过期写入。"})
        )

    assert error.value.code == "GLOBAL_TASK_REVISION_CONFLICT"
    assert second_store.require_task(created.task_id) == saved
    assert saved.revision == created.revision + 1


def test_execution_claim_blocks_other_owner_then_allows_release_and_expiry(
    tmp_path,
) -> None:
    database_path = tmp_path / "erp.sqlite3"
    first = LocalGlobalTaskStore(ErpDatabase(database_path))
    second = LocalGlobalTaskStore(ErpDatabase(database_path))
    task = first.create_task(_task("task-lease"))

    with first.execution_claim(task.task_id, lease_seconds=30) as claimed:
        assert claimed is not None
        with second.execution_claim(task.task_id, lease_seconds=30) as busy:
            assert busy is None

    with second.execution_claim(task.task_id, lease_seconds=30) as released:
        assert released is not None

    # 模拟 owner 进程崩溃：直接领取但不 release，等待已持久化 lease 过期。
    first._db.claim_global_task_execution(
        task.task_id,
        owner="crashed-owner",
        execution_id="crashed-execution",
        lease_seconds=1,
    )
    with second.execution_claim(task.task_id, lease_seconds=30) as busy:
        assert busy is None
    time.sleep(1.05)
    with second.execution_claim(task.task_id, lease_seconds=30) as taken_over:
        assert taken_over is not None
        assert taken_over.execution_id.startswith("gexec_")


def test_expired_execution_token_cannot_renew_or_release_new_claim(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    store = LocalGlobalTaskStore(database)
    task = store.create_task(_task("task-token"))
    owner = "same-process-owner"

    assert database.claim_global_task_execution(
        task.task_id,
        owner=owner,
        execution_id="execution-old",
        lease_seconds=30,
    )
    assert database.claim_global_task_execution(
        task.task_id,
        owner=owner,
        execution_id="execution-overlap",
        lease_seconds=30,
    ) == {}
    with database._connect() as conn:
        conn.execute(
            "UPDATE global_tasks SET execution_lease_expires_at = 0 WHERE task_id = ?",
            (task.task_id,),
        )
        conn.commit()
    assert database.claim_global_task_execution(
        task.task_id,
        owner=owner,
        execution_id="execution-new",
        lease_seconds=30,
    )

    with database._connect() as conn:
        conn.execute(
            "UPDATE global_tasks SET execution_lease_expires_at = 0 "
            "WHERE task_id = ?",
            (task.task_id,),
        )
        conn.commit()
    assert not database.renew_global_task_execution(
        task.task_id,
        owner=owner,
        execution_id="execution-new",
        lease_seconds=30,
    )

    assert not database.renew_global_task_execution(
        task.task_id,
        owner=owner,
        execution_id="execution-old",
        lease_seconds=30,
    )
    assert not database.release_global_task_execution(
        task.task_id,
        owner=owner,
        execution_id="execution-old",
    )
    with database._connect() as conn:
        row = conn.execute(
            "SELECT execution_owner, execution_id FROM global_tasks WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()
    assert row["execution_owner"] == owner
    assert row["execution_id"] == "execution-new"


def test_disallowed_status_claim_keeps_entire_row_unchanged(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    store = LocalGlobalTaskStore(database)
    completed = store.create_task(
        _task("task-status-guard").model_copy(
            update={"status": "completed", "assistant_message": "已完成。"}
        )
    )
    with database._connect() as conn:
        before = dict(
            conn.execute(
                "SELECT * FROM global_tasks WHERE task_id = ?",
                (completed.task_id,),
            ).fetchone()
        )

    assert database.claim_global_task_execution(
        completed.task_id,
        owner="operation-owner",
        execution_id="operation-execution",
        lease_seconds=30,
        allowed_statuses=frozenset({"needs_input"}),
    ) == {}

    with database._connect() as conn:
        after = dict(
            conn.execute(
                "SELECT * FROM global_tasks WHERE task_id = ?",
                (completed.task_id,),
            ).fetchone()
        )
    assert after == before


def test_recoverable_query_filters_in_sql_and_applies_limit(tmp_path) -> None:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    for index in range(3):
        store.create_task(
            _task(
                f"task-waiting-{index}",
            ).model_copy(
                update={
                    "status": "needs_input",
                    "pending_inputs": [
                        RequiredInput(
                            key="clarification",
                            label="补充说明",
                            reason="请补充说明。",
                        )
                    ],
                    "pending_input_owner": "planning",
                }
            )
        )
    for index in range(2):
        store.create_task(
            _task(
                f"task-planning-{index}",
            )
        )

    recovered = store.list_recoverable_tasks(limit=1)

    assert len(recovered) == 1
    assert recovered[0].status == "planning"


def test_recoverable_query_excludes_unexpired_execution_lease(tmp_path) -> None:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    leased = store.create_task(_task("task-leased-1"))
    available = store.create_task(
        _task("task-leased-2")
    )

    with store.execution_claim(leased.task_id, lease_seconds=30) as claimed:
        assert claimed is not None
        recovered = store.list_recoverable_tasks(limit=1)

    assert [task.task_id for task in recovered] == [available.task_id]

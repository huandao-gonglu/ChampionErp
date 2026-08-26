from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
import time

import pytest

from erp_web.db import ErpDatabase
from erp_web.schemas.global_tasks import (
    LocalGlobalTaskState,
    LocalTaskStep,
    RequiredInput,
    TaskActiveJob,
    TaskApprovalRequest,
)
from erp_web.stores.global_task_store import (
    GlobalTaskStoreError,
    LocalGlobalTaskStore,
)


NOW = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)


def _step(
    step_id: str,
    capability_name: str,
    *,
    status: str = "pending",
    arguments: dict | None = None,
    result: dict | None = None,
) -> LocalTaskStep:
    return LocalTaskStep(
        step_id=step_id,
        capability_name=capability_name,
        capability_version="1",
        arguments=arguments or {},
        operation_key=f"global-task:task-x:step:{step_id}",
        status=status,
        result=result,
    )


def _task(
    task_id: str,
    *,
    status: str = "running",
) -> LocalGlobalTaskState:
    return LocalGlobalTaskState(
        task_id=task_id,
        goal="准备第二个草稿并发布到 Ozon",
        status=status,
        assistant_message="正在执行。",
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
        status="in_progress",
        steps=[
            _step(
                "step_1_validate",
                "product_publish_validate",
                status="completed",
                arguments={
                    "draft_id": "draft-2",
                    "nested": {"site": "global", "enabled": True},
                },
                result={"result_version": "product_publish_validate.v1"},
            ),
            _step(
                "step_2_publish",
                "product_publish_request",
                arguments={"draft_id": "draft-2", "platform": "ozon"},
            ),
        ],
        current_step_index=1,
        active_job=TaskActiveJob(
            step_id="step_2_publish",
            capability_name="product_publish_request",
            job_id="publish-job-1",
            job_type="publish",
            started_at=NOW,
        ),
        assistant_message="平台正在处理发布。",
        created_at=NOW,
        updated_at=NOW,
    )

    store.create_task(state)

    # 使用新的 Database/Store 实例读取，确保断言覆盖真实 SQLite 序列化边界。
    loaded = LocalGlobalTaskStore(ErpDatabase(database_path)).require_task(
        state.task_id
    )

    assert loaded == state
    assert loaded.steps[0].arguments["nested"] == {
        "site": "global",
        "enabled": True,
    }
    assert loaded.active_job.started_at == NOW
    assert loaded.steps[1].operation_key == (
        "global-task:task-x:step:step_2_publish"
    )


def test_sqlite_load_migrates_legacy_required_input_string_options(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    store = LocalGlobalTaskStore(database)
    state = LocalGlobalTaskState(
        task_id="task-legacy-input-options",
        goal="确认平台类目",
        status="needs_input",
        steps=[_step("step_1_category", "category_match", status="needs_input")],
        pending_inputs=[
            RequiredInput(
                key="category_id",
                label="平台类目",
                reason="请选择一个候选类目。",
                input_type="select",
                options=["MLM194177"],
            )
        ],
        created_at=NOW,
        updated_at=NOW,
    )
    store.create_task(state)

    with database._connect() as conn:
        row = conn.execute(
            "SELECT task_json FROM global_tasks WHERE task_id = ?",
            (state.task_id,),
        ).fetchone()
        payload = json.loads(row["task_json"])
        payload["pending_inputs"][0]["options"] = ["MLM194177"]
        conn.execute(
            "UPDATE global_tasks SET task_json = ? WHERE task_id = ?",
            (json.dumps(payload, ensure_ascii=False), state.task_id),
        )
        conn.commit()

    loaded = LocalGlobalTaskStore(database).require_task(state.task_id)

    assert loaded.pending_inputs[0].model_dump(mode="json")["options"] == [
        {"value": "MLM194177", "label": "MLM194177"}
    ]


def test_sqlite_roundtrip_preserves_pending_approval_binding(tmp_path) -> None:
    database_path = tmp_path / "erp.sqlite3"
    store = LocalGlobalTaskStore(ErpDatabase(database_path))
    approval = TaskApprovalRequest(
        step_id="step_1_publish",
        capability_name="product_publish_request",
        capability_version="1",
        operation_key="global-task:task-approval:step:step_1_publish",
        task_revision=1,
        digest="a" * 64,
        payload={
            "summary": "发布草稿到 ozon",
            "canonical_payload": {"platform": "ozon", "price": {"amount": "199.00"}},
        },
        requested_at=NOW,
    )
    state = LocalGlobalTaskState(
        task_id="task-approval",
        goal="发布草稿",
        status="pending_approval",
        steps=[
            _step("step_1_publish", "product_publish_request"),
        ],
        pending_approval=approval,
        created_at=NOW,
        updated_at=NOW,
    )

    store.create_task(state)
    loaded = LocalGlobalTaskStore(ErpDatabase(database_path)).require_task(
        state.task_id
    )

    assert loaded == state
    assert loaded.pending_approval.digest == "a" * 64
    assert loaded.pending_approval.requested_at == NOW


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


def test_create_task_with_deferred_link_is_atomic_and_unclaimed(tmp_path) -> None:
    database_path = tmp_path / "erp.sqlite3"
    database = ErpDatabase(database_path)
    store = LocalGlobalTaskStore(database)
    state = _task("task-deferred-create")

    created, link_row = store.create_task_with_deferred_link(
        state,
        link_id="dlink_1",
        conversation_id="conversation_global_chat_" + "a" * 32,
        request_run_id="run-1",
        tool_call_id="call-1",
    )

    assert created.task_id == state.task_id
    # Deferred 创建不领取执行权：worker ready 屏障之前没有任何执行者。
    assert created.execution_id == ""
    assert link_row["link_status"] == "awaiting_history"
    assert link_row["ready_at"] == ""
    assert link_row["task_id"] == state.task_id
    # 没有执行 lease：其他执行者也不会“看到”首次 claim。
    second = LocalGlobalTaskStore(ErpDatabase(database_path))
    with second.execution_claim(state.task_id, lease_seconds=30) as claimed:
        assert claimed is not None


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
        step = _step(
            "step_1_read",
            "product_read",
            status="needs_input",
        )
        store.create_task(
            _task(f"task-needs-input-{index}").model_copy(
                update={
                    "status": "needs_input",
                    "steps": [step],
                    "current_step_index": 0,
                    "pending_inputs": [
                        RequiredInput(
                            key="clarification",
                            label="补充说明",
                            reason="请补充说明。",
                        )
                    ],
                }
            )
        )
    for index in range(2):
        store.create_task(_task(f"task-running-{index}"))

    recovered = store.list_recoverable_tasks(limit=1)

    assert len(recovered) == 1
    assert recovered[0].status == "running"


def test_recoverable_query_excludes_unexpired_execution_lease(tmp_path) -> None:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    leased = store.create_task(_task("task-leased-1"))
    available = store.create_task(_task("task-leased-2"))

    with store.execution_claim(leased.task_id, lease_seconds=30) as claimed:
        assert claimed is not None
        recovered = store.list_recoverable_tasks(limit=1)

    assert [task.task_id for task in recovered] == [available.task_id]


def test_task_lock_serializes_same_task_and_requires_task_id(tmp_path) -> None:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))

    with pytest.raises(GlobalTaskStoreError) as error:
        with store.task_lock("  "):
            pass
    assert error.value.code == "GLOBAL_TASK_ID_REQUIRED"

    acquired_in_other_thread: list[bool] = []
    release = threading.Event()
    probe_done = threading.Event()

    def probe() -> None:
        with store.task_lock("task-lock", blocking=False) as second:
            acquired_in_other_thread.append(second)
        release.set()
        probe_done.wait(5)

    with store.task_lock("task-lock") as acquired:
        assert acquired is True
        worker = threading.Thread(target=probe)
        worker.start()
        assert release.wait(5)
        # 同进程 RLock 可重入，但其他线程在同一 task_id 上无法并发领取。
        assert acquired_in_other_thread == [False]
        probe_done.set()
        worker.join(5)

    with store.task_lock("task-lock", blocking=False) as free:
        assert free is True

"""统一任务的正常完成、有限确认、原领域回写和重启预算。"""
from copy import deepcopy
import json
from urllib.request import Request

import pytest

from erp_web.context import get_context
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.services.external_request_context import request_operation
from erp_web.services.external_request_manager import BufferedResponse
from erp_web.services.task_queue_service import TaskQueueService
from tests.test_online_products import setup_online, price_request  # noqa: F401


class PublishingRemote:
    def __init__(self):
        self.reads, self.writes = 0, 0
        self.result = {"ok": True, "status": "pending_confirmation", "task_id": "remote-task"}
        self.on_read = lambda: None

    def resolve_category(self, product, config):
        return product

    def required_attributes_missing(self, context, config):
        return []

    def publish(self, product, platform, config):
        self.writes += 1
        return {"ok": True, "status": "pending_confirmation", "task_id": "remote-task"}

    def poll_publish_status(self, result, config):
        self.reads += 1
        self.on_read()
        return deepcopy(self.result)


@pytest.fixture
def queue():
    context = get_context()
    remote = PublishingRemote()
    context._publishing_bus = PublishingBus(context.db, {"ozon": remote}, task_queue=context.task_queue, auto_resume_pending=False)
    with request_operation("ai_tool:product_publish_request", trigger="agent_tool"):
        job = context.publishing_bus.enqueue({"product_id": "product", "name": "测试发布商品"}, ["ozon"],
            targets={"ozon": {"draft_id": "draft", "site": "global"}}, idempotency_key="publish-1")
    context.publishing_bus.wait(job["job_id"], timeout=2)
    task_id = f"publish:{job['job_id']}:ozon"
    return context.task_queue, remote, job["job_id"], task_id


def due(queue, task_id):
    queue.detail(task_id)
    with queue.store.connect() as conn:
        conn.execute("UPDATE confirmations SET next_check_at=0,last_checked_at=0 WHERE id=?", (task_id,))
    task = queue.reader.task(task_id)
    if task["source"] == "publish":
        state = queue.context.publishing_bus.get_status(task["job_id"])
        state["platforms"][task["platform"]]["confirmation"]["last_checked_at"] = ""
        queue.context.db.save_publish_job(state)
    else:
        from tests.test_online_products import expire
        expire(queue.context.online_products, task["job_id"])


def test_local_page_reads_do_not_query_and_scheduled_success_updates_original_job(queue):
    service, remote, job_id, task_id = queue
    assert service.list({"source": "publish", "origin": "ai", "q": "测试"})["total"] == 1
    service.detail(task_id)
    service.run_once()
    assert remote.reads == 0 and remote.writes == 1
    remote.result = {"ok": True, "status": "published", "external_id": "remote-item"}
    callbacks = []
    service.context.publishing_bus.terminal_callback = lambda state: callbacks.append(state)
    due(service, task_id)
    service.run_once()
    assert remote.reads == remote.writes == 1
    assert service.detail(task_id)["task"]["status"] == "completed"
    assert service.context.publishing_bus.get_status(job_id)["platforms"]["ozon"]["status"] == "success"
    assert len(callbacks) == 1
    service.run_once()
    assert remote.reads == remote.writes == 1


def test_check_budget_survives_restart_and_timeout_is_not_business_failure(queue):
    service, remote, job_id, task_id = queue
    due(service, task_id)
    with service.store.connect() as conn:
        row = service.store.get(task_id)
        policy = json.loads(row["policy"])
        policy["max_checks"] = 1
        conn.execute("UPDATE confirmations SET policy=? WHERE id=?", (json.dumps(policy), task_id))
    assert service.check(task_id)["checked"]
    restarted = TaskQueueService(service.context)
    assert restarted.detail(task_id)["task"]["status"] == "needs_attention"
    assert restarted.detail(task_id)["task"]["confirmation"]["checks_used"] == 1
    assert restarted.check(task_id, trigger="scheduled")["resolution"] == "budget_exhausted"
    restarted.run_once()
    assert remote.reads == 1 and remote.writes == 1
    assert service.context.publishing_bus.get_status(job_id)["platforms"]["ozon"]["status"] == "pending_confirmation"


def test_actual_http_budget_counts_each_request_and_inherits_operation_id(queue):
    service, remote, job_id, task_id = queue
    due(service, task_id)
    with service.store.connect() as conn:
        row = service.store.get(task_id)
        policy = json.loads(row["policy"])
        policy["max_requests"] = 1
        conn.execute("UPDATE confirmations SET policy=? WHERE id=?", (json.dumps(policy), task_id))
    sent = []
    manager = service.context.external_requests

    def transport(request, **kwargs):
        sent.append(request)
        return BufferedResponse(b"{}", headers={}, status=200, url=request.full_url)

    def read_twice():
        request = Request("https://api-seller.ozon.ru/v1/product/import/info", data=b'{}', headers={"Client-Id": "seller", "Api-Key": "test"})
        with request_operation("platform_reader", operation_id=job_id):
            manager.open(request, transport=transport)
            manager.open(request, transport=transport)

    remote.on_read = read_twice
    response = service.check(task_id)
    assert response["checked"] and len(sent) == 1
    assert response["task"]["status"] == "needs_attention"
    assert response["task"]["confirmation"]["requests_used"] == 1
    with manager.store._connect() as conn:
        rows = conn.execute("SELECT operation_id FROM external_attempts").fetchall()
    assert rows and all(row["operation_id"] == job_id for row in rows)
    assert remote.writes == 1


def test_past_deadline_and_old_tasks_do_not_get_new_automatic_budget(queue):
    service, remote, job_id, task_id = queue
    state = service.context.publishing_bus.get_status(job_id)
    state.pop("task_origin")
    state["created_at"] = "2000-01-01 00:00:00"
    state["platforms"]["ozon"]["confirmation"]["submitted_at"] = "2000-01-01T00:00:00Z"
    service.context.db.save_publish_job(state)
    assert service.list({"status": "needs_attention"})["items"][0]["id"] == task_id
    service.run_once()
    assert service.check(task_id, trigger="scheduled")["resolution"] == "budget_exhausted"
    assert remote.reads == 0 and remote.writes == 1
    assert service.check(task_id)["checked"]
    service.run_once()
    assert remote.reads == remote.writes == 1


def test_platform_failure_is_terminal_and_local_persistence_retries_without_remote_read(queue):
    service, remote, job_id, task_id = queue
    remote.result = {"ok": False, "status": "real_publish_failed", "error": "平台拒绝导入"}
    saves = []
    def save(state):
        saves.append(state)
        if len(saves) == 1:
            raise OSError("本地保存暂时失败")
    service.context.publishing_bus.terminal_callback = save
    assert service.check(task_id)["checked"]
    assert service.detail(task_id)["task"]["status"] == "needs_attention"
    service.run_once()
    assert len(saves) == 2 and remote.reads == 1
    assert service.detail(task_id)["task"]["status"] == "failed"
    assert service.context.publishing_bus.get_status(job_id)["terminal_results_persisted"]


def attach_online(service):
    context = get_context()
    service.context.task_queue = context.task_queue
    context._online_products = service
    return context.task_queue


def test_sync_success_is_history_without_confirmation_plan(setup_online):
    service, remote = setup_online
    queue = attach_online(service)
    job = service.change(price_request(service))["job"]
    service.run_once()
    response = queue.detail(f"online:{job['id']}")
    assert response["task"]["status"] == "completed"
    assert response["task"]["confirmation"] is None
    assert len(remote.writes) == 1


def test_online_async_confirmation_and_quarantine_use_original_job(setup_online):
    service, remote = setup_online
    queue = attach_online(service)
    remote.receipt = {"task_id": "online-task"}
    remote.pending = True
    job = service.change(price_request(service))["job"]
    service.run_once()
    task_id = f"online:{job['id']}"
    assert queue.check(task_id)["task"]["status"] == "waiting"
    # 隔离要求人工处理，不能作为普通 pending 无限查询。
    due(queue, task_id)
    remote.confirmation_details = lambda *args: {"pending": False, "needs_attention": True, "errors": [], "note": "价格隔离"}
    queue.run_once()
    assert service.store.job(job["id"])["status"] == "needs_attention"
    assert queue.detail(task_id)["task"]["confirmation"]["next_check_at"] == 0
    queue.run_once()
    assert len(remote.writes) == 1


def test_online_confirmation_lease_does_not_outlive_the_queue_round(setup_online):
    service, remote = setup_online
    queue = attach_online(service)
    remote.receipt = {"task_id": "online-task"}
    job = service.change(price_request(service))["job"]
    service.run_once()
    seen = []
    original = remote.read_confirmation
    def read(listing, request):
        with service.store.db._connect() as conn:
            online_until = conn.execute("SELECT lease_until FROM online_jobs WHERE id=?", (job["id"],)).fetchone()[0]
        seen.append((online_until, queue.store.get(f"online:{job['id']}")["lease_until"]))
        return original(listing, request)
    remote.read_confirmation = read
    assert queue.check(f"online:{job['id']}")["checked"]
    assert seen and seen[0][0] <= seen[0][1]


def test_active_store_sync_defers_confirmation_without_ending_its_plan(setup_online):
    from uuid import uuid4
    service, remote = setup_online
    queue = attach_online(service)
    remote.receipt = {"task_id": "online-task"}
    job = service.change(price_request(service))["job"]
    service.run_once()
    service.sync("mercadolibre", uuid4().hex)
    result = queue.check(f"online:{job['id']}")
    assert result["task"]["confirmation"]["state"] == "waiting"
    assert service.store.job(job["id"])["status"] == "waiting_confirmation"
    assert len(remote.writes) == 1


def test_manual_and_scheduled_confirmation_share_the_same_lease(queue):
    service, remote, _, task_id = queue
    due(service, task_id)
    lease, _ = service.store.claim(task_id)
    assert lease
    assert service.check(task_id)["resolution"] == "checking"
    service.run_once()
    assert remote.reads == 0


def test_scheduled_confirmation_recovers_an_expired_lease_after_restart(queue):
    service, remote, job_id, task_id = queue
    due(service, task_id)
    lease, _ = service.store.claim(task_id)
    assert lease
    with service.store.connect() as conn:
        conn.execute("UPDATE confirmations SET lease_until=0,last_checked_at=0 WHERE id=?", (task_id,))
    restarted = TaskQueueService(service.context)
    assert restarted.detail(task_id)["task"]["confirmation"]["state"] == "waiting"
    remote.result = {"ok": True, "status": "published", "external_id": "remote-item"}
    restarted.run_once()
    assert remote.reads == remote.writes == 1
    assert restarted.detail(task_id)["task"]["status"] == "completed"
    assert restarted.store.get(task_id)["checks_used"] == 2
    assert service.context.publishing_bus.get_status(job_id)["platforms"]["ozon"]["status"] == "success"


def test_recovered_query_rejects_the_old_owner_and_requests_after_the_round_deadline(queue, monkeypatch):
    import time
    from erp_web.schemas.external_requests import ExternalRequestBlocked
    service, _, _, task_id = queue
    due(service, task_id)
    old_lease, _ = service.store.claim(task_id)
    with service.store.connect() as conn:
        conn.execute("UPDATE confirmations SET lease_until=0,last_checked_at=0 WHERE id=?", (task_id,))
    lease, _ = service.store.claim(task_id)
    with pytest.raises(ExternalRequestBlocked):
        service.store.consume_request(task_id, old_lease, None)
    service.store.finish(task_id, old_lease["token"], done=True)
    assert service.store.get(task_id)["owner"] == lease["token"]
    service.store.consume_request(task_id, lease, None)
    assert service.store.get(task_id)["requests_used"] == 1
    with monkeypatch.context() as clock:
        clock.setattr(time, "time", lambda: lease["deadline"] + 1)
        # 保存租约还有 30 秒回填宽限，但宽限不允许继续外发。
        with pytest.raises(ExternalRequestBlocked):
            service.store.consume_request(task_id, lease, None)
    assert service.store.get(task_id)["requests_used"] == 1


@pytest.mark.parametrize("exhausted", ["deadline_at", "checks_used", "requests_used"])
def test_manual_query_after_automatic_stop_has_a_request_cap_cooldown_and_no_automatic_restart(queue, exhausted):
    import time
    from erp_web.services.external_request_context import request_context
    service, remote, job_id, task_id = queue
    due(service, task_id)
    with service.store.connect() as conn:
        policy = json.loads(service.store.get(task_id)["policy"])
        policy["max_requests"] = 1
        conn.execute("UPDATE confirmations SET policy=? WHERE id=?", (json.dumps(policy), task_id))
        conn.execute(f"UPDATE confirmations SET {exhausted}=? WHERE id=?", (1 if exhausted == "deadline_at" else 1000, task_id))
    before = service.store.get(task_id)
    sent = []
    def transport(request, **kwargs):
        sent.append(request)
        return BufferedResponse(b"{}", headers={}, status=200, url=request.full_url)
    def read_twice():
        context = request_context("https://api-seller.ozon.ru/v1/product/import/info", method="POST")
        assert time.time() < context.deadline_at <= time.time() + policy["round_timeout"]
        assert service.check(task_id)["resolution"] == "checking"
        assert service.detail(task_id)["task"]["confirmation"]["state"] == "checking"
        service.run_once()
        request = Request("https://api-seller.ozon.ru/v1/product/import/info", data=b'{}', headers={"Client-Id": "seller", "Api-Key": "test"})
        service.context.external_requests.open(request, transport=transport)
        service.context.external_requests.open(request, transport=transport)
    remote.on_read = read_twice
    for count in (1, 2):
        response = service.check(task_id)
        assert response["checked"] and len(sent) == count
        assert response["task"]["status"] == "needs_attention"
        assert response["task"]["confirmation"]["requests_used"] == before["requests_used"] + count
        assert service.store.get(task_id)["deadline_at"] == before["deadline_at"]
        assert service.check(task_id)["resolution"] == "cooldown"
        service.run_once()
        assert remote.reads == count and remote.writes == 1
        due(service, task_id)


@pytest.mark.parametrize("exhausted", ["deadline_at", "checks_used", "requests_used"])
@pytest.mark.parametrize("applied", [True, False])
def test_expired_online_task_can_be_manually_confirmed_or_closed(setup_online, exhausted, applied):
    from tests.test_online_products import expire
    service, remote = setup_online
    queue = attach_online(service)
    remote.receipt = {"task_id": "online-task"}
    remote.apply = applied
    job = service.change(price_request(service))["job"]
    service.run_once()
    task_id = f"online:{job['id']}"
    queue.detail(task_id)
    with queue.store.connect() as conn:
        conn.execute(f"UPDATE confirmations SET {exhausted}=? WHERE id=?", (1 if exhausted == "deadline_at" else 1000, task_id))
    original_deadline = queue.store.get(task_id)["deadline_at"]
    assert not queue.check(task_id, trigger="scheduled")["checked"]
    expire(service, job["id"])
    response = service.reconcile(job["id"])
    current = response["job"]
    assert queue.store.get(task_id)["deadline_at"] == original_deadline
    if applied:
        assert current["status"] == "confirmed"
    else:
        assert queue.detail(task_id)["task"]["confirmation"]["state"] == "needs_attention"
        closed = service.close_operation({"job_id": job["id"], "expected_updated_at": current["updated_at"], "acknowledge": True})
        assert closed["job"]["status"] == "closed_unconfirmed"
    assert len(remote.writes) == 1
    assert service.change(price_request(service))["job"]["status"] == "queued"


@pytest.mark.parametrize("existing_plan", [False, True])
def test_manual_confirmation_of_a_legacy_task_does_not_start_automatic_queries(queue, existing_plan):
    service, remote, job_id, task_id = queue
    if existing_plan:
        service.detail(task_id)
    state = service.context.publishing_bus.get_status(job_id)
    state.pop("task_origin")
    service.context.db.save_publish_job(state)
    assert service.detail(task_id)["task"]["confirmation"]["state"] == "needs_attention"
    assert service.check(task_id)["checked"]
    due(service, task_id)
    service.run_once()
    assert remote.reads == remote.writes == 1
    assert service.detail(task_id)["task"]["confirmation"]["state"] == "needs_attention"


@pytest.mark.parametrize("model,operation,scope,receipt,expected", [
    ("user_products", "stock", "shared", {"id": "U1", "success": True}, "confirmed"),
    ("user_products", "stock", "shared", {"id": "U1", "success": False}, "failed"),
    ("user_products", "stock", "shared", {"id": "U2", "success": True}, "outcome_unknown"),
    ("traditional_global_items", "price", "MLM1", {"id": "MLM1", "price": 15.25}, "confirmed"),
    ("traditional_global_items", "price", "MLM1", {"id": "CBT1", "price": 15.25}, "outcome_unknown"),
])
def test_mercado_online_jobs_validate_receipts_in_the_written_scope(setup_online, model, operation, scope, receipt, expected):
    from erp_web.product_model.online_operation_model import effective_job_status
    from erp_web.schemas.online_products import PriceScope
    service, remote = setup_online
    row = remote.rows["CBT1"]
    row.model = model
    row.snapshot = {"siteless_id": "U1"} if model == "user_products" else {}
    row.prices.append(PriceScope(id="MLM1", label="站点价格", amount="12", currency="USD", writable=True))
    service.store.save(row)
    remote.receipt = receipt
    body = price_request(service, operation=operation, scope_id=scope)
    if operation == "stock":
        body["changes"] = {"quantity": 8}
    job = service.change(body)["job"]
    service.run_once()
    stored = service.store.job(job["id"])
    assert len(remote.writes) == 1
    assert stored["status"] == expected
    # 相同回执的持久化历史也使用修正后的范围规则，不改写原任务。
    assert effective_job_status({**stored, "status": "submitted"}) == expected


def test_publish_and_online_confirmation_share_the_same_store_slot(queue):
    from erp_web.runtime_units.publish_confirmation import resolve_publish_store_binding
    from erp_web.schemas.task_queue import confirmation_policy
    import time
    service, _, _, _ = queue
    identity = resolve_publish_store_binding("yandex", {"yandex": {"business_id": "1", "campaign_id": "2"}}).identity
    tasks = [
        {"id": "publish:test:yandex", "source": "publish", "platform": "yandex", "account_id": identity, "origin": "page"},
        {"id": "online:test", "source": "online", "platform": "yandex", "account_id": "1:2", "origin": "page"},
    ]
    for task in tasks:
        service.store.ensure(task, submitted_at=time.time()-120, policy=confirmation_policy("online", "yandex", "price"))
    assert service.store.claim(tasks[0]["id"])[0]
    assert service.store.claim(tasks[1]["id"], manual=True) == (None, "checking")


def test_task_post_contract_and_routes():
    from erp_web.http_routes import GET_API_ROUTES, POST_API_ROUTES
    from erp_web.schemas.requests import RequestValidationError, validate_request_payload
    assert "/api/tasks" in GET_API_ROUTES and "/api/tasks/check" in POST_API_ROUTES
    with pytest.raises(RequestValidationError):
        validate_request_payload({}, endpoint="/api/tasks/check")


@pytest.mark.parametrize("receipt,model,target,scope,expected", [
    ({}, "traditional_global_items", "CBT1", "global", "confirmed"),
    ({"unrecognized": True}, "traditional_global_items", "CBT1", "global", "outcome_unknown"),
    ({"id": "U1", "errors": None, "listing_sites": [{"id": "MLM1", "success": True, "errors": None}]}, "user_products", "U1", "MLM1", "confirmed"),
    ({"id": "U1", "success": True, "listing_sites": [{"id": "MLM1", "success": True}, {"id": "MLC1", "success": False, "errors": ["不能修改"]}]}, "user_products", "U1", "global", "partial"),
    ({"id": "U2", "success": True}, "user_products", "U1", "global", "outcome_unknown"),
    ({"id": "U1", "success": True, "listing_sites": [{"id": "MLM1", "task_id": "task-1"}]}, "user_products", "U1", "MLM1", "waiting_confirmation"),
])
def test_mercado_receipts_use_the_actual_interface_and_target(receipt, model, target, scope, expected):
    from erp_web.product_model.online_operation_model import submission_status
    assert submission_status("mercadolibre", "price", receipt, model=model, target_id=target, scope_id=scope) == expected

"""发布中断、逐项失败和显式重试；平台 I/O 使用隔离替身。"""
from collections import Counter
from copy import deepcopy

import pytest

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.product_model.task_queue_model import has_query_identity
from erp_web.runtime_units.publish_confirmation import canonical_publish_digest, resolve_publish_store_binding
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter
from tests.runtime_test_utils import temp_app_context
from tests.test_sku_workflow import ItemBoundary, context_for, product_fixture


class StagedBoundary(ItemBoundary):
    def __init__(self, app):
        self.app = app
        self.calls = []
        self.crash = False
        self.fail = True

    def publish_payload(self, payload, config):
        sku = payload["offer_id"]
        self.calls.append((sku, "catalog"))
        return {"ok": True, "status": "pending_submission", "plan": {"sku": sku},
                "checkpoint": {"offer_id": sku, "completed_steps": ["catalog"], "phase": "stock"}}

    def advance_submission(self, result, config):
        sku = result["plan"]["sku"]
        persisted = self.app.products.sku_publication("sku-draft", f"fact-{sku[-1]}", "ozon:global")
        assert persisted["status"] == "dispatching"
        self.calls.append((sku, "stock"))
        if sku == "SELL-1" and self.crash:
            raise SystemExit("模拟请求期间进程退出")
        if sku == "SELL-1" and self.fail:
            self.fail = False
            raise PublishAdapterError("EXTERNAL_WRITE_OUTCOME_UNKNOWN", "库存提交失败（HTTP 500）", details={"outcome_unknown": True})
        return {**deepcopy(result), "ok": True, "status": "pending_confirmation", "task_id": f"remote-{sku}"}

    def submission_step_interval_seconds(self, config):
        return 0

    def poll_publish_status(self, result, config):
        return {"ok": True, "status": "published", "id": result["task_id"], "task_id": result["task_id"]}


def prepare(app):
    product = product_fixture()
    fact, row = deepcopy(product["sku_items"][-1]), deepcopy(product["drafts"]["ozon"]["sku_items"][-1])
    fact["id"], row["sku_id"], row["sku"] = "fact-2", "fact-2", "SELL-2"
    product["sku_items"].append(fact)
    product["drafts"]["ozon"]["sku_items"].append(row)
    product = app.products.save_product(product)
    leaf = StagedBoundary(app)
    adapter = SkuGroupPublishingAdapter(leaf)
    payload = adapter.build_payload(context_for(product), {})
    config = {"ozon": {"client_id": "seller", "api_key": "offline"}}
    identity = resolve_publish_store_binding("ozon", config).identity
    approval = {"payload": payload, "store_identity": identity, "validation_digest": canonical_publish_digest(
        product_id=product["product_id"], draft_id="sku-draft", platform="ozon", site="global", store_identity=identity, payload=payload)}
    return product, adapter, leaf, config, approval


def test_failed_sku_keeps_batch_progress_and_manual_retry_skips_unknown_write(tmp_path):
    with temp_app_context(tmp_path) as app:
        product, adapter, leaf, config, approval = prepare(app)
        bus = PublishingBus(app.db, {"ozon": adapter}, config_provider=lambda: config, task_queue=app.task_queue)
        app._publishing_bus = bus
        queued = bus.enqueue(product, ["ozon"], targets={"ozon": {"draft_id": "sku-draft", "site": "global"}},
                             approved_publications={"ozon": approval}, idempotency_key="manual-retry-case")
        job_id = queued["job_id"]
        bus.wait(job_id, timeout=3)
        item = bus.get_status(job_id)["platforms"]["ozon"]
        assert item["status"] == "needs_attention" and item["attempts"] == 1
        assert [row["status"] for row in item["result"]["sku_results"]] == ["pending_confirmation", "outcome_unknown", "pending_submission"]
        assert has_query_identity(item["result"])
        assert "SELL-1" in item["error"] and "库存" in item["error"] and "500" in item["error"]
        assert app.task_queue.detail(f"publish:{job_id}:ozon")["task"]["can_retry"]
        assert Counter(leaf.calls)[("SELL-2", "stock")] == 0
        lease, _ = app.task_queue.store.claim(f"publish:{job_id}:ozon", manual=True)
        with pytest.raises(ValueError, match="正在查询"):
            bus.retry(job_id, "ozon")
        assert bus.get_status(job_id)["platforms"]["ozon"]["status"] == "needs_attention"
        app.task_queue.store.finish(f"publish:{job_id}:ozon", lease["token"])

        # 手动核验已受理的 SKU，不丢失其余未完成项，也不执行剩余写步骤。
        before = list(leaf.calls)
        bus.confirmation.check(job_id, "ozon")
        assert leaf.calls == before
        assert bus.get_status(job_id)["platforms"]["ozon"]["result"]["sku_results"][0]["status"] == "published"

        bus.retry(job_id, "ozon")
        bus.wait(job_id, timeout=3)
        item = bus.get_status(job_id)["platforms"]["ozon"]
        assert item["attempts"] == 2
        counts = Counter(leaf.calls)
        assert counts == Counter({("SELL-0", "catalog"): 1, ("SELL-1", "catalog"): 1, ("SELL-2", "catalog"): 1,
                                  ("SELL-0", "stock"): 1, ("SELL-1", "stock"): 1, ("SELL-2", "stock"): 1})
        bus.confirmation.check(job_id, "ozon")
        item = bus.get_status(job_id)["platforms"]["ozon"]
        assert item["status"] == "outcome_unknown"
        assert [row["status"] for row in item["result"]["sku_results"]] == ["published", "outcome_unknown", "published"]
        assert not has_query_identity(item["result"])
        with pytest.raises(ValueError, match="没有可重试"):
            bus.retry(job_id, "ozon")


def test_restart_marks_interrupted_without_submission_and_uses_saved_sku_progress(tmp_path):
    with temp_app_context(tmp_path) as app:
        product, adapter, leaf, config, approval = prepare(app)
        initial = adapter.publish_payload(approval["payload"], config)
        leaf.crash = True
        with pytest.raises(SystemExit):
            adapter.advance_submission(initial, config)
        app.db.create_publish_job({"job_id": "interrupted-job", "idempotency_key": "interrupted-key", "product": product,
            "draft_id": "sku-draft", "status": "running", "approved_publications": {"ozon": approval},
            "platforms": {"ozon": {"draft_id": "sku-draft", "product_id": product["product_id"], "site": "global",
                "platform": "ozon", "status": "running", "stage": "submitting_steps", "attempts": 1, "result": initial}}})
        before = list(leaf.calls)
        bus = PublishingBus(app.db, {"ozon": adapter}, config_provider=lambda: config)
        app._publishing_bus = bus
        assert bus._futures == {} and leaf.calls == before
        assert bus.mark_interrupted_jobs() == ["interrupted-job"]
        assert bus._futures == {} and leaf.calls == before
        item = bus.get_status("interrupted-job")["platforms"]["ozon"]
        assert item["status"] == "interrupted"
        assert [row["status"] for row in item["result"]["sku_results"]] == ["pending_confirmation", "outcome_unknown", "pending_submission"]
        assert app.products.sku_publication("sku-draft", "fact-1", "ozon:global")["status"] == "outcome_unknown"
        leaf.crash = False
        bus.retry("interrupted-job", "ozon")
        bus.wait("interrupted-job", timeout=3)
        assert bus.get_status("interrupted-job")["platforms"]["ozon"]["attempts"] == 2
        assert leaf.calls[len(before):] == [("SELL-2", "stock")]


def test_manual_retry_keeps_previous_attempt_log_and_updates_original_task(tmp_path):
    from erp_web.runtime_units.publish_bus import persist_publish_bus_terminal_results
    with temp_app_context(tmp_path) as app:
        product, _, _, config, approval = prepare(app)
        leaf = ItemBoundary()
        adapter = SkuGroupPublishingAdapter(leaf)
        bus = PublishingBus(app.db, {"ozon": adapter}, config_provider=lambda: config,
                            terminal_callback=lambda state: persist_publish_bus_terminal_results(state, context=app))
        app._publishing_bus = bus
        queued = bus.enqueue(product, ["ozon"], targets={"ozon": {"draft_id": "sku-draft", "site": "global"}},
                             approved_publications={"ozon": approval}, idempotency_key="manual-known-failure")
        job_id = queued["job_id"]
        bus.wait(job_id, timeout=3)
        assert bus.get_status(job_id)["platforms"]["ozon"]["status"] == "partial"
        bus.retry(job_id, "ozon")
        bus.wait(job_id, timeout=3)
        item = bus.get_status(job_id)["platforms"]["ozon"]
        assert item["status"] == "success" and item["attempts"] == 2
        assert [call["offer_id"] for call in leaf.calls] == ["SELL-0", "SELL-1", "SELL-2", "SELL-1"]
        logs = {row["job_id"]: row for row in app.db.list_publish_logs()}
        assert logs[job_id]["status"] == "partial"
        assert logs[f"{job_id}:retry:2"]["status"] == "published"

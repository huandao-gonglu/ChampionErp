"""在线 SKU 自动详情与主动刷新共用真实服务，验证内容新鲜度和请求次数。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.schemas.ai_tools import AiToolCommand
from erp_web.services.ai_tool_runtime import AiToolRuntime
from erp_web.services.online_product_service import OnlineProductService
from tests.approval_support import _execution
from tests.test_online_products import RemoteFixture, listing, price_request


@pytest.fixture
def details(monkeypatch):
    app = get_context()
    config = {"mercadolibre": {"user_id": "seller"}}
    monkeypatch.setattr(app.config, "load_store_config", lambda: config)
    remote = RemoteFixture()
    service = OnlineProductService(app, adapter_factories={"mercadolibre": lambda _: remote}, start_worker=False)
    app._online_products = service
    service.store.save(remote.rows["CBT1"])
    reads = []
    original = remote.read

    def read(remote_id):
        reads.append(remote_id)
        return original(remote_id)

    monkeypatch.setattr(remote, "read", read)
    execution = replace(_execution(), permissions=frozenset({"online_product.read", "online_product.sync"}), allow_write=True)
    runtime = AiToolRuntime(toolset=build_global_chat_toolset(app), execution_context=execution)
    yield service, remote, reads, config, runtime, execution
    service.close()


def call(runtime, tool="online_products_read", **arguments):
    result = runtime.execute(AiToolCommand(call_id=uuid4().hex, tool_name=tool, tool_version="1", arguments=arguments, round=1))
    assert result.ok, result.to_dict()
    return result.to_dict()["output"]


@pytest.mark.parametrize("refresh_first", [True, False])
def test_auto_detail_and_refresh_share_success_in_either_order(details, refresh_first):
    service, remote, reads, _, runtime, _ = details
    remote.rows["CBT1"].content.update(description="平台最新描述", attributes=[{"id": "color", "value_name": "红色"}])
    remote.rows["CBT1"].title = "平台最新标题"
    before = service.store.get("CBT1")
    if refresh_first:
        result = call(runtime, "online_products_refresh_detail", listing_id="CBT1")
    else:
        result = call(runtime, id="CBT1")
    assert result["item"]["title"] == "平台最新标题"
    assert result["item"]["content"]["description"] == "平台最新描述"
    assert result["item"]["version"] != before.version
    fields = call(runtime, id="CBT1", fields=["content.description", "content.attributes"])
    assert fields["records"][0]["values"] == {
        "content.description": "平台最新描述", "content.attributes": [{"id": "color", "value_name": "红色"}]}
    assert fields["item"] is None
    refreshed = call(runtime, "online_products_refresh_detail", listing_id="CBT1")
    assert refreshed["item"] == result["item"]
    assert reads == ["CBT1"] and not remote.writes


def test_new_ai_execution_fetches_changed_remote_content(details):
    _, remote, reads, _, runtime, execution = details
    first = call(runtime, id="CBT1")
    remote.rows["CBT1"].content["description"] = "下一轮平台新内容"
    assert call(runtime, id="CBT1")["item"] == first["item"]
    new_runtime = AiToolRuntime(toolset=runtime.toolset, execution_context=replace(execution, task_run_id="next-run"))
    result = call(new_runtime, id="CBT1", fields=["content.description"])
    assert result["records"][0]["values"]["content.description"] == "下一轮平台新内容"
    assert reads == ["CBT1", "CBT1"]


def test_snapshot_change_invalidates_same_round_read(details):
    service, remote, reads, _, runtime, _ = details
    call(runtime, id="CBT1")
    current = service.store.get("CBT1")
    current.content["description"] = "并发保存的本地内容"
    service.store.save(current)
    remote.rows["CBT1"].content["description"] = "平台当前内容"
    result = call(runtime, id="CBT1")
    assert result["item"]["content"]["description"] == "平台当前内容"
    assert reads == ["CBT1", "CBT1"]


def test_submission_invalidates_read_and_keeps_independent_prewrite_check(details):
    service, remote, reads, _, runtime, _ = details
    call(runtime, id="CBT1")
    job = service.change(price_request(service))["job"]
    assert service.run_once()
    assert service.store.job(job["id"])["status"] == "confirmed"
    result = call(runtime, id="CBT1", fields=["prices"])
    assert result["records"][0]["values"]["prices"][0]["amount"] == "15.25"
    # 首次取数、后台写前核验、修改后重新读取各一次。
    assert reads == ["CBT1", "CBT1", "CBT1"] and len(remote.writes) == 1


def test_parallel_refresh_and_auto_read_join_one_remote_query(details, monkeypatch):
    service, remote, reads, _, runtime, _ = details
    second_entered, lock = Event(), Lock()
    count = 0
    original_check = service.store.assert_status_refreshable
    original_read = remote.read

    def check(row):
        nonlocal count
        original_check(row)
        with lock:
            count += 1
            if count == 2:
                second_entered.set()

    def read(remote_id):
        assert second_entered.wait(5), "第二个读取应在第一次网络查询结束前到达"
        return original_read(remote_id)

    monkeypatch.setattr(service.store, "assert_status_refreshable", check)
    monkeypatch.setattr(remote, "read", read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        automatic = pool.submit(call, runtime, id="CBT1")
        explicit = pool.submit(call, runtime, "online_products_refresh_detail", listing_id="CBT1")
        assert automatic.result(timeout=10)["item"] == explicit.result(timeout=10)["item"]
    assert reads == ["CBT1"]


def test_failed_read_keeps_snapshot_and_can_be_explicitly_attempted_again(details):
    service, remote, reads, _, runtime, _ = details
    before = service.store.get("CBT1").model_dump()
    remote.read_error = TimeoutError("本次详情查询超时")
    result = runtime.execute(AiToolCommand(call_id="failed-read", tool_name="online_products_read", tool_version="1",
                                         arguments={"id": "CBT1"}, round=1))
    assert not result.ok and result.error["code"] == "ONLINE_PLATFORM_ERROR"
    assert service.store.get("CBT1").model_dump() == before
    assert reads == ["CBT1"]
    remote.read_error = None
    call(runtime, "online_products_refresh_detail", listing_id="CBT1")
    call(runtime, id="CBT1")
    assert reads == ["CBT1", "CBT1"]


def test_cached_read_still_checks_current_account(details):
    _, _, reads, config, runtime, _ = details
    call(runtime, id="CBT1")
    config["mercadolibre"]["user_id"] = "another-store"
    result = runtime.execute(AiToolCommand(call_id="changed-account", tool_name="online_products_read", tool_version="1",
                                         arguments={"id": "CBT1"}, round=1))
    assert not result.ok and result.error["code"] == "ONLINE_CONFLICT"
    assert reads == ["CBT1"]


def test_list_read_stays_local_and_sku_queries_keep_separate_results(details):
    service, remote, reads, _, runtime, _ = details
    remote.rows["CBT2"] = listing("CBT2")
    service.store.save(remote.rows["CBT2"])
    call(runtime, platform="mercadolibre", view="listings", fields=["content.title"])
    assert reads == []
    for remote_id in ("CBT1", "CBT2"):
        remote.rows[remote_id].content["description"] = "当前内容-" + remote_id
        result = call(runtime, id=remote_id, fields=["content.description"])
        assert result["records"][0]["values"]["content.description"] == "当前内容-" + remote_id
    call(runtime, "online_products_refresh_detail", listing_id="CBT1")
    assert reads == ["CBT1", "CBT2"]

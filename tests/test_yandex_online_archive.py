"""归档独立于停售：真实接口形状、目录隔离、逐项失败及跨店铺范围。"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.runtime_units.online_product_capabilities import OnlineProductCapabilityScope, _change_snapshot, online_products_read
from erp_web.schemas.online_product_capabilities import OnlineReadRequest
from erp_web.schemas.online_products import OnlineChange, OnlineListing, listing_identity
from erp_web.services.external_request_context import request_context
from erp_web.services.online_product_service import OnlineProductService
from erp_web.stores.online_product_store import OnlineConflict
from tests.test_yandex_online_sync import Platform, adapter


class ArchivePlatform(Platform):
    def __init__(self):
        super().__init__(3)
        self.hidden = set()
        self.writes = []
        self.receipt = None
        self.timeout = False

    def request(self, path, body=None, *, query=None, method="POST"):
        if path.endswith(("/archive", "/unarchive")):
            self.writes.append((path, deepcopy(body)))
            if self.receipt is not None:
                return deepcopy(self.receipt)
            for row in self.rows:
                if row["offer"]["offerId"] in body["offerIds"]:
                    row["offer"]["archived"] = path.endswith("/archive")
            if self.timeout:
                raise TimeoutError("写入后连接中断")
            return {"status": "OK", "result": {}}
        if path.endswith(("/hidden-offers", "/hidden-offers/delete")):
            if method == "GET":
                target = (query or {}).get("offer_id")
                return {"result": {"hiddenOffers": [{"offerId": sku} for sku in self.hidden if not target or sku == target]}}
            self.writes.append((path, deepcopy(body)))
            for row in body["hiddenOffers"]:
                if path.endswith("/delete"):
                    self.hidden.discard(row["offerId"])
                else:
                    self.hidden.add(row["offerId"])
            return {"status": "OK"}
        return super().request(path, body, query=query, method=method)


@pytest.fixture
def archive_service():
    remote = ArchivePlatform()
    client = adapter(remote)
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: {"yandex": {"business_id": "1", "campaign_id": "2"}}))
    service = OnlineProductService(context, adapter_factories={"yandex": lambda _: client}, start_worker=False)
    service.store.save(client.read("sku-0"))
    yield service, remote, client
    service.close()


def command(service, operation="archive", changes=None, *, listing_id=None):
    listing_id = listing_id or listing_identity("yandex", "1:2", "sku-0")
    row = service.store.get(listing_id)
    return {"listing_id": row.id, "version": row.version, "operation": operation,
            "scope_id": "business" if operation == "archive" else "global", "changes": changes or {"archived": True},
            "idempotency_key": uuid4().hex}


def test_pause_archive_and_restore_are_distinct_and_preserve_stock(archive_service):
    service, remote, client = archive_service
    pause = service.change(command(service, "sale_state", {"state": "paused"}))["job"]
    service.run_once()
    assert service.store.job(pause["id"])["status"] == "confirmed"
    service.refresh_status(pause["target_id"])
    before = service.store.get(pause["target_id"])
    body = command(service)
    job = service.change(body)["job"]
    service.run_once()
    after = service.store.get(job["target_id"])
    assert service.store.job(job["id"])["status"] == "confirmed"
    assert after.archived and after.stocks == before.stocks and after.content == before.content
    assert after.desired_sale_state == before.desired_sale_state == "paused"
    assert remote.hidden == {"sku-0"}
    assert remote.writes == [("/v2/campaigns/2/hidden-offers", {"hiddenOffers": [{"offerId": "sku-0"}]}),
                             ("/v2/businesses/1/offer-mappings/archive", {"offerIds": ["sku-0"]})]
    assert not service.list("yandex")["items"]
    assert service.list("yandex", catalog="archived")["items"][0]["archived"]
    assert service.change(body)["job"]["id"] == job["id"]
    restore = service.change(command(service, changes={"archived": False}))["job"]
    service.run_once()
    restored = service.store.get(job["target_id"])
    assert service.store.job(restore["id"])["status"] == "confirmed"
    assert not restored.archived and restored.stocks == before.stocks
    assert restored.desired_sale_state == restored.sale_state == "paused"
    assert remote.hidden == {"sku-0"}
    assert remote.writes[-1] == ("/v2/businesses/1/offer-mappings/unarchive", {"offerIds": ["sku-0"]})
    assert len(service.list("yandex")["items"]) == 1 and not service.list("yandex", catalog="archived")["items"]
    assert not service.run_once()


@pytest.mark.parametrize("archived,receipt", [
    (False, {"status": "OK", "result": {"notArchivedOffers": [{"offerId": "sku-0", "error": "OFFER_HAS_STOCKS"}]}}),
    (True, {"status": "OK", "result": {"notUnarchivedOfferIds": ["sku-0"]}}),
])
def test_http_200_rejection_does_not_move_listing(archive_service, archived, receipt):
    service, remote, client = archive_service
    remote.rows[0]["offer"]["archived"] = archived
    before = service.store.save(client.read("sku-0"))
    remote.receipt = receipt
    job = service.change(command(service, changes={"archived": not archived}))["job"]
    service.run_once()
    result = service.store.job(job["id"])
    assert result["status"] == "failed" and result["result"]["platform_errors"]
    assert service.store.get(before.id).model_dump() == before.model_dump()
    assert len(remote.writes) == 1


def test_timeout_stays_unknown_and_single_target_query_resolves_without_replay(archive_service):
    service, remote, client = archive_service
    remote.timeout = True
    job = service.change(command(service))["job"]
    service.run_once()
    assert service.store.job(job["id"])["status"] == "outcome_unknown"
    assert not service.store.get(job["target_id"]).archived
    with pytest.raises(OnlineConflict):
        service.change(command(service))
    remote.calls.clear()
    assert service.reconcile(job["id"])["job"]["status"] == "confirmed"
    assert service.store.get(job["target_id"]).archived
    assert len(remote.calls) == 1 and remote.calls[0][:2] == ("offer-mappings", {"offerIds": ["sku-0"]})
    assert len(remote.writes) == 1


@pytest.mark.parametrize("result", [
    {"notArchivedOffers": [{"offerId": "other", "error": "UNKNOWN"}]},
    {"notArchivedOffers": ["sku-0"]}, {"notArchivedOffers": {}}, [], None,
])
def test_invalid_receipt_is_unknown_not_a_success(archive_service, result):
    service, remote, _ = archive_service
    remote.receipt = {"status": "OK", "result": result}
    job = service.change(command(service))["job"]
    service.run_once()
    assert service.store.job(job["id"])["status"] == "outcome_unknown"
    assert not service.store.get(job["target_id"]).archived


def test_archive_query_and_ai_approval_expose_catalog_and_account_scope(archive_service):
    service, remote, client = archive_service
    service.store.save(client.read("sku-2"))
    scope = OnlineProductCapabilityScope(lambda: service)
    normal = online_products_read(OnlineReadRequest(platform="yandex", view="listings"), scope)
    archived = online_products_read(OnlineReadRequest(platform="yandex", view="listings", catalog="archived"), scope)
    assert [row.remote_id for row in normal.items] == ["sku-0"]
    assert [row.remote_id for row in archived.items] == ["sku-2"] and archived.items[0].archived
    assert service.read_page("yandex", catalog="all", view="listings")["listing_total"] == 2
    body = command(service)
    body.pop("idempotency_key")
    approval = _change_snapshot(OnlineChange(**body), scope)
    assert "全部店铺" in approval.summary and approval.canonical_payload["request"]["changes"] == {"archived": True}
    assert service.detail(archived.items[0].id)["item"]["capabilities"]["archive"]["enabled"]
    with pytest.raises(ValueError, match="目录范围"):
        service.list("yandex", catalog="invalid")


@pytest.mark.parametrize("changes,scope", [({"archived": 1}, "business"), ({"archived": True}, "global"), ({"state": "paused"}, "business")])
def test_archive_requires_boolean_and_explicit_business_scope(archive_service, changes, scope):
    service, remote, _ = archive_service
    with pytest.raises(ValueError, match="归档仅支持"):
        service.change({**command(service), "changes": changes, "scope_id": scope})
    assert not remote.writes


def test_archived_item_must_be_restored_before_resuming_sale(archive_service):
    service, remote, client = archive_service
    item = service.store.save(client.read("sku-2"))
    with pytest.raises(ValueError, match="先恢复归档"):
        service.change(command(service, "sale_state", {"state": "active"}, listing_id=item.id))
    assert not remote.writes


def other_shop(service, client, account, remote_id="sku-0"):
    row = client.read(remote_id)
    row.account_id = account
    row.id = listing_identity("yandex", account, remote_id)
    return service.store.save(row)


def test_archive_protects_same_sku_across_shops_and_does_not_lock_other_goods(archive_service):
    service, _, client = archive_service
    second = other_shop(service, client, "1:3")
    foreign = other_shop(service, client, "9:3")
    different = other_shop(service, client, "1:3", "sku-1")
    job = service.change(command(service))["job"]
    claim = service.store.claim("worker")
    service.store.update_job(job["id"], "outcome_unknown", {}, owner=claim["lease_token"], dispatched=True)
    for operation in ("stock", "sale_state", "archive"):
        with pytest.raises(OnlineConflict) as error:
            service.store.enqueue("yandex", second.account_id, operation, second.id, {"operation": operation}, uuid4().hex)
        assert error.value.job_id == job["id"]
    for listing in (foreign, different):
        assert service.store.enqueue("yandex", listing.account_id, "sale_state", listing.id, {"operation": "sale_state"}, uuid4().hex)["status"] == "queued"
    assert service.store.enqueue("yandex", second.account_id, "content", second.id, {"operation": "content"}, uuid4().hex)["status"] == "queued"
    with pytest.raises(OnlineConflict):
        service.reconcile(job["id"])


def test_success_updates_other_shop_cache_without_changing_inventory(archive_service):
    service, _, client = archive_service
    second = other_shop(service, client, "1:3")
    foreign = other_shop(service, client, "9:3")
    job = service.change(command(service))["job"]
    service.run_once()
    updated = service.store.get(second.id)
    assert updated.archived and updated.stocks == second.stocks and updated.synced_at == second.synced_at
    assert not service.store.get(foreign.id).archived
    assert service.store.job(job["id"])["status"] == "confirmed"


@pytest.mark.parametrize("action", ["archive", "unarchive"])
def test_archive_transport_is_write_and_cannot_be_retried_as_a_read(action):
    context = request_context(f"https://api.partner.market.yandex.ru/v2/businesses/1/offer-mappings/{action}", method="POST", data=b'{}')
    assert context.semantics == "write"


def test_old_snapshot_archived_flag_and_capability_are_read_without_migration(archive_service):
    service, _, client = archive_service
    old = client.read("sku-2").model_dump()
    old.pop("archived")
    old["capabilities"].pop("archive")
    loaded = OnlineListing.model_validate(old)
    assert loaded.archived and loaded.capabilities["archive"].enabled

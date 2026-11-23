"""1688 / 万邦 API 节约优化测试：地址持久缓存、免密签约缓存、60 秒防重预览、未发货跳过物流与跨模块商详短缓存。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time

import pytest

from erp_web.context import AppContext, AppPaths, bind_context
from erp_web.db import ErpDatabase
from erp_web.facades import alibaba_direct_purchase_facade, order_procurement_facade
from erp_web.runtime_units import source_collect_1688_api as collector
from erp_web.services import image_service
from erp_web.services.alibaba_api_client import AlibabaApiRejected, LOGISTICS_INFO, LOGISTICS_TRACE, ORDER_DETAIL
from erp_web.services.alibaba_direct_purchase_service import PREVIEW_RECHECK_AFTER_SECONDS
from erp_web.services.alibaba_purchase_sync_service import sync_purchase
from erp_web.services.alibaba_read_cache import AlibabaReadCache
from erp_web.services.fulfillment_service import FulfillmentService
from erp_web.stores.fulfillment_store import FulfillmentStore
from tests.alibaba_test_provider import AlibabaFixtureProvider
from tests.test_alibaba_direct_purchase import _FakeOneBoundClient, _make_context, _setup_onebound_app_and_session
from tests.test_alibaba_purchase_query import CONFIG as OFFICIAL_CONFIG, NUMBER, domain
from tests.test_fulfillment import Bus
from tests.test_order_procurement import confirm, purchase_body


def test_read_cache_persists_addresses_across_reopen_and_respects_ttl_and_scope(tmp_path: Path):
    path = tmp_path / "alibaba-read-cache.sqlite3"
    cache = AlibabaReadCache(path)
    items = [({"id": "1688:1", "text": "张三"}, {"fullName": "张三"})]
    cache.set_addresses("scope-a", items, now=1000.0)

    # 重建实例后仍可持久化命中，且按 scope 隔离。
    reopened = AlibabaReadCache(path)
    assert reopened.get_addresses("scope-a", now=1000.0 + 3600.0) == items
    assert reopened.get_addresses("scope-b", now=1000.0 + 10.0) is None
    assert reopened.get_addresses("scope-a", now=1000.0 + 90000.0) is None

    # 免密签约仅缓存 ready=True，ready=False 不缓存，支持手动失效。
    reopened.set_payment_agreement("scope-a", "alipay", {"channel": "alipay", "ready": False}, now=1000.0)
    assert reopened.get_payment_agreement("scope-a", "alipay", now=1001.0) is None
    reopened.set_payment_agreement("scope-a", "alipay", {"channel": "alipay", "ready": True}, now=1000.0)
    assert reopened.get_payment_agreement("scope-a", "alipay", now=1000.0 + 1000.0)["ready"] is True
    assert reopened.get_payment_agreement("scope-a", "alipay", now=1000.0 + 25000.0) is None
    reopened.set_payment_agreement("scope-a", "shegou", {"channel": "shegou", "ready": True}, now=1000.0)
    reopened.invalidate_payment_agreement("scope-a", "shegou")
    assert reopened.get_payment_agreement("scope-a", "shegou", now=1001.0) is None


def test_direct_purchase_caches_addresses_agreements_and_skips_repreview_within_60s(tmp_path: Path):
    ctx = _make_context(tmp_path / "root")
    try:
        _setup_onebound_app_and_session(ctx, ctx)
        fake = _FakeOneBoundClient({})
        service = alibaba_direct_purchase_facade.create_service(ctx, client_factory=lambda _c: fake)

        # 1) 首次拉地址命中远端，二次拉地址和 preview 均复用缓存（0 次额外 list_addresses）。
        assert len(service.addresses()["items"]) == 1
        assert len(service.addresses()["items"]) == 1
        resolved = service.resolve({"url": "https://detail.1688.com/offer/7788990011.html"})
        preview = service.preview(
            {
                "resolve_token": resolved["resolve_token"],
                "items": [{"sku_id": "5001", "quantity": 1}],
                "address_id": "1688:addr-default",
            }
        )["record"]
        assert sum(1 for name, _ in fake.calls if name == "list_addresses") == 1

        # 显式 refresh=True 才会再次访问远端地址簿。
        assert len(service.addresses(refresh=True)["items"]) == 1
        assert sum(1 for name, _ in fake.calls if name == "list_addresses") == 2

        # 2) 60 秒内建单跳过第二次 preview_order，仅调用 1 次 preview_order + 1 次 create_order。
        created = service.create({"preview_id": preview["id"], "pay_channel": "alipay"})["record"]
        assert created["state"] == "created"
        assert sum(1 for name, _ in fake.calls if name == "preview_order") == 1
        assert sum(1 for name, _ in fake.calls if name == "create_order") == 1

        # 3) 免密签约状态 ready=True 缓存：连续查状态 + pay() 共只调 1 次 get_payment_agreement。
        assert service.payment_status({"pay_channel": "alipay"})["agreement"]["ready"] is True
        assert service.payment_status({"pay_channel": "alipay"})["agreement"]["ready"] is True
        paid = service.pay({"preview_id": created["id"], "confirmed_total_fen": created["preview"]["total_fen"]})["record"]
        assert paid["payment"]["state"] == "succeeded"
        assert sum(1 for name, _ in fake.calls if name == "get_payment_agreement") == 1

        # 显式 refresh=True 强制重查签约。
        assert service.payment_status({"pay_channel": "alipay", "refresh": True})["agreement"]["ready"] is True
        assert sum(1 for name, _ in fake.calls if name == "get_payment_agreement") == 2

        # 4) 支付被平台明确拒绝时，立即清除对应渠道签约缓存。
        preview2 = service.preview(
            {
                "resolve_token": resolved["resolve_token"],
                "items": [{"sku_id": "5001", "quantity": 1}],
                "address_id": "1688:addr-default",
            }
        )["record"]
        created2 = service.create({"preview_id": preview2["id"], "pay_channel": "alipay"})["record"]

        def reject_pay(number, *, channel, amount_fen, request_id):
            raise AlibabaApiRejected("免密协议已解约")

        fake.pay_order = reject_pay
        rejected = service.pay({"preview_id": created2["id"], "confirmed_total_fen": created2["preview"]["total_fen"]})["record"]
        assert rejected["payment"]["state"] == "rejected"
        # 下一次查签约必须重打远端而非命中旧缓存。
        service.payment_status({"pay_channel": "alipay"})
        assert sum(1 for name, _ in fake.calls if name == "get_payment_agreement") == 3
    finally:
        ctx.close()


def test_product_detail_cache_is_shared_across_direct_purchase_sku_resolve_and_collect(tmp_path: Path, monkeypatch):
    ctx = _make_context(tmp_path / "root")
    try:
        _setup_onebound_app_and_session(ctx, ctx, active_admin_provider="onebound")
        fake = _FakeOneBoundClient({})
        with bind_context(ctx):
            direct = alibaba_direct_purchase_facade.create_service(ctx, client_factory=lambda _c: fake)
            sku_svc = order_procurement_facade.create_sku_procurement_service(ctx, client_factory=lambda _c: fake)
            monkeypatch.setattr(collector, "request_1688_product_detail", lambda cfg, oid, url: fake.get_product(oid, url))
            monkeypatch.setattr(image_service, "download_remote_image", lambda _app, url, _product, index: {
                "id": f"img-{index}", "url": url, "status": "ready",
            })

            url = "https://detail.1688.com/offer/7788990011.html"
            # 1) 直购首次解析：触发 1 次远端 get_product
            r1 = direct.resolve({"url": url})
            assert r1["offer_id"] == "7788990011"
            assert sum(1 for name, _ in fake.calls if name == "get_product") == 1

            # 2) SKU 绑货源解析同一商品：命中缓存，0 次额外调用
            r2 = sku_svc.resolve_1688({"url": url})
            assert r2["offer_id"] == "7788990011"
            assert sum(1 for name, _ in fake.calls if name == "get_product") == 1

            # 3) API 采集同一商品：命中缓存，0 次额外调用
            r3 = collector.collect_1688_product_via_api(url)
            assert r3["ok"] is True
            assert sum(1 for name, _ in fake.calls if name == "get_product") == 1

            # 4) 直购显式 refresh=True：强制重新拉取远端
            direct.resolve({"url": url, "refresh": True})
            assert sum(1 for name, _ in fake.calls if name == "get_product") == 2
    finally:
        ctx.close()


@pytest.mark.parametrize("status,once,expect_logistics", [
    ("waitbuyerpay", False, False),
    ("cancel", False, False),
    ("terminated", False, False),
    ("waitsellersend", True, False),
    ("waitsellersend", False, True),
    ("waitbuyerreceive", True, True),
])
def test_purchase_sync_skips_logistics_calls_for_unpaid_or_cancelled_orders(domain, status, once, expect_logistics):
    procurement, order, key = domain
    confirm(procurement, order, key)
    result = procurement.record_purchase({**purchase_body(order, key, quantity=2), "purchase_order_number": NUMBER})
    body = {"order_id": order.identity, "record_id": result["purchases"][0]["id"]}
    store = FulfillmentStore(procurement.store.orders)
    fulfillment = FulfillmentService(store, Bus(store), procurement.accounts_provider, procurement.detail, start_worker=False)
    record = procurement.store.purchase_record(body["order_id"], body["record_id"], procurement.accounts_provider())

    class Client(AlibabaFixtureProvider):
        calls: list[str] = []

        def __init__(self, _config):
            pass

        def query(self, api_name, number):
            self.calls.append(api_name)
            if api_name == ORDER_DETAIL:
                return {
                    "result": {
                        "baseInfo": {"idOfStr": NUMBER, "status": status},
                        "productItems": [
                            {
                                "productID": "456",
                                "skuID": record.items[0].source.source_sku_id,
                                "subItemIDString": "entry",
                                "quantity": record.items[0].quantity,
                            }
                        ],
                    }
                }
            if api_name == LOGISTICS_INFO:
                return {"result": []}
            return {"logisticsTrace": []}

    Client.calls = []
    detail = sync_purchase(procurement, fulfillment, lambda: OFFICIAL_CONFIG, body, once=once, client_factory=Client)
    progress = detail["purchases"][0]["progress"]
    assert progress["state"] == "synced"
    assert ORDER_DETAIL in Client.calls
    assert (LOGISTICS_INFO in Client.calls) is expect_logistics
    assert LOGISTICS_TRACE not in Client.calls

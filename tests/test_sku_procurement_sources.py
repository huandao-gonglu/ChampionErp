"""独立 SKU 货源映射（默认 + 备选 + 1688 API 核验绑源）及跨订单/在线商品复用测试。"""

from __future__ import annotations

import pytest

from erp_web.context import get_context
from erp_web.facades.order_procurement_facade import create_sku_procurement_service
from erp_web.schemas.order_procurement import order_line_key
from erp_web.schemas.orders import OrderLine, OrderSnapshot, utc_iso
from erp_web.services.alibaba_product_payload import parse_1688_api_product
from erp_web.services.alibaba_self_purchase_service import AlibabaSelfPurchaseService
from erp_web.stores.alibaba_self_purchase_store import AlibabaSelfPurchaseStore
from erp_web.stores.order_address_note_store import OrderAddressNoteStore
from tests.test_alibaba_self_purchase import CONFIG, Client as FakeAlibabaClient
from tests.receiving_warehouse_support import assign_receiving, receiving_service


class FakeProductAlibabaClient(FakeAlibabaClient):
    def __init__(self, products_by_offer: dict[str, dict]):
        super().__init__()
        self.products_by_offer = products_by_offer

    def get_product(self, offer_id: str, source_url: str):
        raw = self.products_by_offer.get(offer_id)
        if not raw:
            raise ValueError(f"未找到商品 {offer_id}")
        return {
            "product": parse_1688_api_product(raw, source_url, offer_id),
            "http_status": 200,
            "request": {"method": "product.search.queryProductDetail", "offer_id": offer_id},
        }


def _configure_yandex_store(context):
    store_cfg = context.config.load_store_config()
    store_cfg["yandex"] = {
        **(store_cfg.get("yandex") or {}),
        "business_id": "9988",
        "campaign_id": "1001",
        "api_token": "tok",
        "shop_name": "Yandex 主店",
    }
    context.config.save_store_config(store_cfg)


def _save_order(context, *, order_id: str, account_id: str = "1001", sku: str = "OLD-SKU-1", platform: str = "yandex"):
    snapshot = OrderSnapshot(
        platform=platform,
        account_id=account_id,
        order_id=order_id,
        fulfillment="FBS",
        title="老店存量商品",
        status="PROCESSING",
        shipping_status="READY_TO_SHIP",
        state="pending_shipment",
        amount="1999",
        currency="RUB",
        updated_at=utc_iso(),
        items=[
            OrderLine(
                line_id=f"line-{order_id}",
                remote_id=sku,
                sku=sku,
                title="老店存量商品",
                quantity=2,
                amount="1999",
                currency="RUB",
            )
        ],
    )
    now_iso = utc_iso()
    with context.order_notifications.store.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?)",
            (snapshot.identity, platform, account_id, snapshot.state, snapshot.model_dump_json(), 1.0, now_iso),
        )
        conn.commit()
    return snapshot


def test_bind_missing_source_via_1688_api_enables_auto_purchase_and_reuses_across_orders(monkeypatch):
    context = get_context()
    _configure_yandex_store(context)
    monkeypatch.setattr(context.order_notifications, "accounts", lambda: {"yandex": ["1001", "1002"]})
    monkeypatch.setattr(context.order_procurement, "accounts_provider", lambda: {"yandex": ["1001", "1002"]})

    # 外部/老店存量商品：完全没有 products、platform_drafts 或 publish_jobs 记录！
    order_1 = _save_order(context, order_id="ORD-1", account_id="1001", sku="LEGACY-SKU-1")
    line_key = order_line_key(order_1.items[0])

    # 初始未匹配，不能自动采购
    detail_before = context.order_procurement.detail(order_1.identity)
    assert detail_before["lines"][0]["selection"]["status"] == "unmatched"

    fake_client = FakeProductAlibabaClient({
        "555666": {
            "result": {
                "productID": 555666,
                "subject": "替补货源商品 A",
                "sellerName": "优质厂家 A",
                "skuInfos": [
                    {"skuId": "7001", "specId": "spec-a-black", "skuName": "黑色大号", "price": "18.5", "stock": 500, "attributes": {"颜色": "黑色", "尺寸": "大号"}},
                    {"skuId": "7002", "specId": "", "skuName": "缺spec规格", "price": "18.5", "stock": 10},
                ],
            }
        },
        "777888": {
            "result": {
                "productID": 777888,
                "subject": "备用供应商商品 B",
                "sellerName": "备用厂家 B",
                "skuInfos": [
                    {"skuId": "8001", "specId": "spec-b-black", "skuName": "黑色标准", "price": "17.0", "stock": 300, "attributes": {"颜色": "黑色"}},
                ],
            }
        },
    })
    sku_service = create_sku_procurement_service(context, client_factory=lambda _: fake_client)

    # 1. 粘贴分享文案解析 1688 商品规格
    resolved_a = sku_service.resolve_1688({"url": "【超级爆款】https://detail.1688.com/offer/555666.html?spm=a261y 复制打开"})
    assert resolved_a["offer_id"] == "555666"
    assert resolved_a["supplier"] == "优质厂家 A"
    assert len(resolved_a["skus"]) == 2
    assert resolved_a["skus"][0]["can_auto_purchase"] is True
    assert resolved_a["skus"][1]["can_auto_purchase"] is False

    # 缺 specId 的规格拒绝绑定
    with pytest.raises(ValueError, match="specId"):
        sku_service.bind_source({
            "platform": "yandex",
            "account_id": "9988:1001",
            "seller_sku": "LEGACY-SKU-1",
            "resolve_id": resolved_a["resolve_id"],
            "source_sku_id": "7002",
        })

    # 2. 在订单详情页绑定规格 A 为默认采购源，同步确认当前订单行
    bound_a = sku_service.bind_source({
        "platform": "yandex",
        "account_id": "1001",
        "seller_sku": "LEGACY-SKU-1",
        "resolve_id": resolved_a["resolve_id"],
        "source_sku_id": "7001",
        "set_default": True,
        "order_id": order_1.identity,
        "line_key": line_key,
    })
    assert bound_a["binding"]["origin"] == "alibaba_api"
    assert bound_a["binding"]["is_default"] is True
    assert bound_a["order_detail"]["lines"][0]["selection"]["status"] == "confirmed"
    assert bound_a["order_detail"]["lines"][0]["selection"]["source"]["source_spec_id"] == "spec-a-black"

    # 3. 直接具备 1688 自动采购资格（不依赖草稿或 publish_jobs）
    receiving = receiving_service(context.order_procurement)
    assign_receiving(receiving, order_1)
    purchase_service = AlibabaSelfPurchaseService(
        AlibabaSelfPurchaseStore(context.paths.data_dir / "alibaba-self-purchases.sqlite3"),
        lambda: CONFIG,
        context.order_procurement,
        address_notes=OrderAddressNoteStore(context.order_notifications.store),
        receiving=receiving,
        client_factory=lambda _: fake_client,
    )
    opts = purchase_service.options(order_1.identity, line_key)
    assert opts["can_purchase"] is True
    assert opts["candidates"][0]["offer_id"] == "555666"
    assert opts["candidates"][0]["sku_id"] == "7001"

    # 4. 在「在线商品」页（account_id 为 business:campaign，且跨到另一仓 1002）查询与添加备选源 B
    queried = sku_service.query_sources({
        "items": [{"platform": "yandex", "account_id": "9988:1001", "seller_sku": "LEGACY-SKU-1"}]
    })
    assert len(queried["items"][0]["bindings"]) == 1
    assert queried["items"][0]["default_binding_id"] == bound_a["binding"]["id"]

    resolved_b = sku_service.resolve_1688({"url": "https://detail.1688.com/offer/777888.html"})
    bound_b = sku_service.bind_source({
        "platform": "yandex",
        "account_id": "9988:1001",
        "seller_sku": "LEGACY-SKU-1",
        "resolve_id": resolved_b["resolve_id"],
        "source_sku_id": "8001",
        "set_default": True,
    })
    assert bound_b["binding"]["is_default"] is True
    assert len(bound_b["bindings"]) == 2
    assert bound_b["bindings"][0]["id"] == bound_b["binding"]["id"]
    assert bound_b["bindings"][1]["is_default"] is False

    # 5. 新订单（即便来自同企业另一个仓库 1002）自动命中新的默认采购源 B，同时保留 A 作为备选
    order_2 = _save_order(context, order_id="ORD-2", account_id="1002", sku="LEGACY-SKU-1")
    detail_2 = context.order_procurement.detail(order_2.identity)
    sel_2 = detail_2["lines"][0]["selection"]
    assert sel_2["status"] == "matched"
    assert sel_2["source"]["source_offer_id"] == "777888"
    assert sel_2["source"]["source_spec_id"] == "spec-b-black"
    assert len(sel_2["candidates"]) == 2

    # 6. 一键切回备选源 A 为默认，同时更新订单 2
    switched = sku_service.set_default({
        "platform": "yandex",
        "account_id": "1002",
        "seller_sku": "LEGACY-SKU-1",
        "binding_id": bound_a["binding"]["id"],
        "order_id": order_2.identity,
        "line_key": order_line_key(order_2.items[0]),
    })
    assert switched["binding"]["id"] == bound_a["binding"]["id"]
    assert switched["order_detail"]["lines"][0]["selection"]["status"] == "confirmed"
    assert switched["order_detail"]["lines"][0]["selection"]["source"]["source_offer_id"] == "555666"

    # 7. 删除备选源 B 后仅剩 A
    removed = sku_service.remove_source({
        "platform": "yandex",
        "account_id": "9988:1001",
        "seller_sku": "LEGACY-SKU-1",
        "binding_id": bound_b["binding"]["id"],
    })
    assert removed["removed"] is True
    assert len(removed["bindings"]) == 1
    assert removed["bindings"][0]["id"] == bound_a["binding"]["id"]


def test_backfill_marker_avoids_rescanning_publish_jobs(monkeypatch):
    context = get_context()
    # 首次初始化会标记 publish_jobs_backfilled_v1
    procurement = context.order_procurement
    assert procurement.store.meta("publish_jobs_backfilled_v1") == "1"

    called = {"count": 0}
    original = context.db.list_publish_jobs

    def tracked(*args, **kwargs):
        called["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(context.db, "list_publish_jobs", tracked)
    from erp_web.facades.order_procurement_facade import create_service
    create_service(context)
    assert called["count"] == 0


def test_cross_platform_master_sku_shares_1688_source_and_survives_reauth(monkeypatch):
    """跨平台同 master_sku / 关联不同名老 SKU 共享 1688 货源，且 Ozon/Mercado 重授权补齐字段不失联。"""
    context = get_context()
    store_cfg = context.config.load_store_config()
    store_cfg["yandex"] = {
        **(store_cfg.get("yandex") or {}),
        "business_id": "9988",
        "campaign_id": "1001",
        "api_token": "tok",
    }
    store_cfg["ozon"] = {
        **(store_cfg.get("ozon") or {}),
        "client_id": "oz-client-1",
        "api_key": "oz-key",
        "shop_name": "Ozon 店铺",
    }
    store_cfg["mercadolibre"] = {
        **(store_cfg.get("mercadolibre") or {}),
        "seller_id": "ml-seller-9",
        "access_token": "ml-tok",
        "shop_name": "ML 店铺",
    }
    context.config.save_store_config(store_cfg)
    monkeypatch.setattr(context.order_notifications, "accounts", lambda: {"yandex": ["1001"], "ozon": ["oz-client-1"], "mercadolibre": ["ml-seller-9"]})
    monkeypatch.setattr(context.order_procurement, "accounts_provider", lambda: {"yandex": ["1001"], "ozon": ["oz-client-1"], "mercadolibre": ["ml-seller-9"]})

    # 1. 新商品认领到 Yandex 与 Ozon 时，自动共享同一个稳定 master_sku
    saved_product = context.products.save_product({
        "schema_version": 5,
        "name": "跨平台测试商品",
        "source": {
            "source_platform": "1688",
            "source_url": "https://detail.1688.com/offer/888999.html",
            "title": "跨平台测试商品",
            "skus": [{"id": "s1", "offer_id": "888999", "spec_id": "sp1", "name": "黑色"}],
        },
        "sku_items": [
            {"id": "phys-sku-1", "master_sku": "M-SHARED-001", "source_sku_id": "s1", "source_offer_id": "888999", "source_spec_id": "sp1", "name": "黑色", "active": True},
        ],
    })
    pid = saved_product["product_id"]
    draft_y_id = context.db.upsert_draft_model(pid, "yandex", {"platform": "yandex", "site": "RU", "sku_items": [{"sku_id": "phys-sku-1", "selected": True}]})
    draft_o_id = context.db.upsert_draft_model(pid, "ozon", {"platform": "ozon", "site": "RU", "sku_items": [{"sku_id": "phys-sku-1", "selected": True}]})
    draft_y = context.db.load_draft_model(draft_y_id)
    draft_o = context.db.load_draft_model(draft_o_id)
    assert draft_y["sku_items"][0]["sku"] == "M-SHARED-001"
    assert draft_o["sku_items"][0]["sku"] == "M-SHARED-001"

    # 2. 在采集箱移除已建草稿的商品时，不级联毁掉草稿与商品主档；删完所有草稿后自动清理孤儿主档
    removal = context.products.delete_products_from_index([pid])
    assert removal["ok"] is True
    assert removal["detachedIds"] == [pid]
    assert removal["preservedDraftCount"] == 2
    assert all(row["product_id"] != pid for row in context.products.load_products_index())
    assert context.db.load_draft_model(draft_y["draft_id"])["draft_id"] == draft_y["draft_id"]
    assert context.db.load_product_model(pid)["product_id"] == pid

    # 删掉全部草稿后，已移出采集箱的孤儿商品主档自动回收
    context.db.delete_draft_model(draft_y["draft_id"])
    assert context.db.load_product_model(pid)["product_id"] == pid
    context.db.delete_draft_model(draft_o["draft_id"])
    assert context.db.load_product_model(pid) == {}

    # 3. 绑定跨平台共享 1688 货源到 M-SHARED-001：Yandex 绑定一次，Ozon 同货号订单自动命中！
    fake_client = FakeProductAlibabaClient({
        "888999": {
            "result": {
                "productID": 888999,
                "subject": "跨平台共享货源",
                "sellerName": "源头工厂",
                "skuInfos": [{"skuId": "9001", "specId": "spec-shared", "skuName": "黑色", "price": "12.0", "stock": 999}],
            }
        }
    })
    sku_service = create_sku_procurement_service(context, client_factory=lambda _: fake_client)
    resolved = sku_service.resolve_1688({"url": "https://detail.1688.com/offer/888999.html"})
    sku_service.bind_source({
        "platform": "yandex",
        "account_id": "1001",
        "seller_sku": "M-SHARED-001",
        "resolve_id": resolved["resolve_id"],
        "source_sku_id": "9001",
        "set_default": True,
        "shared_across_platforms": True,
    })

    ozon_order = _save_order(context, order_id="OZ-1", account_id="oz-client-1", sku="M-SHARED-001", platform="ozon")
    ozon_detail = context.order_procurement.detail(ozon_order.identity)
    assert ozon_detail["lines"][0]["selection"]["status"] == "matched"
    assert ozon_detail["lines"][0]["selection"]["source"]["source_spec_id"] == "spec-shared"

    # 4. 老店不同名 SKU（Mercado 上的 OLD-ML-SKU）显式关联到 M-SHARED-001 后立刻共享同一货源
    linked = sku_service.link_master_sku({
        "platform": "mercadolibre",
        "account_id": "ml-seller-9",
        "seller_sku": "OLD-ML-SKU",
        "master_sku": "M-SHARED-001",
    })
    assert linked["master_sku"] == "M-SHARED-001"
    assert len(linked["bindings"]) == 1

    ml_order = _save_order(context, order_id="ML-1", account_id="ml-seller-9", sku="OLD-ML-SKU", platform="mercadolibre")
    ml_detail = context.order_procurement.detail(ml_order.identity)
    assert ml_detail["lines"][0]["selection"]["status"] == "matched"
    assert ml_detail["lines"][0]["selection"]["source"]["source_offer_id"] == "888999"

    # 5. 3-A 验证：Mercado 重授权补齐 user_id（导致旧 store_identity 哈希变化）时，按稳定 account_id 仍不失联！
    store_cfg["mercadolibre"]["user_id"] = "ml-seller-9"
    store_cfg["mercadolibre"]["shop_name"] = "ML 改名新店"
    context.config.save_store_config(store_cfg)
    ml_detail_after_reauth = context.order_procurement.detail(ml_order.identity)
    assert ml_detail_after_reauth["lines"][0]["selection"]["status"] == "matched"
    assert ml_detail_after_reauth["lines"][0]["selection"]["source"]["source_spec_id"] == "spec-shared"

"""买家链接只来自正确的平台刊登，不影响在线商品修改与同步事实。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from erp_web.context import get_context
from erp_web.marketplaces.online_buyer_links import mercado_buyer_links, yandex_buyer_links
from erp_web.runtime_units.online_mercadolibre import MercadoOnlineAdapter
from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
from erp_web.schemas.online_products import BuyerLink, OnlineListing, snapshot_version
from erp_web.services.online_product_service import OnlineProductService
from erp_web.stores.online_product_store import OnlineProductStore


YANDEX_URL = "https://market.yandex.ru/card/slug/123?businessId=456"
MERCADO_URL = "https://articulo.mercadolibre.com.mx/MLM-123-producto-_JM"


def test_yandex_uses_b2c_url_and_retains_seller_identity():
    links = yandex_buyer_links({"showcaseUrls": [
        {"showcaseType": "B2B", "showcaseUrl": "https://business.market.yandex.ru/123"},
        {"showcaseType": "B2C", "showcaseUrl": YANDEX_URL},
        {"showcaseType": "B2C", "showcaseUrl": YANDEX_URL},
    ]})
    assert [link.model_dump(mode="json") for link in links] == [
        {"label": "Yandex Market", "url": YANDEX_URL, "site_id": "B2C"},
    ]


@pytest.mark.parametrize("url", [None, "", "javascript:alert(1)", "data:text/html,test", "/card/123", "https://user:password@market.yandex.ru/card/123"])
def test_invalid_links_do_not_break_sync_or_become_navigation(url):
    assert yandex_buyer_links({"showcaseUrls": [{"showcaseType": "B2C", "showcaseUrl": url}]}) == []


def test_missing_links_do_not_guess_an_address_from_identifiers():
    assert yandex_buyer_links({"mapping": {"marketSku": 123, "marketModelId": 456}}) == []
    assert mercado_buyer_links({"MLM1": {"site_id": "MLM", "id": "MLM1"}}) == []
    assert mercado_buyer_links({"CBT1": {"site_id": "CBT", "id": "CBT1", "permalink": MERCADO_URL}}) == []


def test_mercado_keeps_each_market_and_distinguishes_same_site_listings():
    children = {
        "MLM1": {"site_id": "MLM", "id": "MLM1", "permalink": MERCADO_URL},
        "MLM2": {"site_id": "MLM", "id": "MLM2", "permalink": MERCADO_URL + "?variant=2"},
        "MLB3": {"site_id": "MLB", "id": "MLB3", "permalink": "https://produto.mercadolivre.com.br/MLB-3-produto-_JM"},
    }
    links = mercado_buyer_links(children)
    assert [(link.site_id, link.label) for link in links] == [
        ("MLM", "墨西哥 · MLM1"), ("MLM", "墨西哥 · MLM2"), ("MLB", "巴西 · MLB3"),
    ]


def test_yandex_sync_carries_the_platform_url():
    adapter = object.__new__(YandexOnlineAdapter)
    adapter.token, adapter.business, adapter.campaign = "token", "456", "789"
    adapter.account_id, adapter.settings, adapter.mode = "456:789", {}, "none"
    adapter.warehouses = []
    adapter.request = lambda path, *args, **kwargs: {"result": {
        "offerMappings": [{"offer": {"offerId": "seller-sku", "name": "测试商品"},
                           "showcaseUrls": [{"showcaseType": "B2C", "showcaseUrl": YANDEX_URL}]}],
        "hiddenOffers": [],
        "offerCards": [{"offerId": "seller-sku", "cardStatus": "HAS_CARD_CAN_UPDATE"}],
        "offers": [],
    }}
    result = adapter.read("seller-sku")
    assert str(result.buyer_links[0].url) == YANDEX_URL


def test_mercado_sync_uses_market_permalink_without_extra_request():
    adapter = object.__new__(MercadoOnlineAdapter)
    adapter.account_id = "seller"
    parent = {"id": "CBT1", "seller_id": "seller", "site_id": "CBT", "status": "active", "marketplace_items": [{"item_id": "MLM1", "site_id": "MLM"}]}
    child = {"id": "MLM1", "site_id": "MLM", "status": "active", "permalink": MERCADO_URL}
    calls = []
    def get(path):
        calls.append(path)
        return deepcopy(parent if path.endswith("CBT1") else child)
    adapter.get = get
    result = adapter.read("CBT1")
    assert str(result.buyer_links[0].url) == MERCADO_URL
    assert calls == ["/marketplace/items/CBT1", "/marketplace/items/MLM1"]


def test_backfilled_links_preserve_business_facts_and_are_exposed_by_api():
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: {"yandex": {"business_id": "456", "campaign_id": "789"}}))
    store = OnlineProductStore(context.db)
    item = store.save(OnlineListing(id="listing", platform="yandex", account_id="456:789", remote_id="sku", model="business_offer", content={"title": "原商品"}))
    links = [BuyerLink(url=YANDEX_URL, label="Yandex Market", site_id="B2C")]
    assert store.update_buyer_links(item.id, links, expected_version=item.version)
    fresh = store.get(item.id)
    assert fresh.buyer_links == links
    assert fresh.model_dump(exclude={"buyer_links"}) == item.model_dump(exclude={"buyer_links"})
    assert snapshot_version(fresh) == item.version
    assert not store.update_buyer_links(item.id, [], expected_version="已过期版本")
    service = OnlineProductService(context, adapter_factories={}, start_worker=False)
    try:
        assert service.list("yandex")["items"][0]["buyer_links"][0]["url"] == links[0].url
        assert service.detail(item.id)["item"]["buyer_links"] == fresh.model_dump()["buyer_links"]
        assert "snapshot" not in service.detail(item.id)["item"]
    finally:
        service.close()


def test_existing_persisted_snapshots_remain_readable():
    item = OnlineListing(id="listing", platform="yandex", account_id="account", remote_id="sku", model="business_offer")
    payload = item.model_dump(exclude={"buyer_links"})
    assert OnlineListing.model_validate(payload).buyer_links == []


def test_batch_backfill_is_scoped_idempotent_and_preserves_unreturned_items(monkeypatch):
    from scripts.backfill_online_buyer_links import backfill

    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: {
        "yandex": {"business_id": "456", "campaign_id": "789", "api_token": "test-token"},
        "mercadolibre": {"user_id": "seller"},
    }))
    store = OnlineProductStore(context.db)
    original = [store.save(OnlineListing(
        id=f"listing-{index}", platform="yandex", account_id="456:789", remote_id=f"sku-{index}",
        model="business_offer", content={"title": f"测试商品 {index}"},
    )) for index in range(51)]
    unrelated = store.save(OnlineListing(id="other-account", platform="yandex", account_id="other", remote_id="other-sku", model="business_offer"))
    mercado = store.save(OnlineListing(id="mercado", platform="mercadolibre", account_id="seller", remote_id="CBT1", model="global_item", snapshot={
        "children": {"MLM1": {"id": "MLM1", "site_id": "MLM", "permalink": MERCADO_URL}},
    }))
    previous_link = BuyerLink(url=YANDEX_URL, label="原有买家链接")
    missing = original[0]
    store.update_buyer_links(missing.id, [previous_link], expected_version=missing.version)
    calls = []

    def fetch(token, business, offer_ids):
        assert (token, business) == ("test-token", "456")
        assert unrelated.remote_id not in offer_ids
        calls.extend(offer_ids)
        return [{"offer": {"offerId": offer_id}, "showcaseUrls": [
            {"showcaseType": "B2C", "showcaseUrl": YANDEX_URL + f"&offerId={offer_id}"},
        ]} for offer_id in offer_ids if offer_id != missing.remote_id]

    monkeypatch.setattr("scripts.backfill_online_buyer_links.fetch_yandex_offer_mapping", fetch)
    assert backfill(context) == {"已更新": 51, "无需更新": 0, "并发跳过": 0, "未取得商品": 1}
    assert sorted(calls) == sorted(item.remote_id for item in original)
    for item in [*original, mercado, unrelated]:
        assert store.get(item.id).model_dump(exclude={"buyer_links"}) == item.model_dump(exclude={"buyer_links"})
    assert store.get(missing.id).buyer_links == [previous_link]
    assert store.get(unrelated.id).buyer_links == []
    assert store.get(mercado.id).buyer_links[0].url == MERCADO_URL
    assert backfill(context) == {"已更新": 0, "无需更新": 51, "并发跳过": 0, "未取得商品": 1}

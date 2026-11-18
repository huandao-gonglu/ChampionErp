from __future__ import annotations
from erp_web.services.alibaba_product_payload import parse_1688_api_product

from erp_web.runtime_units.source_collect_1688_api import (
    extract_1688_offer_id,
)


def test_extract_1688_offer_id_from_detail_url() -> None:
    assert extract_1688_offer_id("https://detail.1688.com/offer/735716533861.html?spm=test") == "735716533861"
    assert extract_1688_offer_id("735716533861") == "735716533861"


def test_web_api_collection_accepts_detail_url_without_desktop(monkeypatch):
    from erp_web.context import get_context
    from erp_web.facades.collect_facade import collect_1688_payload
    from erp_web.runtime_units import source_collect_1688_api as collector
    from erp_web.services import image_service

    url = "https://detail.1688.com/offer/735716533861.html?spm=test"
    def fetch_detail(config, offer_id, source_url):
        assert offer_id == "735716533861"
        assert config == {"access_token": "saved-token"}
        return {"product": parse_1688_api_product({"subject": "厨房置物架", "images": ["https://example.test/product.jpg"]}, source_url, offer_id)}

    monkeypatch.setattr(collector, "alibaba_runtime_config", lambda _: {"access_token": "saved-token"})
    monkeypatch.setattr(collector, "request_1688_product_detail", fetch_detail)
    monkeypatch.setattr(image_service, "download_remote_image", lambda _app, url, _product, index: {
        "id": f"download-{index}", "url": url, "status": "ready",
    })
    result, status = collect_1688_payload({"url": url, "mode": "api"})
    assert status == 200 and result["ok"]
    assert result["diagnostics"]["collect_mode"] == "api"
    saved = get_context().products.load_product()["source"]
    assert saved["source_url"] == url and saved["title"] == "厨房置物架"


def test_crossborder_product_request_and_sku_mapping(monkeypatch):
    import io
    import json
    from urllib.parse import parse_qs
    from erp_web.services import alibaba_api_client as api

    product = {"offerId": 123456, "subject": "商品", "productSkuInfos": [{
        "skuId": 7, "specId": "blue", "amountOnSale": 0, "price": "4.20",
        "skuAttributes": [{"attributeName": "颜色", "value": "蓝色", "skuImageUrl": "https://example.test/blue.jpg"}]}]}
    def send(request, **kwargs):
        assert request.full_url.endswith('/com.alibaba.fenxiao.crossborder/product.search.queryProductDetail/4848173')
        params = parse_qs(request.data.decode())
        assert json.loads(params['offerDetailParam'][0]) == {'offerId': 123456, 'country': 'en'}
        assert params['access_token'] == ['buyer-token'] and len(params['_aop_signature'][0]) == 40
        assert not kwargs['follow_redirects']
        return io.BytesIO(json.dumps({'result': {'success': True, 'result': product}}).encode())
    monkeypatch.setattr(api, 'managed_urlopen', send)
    result = api.alibaba_provider({'app_key': '4848173', 'app_secret': 'secret', 'access_token': 'buyer-token'}).get_product('123456', 'https://detail.1688.com/offer/123456.html')['product']['api_raw']
    source = parse_1688_api_product(result, 'https://detail.1688.com/offer/123456.html', '123456')
    assert source['skus'][0]['options'] == {'颜色': '蓝色'}
    assert source['skus'][0]['stock'] == '0' and source['skus'][0]['price'] == '4.20'


def test_self_application_product_request_uses_its_own_api_and_validates_identity(monkeypatch):
    import io
    import json
    import pytest
    from urllib.parse import parse_qs
    from erp_web.services import alibaba_api_client as api
    from erp_web.runtime_units.source_collect_1688_api import request_1688_product_detail

    product = {'productID': 123456, 'subject': '企业自用商品', 'skuInfos': [{'skuId': 7, 'specId': 'blue'}]}
    calls = []
    def send(request, **kwargs):
        calls.append(request)
        assert request.full_url.endswith('/com.alibaba.product/alibaba.product.get/1938727')
        params = parse_qs(request.data.decode())
        assert params['productID'] == ['123456'] and params['webSite'] == ['1688']
        assert params['access_token'] == ['self-token'] and 'offerDetailParam' not in params
        assert kwargs['request_context'].semantics == 'read' and not kwargs['follow_redirects']
        return io.BytesIO(json.dumps({'productInfo': product}).encode())

    monkeypatch.setattr(api, 'managed_urlopen', send)
    config = {'application_type': 'alibaba', 'app_key': '1938727', 'app_secret': 'secret', 'access_token': 'self-token'}
    result = request_1688_product_detail(config, '123456', 'https://detail.1688.com/offer/123456.html')
    assert result['request']['method'] == 'alibaba.product.get'
    assert result['product']['api_raw'] == product and len(calls) == 1
    assert result['product']['skus'][0]['id'] == '7'
    product['productID'] = 999999
    with pytest.raises(api.AlibabaApiError, match='商品详情与请求不符'):
        request_1688_product_detail(config, '123456', 'https://detail.1688.com/offer/123456.html')


def test_parse_1688_api_product_maps_common_fields() -> None:
    raw = {
        "result": {
            "subject": "不锈钢厨房置物架",
            "price": "12.50",
            "description": "厨房收纳用品",
            "brandName": "OEM",
            "skuInfos": [
                {"skuId": "1", "skuName": "黑色", "price": "12.50", "stock": 99, "attributes": {"颜色": "黑色"}}
            ],
            "images": ["//cbu01.alicdn.com/img/test.jpg"],
            "attributes": [
                {"name": "材质", "value": "不锈钢"},
                {"name": "规格", "value": "30 x 20 x 10 cm"},
            ],
        }
    }

    source = parse_1688_api_product(raw, "https://detail.1688.com/offer/735716533861.html", "735716533861")

    assert source["title"] == "不锈钢厨房置物架"
    assert source["price"] == "12.50"
    assert source["currency"] == "CNY"
    assert source["images"] == ["https://cbu01.alicdn.com/img/test.jpg"]
    assert source["attributes"]["材质"] == "不锈钢"
    assert source["dimensions"]["length_cm"] == "30"
    assert source["skus"][0]["price"] == "12.50"


def test_parse_1688_api_product_defaults_unitless_dimensions_to_millimetres() -> None:
    raw = {"result": {"subject": "风扇", "dimensions": "60 x 55 x 155"}}

    source = parse_1688_api_product(
        raw,
        "https://detail.1688.com/offer/735716533861.html",
        "735716533861",
    )

    assert source["dimensions"] == {
        "length_cm": "6",
        "width_cm": "5.5",
        "height_cm": "15.5",
    }

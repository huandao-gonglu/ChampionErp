from __future__ import annotations

from erp_web.runtime_units.source_collect_1688_api import (
    extract_1688_offer_id,
    parse_1688_api_product,
)


def test_extract_1688_offer_id_from_detail_url() -> None:
    assert extract_1688_offer_id("https://detail.1688.com/offer/735716533861.html?spm=test") == "735716533861"
    assert extract_1688_offer_id("735716533861") == "735716533861"


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
    result = api.AlibabaApiClient({'app_key': '4848173', 'app_secret': 'secret', 'access_token': 'buyer-token'}).product_detail('123456')
    source = parse_1688_api_product(result, 'https://detail.1688.com/offer/123456.html', '123456')
    assert source['skus'][0]['options'] == {'颜色': '蓝色'}
    assert source['skus'][0]['stock'] == '0' and source['skus'][0]['price'] == '4.20'


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

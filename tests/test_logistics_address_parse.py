"""物流设置解析地址复用采购解析器，不依赖订单、授权或持久化。"""
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from erp_web.facades import fulfillment_facade
from erp_web.http_route_units.fulfillment_routes import handle_post
from erp_web.services.alibaba_purchase_address import parse_address


def request(body, monkeypatch):
    def no_context():
        pytest.fail('本地文本解析不应初始化订单、授权或数据库')
    monkeypatch.setattr(fulfillment_facade, 'get_context', no_context)
    responses = []
    path = '/api/logistics/parse-address'
    handler = SimpleNamespace(path=path, read_body=lambda: body,
                              send_json=lambda value, status=200: responses.append((status, value)))
    assert handle_post(handler, urlparse(path))
    return responses[0]


@pytest.mark.parametrize('text', [
    '张三 13800138000 广东省深圳市南山区科技路1号',
    '收件人：CEL转ID203299\n电话：17758059061\n实际地址：浙江省金华市义乌市某路23号',
    '深圳市南山区科技路1号',
    '收货人：张三 手机：13800138000 收货人：李四 手机：13900139000 地址：四川省成都市武侯区1号',
])
def test_settings_endpoint_reuses_parser_without_order_context(text, monkeypatch):
    status, value = request({'text': text}, monkeypatch)
    assert status == 200
    assert value == parse_address(text)


@pytest.mark.parametrize('body', [{}, {'text': ''}, {'text': '  \n '}, {'text': '地' * 4001}, {'text': 123}, {'text': '地址', 'order_id': 'fake'}])
def test_settings_parse_rejects_invalid_text(body, monkeypatch):
    status, value = request(body, monkeypatch)
    assert status == 400 and not value['ok']

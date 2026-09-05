"""验证码由用户完成；等待不访问新页面，也不写入商品。"""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from erp_web.context import get_context
from erp_web.facades import collect_facade
from erp_web.runtime_units import source_collect_workflows as workflows
from erp_web.runtime_units import source_collect_verification as verification
from erp_web.runtime_units.source_sites import source_site
from erp_web.schemas.requests import RequestValidationError, validate_request_payload

URL = 'https://detail.1688.com/offer/123.html'
TARGET = {'id': 'original', 'type': 'page', 'url': URL, 'webSocketDebuggerUrl': 'ws://localhost/original'}


def verification_snapshot():
    return {'url': URL, 'html': '<html><body>安全验证：请拖动滑块</body></html>',
            'text': '安全验证：请拖动滑块', 'title': '安全验证', 'browser_tab_id': 'original',
            'html_snapshot_path': '/tmp/verification.html'}


@pytest.mark.parametrize('entry', ['url', 'browser', 'batch', 'http'])
def test_verification_never_parses_downloads_or_saves_product(monkeypatch, entry):
    context = get_context()
    before = context.products.save_product({'name': '原商品', 'source': {'title': '原商品'}})
    index_before = context.products.load_products_index()
    parse = Mock(side_effect=AssertionError('验证码页不能解析'))
    images = Mock(side_effect=AssertionError('验证码页不能下载商品图片'))
    monkeypatch.setattr(workflows, 'source_site', lambda _: replace(source_site('1688'), parser=parse))
    monkeypatch.setattr(workflows, 'normalize_collect_source_images', images)
    snapshot = verification_snapshot()
    if entry == 'http':
        snapshot.pop('browser_tab_id')
    fetch = Mock(return_value=snapshot)
    monkeypatch.setattr(workflows, 'fetch_page_snapshot_with_browser_session', fetch)
    monkeypatch.setattr(workflows, 'browser_debug_status', lambda _: {'connected': True})
    monkeypatch.setattr(workflows, 'http_json', lambda _: [TARGET])
    monkeypatch.setattr(workflows, 'snapshot_from_cdp_target', lambda *_: snapshot)
    if entry == 'browser':
        result = workflows.collect_from_browser_tab(tab_url=URL, product_url=URL)
    elif entry == 'batch':
        batch = workflows.collect_batch_products([URL, 'https://detail.1688.com/offer/456.html'])
        assert [row['status'] for row in batch['items']] == ['waiting_verification', 'pending']
        assert batch['failed_count'] == 0
        fetch.assert_called_once()
        result = batch['items'][0]
    else:
        result = workflows.collect_source_product(URL)
    assert result['status'] == ('failed' if entry == 'http' else 'waiting_verification')
    if entry != 'http':
        assert result['verification'] == {'browser_tab_id': 'original', 'source_url': URL, 'platform': '1688'}
    parse.assert_not_called()
    images.assert_not_called()
    assert context.products.load_product() == before
    assert context.products.load_products_index() == index_before


@pytest.mark.parametrize(('page', 'expected'), [
    ({'url': URL, 'title': '安全验证', 'text': '拖动滑块', 'html': '', 'ready': 'complete'}, 'waiting_verification'),
    ({'url': 'https://login.1688.com/member/signin.htm', 'title': '登录', 'ready': 'complete'}, 'waiting_verification'),
    ({'url': URL, 'ready': 'loading'}, 'loading'),
    ({'url': URL + '?verified=1', 'title': '产品', 'text': '登录 产品信息', 'html': '<script>captcha.verify()</script>', 'ready': 'complete'}, 'ready'),
    ({'url': 'https://detail.1688.com/offer/456.html', 'ready': 'complete'}, 'unavailable'),
])
def test_probe_only_reads_original_tab_without_refresh_or_capture(monkeypatch, page, expected):
    monkeypatch.setattr(verification, 'http_json', lambda _: [TARGET])
    cdp = Mock()
    cdp.call.return_value = {'result': {'value': page}}
    connect = Mock(return_value=cdp)
    monkeypatch.setattr(verification, 'CdpWebSocket', connect)
    result = verification.inspect_collection_verification('original', URL)
    assert result['status'] == expected
    connect.assert_called_once_with(TARGET['webSocketDebuggerUrl'])
    cdp.call.assert_called_once()
    assert cdp.call.call_args.args[0] == 'Runtime.evaluate'
    expression = cdp.call.call_args.args[1]['expression']
    assert 'scrollTo' not in expression and 'reload' not in expression
    cdp.close.assert_called_once()


def test_closed_original_tab_does_not_switch_to_other_product(monkeypatch):
    monkeypatch.setattr(verification, 'http_json', lambda _: [{**TARGET, 'id': 'other'}])
    connect = Mock()
    monkeypatch.setattr(verification, 'CdpWebSocket', connect)
    assert verification.inspect_collection_verification('original', URL)['status'] == 'unavailable'
    connect.assert_not_called()


def test_resume_uses_stable_tab_id_and_preserves_product_identity(monkeypatch):
    context = get_context()
    before = context.products.save_product({'name': '原商品', 'source': {'title': '原商品', 'source_url': URL}})
    target = {**TARGET, 'url': URL + '?verified=1'}
    monkeypatch.setattr(workflows, 'browser_debug_status', lambda _: {'connected': True})
    monkeypatch.setattr(workflows, 'http_json', lambda _: [{**TARGET, 'id': 'wrong', 'url': URL}, target])
    snapshot = Mock(return_value={'url': target['url'], 'html': '<html>新商品标题</html>', 'text': '新商品标题', 'html_snapshot_path': '/tmp/product.html'})
    monkeypatch.setattr(workflows, 'snapshot_from_cdp_target', snapshot)
    source = {'title': '新商品标题', 'source_url': target['url'], 'images': ['https://example.test/product.jpg']}
    monkeypatch.setattr(workflows, 'source_site', lambda _: replace(source_site('1688'), parser=lambda *_: {'source': source}))
    monkeypatch.setattr(workflows, 'normalize_collect_source_images', lambda source, *_: source)
    result = workflows.collect_from_browser_tab(product_url=URL, browser_tab_id='original')
    assert result['ok'] is True
    assert result['product']['product_id'] == before['product_id']
    assert result['product']['source']['source_url'] == URL
    snapshot.assert_called_once_with(target, '')


def test_resume_rechecks_identity_before_parsing(monkeypatch):
    monkeypatch.setattr(workflows, 'browser_debug_status', lambda _: {'connected': True})
    monkeypatch.setattr(workflows, 'http_json', lambda _: [TARGET])
    monkeypatch.setattr(workflows, 'snapshot_from_cdp_target', lambda *_: {'url': 'https://detail.1688.com/offer/456.html', 'html': '<html>其他商品</html>', 'html_snapshot_path': '/tmp/other.html'})
    parser = Mock(side_effect=AssertionError('不能解析其他商品'))
    monkeypatch.setattr(workflows, 'source_site', lambda _: replace(source_site('1688'), parser=parser))
    result = workflows.collect_from_browser_tab(product_url=URL, browser_tab_id='original')
    assert result['ok'] is False
    assert '原标签页已离开' in result['error']
    parser.assert_not_called()


def test_verification_request_validation_and_1688_waiting_http_status(monkeypatch):
    with pytest.raises(RequestValidationError):
        validate_request_payload({'source_url': URL}, endpoint='/api/collect-verification')
    with pytest.raises(RequestValidationError):
        validate_request_payload({'source_url': URL, 'browser_tab_id': {}}, endpoint='/api/collect-verification')
    monkeypatch.setattr(collect_facade, 'collect_1688_payload_service', lambda _: {'ok': False, 'status': 'waiting_verification'})
    assert collect_facade.collect_1688_payload({'url': URL})[1] == 200


@pytest.mark.parametrize('platform', ['1688', 'amazon'])
def test_navigation_sign_in_and_hidden_captcha_script_are_not_a_verification_page(platform):
    spec = source_site(platform)
    url = URL if platform == '1688' else 'https://www.amazon.com/dp/B123456789'
    _, reason = spec.diagnose(url, '<script>captcha.verify()</script><nav>登录 Sign in</nav><h1>商品</h1>', '登录 Sign in 商品', '商品')
    assert reason == ''

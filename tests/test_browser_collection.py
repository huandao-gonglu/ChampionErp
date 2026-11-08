"""迁移后的采集入口：目标绑定、本机读取与后端入库分离。"""
from unittest.mock import Mock
import pytest
from desktop_runtime import collection
from desktop_runtime.config import LocalConfig
from erp_web.context import get_context
from erp_web.runtime_units import source_collect_workflows as workflows
from erp_web.services.desktop_connection_service import DesktopUnavailable

URL = 'https://detail.1688.com/offer/123.html'


def test_new_target_uses_put_and_preserves_verified_tab(monkeypatch, tmp_path):
    config = LocalConfig(tmp_path)
    monkeypatch.setattr(collection, 'ensure_browser_debug_session', lambda *_: None)
    target = {'type': 'page', 'url': URL + '?verified=1', 'webSocketDebuggerUrl': 'ws://local/original'}
    request = Mock(return_value=[target])
    monkeypatch.setattr(collection, 'browser_debug_json', request)
    assert collection.target_for_url(config, URL) is target
    request.assert_called_once_with(config.browser_port, '/json/list')
    request.side_effect = [[{**target, 'url': URL.replace('123', '456')}], {'id': 'new'}]
    assert collection.target_for_url(config, URL)['id'] == 'new'
    assert request.call_args.kwargs['method'] == 'PUT'


@pytest.mark.parametrize('selector', ['tab_url', 'browser_tab_id'])
def test_explicit_closed_target_never_uses_another_product(monkeypatch, tmp_path, selector):
    config = LocalConfig(tmp_path)
    monkeypatch.setattr(collection, 'browser_debug_json', lambda *_: [{'type': 'page', 'id': 'other', 'url': URL}])
    connect = Mock(side_effect=AssertionError('不能读取其他商品'))
    monkeypatch.setattr(collection, 'CdpWebSocket', connect)
    with pytest.raises(RuntimeError, match='NO_PRODUCT_TAB_FOUND'):
        collection.read_collection(config, **{selector: 'missing'})
    connect.assert_not_called()


def test_disconnected_read_never_changes_current_product(monkeypatch):
    context = get_context()
    before = context.products.save_product({'name': '原商品', 'source': {'title': '原商品'}})
    monkeypatch.setattr(workflows, 'call_desktop', Mock(side_effect=DesktopUnavailable()))
    result = workflows.collect_browser_url(product_url=URL)
    assert not result['ok']
    assert result['diagnostics']['error_code'] == 'DESKTOP_UNAVAILABLE'
    assert context.products.load_product() == before




@pytest.mark.parametrize('url', ['file:///etc/passwd', 'javascript:alert(1)', 'http://user:secret@example.test/product'])
def test_collection_does_not_turn_into_remote_local_file_or_script_access(monkeypatch, tmp_path, url):
    launch = Mock(side_effect=AssertionError('非法采集地址不能启动浏览器'))
    monkeypatch.setattr(collection, 'ensure_browser_debug_session', launch)
    with pytest.raises(ValueError, match='HTTP/HTTPS'):
        collection.target_for_url(LocalConfig(tmp_path), url)
    launch.assert_not_called()

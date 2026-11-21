"""URL 清单跨请求同步、断线执行及原标签验证。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
import json
import sqlite3
from threading import Event
from time import monotonic, sleep
from urllib.error import HTTPError
from uuid import uuid4

import pytest
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from erp_web.services.alibaba_product_payload import parse_1688_api_product

from erp_web.context import get_context
from erp_web.facades import collect_facade
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.schemas.requests import RequestValidationError
from erp_web.services import collection_link_resolution
from erp_web.stores import collection_links
from erp_web.stores.collection_links import CollectionLinks
from tests.ai_code_mode_helpers import business_returns, discover_tools
from tests.test_native_agent_integration import body, service


def test_management_http_persistence_and_conflict(backend_server):
    server = backend_server
    path = f'{server}/api/collection-links/manage'
    url = 'https://example.test/item?sku=4'
    add = {'action': 'add', 'items': [{'url': url}, {'url': url + '#a'}]}
    assert server.client.post(path, json=add).json() == {'added': 1, 'duplicates': 1}
    invalid = {'action': 'add', 'items': [{'url': url + 'a'}, {'url': url, 'mode': 'api'}]}
    assert server.client.post(path, json=invalid).status_code == 400
    invalid_batch = {'action': 'add', 'items': [{'url': f'{url}a\njavascript:invalid'}]}
    response = server.client.post(path, json=invalid_batch)
    assert response.status_code == 400 and '第 2 个链接无效' in response.json()['error']

    def rows():
        return server.client.get(f'{server}/api/business-queries/collection.links').json()['items']

    row, = rows()
    assert row['url'] == url and row['status'] == 'pending'
    assert row['createdAt'] == row['updatedAt']
    target = {'id': row['id'], 'revision': row['revision']}
    edit = {'action': 'edit', **target, 'url': 'https://detail.1688.com/offer/123.html', 'mode': 'api'}
    assert server.client.post(path, json=edit).status_code == 200
    assert server.client.post(path, json={'action': 'remove', **target}).status_code == 409
    updated, = rows()
    assert updated['url'] == edit['url'] and updated['mode'] == 'api'
    assert updated['createdAt'] == row['createdAt']
    events = server.client.get(f'{server}/api/business-queries/collection.history', params={'id': row['id']}).json()
    assert [e['message'] for e in events['items']] == ['已更新链接设置']
    assert server.client.post(path, json={'action': 'remove', **target, 'revision': updated['revision']}).ok
    assert not rows()


def test_migration_recovers_creation_time_only_from_original_add_events(tmp_path, monkeypatch):
    path = tmp_path / 'links.sqlite3'
    links = CollectionLinks(path)
    added_at = '2026-11-19T01:30:00+00:00'
    later_at = '2026-11-20T01:30:00+00:00'
    monkeypatch.setattr(collection_links, '_now', lambda: added_at)
    links.add([{'url': 'https://example.test/collected'}, {'url': 'https://example.test/edited'}])
    links.add([{'url': 'https://example.test/imported'}], legacy=True)
    collected, edited, _ = links.list()['items']

    monkeypatch.setattr(collection_links, '_now', lambda: later_at)
    running = links.begin(collected['id'], collected['revision'])
    receipt = links.finish(collected['id'], running, 'success', {'productId': 'p1'})
    assert receipt['createdAt'] == added_at and receipt['updatedAt'] == later_at
    links.manage({'action': 'edit', 'id': edited['id'], 'revision': edited['revision'],
                  'url': 'https://example.test/replaced'})

    with closing(sqlite3.connect(path)) as db, db:
        db.execute('ALTER TABLE links DROP COLUMN created')

    migrated = CollectionLinks(path).list()['items']
    assert [(row['createdAt'], row['updatedAt']) for row in migrated] == [
        (added_at, later_at), (None, later_at), (added_at, added_at),
    ]
    assert CollectionLinks(path).list()['items'] == migrated


@pytest.mark.parametrize('mode', ['browser', 'api'])
def test_1688_share_is_resolved_and_deduplicated_when_added(monkeypatch, mode):
    short = 'https://qr.1688.com/s/Example'
    url = 'https://detail.1688.com/offer/826923188477.html'
    direct = 'https://detail.1688.com/offer/123456.html?sku=blue'
    share = r'【运动水壶】复制￥test￥，打开【手机阿里】查看：https\://qr.1688.com/s/Example CZ1387'
    calls = []

    def read(request, *, follow_redirects, **kwargs):
        assert not follow_redirects
        calls.append(request.full_url)
        return BytesIO(b'wireless1688://ma.m.1688.com/offer?id=826923188477.html?share_token=test&amp;offerId=826923188477')

    def collect(*_):
        pytest.fail('添加链接不应开始采集商品')

    monkeypatch.setattr(collection_link_resolution, 'managed_urlopen', read)
    monkeypatch.setattr(collect_facade, 'collect_source_product', collect)
    result = collect_facade.manage_links_payload({'action': 'add', 'items': [
        {'mode': mode, 'url': f'{share}\n{short}\n{url}\n{direct}'},
    ]})
    assert result == {'added': 2, 'duplicates': 2}
    rows = get_context().collection_links.list()['items']
    assert [(row['url'], row['mode'], row['status']) for row in rows] == [(url, mode, 'pending'), (direct, mode, 'pending')]
    assert not get_context().products.load_products_index()
    assert calls == [short]


def test_main_agent_adds_share_to_pending_list_without_desktop(tmp_path, monkeypatch):
    url = 'https://detail.1688.com/offer/826923188477.html'
    share = r'【运动水壶】复制￥test￥，打开【手机阿里】查看：https\://qr.1688.com/s/Example CZ1387'
    tool = 'collection_links_add'
    app = get_context()
    toolset = build_global_chat_toolset(app)
    monkeypatch.setattr(collection_link_resolution, 'managed_urlopen', lambda *_a, **_kw: BytesIO(
        b'wireless1688://ma.m.1688.com/offer?offerId=826923188477',
    ))

    async def model(messages, info):
        if discovery := discover_tools(info, tool):
            yield {0: discovery}
            return
        if not business_returns(messages):
            yield {0: DeltaToolCall(name=tool, json_args=json.dumps({'text': share}), tool_call_id='add-link')}
            return
        receipt, = business_returns(messages)
        assert receipt.tool_name == tool
        assert receipt.content == {'added': 1, 'duplicates': 0, 'urls': [url]}
        yield '已加入待采集列表。'

    ui = service(tmp_path, FunctionModel(stream_function=model), toolset)
    events = []
    asyncio.run(ui.prepare_run(body('把这个分享链接加入待采集列表：' + share)).stream(events.append))
    assert '已加入待采集列表'.encode() in b''.join(events)
    row, = app.collection_links.list()['items']
    assert (row['url'], row['mode'], row['status']) == (url, 'browser', 'pending')
    assert not app.products.load_products_index()
    assert collect_facade.manage_links_payload({'action': 'add', 'items': [{'url': url}]}) == {'added': 0, 'duplicates': 1}


@pytest.mark.parametrize('location', [
    'https://detail.1688.com/offer/826923188477.html?from=share',
    'https://other.example/offer/826923188477.html',
])
def test_1688_short_link_redirect_does_not_fetch_target(monkeypatch, location):
    short = 'https://qr.1688.com/s/Example'
    direct = 'https://detail.1688.com/offer/123456.html'

    def read(request, *, follow_redirects, **kwargs):
        assert not follow_redirects and request.full_url == short
        raise HTTPError(short, 302, 'Found', {'Location': location}, BytesIO())

    monkeypatch.setattr(collection_link_resolution, 'managed_urlopen', read)
    payload = {'action': 'add', 'items': [{'mode': 'api', 'url': direct}, {'mode': 'api', 'url': short}]}
    links = get_context().collection_links
    if location.startswith('https://detail.1688.com/'):
        assert collect_facade.manage_links_payload(payload)['added'] == 2
        assert [row['url'] for row in links.list()['items']] == [direct, location.partition('?')[0]]
    else:
        with pytest.raises(RequestValidationError, match='不支持该 1688 分享地址'):
            collect_facade.manage_links_payload(payload)
        assert not links.list()['items']


def test_two_clients_cannot_claim_same_link_and_restart_never_replays(tmp_path):
    path = tmp_path / 'a' / 'links.sqlite3'
    a, b = CollectionLinks(path), CollectionLinks(path)
    a.add([{'url': 'https://example.test/offer'}])
    row, = b.list()['items']

    def claim(store):
        try:
            store.begin(row['id'], row['revision'])
            return True
        except RequestValidationError as exc:
            assert exc.status_code == 409
            return False

    with ThreadPoolExecutor() as executor:
        assert sorted(executor.map(claim, (a, b))) == [False, True]
    assert CollectionLinks(path).list()['items'][0]['status'] == 'running'
    assert not CollectionLinks(tmp_path / 'b' / 'links.sqlite3').list()['items']
    b.mark_interrupted()
    assert a.list()['items'][0]['status'] == 'unconfirmed'
    assert a.history(row['id'])['items'][0]['code'] == 'COLLECT_RESULT_UNCONFIRMED'
    with pytest.raises(RequestValidationError):
        a.finish(row['id'], row['revision'] + 1, 'success', {'productId': '过期回执'})


def _run(row):
    receipt = str(uuid4())
    collect_facade.run_link_payload({'id': row['id'], 'revision': row['revision'], 'receipt_id': receipt})
    return receipt


def _await_receipt(receipt):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = collect_facade.collection_receipt_payload({'receipt_id': receipt})
        if response['status'] == 'finished':
            return response['result']['item']
        sleep(.01)
    pytest.fail('未收到采集回执')


def test_server_saves_result_without_client_polling_and_resumes_original_tab(monkeypatch):
    links = get_context().collection_links
    url = 'https://example.test/offer'
    links.add([{'url': url}])
    initial, = links.list()['items']
    waiting = {'source_url': url, 'browser_tab_id': 'original', 'platform': 'generic'}
    started, release = Event(), Event()

    def collect(target, mode):
        assert (target, mode) == (url, 'browser')
        started.set()
        assert release.wait(5)
        return {'status': 'waiting_verification', 'verification': waiting, 'next_action': '请完成验证'}

    monkeypatch.setattr(collect_facade, 'collect_platform_reason', lambda *_: '')
    monkeypatch.setattr(collect_facade, 'collect_source_product', collect)
    receipt = _run(initial)
    assert started.wait(3)
    try:
        assert links.list()['items'][0]['status'] == 'running'
        with pytest.raises(RequestValidationError):
            _run(initial)
    finally:
        release.set()
    row = _await_receipt(receipt)
    assert row['status'] == 'waiting_verification'
    assert 'original' not in str(links.list())

    def resume(verification):
        assert verification == waiting
        return {'ok': True, 'product': {'product_id': 'p1', 'source': {'title': '商品', 'price': '4.70', 'skus': [{}]}}}

    monkeypatch.setattr(collect_facade, '_resume_collection', resume)
    _run(row)
    deadline = monotonic() + 5
    while monotonic() < deadline:
        saved, = CollectionLinks(links.path).list()['items']
        if saved['status'] == 'success':
            break
        sleep(.01)
    assert (saved['status'], saved['productId'], saved['skuCount']) == ('success', 'p1', 1)
    assert [e['status'] for e in links.history(initial['id'])['items']] == ['success', 'running', 'waiting_verification', 'running', 'pending']


def test_import_preserves_existing_rows_and_never_links_unrelated_failure():
    links = get_context().collection_links
    url = 'https://example.test/a'
    legacy = {'url': url, 'status': 'success', 'productId': 'p1', 'title': '已采集商品'}
    links.add([legacy], legacy=True)
    links.add([{**legacy, 'status': 'failed'}], legacy=True)
    saved, = links.list()['items']
    assert saved['status'] == 'success' and saved['productId'] == 'p1'
    status, summary = collect_facade._link_result({'ok': False, 'product': {'product_id': 'other'},
                                                  'diagnostics': {'error_code': 'DESKTOP_RESULT_UNKNOWN'}})
    assert status == 'unconfirmed' and 'productId' not in summary


def test_api_link_records_the_saved_product_and_only_its_own_images(monkeypatch):
    from erp_web.runtime_units import source_collect_1688_api as collector
    from erp_web.services import image_service

    url = 'https://detail.1688.com/offer/123456.html?sku=blue'
    cover = 'https://example.test/cover.jpg'
    monkeypatch.setattr(collector, 'alibaba_runtime_config', lambda _: {'access_token': 'test-token'})
    monkeypatch.setattr(collector, 'request_1688_product_detail', lambda _, offer_id, source_url: {'product': parse_1688_api_product({
        'subject': '蓝色扳手', 'price': '4.70', 'images': [cover],
        'productSkuInfos': [{'skuId': 7, 'specId': 'blue', 'price': '4.70',
                             'skuAttributes': [{'attributeName': '颜色', 'value': '蓝色'}]}],
    }, source_url, offer_id)})
    monkeypatch.setattr(image_service, 'download_remote_image', lambda _app, url, _product, i: {
        'id': f'image-{i}', 'url': url, 'status': 'ready',
    })
    links = get_context().collection_links
    links.add([{'url': url, 'mode': 'api'}])
    row = _await_receipt(_run(links.list()['items'][0]))
    assert (row['status'], row['title'], row['price'], row['skuCount'], row['imageCount']) == (
        'success', '蓝色扳手', '4.70', 1, 1,
    )
    product, error, _ = get_context().products.load_required_product_from_body({'product_id': row['productId']})
    assert not error and product['source']['source_url'] == url
    links.manage({'action': 'remove', 'id': row['id'], 'revision': row['revision']})
    assert get_context().products.find_indexed_product(row['productId'])['source']['title'] == '蓝色扳手'

"""URL 清单跨请求同步、断线执行及原标签验证。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.facades import collect_facade
from erp_web.schemas.requests import RequestValidationError
from erp_web.stores.collection_links import CollectionLinks


def test_management_http_persistence_and_conflict(backend_server):
    server = backend_server
    path = f'{server}/api/collection-links/manage'
    url = 'https://example.test/item?sku=4'
    add = {'action': 'add', 'items': [{'url': url}, {'url': url + '#a'}]}
    assert server.client.post(path, json=add).json() == {'added': 1, 'duplicates': 1}
    invalid = {'action': 'add', 'items': [{'url': url + 'a'}, {'url': url, 'mode': 'api'}]}
    assert server.client.post(path, json=invalid).status_code == 400

    def rows():
        return server.client.get(f'{server}/api/business-queries/collection.links').json()['items']

    row, = rows()
    assert row['url'] == url and row['status'] == 'pending'
    target = {'id': row['id'], 'revision': row['revision']}
    edit = {'action': 'edit', **target, 'url': 'https://detail.1688.com/offer/123.html', 'mode': 'api'}
    assert server.client.post(path, json=edit).status_code == 200
    assert server.client.post(path, json={'action': 'remove', **target}).status_code == 409
    updated, = rows()
    assert updated['url'] == edit['url'] and updated['mode'] == 'api'
    events = server.client.get(f'{server}/api/business-queries/collection.history', params={'id': row['id']}).json()
    assert [e['message'] for e in events['items']] == ['已更新链接设置']
    assert server.client.post(path, json={'action': 'remove', **target, 'revision': updated['revision']}).ok
    assert not rows()


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
    monkeypatch.setattr(collector, 'request_1688_product_detail', lambda *_: {'raw': {
        'subject': '蓝色扳手', 'price': '4.70', 'images': [cover],
        'productSkuInfos': [{'skuId': 7, 'specId': 'blue', 'price': '4.70',
                             'skuAttributes': [{'attributeName': '颜色', 'value': '蓝色'}]}],
    }})
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

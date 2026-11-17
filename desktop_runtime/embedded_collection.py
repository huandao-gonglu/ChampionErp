"""1688 内置商品页，人工验证跨调用保留同一标签。"""
import time
from urllib.parse import urlsplit

from erp_shared.collection import same_collection_page
from . import collection
from .embedded_browser import EmbeddedPage, is_navigation_error, request
from .operation import LocalOperationError, check_cancellation


def is_1688_url(url):
    host = urlsplit(url).hostname or ''
    return host == '1688.com' or host.endswith('.1688.com')


def status():
    tabs = request('list')
    for tab in tabs:
        tab['url'] = urlsplit(tab['url'])._replace(query='', fragment='').geturl()
    return {'ok': True, 'browser': 'ERP', 'connected': bool(tabs), 'tabs_count': len(tabs),
            'current_tabs': [{key: tab[key] for key in ('id', 'type', 'url', 'title')} for tab in tabs]}


def read(*, product_url='', tab_url='', browser_tab_id='', open_url=False, **_):
    url = product_url or tab_url
    collection._require_web_url(url)
    tabs = request('list')
    if browser_tab_id:
        page_id = next((tab['id'] for tab in tabs if tab['id'] == browser_tab_id
                        and same_collection_page(url, tab['source_url'])), None)
    else:
        page_id = next((tab['id'] for tab in tabs if same_collection_page(url, tab['source_url'])), None)
    if page_id:
        request('resume', page=page_id)
    elif open_url and not browser_tab_id:
        page_id = request('open', kind='1688', url=url)
    if not page_id:
        raise LocalOperationError('NO_PRODUCT_TAB_FOUND', '原商品页面已关闭，请重新开始采集。')
    page = EmbeddedPage(page_id)
    deadline = time.monotonic() + 30
    while True:
        check_cancellation()
        try:
            snapshot, assets, verification = collection._read_connected_target(
                page, {'id': page_id}, product_url, tab_url, '1688', open_url, fetch=page.fetch_asset,
            )
            break
        except RuntimeError as error:
            if not is_navigation_error(error) or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    if verification:
        page.show(snapshot['verification_reason'])
        # 验证页可能包含一次性登录参数，后端只需要原标签、页面位置和等待原因。
        location = urlsplit(snapshot['url'])._replace(query='', fragment='').geturl()
        snapshot = {'url': location, 'browser_tab_id': page_id, 'verification_reason': snapshot['verification_reason']}
    else:
        request('close', page=page_id)
    return {'snapshot': snapshot, 'assets': assets, 'browserStatus': status()}


def verify(browser_tab_id, source_url):
    tab = next((item for item in request('list') if item['id'] == browser_tab_id
                and same_collection_page(item['source_url'], source_url)), None)
    if not tab:
        return {'ok': True, 'status': 'unavailable', 'message': '原验证页面已关闭，请重新开始采集。'}
    page = EmbeddedPage(browser_tab_id)
    try:
        result = collection._inspect_page(page, source_url, report_reason=True)
    except RuntimeError as error:
        if is_navigation_error(error):
            return {'ok': True, 'status': 'loading', 'message': '正在等待原商品页跳转完成…'}
        raise
    reason = result.pop('reason', '')
    if ((reason == 'LOGIN' and tab.get('login_popup_closed')) or
            (result['status'] == 'unavailable' and tab.get('visited_login'))):
        if request('return_to_product', page=browser_tab_id, reason=reason):
            return {'ok': True, 'status': 'loading', 'message': '正在返回原商品页确认登录状态…'}
    if result['status'] == 'waiting_verification':
        page.show(reason)
    elif result['status'] == 'ready':
        page.hide()
    return result

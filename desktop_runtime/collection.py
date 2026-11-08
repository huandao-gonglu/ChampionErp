"""采集读取：只返回页面事实与素材，不读取或保存 ERP 商品。"""
from __future__ import annotations

import base64
import time
import urllib.parse
import urllib.request

from erp_shared.source_parser import extract_1688_context_data, extract_1688_detail_images, collect_product_image_urls, validated_1688_detail_url
from erp_shared.source_sites import detect_source_site, source_site
from .browser_debug_http import browser_debug_json
from .browser_debug_service import (
    CdpWebSocket, browser_debug_commands, browser_operation, ensure_browser_debug_session,
    finish_browser_session,
)
from .operation import check_cancellation, remaining_seconds, LocalOperationError
from erp_shared.collection import same_collection_page, VERIFICATION_REASONS, WAIT_MESSAGE


def browser_status(config):
    commands = browser_debug_commands(config.browser_port, config.browser_profile)
    try:
        tabs = browser_debug_json(config.browser_port, '/json')
        pages = [{k: tab.get(k, '') for k in ('id', 'type', 'url', 'title')}
                 for tab in tabs if isinstance(tab, dict) and tab.get('type') == 'page']
        for tab in pages:
            tab['platform_detected'] = detect_source_site(tab['url'])
        return {'ok': True, 'connected': True, 'port': config.browser_port, 'browser': 'chrome',
                'tabs_count': len(pages), 'current_tabs': pages, **commands}
    except (OSError, ValueError) as exc:
        return {'ok': True, 'connected': False, 'current_tabs': [], 'tabs_count': 0,
                'error_code': 'REMOTE_DEBUGGING_NOT_CONNECTED', 'error_message': str(exc),
                'next_action': '请打开采集浏览器并登录后重试。', **commands}


def _require_web_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('采集只支持 HTTP/HTTPS 商品页面。')


def target_for_url(config, url):
    _require_web_url(url)
    ensure_browser_debug_session(config.browser_port, config.browser_profile)
    for tab in browser_debug_json(config.browser_port, '/json/list'):
        if tab.get('type') == 'page' and same_collection_page(url, str(tab.get('url') or '')):
            return tab
    return browser_debug_json(config.browser_port, '/json/new?' + urllib.parse.quote(url, safe=''), method='PUT')


def _snapshot(cdp, target):
    cdp.call('Page.enable')
    cdp.call('Runtime.enable')
    # 保留原有滚动加载，不刷新人工完成验证后的页面。
    for _ in range(3):
        check_cancellation()
        cdp.call('Runtime.evaluate', {'expression': 'window.scrollTo(0, document.body ? document.body.scrollHeight : 0)'})
        time.sleep(0.6)
    expressions = {
        'html': "document.documentElement ? document.documentElement.outerHTML : ''",
        'text': "document.body ? document.body.innerText : ''",
        'title': "document.title || ''", 'url': "location.href || ''",
        'image_urls': "[...new Set([...document.images].map(img => img.currentSrc || img.src || '').filter(Boolean))].slice(0,80)",
    }
    result = {}
    for key, expression in expressions.items():
        check_cancellation()
        response = cdp.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, timeout=30)
        result[key] = response.get('result', {}).get('value', [] if key == 'image_urls' else '')
    if not result.get('html'):
        raise LocalOperationError('TAB_NOT_ACCESSIBLE', 'TAB_NOT_ACCESSIBLE：页面没有可读取的 HTML。')
    result['final_url'] = result['url']
    result['browser_tab_id'] = str(target.get('id') or '')
    return result


def _fetch(cdp, url, referer, limit):
    check_cancellation()
    if urllib.parse.urlsplit(url).scheme not in {'http', 'https'}:
        raise ValueError('采集资源必须使用 HTTP 或 HTTPS。')
    cookies = cdp.call('Network.getCookies', {'urls': [url]}).get('cookies') or []
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': referer}
    if cookies:
        headers['Cookie'] = '; '.join(f"{item['name']}={item['value']}" for item in cookies)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=remaining_seconds(30)) as response:
        raw = response.read(limit + 1)
        mime = response.headers.get_content_type()
    if len(raw) > limit:
        raise ValueError('采集资源超过大小上限。')
    return raw, mime


def read_collection(config, *, tab_url='', platform_hint='', product_url='', browser_tab_id='', save_only=False, open_url=False):
    """显式接收本机配置和目标；返回的页面、素材与错误均不含业务写入。"""
    if open_url:
        _require_web_url(product_url or tab_url)
    with browser_operation(config.browser_port):
        target = None
        keep_page = not open_url
        page_url = product_url or tab_url
        try:
            if open_url:
                target = target_for_url(config, product_url or tab_url)
            else:
                target = _select_target(config, browser_tab_id, product_url, tab_url, platform_hint)
            if not target:
                raise LocalOperationError('NO_PRODUCT_TAB_FOUND', 'NO_PRODUCT_TAB_FOUND：未找到指定的商品标签页。')
            snapshot, assets, verification = _read_target(
                config, target, product_url, tab_url, platform_hint, open_url,
            )
            # 人工验证继续使用原商品页，完成后正常关闭本次会话。
            page_url = snapshot.get('url') or page_url
            keep_page = verification
        finally:
            if not keep_page and (target or open_url):
                finish_browser_session(config.browser_port, config.browser_profile,
                                       completed_page=(str((target or {}).get('id') or ''), page_url),
                                       landing_page=('', ''))
        return {'snapshot': snapshot, 'assets': assets, 'browserStatus': browser_status(config)}


def _select_target(config, browser_tab_id, product_url, tab_url, platform_hint):
    try:
        tabs = browser_debug_json(config.browser_port, '/json')
    except OSError as exc:
        raise LocalOperationError('REMOTE_DEBUGGING_NOT_CONNECTED', '未连接采集浏览器，请打开专用浏览器后重试。') from exc
    pages = [tab for tab in tabs if tab.get('type') == 'page']
    if browser_tab_id:
        return next((tab for tab in pages if tab.get('id') == browser_tab_id), None)
    explicit = tab_url or product_url
    if explicit:
        return next((tab for tab in pages if same_collection_page(explicit, str(tab.get('url') or ''))), None)
    return None


def _read_target(config, target, product_url, tab_url, platform_hint, open_url):
    if open_url:
        # 新页面仍沿用原来的 complete 等待上限。
        with_cdp = CdpWebSocket(target['webSocketDebuggerUrl'])
        try:
            for _ in range(25):
                check_cancellation()
                state = with_cdp.call('Runtime.evaluate', {'expression': 'document.readyState', 'returnByValue': True})
                if state.get('result', {}).get('value') == 'complete':
                    break
                time.sleep(1)
        finally:
            with_cdp.close()
    _require_web_url(str(target.get('url') or product_url or tab_url))
    cdp = CdpWebSocket(target['webSocketDebuggerUrl'])
    try:
        snapshot = _snapshot(cdp, target)
        detected = detect_source_site(product_url or snapshot['url'])
        platform = detected if detected != 'generic' else platform_hint or detected
        _, reason = source_site(platform).diagnose(snapshot['url'], snapshot['html'], snapshot['text'], snapshot['title'], detect_slider=True)
        if reason in VERIFICATION_REASONS:
            return snapshot, [], reason in VERIFICATION_REASONS
        if (product_url or tab_url) and not same_collection_page(product_url or tab_url, snapshot['url']):
            raise LocalOperationError('TAB_NOT_ACCESSIBLE', 'TAB_NOT_ACCESSIBLE：原标签页已离开目标商品，请返回商品页后重试。')
        assets = []
        context = extract_1688_context_data(snapshot['html']) if platform == '1688' else {}
        detail_url = context.get('detail_url')
        if detail_url:
            detail_url = validated_1688_detail_url(detail_url)
            raw, _ = _fetch(cdp, detail_url, snapshot['url'], 10 * 1024 * 1024)
            snapshot['detail_html'] = raw.decode('utf-8', errors='replace')
        urls = list(context.get('main_image_urls') or []) or collect_product_image_urls(snapshot['html'], snapshot['url'], snapshot['image_urls'], limit=20)
        urls.extend(extract_1688_detail_images(snapshot.get('detail_html', ''), detail_url) if detail_url else [])
        urls.extend(row.get('image') for row in context.get('skus', []) if row.get('image'))
        for url in dict.fromkeys(urls):
            try:
                raw, mime = _fetch(cdp, url, snapshot['url'], 20 * 1024 * 1024)
                assets.append({'url': url, 'mime': mime, 'data': base64.b64encode(raw).decode('ascii')})
            except (OSError, ValueError) as exc:
                # 后端仍可下载公开素材；无法下载的资料由原完整性规则判断。
                assets.append({'url': url, 'error': str(exc)})
        return snapshot, assets, False
    finally:
        cdp.close()


def inspect_verification(config, browser_tab_id, source_url):
    tabs = browser_debug_json(config.browser_port, '/json')
    target = next((tab for tab in tabs if tab.get('id') == browser_tab_id and tab.get('type') == 'page'), None)
    if not target:
        return {'ok': True, 'status': 'unavailable', 'message': '原商品标签页已关闭，请重新开始采集。'}
    cdp = CdpWebSocket(target['webSocketDebuggerUrl'])
    try:
        result = cdp.call('Runtime.evaluate', {
            'expression': "({url:location.href,title:document.title,text:document.body?.innerText||'',html:document.documentElement?.outerHTML||'',ready:document.readyState})", 'returnByValue': True,
        }, timeout=10)
        page = result.get('result', {}).get('value') or {}
    finally:
        cdp.close()
    if page.get('ready') != 'complete':
        return {'ok': True, 'status': 'loading', 'message': '正在等待原商品页加载完成…'}
    _, reason = source_site(detect_source_site(source_url)).diagnose(str(page.get('url') or ''), str(page.get('html') or ''), str(page.get('text') or ''), str(page.get('title') or ''), detect_slider=True)
    if reason in VERIFICATION_REASONS:
        return {'ok': True, 'status': 'waiting_verification', 'message': WAIT_MESSAGE}
    if not same_collection_page(source_url, page['url']):
        return {'ok': True, 'status': 'unavailable', 'message': '原标签页已离开目标商品，请返回原商品页后重新采集。'}
    return {'ok': True, 'status': 'ready', 'message': '验证已完成，继续采集原商品页。'}

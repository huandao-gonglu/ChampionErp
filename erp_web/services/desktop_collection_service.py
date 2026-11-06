"""后端采集的本机事实适配与素材关联。"""
from copy import deepcopy
from .desktop_connection_service import call_desktop, DesktopUnavailable


def browser_debug_status():
    try:
        return call_desktop('browser.status', timeout=15)
    except DesktopUnavailable as exc:
        return {'ok': True, 'connected': False, 'current_tabs': [], 'tabs_count': 0,
                'error_code': exc.code, 'error_message': str(exc), 'next_action': str(exc)}


def apply_uploaded_assets(source, uploaded):
    result = deepcopy(source)
    pool = result.get('image_pool') or []
    for item in pool:
        if not isinstance(item, dict):
            continue
        url = item.get('url') or item.get('path')
        if url in uploaded:
            # 保留解析器给出的稳定资产身份、规格/主图标记。
            saved = uploaded[url]
            item.update({key: saved[key] for key in ('path', 'local_path', 'preview_url', 'width', 'height', 'status') if key in saved})
            item['raw'] = {**item.get('raw', {}), 'source_url': url}
    # SKU 图片可能只存在于规格关联中；添加到统一素材池后仍由原规则决定用途。
    known = {item.get('url') for item in pool if isinstance(item, dict)}
    for sku in result.get('skus', []):
        url = sku.get('image')
        if url in uploaded and url not in known:
            pool.append({**uploaded[url], 'url': url, 'is_sku': True, 'usage': 'sku', 'is_main': False})
            known.add(url)
    result['image_pool'] = pool
    return result

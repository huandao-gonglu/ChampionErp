"""类目只读响应缓存：属性定义与候选分页复用同一份平台事实。"""
from copy import deepcopy
import hashlib
from threading import RLock
import time

from erp_web.context import get_context


def cached_category_http(fetch, url, access_token=None, **kwargs):
    if kwargs.get("method", "GET") != "GET" or not any(part in url for part in ("/categories/", "/catalog_domains/")):
        return fetch(url,access_token,**kwargs)
    context = get_context()
    key = hashlib.sha256((url+"\0"+(access_token or "")).encode()).hexdigest()
    with context._lazy_lock:
        if not hasattr(context,"_category_http_cache"):
            context._category_http_cache = {}
            context._category_http_cache_lock = RLock()
    # 共享一次加载结果；缓存内只保留业务类目响应，不存 token 或 URL。
    with context._category_http_cache_lock:
        cache = context._category_http_cache
        now = time.monotonic()
        entry = cache.get(key)
        if entry and entry[0] > now:
            return deepcopy(entry[1])
        result = fetch(url,access_token,**kwargs)
        for expired in [k for k,v in cache.items() if v[0] <= now]:
            cache.pop(expired,None)
        if len(cache) >= 128:
            cache.pop(next(iter(cache)))
        cache[key] = (now+900,deepcopy(result))
        return result

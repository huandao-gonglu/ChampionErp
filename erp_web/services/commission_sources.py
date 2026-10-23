"""官方公开佣金资料的只读 HTTP 入口。"""
from http.cookiejar import CookieJar
import urllib.error
import urllib.request

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.services.external_request_manager import managed_urlopen


def read_official_content(url: str, *, referer: str = "") -> bytes:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    # 官方帮助站点的地区重定向需要保留响应 Cookie，否则会循环返回 307。
    try:
        with managed_urlopen(request, timeout=30, source=__name__, cookie_jar=CookieJar()) as response:
            content = response.read(12 * 1024 * 1024 + 1)
    except PublishAdapterError as exc:
        raise ValueError(f"官方佣金资料读取失败（{exc.code}）") from None
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError(f"官方佣金资料读取失败（{type(exc).__name__}）") from None
    if len(content) > 12 * 1024 * 1024:
        raise ValueError("官方佣金文件超出可读取大小")
    return content


__all__ = ["read_official_content"]

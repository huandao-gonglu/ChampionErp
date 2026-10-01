"""按需探测实际图片地址；连通性证据与确定性发布校验分开返回。"""

from urllib.error import HTTPError
from urllib.request import Request

from erp_web.schemas.publish_capabilities import ImagePublicAccessCheck
from erp_web.services.external_request_manager import managed_urlopen


def _check_image(url: str) -> ImagePublicAccessCheck:
    try:
        with managed_urlopen(Request(url, method="HEAD"), timeout=8, source=__name__) as response:
            status = response.status
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        image_response = 200 <= status < 300 and content_type.startswith("image/")
        return ImagePublicAccessCheck(
            url=url, status="reachable" if image_response else "inconclusive",
            http_status=status, content_type=content_type,
            message="当前服务器 HEAD 请求成功，返回图片类型；尚未验证平台侧抓取或图片内容" if image_response else "当前服务器收到响应，但未确认返回有效图片",
        )
    except HTTPError as exc:
        return ImagePublicAccessCheck(
            url=url, status="inconclusive" if exc.code in {405, 501} else "failed",
            http_status=exc.code,
            message="图片服务不支持 HEAD，无法确认可访问性" if exc.code in {405, 501} else f"当前服务器探测图片返回 HTTP {exc.code}",
        )
    except Exception as exc:
        return ImagePublicAccessCheck(url=url, status="failed", message=f"当前服务器探测图片失败：{exc}")


def check_image_public_access(urls: list[str]) -> list[ImagePublicAccessCheck]:
    """只探测传入的实际 HTTP(S) 图片地址，每个唯一 URL 最多一次。"""
    unique_urls = list(dict.fromkeys(url for url in urls if url.startswith(("https://", "http://"))))
    return [_check_image(url) for url in unique_urls]


__all__ = ["check_image_public_access"]

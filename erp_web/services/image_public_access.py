"""匿名 GET 的当前服务器访问证据；不代表市场侧已抓取成功。"""
from __future__ import annotations

import hashlib
from urllib.error import HTTPError
from urllib.request import Request

from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.schemas.publish_capabilities import ImagePublicAccessCheck
from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.services.image_hosting_config import validate_public_url
from erp_web.services.image_hosting_transport import safe_image_urlopen
from erp_web.services.image_content import image_content


def _check_image(url: str, *, expected_sha256: str = "", profile_id: str = "anonymous") -> ImagePublicAccessCheck:
    try:
        validate_public_url(url)
        ctx = request_context(url, method="GET", timeout=10, source=__name__, platform="image_hosting:public", account_id=profile_id, semantics="read")
        with managed_urlopen(Request(url, method="GET", headers={"Accept": "image/*"}), timeout=10,
                             request_context=ctx, transport=safe_image_urlopen, source=__name__) as response:
            status = response.status
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            raw = response.read()
        image_content(raw)
        if not 200 <= status < 300 or not content_type.startswith("image/"):
            raise ValueError("未返回图片类型")
        if expected_sha256 and hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("图片内容与上传对象不一致")
        return ImagePublicAccessCheck(url=url, status="reachable", http_status=status, content_type=content_type,
                                      message="匿名读取成功，图片内容有效")
    except HTTPError as exc:
        status = exc.code
        exc.close()
        return ImagePublicAccessCheck(url=url, status="failed", http_status=status, message=f"匿名 GET 返回 HTTP {status}，请检查公开权限及地址（不跟随重定向）")
    except (ImageHostingError, ExternalRequestBlocked) as exc:
        return ImagePublicAccessCheck(url=url, status="failed", message=str(exc))
    except Exception:
        return ImagePublicAccessCheck(url=url, status="failed", message="匿名图片读取或内容校验失败，请检查公开权限、图片内容与网络")


def check_image_public_access(urls: list[str]) -> list[ImagePublicAccessCheck]:
    return [_check_image(url) for url in dict.fromkeys(urls) if url.startswith(("https://", "http://"))]


__all__ = ["check_image_public_access", "_check_image"]

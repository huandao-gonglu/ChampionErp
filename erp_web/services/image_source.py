"""解析商品素材的个人空间路径，并通过受管传输读取公开原图。"""
import urllib.parse
from pathlib import Path
from urllib.request import Request

from erp_web.schemas.image_hosting import ImageHostingError
from .external_request_context import request_context
from .external_request_manager import managed_urlopen
from .image_content import MAX_IMAGE_BYTES, read_image_file
from .image_hosting_config import validate_public_url
from .image_hosting_transport import safe_image_urlopen
from .workspace_files import owned_path


def source_image_path(app_dir: Path, item: dict) -> Path | None:
    for key in ("path", "preview_url", "url"):
        value = str(item.get(key) or "").strip()
        if not value:
            continue
        if value.startswith("/file?"):
            value = urllib.parse.parse_qs(urllib.parse.urlparse(value).query).get("path", [""])[0]
        elif value.startswith("file:"):
            value = urllib.parse.unquote(urllib.parse.urlparse(value).path)
        elif value.startswith(("http://", "https://", "ml-id:", "data:", "blob:")):
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = app_dir / candidate
        candidate = owned_path(candidate, app_dir=app_dir)
        if candidate.is_file():
            return candidate.resolve()
    return None


def read_source_image(path: Path | None, source_url: str, *, account_id: str, timeout: float = 30, allow_query: bool = False) -> bytes:
    if path:
        return read_image_file(path)
    if not source_url:
        raise ImageHostingError("IMAGE_SOURCE_MISSING", "找不到原图，请重新导入图片。")
    validate_public_url(source_url, allow_query=allow_query)
    context = request_context(source_url, method="GET", timeout=timeout, source=__name__,
                              platform="image_hosting:public", account_id=account_id, semantics="read")
    with managed_urlopen(Request(source_url, headers={"Accept": "image/*"}), timeout=timeout,
                         request_context=context, transport=safe_image_urlopen, source=__name__, allow_query=allow_query) as response:
        return response.read(MAX_IMAGE_BYTES + 1)

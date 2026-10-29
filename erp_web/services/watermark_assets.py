"""在临时目录生成发布副本，远程原图沿用受管安全下载。"""
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request

from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.services.image_content import MAX_IMAGE_BYTES, image_content, read_image_file
from erp_web.services.image_hosting_config import validate_public_url
from erp_web.services.image_hosting_transport import safe_image_urlopen
from erp_web.services.watermark_service import apply_watermark


@contextmanager
def publication_image(path: Path | None, source_url: str, settings: dict) -> Iterator[tuple[Path, str]]:
    if path:
        data = read_image_file(path)
    else:
        if not source_url:
            raise ImageHostingError("IMAGE_SOURCE_MISSING", "原图不存在，无法添加或更新水印，请重新导入原图")
        validate_public_url(source_url)
        context = request_context(source_url, method="GET", timeout=30, source=__name__,
            platform="image_hosting:public", account_id="watermark-source", semantics="read")
        with managed_urlopen(Request(source_url, headers={"Accept": "image/*"}), timeout=30,
                request_context=context, transport=safe_image_urlopen, source=__name__) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    source_sha, _, _ = image_content(data)
    output = apply_watermark(data, settings)
    _, extension, _ = image_content(output)
    with TemporaryDirectory(prefix="erp-watermark-") as directory:
        target = Path(directory) / f"image.{extension}"
        target.write_bytes(output)
        yield target, source_sha
        if path and image_content(read_image_file(path))[0] != source_sha:
            raise ImageHostingError("IMAGE_DELIVERY_STALE", "上传期间原图已变化，请重新准备")


__all__ = ["publication_image"]

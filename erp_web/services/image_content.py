"""无网络与持久化依赖的图片字节校验、格式及内容身份。"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image

from erp_web.schemas.image_hosting import ImageHostingError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


def image_content(data: bytes) -> tuple[str, str, str]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageHostingError("IMAGE_SOURCE_MISSING", "图片为空或超过 20 MiB 上限", "导入有效图片后重新准备")
    try:
        with Image.open(io.BytesIO(data)) as image:
            kind = _FORMATS[image.format]
            if image.width * image.height > 40_000_000:
                raise ValueError("图片尺寸超过安全上限")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Exception:
        raise ImageHostingError("IMAGE_SOURCE_MISSING", "图片内容损坏或格式不受支持（支持 JPEG、PNG、WebP）", "重新导入有效图片") from None
    return hashlib.sha256(data).hexdigest(), *kind


def read_image_file(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(MAX_IMAGE_BYTES + 1)


__all__ = ["image_content", "read_image_file", "MAX_IMAGE_BYTES"]

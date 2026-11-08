"""在临时目录生成发布副本，远程原图沿用受管安全下载。"""
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services.image_content import image_content, read_image_file
from erp_web.services.image_source import read_source_image
from erp_web.services.watermark_service import apply_watermark


@contextmanager
def publication_image(path: Path | None, source_url: str, settings: dict) -> Iterator[tuple[Path, str]]:
    data = read_source_image(path, source_url, account_id="watermark-source")
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

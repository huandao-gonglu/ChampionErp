"""在线图片准备：复用 Mercado 上传客户端，临时素材在任务结束时清理。"""
from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request

from PIL import Image

from erp_web.marketplaces.config_http import upload_mercadolibre_picture
from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.services.image_content import image_content, read_image_file
from erp_web.services.image_hosting_transport import safe_image_urlopen


def prepare_mercado_pictures(adapter, pictures: list, assets: dict) -> list:
    prepared = []
    with TemporaryDirectory(prefix="erp-online-images-") as directory:
        for index, picture in enumerate(pictures):
            if "asset_id" not in picture:
                prepared.append(dict(picture))
                continue
            asset = assets[picture["asset_id"]]
            if asset.get("path"):
                data = read_image_file(Path(asset["path"]))
            else:
                url = asset["url"]
                context = request_context(url, method="GET", timeout=30, source=__name__,
                    platform="image_hosting:public", account_id="online-source", semantics="read")
                with managed_urlopen(Request(url, headers={"Accept": "image/*"}), timeout=30,
                        request_context=context, transport=safe_image_urlopen, source=__name__) as response:
                    data = response.read()
            _, extension, _ = image_content(data)
            if extension == "webp":
                with Image.open(io.BytesIO(data)) as image:
                    buffer = io.BytesIO()
                    image.convert("RGBA" if "A" in image.getbands() else "RGB").save(buffer, format="PNG")
                    data, extension = buffer.getvalue(), "png"
            if len(data) > 10 * 1024 * 1024:
                raise ValueError("Mercado 图片不得超过 10 MiB，请先压缩源图片")
            path = Path(directory) / f"image-{index}.{extension}"
            path.write_bytes(data)
            uploaded = upload_mercadolibre_picture(path, adapter.token)
            picture_id = str(uploaded.get("id") or "").strip()
            if not picture_id:
                raise ValueError("Mercado 图片上传未返回图片 ID，尚未修改商品")
            prepared.append({"id": picture_id})
    return prepared


__all__ = ["prepare_mercado_pictures"]

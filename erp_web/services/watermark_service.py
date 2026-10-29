"""发布与预览共用的水印合成；原始素材始终保持不变。"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from erp_web.schemas.config import WatermarkSettingsRequest
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services.image_content import image_content

FONT_PATH = Path(__file__).resolve().parents[1] / "assets/fonts/NotoSansCJKsc-Regular.otf"


def decode_image_upload(value: str, *, maximum: int = 2 * 1024 * 1024) -> bytes:
    if len(value) > (maximum + 2) // 3 * 4 + 100:
        raise ValueError("图片文件超过大小限制")
    header, separator, encoded = value.partition(",")
    if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
        raise ValueError("请上传 PNG、JPEG 或 WebP 图片")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("图片数据无效") from None
    if len(data) > maximum:
        raise ValueError("图片文件超过大小限制")
    image_content(data)
    with Image.open(io.BytesIO(data)) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("水印处理仅支持静态图片，请上传静态 PNG、JPEG 或 WebP")
    return data


def validate_watermark(value: dict) -> dict:
    settings = WatermarkSettingsRequest.model_validate(value).model_dump()
    if settings["image_data_url"]:
        data = decode_image_upload(settings["image_data_url"])
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 4_000_000:
                raise ValueError("水印图片不得超过 400 万像素")
    return settings


def watermark_settings(config: dict) -> dict:
    return WatermarkSettingsRequest.model_validate(config.get("system_settings", {}).get("watermark", {})).model_dump()


def has_watermark_content(settings: dict) -> bool:
    return bool(str(settings.get("text") or "").strip() or settings.get("image_data_url"))


def watermark_fingerprint(settings: dict) -> str:
    if not has_watermark_content(settings):
        return ""
    active = {key: value for key, value in settings.items() if key != "color" or settings["text"]}
    return hashlib.sha256(json.dumps([2, active], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _watermark_tile(settings: dict, width: int) -> Image.Image:
    """文字和图片组成一个重复单元，再统一旋转与透明化。"""
    parts = []
    if settings["image_data_url"]:
        with Image.open(io.BytesIO(decode_image_upload(settings["image_data_url"]))) as logo:
            mark = ImageOps.exif_transpose(logo).convert("RGBA")
        parts.append(ImageOps.contain(mark, (width, max(1, round(width * 0.55))), Image.Resampling.LANCZOS))
    if settings["text"]:
        font = ImageFont.truetype(str(FONT_PATH), 80)
        bbox = ImageDraw.Draw(Image.new("RGBA", (1, 1))).multiline_textbbox((0, 0), settings["text"], font=font)
        mark = Image.new("RGBA", (max(1, bbox[2] - bbox[0] + 4), max(1, bbox[3] - bbox[1] + 4)))
        ImageDraw.Draw(mark).multiline_text((2 - bbox[0], 2 - bbox[1]), settings["text"], font=font,
            fill=ImageColor.getrgb(settings["color"]))
        parts.append(ImageOps.contain(mark, (width, max(1, round(width * 0.4))), Image.Resampling.LANCZOS))
    gap = max(1, round(width * 0.08))
    tile = Image.new("RGBA", (max(part.width for part in parts), sum(part.height for part in parts) + gap * (len(parts) - 1)))
    y = 0
    for part in parts:
        tile.alpha_composite(part, ((tile.width - part.width) // 2, y))
        y += part.height + gap
    tile = tile.rotate(30, resample=Image.Resampling.BICUBIC, expand=True)
    tile.putalpha(tile.getchannel("A").point(lambda alpha: round(alpha * settings["opacity"] / 100)))
    return tile


def apply_watermark(data: bytes, settings: dict) -> bytes:
    if not has_watermark_content(settings):
        return data
    image_content(data)
    with Image.open(io.BytesIO(data)) as original:
        if getattr(original, "n_frames", 1) != 1:
            raise ImageHostingError("WATERMARK_INVALID_IMAGE", "水印仅支持静态图片，请替换动态图片")
        output_format = original.format
        base = ImageOps.exif_transpose(original).convert("RGBA")
    short_side = min(base.size)
    tile = _watermark_tile(settings, max(1, round(short_side * settings["size_percent"] / 100)))
    gap = max(4, round(short_side * (0.9 - settings["density"] * 0.08)))
    step_x, step_y = tile.width + gap, tile.height + max(2, round(gap * 0.55))
    # 以中央单元为基准错行平铺，四边允许裁切，宽图、竖图均覆盖完整画面。
    center_x, center_y = (base.width - tile.width) // 2, (base.height - tile.height) // 2
    for row in range(-base.height // step_y - 1, base.height // step_y + 2):
        y = center_y + row * step_y
        if y >= base.height or y + tile.height <= 0:
            continue
        offset = step_x // 2 if row % 2 else 0
        for column in range(-base.width // step_x - 2, base.width // step_x + 2):
            x = center_x + column * step_x + offset
            if x < base.width and x + tile.width > 0:
                base.alpha_composite(tile, (x, y))
    output = io.BytesIO()
    if output_format == "JPEG":
        base.convert("RGB").save(output, format="JPEG", quality=95)
    elif output_format == "WEBP":
        base.save(output, format="WEBP", quality=95)
    else:
        base.save(output, format="PNG")
    result = output.getvalue()
    image_content(result)
    return result


def preview_watermark(settings: dict, source: bytes) -> str:
    result = apply_watermark(source, validate_watermark(settings))
    _, _, mime = image_content(result)
    return f"data:{mime};base64," + base64.b64encode(result).decode()


__all__ = ["apply_watermark", "has_watermark_content", "decode_image_upload", "preview_watermark", "validate_watermark", "watermark_fingerprint", "watermark_settings"]

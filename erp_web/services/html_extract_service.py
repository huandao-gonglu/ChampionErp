from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import ssl
import time
import urllib.request

from erp_web.services.external_request_manager import managed_urlopen
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from . import ai_model_config

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MIN_COLLECT_IMAGE_SIDE = 240


def load_app_config() -> dict[str, Any]:
    """Default app config for the web backend.

    Kept here so erp_web_app no longer imports the retired tkinter desktop app.
    """
    return {
        "ai_models": ai_model_config.default_ai_models(),
        "ai_use_case_bindings": {},
        "auto_ai_recognition": "0",
        "alibaba_cookie": "",
    }


from erp_shared.html import (
    normalize_space,
    html_to_text,
    clean_price_number,
    normalize_image_url,
    extract_product_image_urls,
    upscale_amazon_image_url,
    extract_page_title,
    infer_product_from_title,
    extract_price_currency,
    inch_to_cm,
    ounce_to_kg,
    pound_to_kg,
    format_cm,
    extract_measurements,
)


def extension_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in reversed(Path(path).suffixes):
        if ext in IMAGE_SUFFIXES:
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def _suffix_from_response(url: str, content_type: str, data: bytes) -> str:
    content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    sample = data[:32]
    if sample.startswith(b"\xff\xd8"):
        return ".jpg"
    if sample.startswith(b"\x89PNG"):
        return ".png"
    if sample.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return ".webp"
    if "svg" in content_type or data.lstrip().startswith(b"<svg"):
        return ""
    guessed = mimetypes.guess_extension(content_type) if content_type else ""
    if guessed == ".jpe":
        guessed = ".jpg"
    if guessed in IMAGE_SUFFIXES:
        return ".jpg" if guessed == ".jpeg" else guessed
    return extension_from_url(url)


def _image_size_from_bytes(data: bytes) -> tuple[int, int]:
    if not Image:
        return 0, 0
    try:
        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def download_images(urls: list[str], dest_dir: Path) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for url in urls:
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.1688.com/",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
            )
            with managed_urlopen(request, timeout=30, source=__name__) as response:
                content_type = str(response.headers.get("Content-Type") or "")
                data = response.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                continue
            suffix = _suffix_from_response(url, content_type, data)
            if not suffix:
                continue
            width, height = _image_size_from_bytes(data)
            if Image and (width <= 0 or height <= 0):
                continue
            if width and height and max(width, height) < MIN_COLLECT_IMAGE_SIDE:
                continue
            path = dest_dir / f"url_main_{len(paths) + 1}{suffix}"
            path.write_bytes(data)
            paths.append(str(path))
        except Exception:
            continue
    return paths


def fetch_1688_detail_html(url: str) -> str:
    """读取浏览器商品快照引用的 1688 独立详情。"""
    from erp_shared.source_parser import validated_1688_detail_url
    detail_url = validated_1688_detail_url(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", ";Not A Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.1688.com/",
        "Connection": "close",
    }
    request = urllib.request.Request(detail_url, headers=headers)
    try:
        with managed_urlopen(request, timeout=25, source=__name__) as response:
            return response.read(900_000).decode("utf-8", errors="ignore")
    except Exception as exc:
        raise RuntimeError("1688 详情图片读取失败，请重试采集") from exc


__all__ = ['normalize_space', 'html_to_text', 'clean_price_number', 'normalize_image_url', 'extract_product_image_urls', 'upscale_amazon_image_url', 'extract_page_title', 'infer_product_from_title', 'extract_price_currency', 'inch_to_cm', 'ounce_to_kg', 'pound_to_kg', 'format_cm', 'extract_measurements', 'load_app_config', 'extension_from_url', 'download_images', 'fetch_1688_detail_html']

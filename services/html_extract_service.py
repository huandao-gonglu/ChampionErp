from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import ssl
import time
import urllib.request
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MIN_COLLECT_IMAGE_SIDE = 240


def load_app_config() -> dict[str, Any]:
    """Default app config for the web backend.

    Kept here so erp_web_app no longer imports the legacy tkinter desktop app.
    """
    return {
        "api_provider": "DeepSeek",
        "openai_api_key": "",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_image_model": "gpt-image-1.5",
        "nvidia_api_key": "",
        "nvidia_base_url": "https://integrate.api.nvidia.com/v1",
        "nvidia_model": "minimaxai/minimax-m2.7",
        "deepseek_api_key": "",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-chat",
        "auto_ai_recognition": "0",
        "alibaba_cookie": "",
    }


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def html_to_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    cleaned = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", cleaned)
    text = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()[:30000]


def clean_price_number(value: str) -> str:
    value = normalize_space(value)
    value = re.sub(r"[^0-9.,]", "", value)
    if "," in value and "." in value:
        value = value.replace(",", "")
    elif "," in value:
        parts = value.split(",")
        if len(parts[-1]) == 3:
            value = "".join(parts)
        else:
            value = ".".join(parts)
    return value.strip(".")


def normalize_image_url(value: str, page_url: str) -> str:
    value = value.replace("\\/", "/").strip()
    value = unescape(value)
    if not value or value.startswith("data:"):
        return ""
    return urljoin(page_url, value)


def extract_product_image_urls(html: str, page_url: str, limit: int = 7) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r'"hiRes"\s*:\s*"([^"]+)"',
        r'"large"\s*:\s*"([^"]+)"',
        r'"mainUrl"\s*:\s*"([^"]+)"',
        r'"landingImage"\s*:\s*"([^"]+)"',
        r'"imageUrl"\s*:\s*"([^"]+)"',
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<img[^>]+(?:data-old-hires|data-a-dynamic-image|data-large-image|data-src|src)=["\']([^"\']+)["\']',
        r'data-a-dynamic-image=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.I):
            if match.startswith("{"):
                candidates.extend(re.findall(r'"(https?://[^"]+)"', match))
            else:
                candidates.append(match)
    for block_pattern in [
        r'(?is)"colorImages"\s*:\s*\{.*?\]\s*\}',
        r'(?is)"imageBlockData"\s*:\s*\{.*?\}\s*,\s*"',
    ]:
        for block in re.findall(block_pattern, html):
            candidates.extend(
                re.findall(r'https?:\\?/\\?/[^"\'\s<>]+?\.(?:jpg|jpeg|png|webp)[^"\'\s<>]*', block, flags=re.I)
            )

    clean: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        for raw_url in re.findall(r"https?:\\?/\\?/[^\"'{}\s<>]+|//[^\"'{}\s<>]+|^[^\"'{}\s]+$", item):
            url = normalize_image_url(raw_url, page_url)
            url = upscale_amazon_image_url(url)
            lowered = url.lower()
            if not url or url in seen:
                continue
            if not any(ext in lowered for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                continue
            if any(skip in lowered for skip in ["sprite", "logo", "avatar", "icon"]):
                continue
            seen.add(url)
            clean.append(url)
            if len(clean) >= limit:
                return clean
    return clean[:limit]


def upscale_amazon_image_url(url: str) -> str:
    if "media-amazon." not in url.lower() and "ssl-images-amazon." not in url.lower():
        return url
    return re.sub(r"\._[^./]*(?=\.(?:jpg|jpeg|png|webp)(?:$|\?))", "", url, flags=re.I)


def extract_amazon_bullets(html: str) -> list[str]:
    section = ""
    feature = re.search(r'(?is)id=["\']feature-bullets["\'][^>]*>(.*?)(?:›\s*See more product details|id=["\']productOverview_feature_div|id=["\']productDetails)', html)
    if feature:
        section = feature.group(1)
    else:
        about = re.search(r"(?is)About this item(.*?)(?:›\s*See more product details|Product details|From the manufacturer)", html)
        section = about.group(1) if about else ""
    if not section:
        return []
    bullets = []
    for item in re.findall(r"(?is)<li[^>]*>(.*?)</li>", section):
        text = html_to_text(item)
        text = re.sub(r"^\s*[•\-]\s*", "", text).strip()
        lowered = text.lower()
        if text and "make sure this fits" not in lowered and "see more" not in lowered:
            bullets.append(text)
        if len(bullets) >= 5:
            break
    if len(bullets) < 5:
        plain = html_to_text(section)
        for line in re.split(r"\n|•|·|●|(?<=\.)\s+(?=[A-Z])", plain):
            text = normalize_space(line)
            lowered = text.lower()
            if (
                len(text) > 25
                and "about this item" not in lowered
                and "see more" not in lowered
                and "make sure this fits" not in lowered
                and text not in bullets
            ):
                bullets.append(text)
            if len(bullets) >= 5:
                break
    return bullets[:5]


def extract_page_title(html: str) -> str:
    for pattern in [
        r'(?is)<span[^>]+id=["\']productTitle["\'][^>]*>(.*?)</span>',
        r'(?is)<h1[^>]*>(.*?)</h1>',
        r'(?is)<title[^>]*>(.*?)</title>',
    ]:
        match = re.search(pattern, html)
        if match:
            return normalize_space(html_to_text(match.group(1)))
    return ""


def infer_product_from_title(title: str) -> dict[str, Any]:
    lowered = title.lower()
    inferred: dict[str, Any] = {}
    words = title.split()
    if words:
        inferred["brand"] = words[0]
    if any(term in lowered for term in ["fishing", "bobber", "float", "crappie", "bass", "trout"]):
        inferred["category"] = "钓鱼浮漂/渔具配件"
        inferred["target_customer"] = "钓鱼爱好者、户外垂钓用户"
        inferred["selling_points"] = [
            "钓鱼浮漂套装，适合多种淡水鱼垂钓",
            "醒目配色，便于观察鱼讯",
            "泡沫材质轻便耐用，适合户外携带",
            "带夹设计，安装和更换更方便",
            "适合鲈鱼、鳟鱼、蓝鳃鱼等垂钓场景",
        ]
    count = re.search(r"\b(\d+)\s*(?:pcs|pieces|pack|count)\b", lowered)
    if count:
        inferred["package_includes"] = [f"{count.group(1)} 件产品"]
    size = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:-|,|/|to)?\s*(\d+(?:\.\d+)?)?\s*(?:inch|inches|in)\b", lowered)
    if size:
        if size.group(2):
            inferred["dimensions"] = f"{size.group(1)}-{size.group(2)} inch"
        else:
            inferred["dimensions"] = f"{size.group(1)} inch"
    if "foam" in lowered:
        inferred["materials"] = ["泡沫"]
    return inferred


def extract_price_currency(html: str) -> tuple[str, str]:
    for pattern in [
        r'"displayPrice"\s*:\s*"\$([0-9][0-9,.]*)"',
        r'<meta[^>]+property=["\']product:price:amount["\'][^>]+content=["\']([0-9][0-9,.]*)["\']',
        r'<span[^>]+id=["\']priceblock_(?:ourprice|dealprice|saleprice)["\'][^>]*>\s*\$([0-9][0-9,.]*)',
        r'<input[^>]+id=["\']twister-plus-price-data-price["\'][^>]+value=["\']([0-9][0-9,.]*)["\']',
    ]:
        match = re.search(pattern, html, flags=re.I)
        if match:
            price = clean_price_number(match.group(1))
            if price:
                return price, "USD"

    is_amazon = "amazon.com" in html.lower() or "Amazon.com" in html
    amazon_regions = []
    for region_id in [
        "corePrice_feature_div",
        "corePriceDisplay_desktop_feature_div",
        "apex_desktop",
        "desktop_buybox",
        "buybox",
    ]:
        match = re.search(
            rf'(?is)id=["\']{region_id}["\'][^>]*>(.*?)(?:id=["\'][^"\']+["\']|</body>)',
            html,
        )
        if match:
            amazon_regions.append(match.group(1))
    for region in amazon_regions:
        offscreen = re.search(
            r'(?is)<span[^>]+class=["\'][^"\']*a-offscreen[^"\']*["\'][^>]*>\s*(?:US\$|\$)\s*([0-9][0-9,.]*)',
            region,
        )
        if offscreen:
            price = clean_price_number(offscreen.group(1))
            if price:
                return price, "USD"
        amazon = re.search(
            r'(?is)class=["\'][^"\']*a-price-whole[^"\']*["\'][^>]*>(.*?)<.*?class=["\'][^"\']*a-price-fraction[^"\']*["\'][^>]*>(.*?)<',
            region,
        )
        if amazon:
            whole = clean_price_number(html_to_text(amazon.group(1)))
            fraction = clean_price_number(html_to_text(amazon.group(2)))
            if whole:
                return f"{whole}.{fraction or '00'}", "USD"

    amazon_price = re.search(
        r'(?is)<span[^>]+class=["\'][^"\']*a-price-whole[^"\']*["\'][^>]*>\s*([0-9][0-9,]*)\s*<.*?'
        r'<span[^>]+class=["\'][^"\']*a-price-fraction[^"\']*["\'][^>]*>\s*([0-9]{2})\s*<',
        html,
    )
    if amazon_price:
        whole = clean_price_number(amazon_price.group(1))
        fraction = clean_price_number(amazon_price.group(2))
        try:
            value = float(f"{whole}.{fraction or '00'}")
            if 0 < value < 10000:
                return f"{whole}.{fraction or '00'}", "USD"
        except ValueError:
            pass

    if is_amazon:
        return "", ""

    text = html_to_text(html)
    patterns = [
        (r"(MXN|Mex\$)\s*([0-9][0-9,.]*)", "MXN"),
        (r"(RUB|₽)\s*([0-9][0-9,.]*)", "RUB"),
        (r"(￥|¥|CNY|RMB)\s*([0-9][0-9,.]*)", "CNY"),
        (r"(US\$|USD|\$)\s*([0-9][0-9,.]*)", "USD"),
    ]
    for pattern, currency in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            price = clean_price_number(match.group(2))
            try:
                if price and 0 < float(price) < 100000:
                    return price, currency
            except ValueError:
                continue
    return "", ""


def inch_to_cm(value: float) -> float:
    return value * 2.54


def ounce_to_kg(value: float) -> float:
    return value * 0.0283495231


def pound_to_kg(value: float) -> float:
    return value * 0.45359237


def format_cm(value: float) -> str:
    text = f"{value:.1f}"
    return text.rstrip("0").rstrip(".")


def extract_measurements(html: str) -> tuple[str, str]:
    text = html_to_text(html)
    text = normalize_space(text)
    raw = unescape(html)
    search_text = normalize_space(f"{text} {raw}")
    dimensions = ""
    weight_kg = ""

    dim_patterns = [
        r"(?:Product Dimensions|Item Dimensions|Package Dimensions|Product size|Package size|Assembled Dimensions|商品尺寸|包装尺寸|产品尺寸)[^0-9]{0,80}([0-9.,]+)\s*(?:x|×|\*)\s*([0-9.,]+)\s*(?:x|×|\*)\s*([0-9.,]+)\s*(inches|inch|in|cm|centimeters|厘米)",
        r"(?:尺寸|规格)[^0-9]{0,30}([0-9.,]+)\s*(?:x|×|\*)\s*([0-9.,]+)\s*(?:x|×|\*)\s*([0-9.,]+)\s*(inches|inch|in|cm|centimeters|厘米)",
        r'"(?:item_dimensions|product_dimensions|package_dimensions)"[^{}]{0,300}?"length"[^0-9]{0,40}([0-9.]+)[^{}]{0,120}?"width"[^0-9]{0,40}([0-9.]+)[^{}]{0,120}?"height"[^0-9]{0,40}([0-9.]+)[^{}]{0,120}?"unit"[^A-Za-z]{0,20}"?(inches|inch|in|cm|centimeters)',
        r"([0-9.,]+)\s*(?:x|×|\*)\s*([0-9.,]+)\s*(?:x|×|\*)\s*([0-9.,]+)\s*(inches|inch|in|cm|centimeters|厘米)",
    ]
    for pattern in dim_patterns:
        match = re.search(pattern, search_text, flags=re.I)
        if match:
            values = [float(match.group(i).replace(",", ".")) for i in range(1, 4)]
            unit = match.group(4).lower()
            if unit in {"inches", "inch", "in"}:
                values = [inch_to_cm(value) for value in values]
            dimensions = " x ".join(format_cm(value) for value in values) + " cm"
            break

    weight_patterns = [
        r"(?:Item Weight|Product Weight|Package Weight|Shipping Weight|商品重量|产品重量|包装重量|净重|毛重)[^0-9]{0,80}([0-9.,]+)\s*(kilograms|kilogram|kg|grams|gram|g|ounces|ounce|oz|pounds|pound|lbs|lb|千克|克|磅)",
        r'"(?:item_weight|product_weight|package_weight)"[^{}]{0,200}?"value"[^0-9]{0,40}([0-9.]+)[^{}]{0,120}?"unit"[^A-Za-z]{0,20}"?(kilograms|kilogram|kg|grams|gram|g|ounces|ounce|oz|pounds|pound|lbs|lb)',
        r"([0-9.,]+)\s*(kilograms|kilogram|kg|grams|gram|g|ounces|ounce|oz|pounds|pound|lbs|lb|千克|克|磅)\s*(?:Item Weight|Product Weight|Package Weight|Shipping Weight|商品重量|产品重量|包装重量)",
    ]
    for pattern in weight_patterns:
        match = re.search(pattern, search_text, flags=re.I)
        if match:
            value = float(match.group(1).replace(",", "."))
            unit = match.group(2).lower()
            if unit in {"grams", "gram", "g", "克"}:
                value = value / 1000
            elif unit in {"ounces", "ounce", "oz"}:
                value = ounce_to_kg(value)
            elif unit in {"pounds", "pound", "lbs", "lb", "磅"}:
                value = pound_to_kg(value)
            weight_kg = f"{value:.3f}".rstrip("0").rstrip(".")
            break

    return dimensions, weight_kg


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
            with urllib.request.urlopen(request, timeout=30) as response:
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


def fetch_url_html(url: str, cookie: str = "") -> str:
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
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    errors: list[str] = []
    contexts = [ssl.create_default_context(), ssl._create_unverified_context()]
    for attempt in range(3):
        for context in contexts:
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=25, context=context) as response:
                    raw = response.read(900_000)
                return raw.decode("utf-8", errors="ignore")
            except Exception as exc:
                errors.append(str(exc))
                time.sleep(0.8 + attempt * 0.6)
    tail = errors[-1] if errors else "unknown error"
    raise RuntimeError(
        "网页打开超时或 SSL 握手失败。请先在浏览器打开一次该链接，"
        f"再重新获取；最后错误: {tail}"
    )

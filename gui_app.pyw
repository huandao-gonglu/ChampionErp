from __future__ import annotations

import json
import mimetypes
import os
import re
import traceback
import base64
import hashlib
from io import BytesIO
import shutil
import socket
import ssl
import subprocess
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from html import unescape
from urllib.parse import quote, urljoin, urlparse
from urllib.request import url2pathname
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    X,
    Y,
    BooleanVar,
    Button,
    Canvas,
    Checkbutton,
    Entry,
    Frame,
    Label,
    LabelFrame,
    Scrollbar,
    Spinbox,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
)
from tkinter.ttk import Combobox, Notebook, Progressbar, Style

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

import main as generator
import erp_db
import marketplace_publish as publisher
from product_model import default_product_model

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS"))
else:
    APP_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = APP_DIR
OUTPUT_DIR = APP_DIR / "output"
STORE_CONFIG_PATH = APP_DIR / "store_config.json"
APP_CONFIG_PATH = APP_DIR / "app_config.json"
UPC_POOL_PATH = APP_DIR / "upc_pool.json"
ML_ATTR_CACHE_PATH = APP_DIR / "ml_category_attributes_cache.json"


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def join_lines(values: list[str]) -> str:
    return "\n".join(values or [])


def load_app_config() -> dict:
    if APP_CONFIG_PATH.exists():
        return json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8-sig"))
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


def save_app_config(config: dict) -> None:
    APP_CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def upc_check_digit(first_11: str) -> str:
    digits = [int(char) for char in first_11]
    odd_sum = sum(digits[0::2])
    even_sum = sum(digits[1::2])
    return str((10 - ((odd_sum * 3 + even_sum) % 10)) % 10)


def generate_upc_pool(count: int = 5000) -> dict:
    prefix = "725272"
    values = []
    for number in range(count):
        first_11 = f"{prefix}{number:05d}"
        values.append(first_11 + upc_check_digit(first_11))
    return {"values": values, "used": []}


def next_upc() -> str:
    if UPC_POOL_PATH.exists():
        try:
            pool = json.loads(UPC_POOL_PATH.read_text(encoding="utf-8"))
        except Exception:
            pool = generate_upc_pool()
    else:
        pool = generate_upc_pool()
    values = list(pool.get("values") or [])
    used = set(pool.get("used") or [])
    for value in values:
        if value not in used:
            used.add(value)
            pool["used"] = sorted(used)
            UPC_POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
            return value
    pool = generate_upc_pool(len(values) + 5000)
    UPC_POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
    return next_upc()


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


def html_to_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    cleaned = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", cleaned)
    text = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()[:30000]


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


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


def infer_product_from_title(title: str) -> dict:
    lowered = title.lower()
    inferred: dict[str, object] = {}
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
    for ext in [".jpg", ".jpeg", ".png", ".webp"]:
        if ext in path:
            return ext
    return ".jpg"


def download_images(urls: list[str], dest_dir: Path) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, url in enumerate(urls, start=1):
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        path = dest_dir / f"url_main_{index}{extension_from_url(url)}"
        path.write_bytes(data)
        paths.append(str(path))
    return paths


def file_url(path: Path) -> str:
    return path.resolve().as_uri()


def default_product() -> dict:
    return default_product_model()


def load_current_product_from_sqlite() -> dict:
    erp_db.initialize_database(APP_DIR)
    records = erp_db.list_product_records(APP_DIR, limit=1)
    if records:
        loaded = erp_db.load_product_model(APP_DIR, records[0]["product_id"])
        if loaded:
            return loaded
    return default_product()


def save_product_to_sqlite(product: dict) -> dict:
    erp_db.initialize_database(APP_DIR)
    product = dict(product or {})
    product["product_id"] = erp_db.upsert_product_model(APP_DIR, product)
    return product


class App:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("美客多 / WB 上架素材生成器")
        self.root.geometry("1080x900")
        self.product = self.load_product()
        self.app_config = load_app_config()
        self.source_images = list(self.product.get("source_images", []))

        self.vars: dict[str, StringVar] = {}
        self.texts: dict[str, Text] = {}
        self.image_urls_text: Text | None = None
        self.image_preview_frame: Frame | None = None
        self.ai_image_preview_frame: Frame | None = None
        self.zh_copy_text: Text | None = None
        self.translation_language = StringVar(value="中文")
        self.ml_attrs_frame: Frame | None = None
        self.ml_attribute_vars: dict[str, StringVar] = {}
        self.ml_attribute_meta: list[dict] = []
        self.image_task_prompt_text: Text | None = None
        self.image_log: Text | None = None
        self.image_task_status = StringVar(value="等待生成任务包")
        self.preview_images: list[object] = []
        self.platform = StringVar(value="all")
        self.api_provider = StringVar(value="DeepSeek")
        self.api_key = StringVar(value="")
        self.load_provider_key()
        self.openai_api_key = StringVar(value=self.app_config.get("openai_api_key", ""))
        self.openai_base_url = StringVar(
            value=self.app_config.get("openai_base_url") or "https://api.openai.com/v1"
        )
        self.openai_image_model = StringVar(value=self.app_config.get("openai_image_model") or "gpt-image-1.5")
        self.deepseek_base_url = StringVar(
            value=self.app_config.get("deepseek_base_url") or "https://api.deepseek.com"
        )
        self.deepseek_model = StringVar(value=self.app_config.get("deepseek_model") or "deepseek-chat")
        self.alibaba_cookie = StringVar(value=self.app_config.get("alibaba_cookie", ""))
        self.auto_ai_recognition = StringVar(value=self.app_config.get("auto_ai_recognition", "0"))
        self.product_url = StringVar(value="")
        self.output_dir = StringVar(value=str(OUTPUT_DIR))
        self.status_text = StringVar(value="就绪")
        self.image_count = StringVar(value="10")
        self.image_generation_mode = StringVar(value="ChatGPT提示词")
        self.image_market = StringVar(value="美客多墨西哥")
        self.image_translate_language = StringVar(value="俄语")
        self.media_language = StringVar(value="西班牙语")
        self.materials_var = StringVar(value="")
        self.detected_price = StringVar(value="")
        self.latest_plan: dict | None = None
        self.cancel_requested = False
        self.source_gallery_selection: set[int] = set()
        self.ai_gallery_selection: set[int] = set()
        self.store_config = publisher.load_store_config(STORE_CONFIG_PATH)
        listing_settings = self.store_config.get("listing", {})
        self.mx_category = StringVar(value=self.store_config.get("mercadolibre", {}).get("category_id", ""))
        self.mx_category_path = StringVar(value=self.store_config.get("mercadolibre", {}).get("category_path", ""))
        self.mx_price = StringVar(value=listing_settings.get("mercadolibre_price", listing_settings.get("price", "")))
        self.ru_subject = StringVar(value=self.store_config.get("wildberries", {}).get("subject_id", ""))
        self.ru_price = StringVar(value=listing_settings.get("wildberries_price", listing_settings.get("price", "")))
        self.sku_var = StringVar(value=listing_settings.get("sku") or "100")
        self.model_var = StringVar(value=listing_settings.get("model") or "100")
        self.stock_var = StringVar(value=listing_settings.get("stock", "10"))
        self.upc_var = StringVar(value=listing_settings.get("upc") or next_upc())
        self.auto_fill_listing_ids = BooleanVar(value=True)
        self.ml_listing_title = StringVar(value=listing_settings.get("mercadolibre_title", ""))
        self.length_cm = StringVar(value="")
        self.width_cm = StringVar(value="")
        self.height_cm = StringVar(value="")
        self.weight_kg = StringVar(value="")
        self.cost_cny = StringVar(value="")
        self.freight_cny = StringVar(value="")
        self.margin_percent = StringVar(value="25")
        self.ml_commission_percent = StringVar(value="16")
        self.wb_commission_percent = StringVar(value="20")
        self.usd_cny_rate = StringVar(value=listing_settings.get("usd_cny_rate", "7.20"))
        self.mxn_rate = StringVar(value=listing_settings.get("mxn_usd_rate", "18.00"))
        self.rub_rate = StringVar(value=listing_settings.get("rub_cny_rate", "11.0"))
        self.russia_freight_rate = StringVar(value=listing_settings.get("russia_freight_rate", "55"))
        self.russia_freight_cny = StringVar(value=listing_settings.get("russia_freight_cny", ""))
        self.ml_shipping_usd = StringVar(value=listing_settings.get("ml_shipping_usd", ""))
        self.ml_shipping_mxn = StringVar(value=listing_settings.get("ml_shipping_mxn", ""))
        self.ml_prep_fee_cny = StringVar(value=listing_settings.get("ml_prep_fee_cny", "2.5"))
        self.billable_weight_text = StringVar(value="")
        self.ml_price_usd = StringVar(value="")
        self.ml_profit_cny = StringVar(value="")
        self.ml_profit_usd = StringVar(value="")
        self.ml_net_proceeds_usd = StringVar(value="")
        self.ml_profit_percent_text = StringVar(value="")
        self.ml_zip_from = StringVar(value="01000")
        self.ml_zip_to = StringVar(value="05000")
        self.ml_listing_type = StringVar(value=listing_settings.get("listing_type_id") or "gold_special")
        self.ml_logistic_type = StringVar(value=listing_settings.get("mercadolibre_logistic_type") or "remote")
        self.ml_missing_attributes: set[str] = set()
        self.ml_missing_fields: set[str] = set()
        self.ml_attribute_widgets: dict[str, dict[str, object]] = {}

        self.build_ui()
        self.bind_calculator_traces()
        self.fill_fields()

    def load_product(self) -> dict:
        return load_current_product_from_sqlite()

    def provider_key_name(self) -> str:
        return "deepseek_api_key"

    def provider_env_name(self) -> str:
        return "DEEPSEEK_API_KEY"

    def provider_code(self) -> str:
        return "deepseek"

    def load_provider_key(self) -> None:
        key = self.app_config.get("deepseek_api_key") or os.getenv("DEEPSEEK_API_KEY", "")
        self.api_key.set(key)

    def on_provider_change(self, _event: object | None = None) -> None:
        self.app_config["api_provider"] = "DeepSeek"
        self.load_provider_key()
        save_app_config(self.app_config)
        self.write_log(f"模型通道已切换为 {self.api_provider.get()}。")

    def save_auto_ai_setting(self) -> None:
        self.app_config["auto_ai_recognition"] = self.auto_ai_recognition.get()
        save_app_config(self.app_config)
        status = "开启" if self.auto_ai_recognition.get() == "1" else "关闭"
        self.write_log(f"获取网址后自动AI识别已{status}。")

    def save_alibaba_cookie(self) -> None:
        self.app_config["alibaba_cookie"] = self.alibaba_cookie.get().strip()
        save_app_config(self.app_config)
        self.write_log("1688 Cookie 已保存。")

    def set_provider_env(self) -> dict[str, str | None]:
        env_values = {
            "DEEPSEEK_API_KEY": self.api_key.get().strip(),
            "DEEPSEEK_BASE_URL": self.deepseek_base_url.get().strip() or "https://api.deepseek.com",
            "DEEPSEEK_MODEL": self.deepseek_model.get().strip() or "deepseek-chat",
        }
        old_values = {name: os.environ.get(name) for name in env_values}
        for name, value in env_values.items():
            os.environ[name] = value
        return old_values

    def restore_provider_env(self, old_values: dict[str, str | None]) -> None:
        for name, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value

    def configure_ui_theme(self) -> None:
        try:
            self.root.configure(bg="#F5F7FA")
        except Exception:
            pass
        try:
            style = Style()
            style.configure("TNotebook", background="#F5F7FA", borderwidth=0)
            style.configure("TNotebook.Tab", padding=(14, 8))
            style.configure("TFrame", background="#F5F7FA")
            style.configure("TLabelFrame", background="#FFFFFF", padding=8)
            style.configure("TLabelFrame.Label", background="#FFFFFF", foreground="#333")
            style.configure("TProgressbar", thickness=10)
        except Exception:
            pass

    def collect_ai_source_text(self, product: dict | None = None) -> str:
        product = product or self.product or {}
        parts = [
            str(product.get("source_url") or ""),
            str(product.get("source_text") or ""),
            str(product.get("supplemental_info") or ""),
            self.get_text("selling_points") if "selling_points" in self.texts else "",
            self.get_text("package_includes") if "package_includes" in self.texts else "",
            self.materials_var.get().strip(),
            self.detected_price.get().strip(),
        ]
        return "\n".join(part for part in parts if str(part).strip())

    def schedule_auto_ai_after_import(self) -> None:
        if self.auto_ai_recognition.get() != "1":
            return
        if not self.api_key.get().strip():
            self.write_log("已勾选获取后自动AI识别，但当前没有 DeepSeek Key。")
            self.root.after(
                0,
                lambda: self.show_error("自动AI识别失败", "已勾选获取后自动AI识别，但没有填写 DeepSeek API Key。"),
            )
            return
        self.write_log("网页获取完成，已排队自动AI识别。")
        self.root.after(350, self._auto_ai_after_url_import)

    def _auto_ai_after_url_import(self) -> None:
        if self.cancel_requested:
            return
        try:
            self.write_log("开始执行自动AI识别。")
            self.run_analysis()
        except Exception as exc:
            detail = traceback.format_exc()
            self.write_log(f"自动AI识别触发失败: {exc}\n{detail}")
            self.show_error("自动AI识别失败", detail[:2000])

    def add_card(self, parent: Frame, title: str) -> LabelFrame:
        return LabelFrame(parent, text=title, padx=10, pady=10, bd=1, relief="groove")

    def build_ui(self) -> None:
        self.configure_ui_theme()
        self.collection_panel = Frame(self.root)
        self.collection_panel.pack(fill=X)

        top = Frame(self.collection_panel, padx=12, pady=10)
        top.pack(fill=X)

        Button(top, text="选择参考图片", command=self.choose_images).pack(side=LEFT, padx=4)
        Button(top, text="清除参考图片", command=self.clear_source_images).pack(side=LEFT, padx=4)
        Button(top, text="清空当前商品", command=self.clear_current_product_context).pack(side=LEFT, padx=4)
        Button(top, text="开始识别图片", command=self.run_analysis).pack(side=LEFT, padx=4)
        Button(top, text="生成文案", command=self.run_copy_generation).pack(side=LEFT, padx=4)
        Button(top, text="授权店铺", command=self.open_store_auth).pack(side=LEFT, padx=4)

        self.settings_button = Button(top, text="设置", command=self.toggle_advanced_settings)
        self.settings_button.pack(side=RIGHT, padx=4)

        self.advanced_settings_frame = Frame(self.collection_panel, padx=12, pady=4)
        self.advanced_settings_visible = False

        api = Frame(self.advanced_settings_frame)
        api.pack(fill=X)
        Label(api, text="文案DeepSeek Key").pack(side=LEFT)
        Entry(api, textvariable=self.api_key, show="*", width=26).pack(side=LEFT, padx=8)
        Button(api, text="绑定", command=self.bind_api_key).pack(side=LEFT, padx=4)
        Button(api, text="检测", command=self.test_deepseek_api_key).pack(side=LEFT, padx=4)
        Button(api, text="清除", command=self.clear_api_key).pack(side=LEFT, padx=4)
        Label(api, text="图片OpenAI Key").pack(side=LEFT, padx=(18, 0))
        Entry(api, textvariable=self.openai_api_key, show="*", width=26).pack(side=LEFT, padx=8)
        Button(api, text="绑定", command=self.bind_openai_api_key).pack(side=LEFT, padx=4)
        Button(api, text="检测", command=self.test_openai_api_key).pack(side=LEFT, padx=4)
        Button(api, text="清除", command=self.clear_openai_api_key).pack(side=LEFT, padx=4)

        deepseek_row = Frame(self.advanced_settings_frame, pady=4)
        deepseek_row.pack(fill=X)
        Label(deepseek_row, text="DeepSeek Base URL").pack(side=LEFT)
        Entry(deepseek_row, textvariable=self.deepseek_base_url, width=34).pack(side=LEFT, padx=8)
        Label(deepseek_row, text="Model").pack(side=LEFT)
        Entry(deepseek_row, textvariable=self.deepseek_model, width=26).pack(side=LEFT, padx=8)
        Label(deepseek_row, text="图片Base URL").pack(side=LEFT, padx=(18, 0))
        Entry(deepseek_row, textvariable=self.openai_base_url, width=34).pack(side=LEFT, padx=8)
        Label(deepseek_row, text="图片模型").pack(side=LEFT)
        Entry(deepseek_row, textvariable=self.openai_image_model, width=16).pack(side=LEFT, padx=8)
        cookie_row = Frame(self.advanced_settings_frame, pady=4)
        cookie_row.pack(fill=X)
        Label(cookie_row, text="1688 Cookie").pack(side=LEFT)
        Entry(cookie_row, textvariable=self.alibaba_cookie, width=110).pack(side=LEFT, fill=X, expand=True, padx=8)
        Button(cookie_row, text="保存", command=self.save_alibaba_cookie).pack(side=LEFT, padx=4)

        url_row = Frame(self.collection_panel, padx=12, pady=6)
        url_row.pack(fill=X)
        Label(url_row, text="产品网址").pack(side=LEFT)
        Entry(url_row, textvariable=self.product_url).pack(side=LEFT, fill=X, expand=True, padx=8)
        Button(url_row, text="获取产品信息", command=self.run_url_import).pack(side=LEFT, padx=4)
        Checkbutton(
            url_row,
            text="获取后自动AI识别",
            variable=self.auto_ai_recognition,
            onvalue="1",
            offvalue="0",
            command=self.save_auto_ai_setting,
        ).pack(side=LEFT, padx=4)
        self.url_progress = Progressbar(url_row, mode="determinate", maximum=100, length=150)
        self.url_progress.pack(side=LEFT, padx=(8, 0))
        self.url_status = Label(url_row, textvariable=self.status_text, fg="#666")
        self.url_status.pack(side=LEFT, padx=(8, 0))

        output = Frame(self.collection_panel, padx=12, pady=6)
        output.pack(fill=X)
        Label(output, text="保存位置").pack(side=LEFT)
        Entry(output, textvariable=self.output_dir).pack(side=LEFT, fill=X, expand=True, padx=8)
        Button(output, text="选择保存位置", command=self.choose_output_dir).pack(side=LEFT, padx=4)

        progress = Frame(self.root, padx=12, pady=6)
        self.progress_row = progress
        progress.pack(fill=X)
        step_strip = Frame(progress)
        step_strip.pack(side=LEFT, padx=(0, 10))
        for idx, title in enumerate(["1 采集/文案", "2 AI生图", "3 上架"]):
            Label(step_strip, text=title, fg="#409EFF" if idx == 0 else "#666").pack(side=LEFT, padx=(0, 10))
        self.progress = Progressbar(progress, mode="determinate", maximum=100)
        self.progress.pack(side=LEFT, fill=X, expand=True)
        Button(progress, text="终止", command=self.cancel_current_task).pack(side=LEFT, padx=8)

        style = Style()
        try:
            style.configure("StableBottom.TNotebook", tabposition="s")
            tabs = Notebook(self.root, style="StableBottom.TNotebook")
        except Exception:
            tabs = Notebook(self.root)
        tabs.pack(fill=BOTH, expand=True, padx=12, pady=8)
        copy_page = Frame(tabs)
        image_task_page = Frame(tabs)
        listing_page = Frame(tabs)
        tabs.bind("<<NotebookTabChanged>>", lambda _event: self.toggle_collection_panel(tabs))
        tabs.add(copy_page, text="1 采集 / 文案")
        tabs.add(image_task_page, text="2 AI生图")
        tabs.add(listing_page, text="3 核价 / 上架")

        workspace = Frame(copy_page, padx=0, pady=8)
        workspace.pack(fill=BOTH, expand=True)

        upper = Frame(workspace)
        upper.pack(fill=BOTH, expand=True, pady=(0, 8))

        left_panel = self.add_card(upper, "基础信息")
        left_panel.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 8))
        self.add_entry(left_panel, "name", "产品名")
        self.add_entry(left_panel, "brand", "品牌")
        self.add_entry(left_panel, "category", "品类")
        self.add_entry(left_panel, "target_customer", "目标买家")
        self.add_entry(left_panel, "dimensions", "尺寸/规格")
        sell_box = LabelFrame(left_panel, text="卖点（每行一个）", padx=8, pady=6)
        sell_box.pack(fill=BOTH, expand=True, pady=(6, 0))
        self.texts["selling_points"] = Text(sell_box, height=4)
        self.texts["selling_points"].pack(fill=BOTH, expand=True)
        left_controls = Frame(left_panel, pady=4)
        left_controls.pack(fill=X)
        Button(left_controls, text="清除左侧信息", command=self.clear_left_fields).pack(side=RIGHT)

        right_panel = Frame(upper)
        right_panel.pack(side=LEFT, fill=BOTH, expand=True, padx=(8, 0))

        right_top = self.add_card(right_panel, "包装与补充")
        right_top.pack(fill=X, pady=(0, 8))
        package_card = LabelFrame(right_top, text="包装清单（每行一个）", padx=8, pady=6)
        package_card.pack(fill=BOTH, expand=True)
        self.texts["package_includes"] = Text(package_card, height=5)
        self.texts["package_includes"].pack(fill=BOTH, expand=True)
        material_row = Frame(right_top, pady=6)
        material_row.pack(fill=X)
        Label(material_row, text="材质", width=10, anchor="w").pack(side=LEFT)
        Entry(material_row, textvariable=self.materials_var).pack(side=LEFT, fill=X, expand=True)
        Label(material_row, text="识别价格", width=10, anchor="w").pack(side=LEFT, padx=(12, 0))
        Entry(material_row, textvariable=self.detected_price, width=18).pack(side=LEFT)

        copy_cards = self.add_card(right_panel, "跨境电商文案")
        copy_cards.pack(fill=BOTH, expand=True)
        result_row = Frame(copy_cards)
        result_row.pack(fill=BOTH, expand=True)
        mexico_box = LabelFrame(result_row, text="墨西哥 Mercado Libre", padx=8, pady=8)
        mexico_box.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 6))
        self.mx_title_var = StringVar(value="")
        self.mx_title_alt_var = StringVar(value="")
        self.add_result_title(mexico_box, "标题 1", self.mx_title_var)
        self.add_result_title(mexico_box, "标题 2（无品牌）", self.mx_title_alt_var)
        mexico_box.winfo_children()[-1].pack_forget()
        self.mx_desc = Text(mexico_box, height=10)
        self.mx_desc.pack(fill=BOTH, expand=True, pady=(6, 0))

        russia_box = LabelFrame(result_row, text="俄罗斯 Wildberries", padx=8, pady=8)
        russia_box.pack(side=LEFT, fill=BOTH, expand=True, padx=(6, 6))
        self.ru_title_var = StringVar(value="")
        self.ru_title_alt_var = StringVar(value="")
        self.add_result_title(russia_box, "标题 1", self.ru_title_var)
        self.add_result_title(russia_box, "标题 2（无品牌）", self.ru_title_alt_var)
        russia_box.winfo_children()[-1].pack_forget()
        self.ru_desc = Text(russia_box, height=10)
        self.ru_desc.pack(fill=BOTH, expand=True, pady=(6, 0))

        translation_box = self.add_card(workspace, "墨西哥文案翻译对照")
        translation_box.pack(fill=BOTH, expand=False, pady=(0, 8))
        translation_toolbar = Frame(translation_box)
        translation_toolbar.pack(fill=X)
        Label(translation_toolbar, text="输出").pack(side=LEFT)
        Combobox(
            translation_toolbar,
            textvariable=self.translation_language,
            values=["中文", "English", "Português"],
            width=12,
            state="readonly",
        ).pack(side=LEFT, padx=(4, 8))
        Button(translation_toolbar, text="翻译墨西哥文案", command=self.run_translate_mx_copy).pack(side=LEFT)
        self.zh_copy_text = Text(translation_box, height=5, wrap="word")
        self.zh_copy_text.pack(fill=BOTH, expand=True, pady=(6, 0))
        self.build_listing_page(listing_page)
        self.build_image_task_page(image_task_page)

        log_box = LabelFrame(self.root, text="运行日志", padx=8, pady=8)
        log_box.pack(fill=BOTH, expand=False, padx=12, pady=(0, 12))
        self.log = Text(log_box, height=6)
        self.log.pack(fill=BOTH, expand=True)

    def toggle_advanced_settings(self) -> None:
        if getattr(self, "advanced_settings_visible", False):
            self.advanced_settings_frame.pack_forget()
            self.advanced_settings_visible = False
            self.settings_button.configure(text="设置")
        else:
            self.advanced_settings_frame.pack(fill=X, before=self.progress_row)
            self.advanced_settings_visible = True
            self.settings_button.configure(text="收起")

    def toggle_collection_panel(self, tabs: Notebook) -> None:
        selected_index = tabs.index(tabs.select())
        if selected_index == 0:
            if not self.collection_panel.winfo_ismapped():
                self.collection_panel.pack(fill=X, before=self.progress_row)
        else:
            if self.collection_panel.winfo_ismapped():
                self.collection_panel.pack_forget()

    def add_entry(self, parent: Frame, key: str, label: str) -> None:
        frame = Frame(parent, pady=3)
        frame.pack(fill=X)
        Label(frame, text=label, width=14, anchor="w").pack(side=LEFT)
        var = StringVar()
        Entry(frame, textvariable=var, width=34).pack(side=LEFT, fill=X, expand=True)
        self.vars[key] = var

    def add_text(self, parent: Frame, key: str, label: str, height: int) -> None:
        box = LabelFrame(parent, text=label, padx=6, pady=4)
        box.pack(fill=X, pady=4)
        toolbar = Frame(box)
        toolbar.pack(fill=X)
        Button(toolbar, text="清除", command=lambda k=key: self.set_text(k, "")).pack(side=RIGHT)
        text = Text(box, height=height)
        text.pack(fill=X)
        self.texts[key] = text

    def add_simple_entry(self, parent: Frame, label: str, var: StringVar) -> None:
        frame = Frame(parent, pady=3)
        frame.pack(fill=X)
        Label(frame, text=label, width=12, anchor="w").pack(side=LEFT)
        Entry(frame, textvariable=var).pack(side=LEFT, fill=X, expand=True)
        Button(frame, text="清除", command=lambda: var.set("")).pack(side=LEFT, padx=(6, 0))

    def add_result_title(self, parent: Frame, label: str, title_var: StringVar) -> None:
        row = Frame(parent)
        row.pack(fill=X, pady=2)
        Label(row, text=label, width=14, anchor="w").pack(side=LEFT)
        Entry(row, textvariable=title_var).pack(side=LEFT, fill=X, expand=True)
        Button(row, text="⧉", width=3, command=lambda: self.copy_text(title_var.get())).pack(
            side=LEFT, padx=(6, 0)
        )

    def clear_left_fields(self) -> None:
        for key in ["name", "brand", "category", "target_customer", "dimensions"]:
            if key in self.vars:
                self.vars[key].set("")
        self.set_text("selling_points", "")

    def clear_current_product_context(self) -> None:
        self.clear_left_fields()
        for key in ["package_includes"]:
            if key in self.texts:
                self.set_text(key, "")
        self.materials_var.set("")
        self.detected_price.set("")
        self.product_url.set("")
        self.source_images = []
        self.latest_plan = None
        self.ml_listing_title.set("")
        self.mx_category.set("")
        self.mx_category_path.set("")
        self.ru_subject.set("")
        self.mx_price.set("")
        self.ru_price.set("")
        self.ml_price_usd.set("")
        self.ml_profit_cny.set("")
        self.ml_profit_usd.set("")
        self.ml_net_proceeds_usd.set("")
        self.ml_profit_percent_text.set("")
        self.upc_var.set(next_upc())
        self.ml_missing_attributes = set()
        self.ml_missing_fields = set()
        self.ml_attribute_meta = []
        self.ml_attribute_vars = {}
        self.ml_attribute_widgets = {}
        self._reset_ml_publish_field_highlights()
        self.render_ml_attribute_fields()
        if hasattr(self, "mx_title_var"):
            self.mx_title_var.set("")
            self.mx_title_alt_var.set("")
            self.mx_desc.delete("1.0", END)
        if hasattr(self, "ru_title_var"):
            self.ru_title_var.set("")
            self.ru_title_alt_var.set("")
            self.ru_desc.delete("1.0", END)
        if self.zh_copy_text:
            self.zh_copy_text.delete("1.0", END)
        self.set_image_urls([])
        self.product = default_product()
        self.write_log("已清空当前商品、卖点、包装清单和图片信息。")

    def refresh_upc_from_pool(self) -> None:
        self.upc_var.set(next_upc())
        self.write_log(f"已从 UPC 池取出新 UPC: {self.upc_var.get()}")

    def auto_fill_listing_identity(self) -> None:
        if not self.auto_fill_listing_ids.get():
            return
        upc = next_upc()
        self.upc_var.set(upc)
        self.model_var.set(upc[:10])
        self.sku_var.set(upc[:8])
        self.write_log("已自动填充合规 UPC / 货号。")

    def show_upc_pool_status(self) -> None:
        try:
            pool = json.loads(UPC_POOL_PATH.read_text(encoding="utf-8")) if UPC_POOL_PATH.exists() else generate_upc_pool()
            values = list(pool.get("values") or [])
            used = set(pool.get("used") or [])
            messagebox.showinfo(
                "UPC 池",
                f"总量: {len(values)}\n已使用: {len(used)}\n剩余: {max(len(values) - len(used), 0)}\n\n点击“换UPC”会自动取一个未使用的新码。",
            )
        except Exception as exc:
            self.show_error("UPC 池读取失败", str(exc))

    def add_listing_fields(
        self,
        parent: Frame,
        category_label: str,
        category_var: StringVar,
        price_label: str,
        price_var: StringVar,
    ) -> None:
        row = Frame(parent)
        row.pack(fill=X, pady=4)
        Label(row, text=category_label, width=14, anchor="w").pack(side=LEFT)
        Entry(row, textvariable=category_var, width=18).pack(side=LEFT, padx=(0, 8))
        Label(row, text=price_label, width=10, anchor="w").pack(side=LEFT)
        Entry(row, textvariable=price_var, width=14).pack(side=LEFT)

    def add_calc_entry(self, parent: Frame, label: str, var: StringVar, width: int = 12) -> None:
        row = Frame(parent, pady=3)
        row.pack(fill=X)
        Label(row, text=label, width=14, anchor="w").pack(side=LEFT)
        Entry(row, textvariable=var, width=width).pack(side=LEFT)

    def add_result_row(self, parent: Frame, label: str, var: StringVar) -> None:
        row = Frame(parent, pady=3)
        row.pack(fill=X)
        Label(row, text=label, width=14, anchor="w").pack(side=LEFT)
        Label(row, textvariable=var, anchor="w", fg="#00468b").pack(side=LEFT)

    def add_profit_input_row(self, parent: Frame) -> None:
        row = Frame(parent, pady=3)
        row.pack(fill=X)
        Label(row, text="墨西哥利润", width=14, anchor="w").pack(side=LEFT)
        Entry(row, textvariable=self.ml_profit_cny, width=12).pack(side=LEFT)
        Button(row, text="倒推售价", command=self.reverse_price_from_profit).pack(side=LEFT, padx=(6, 0))

    def add_net_proceeds_input_row(self, parent: Frame) -> None:
        row = Frame(parent, pady=3)
        row.pack(fill=X)
        Label(row, text="美客多净收益USD", width=14, anchor="w").pack(side=LEFT)
        Entry(row, textvariable=self.ml_net_proceeds_usd, width=12).pack(side=LEFT)
        Button(row, text="按净收益定价", command=self.reverse_price_from_net_proceeds).pack(side=LEFT, padx=(6, 0))

    def build_image_task_page(self, parent: Frame) -> None:
        content = Frame(parent, padx=12, pady=12)
        content.pack(fill=BOTH, expand=True)
        left = Frame(content, padx=8, pady=8)
        left.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 8))
        right = Frame(content, padx=8, pady=8)
        right.pack(side=LEFT, fill=BOTH, expand=True)

        tools = LabelFrame(left, text="双轨图片中心", padx=8, pady=8)
        tools.pack(fill=X, pady=(0, 8))
        Button(tools, text="生成图片提示词", command=self.run_manual_image_prompts).pack(side=LEFT, padx=4)
        Button(tools, text="打开GPT并发送", command=self.send_image_task_to_gpt).pack(side=LEFT, padx=4)
        Button(tools, text="通过文案API生图", command=self.run_copy_based_api_image_generation).pack(side=LEFT, padx=4)
        Button(tools, text="导入生成图片", command=self.import_generated_images).pack(side=LEFT, padx=4)
        Label(tools, text="平台/国家").pack(side=LEFT, padx=(10, 2))
        Combobox(
            tools,
            textvariable=self.image_market,
            values=("美客多墨西哥", "WB俄罗斯", "Ozon俄罗斯", "全部平台"),
            state="readonly",
            width=12,
        ).pack(side=LEFT, padx=(0, 6))
        Label(tools, text="张数").pack(side=LEFT)
        Spinbox(tools, from_=1, to=10, textvariable=self.image_count, width=4).pack(side=LEFT, padx=(2, 6))
        Label(tools, text="图片语言").pack(side=LEFT)
        Combobox(
            tools,
            textvariable=self.media_language,
            values=("西班牙语", "俄语", "英语"),
            state="readonly",
            width=8,
        ).pack(side=LEFT, padx=(2, 6))
        Combobox(
            tools,
            textvariable=self.image_generation_mode,
            values=("ChatGPT提示词", "API生成"),
            state="readonly",
            width=12,
        ).pack(side=LEFT, padx=(6, 0))

        prompt_box = LabelFrame(left, text="生图提示词 / 任务", padx=8, pady=8)
        prompt_box.pack(fill=BOTH, expand=True, pady=(0, 8))
        task_actions = Frame(prompt_box)
        task_actions.pack(fill=X)
        Label(task_actions, textvariable=self.image_task_status, fg="#00468b").pack(side=LEFT)
        Button(
            task_actions,
            text="复制提示词",
            command=lambda: self.copy_to_clipboard(
                self.image_task_prompt_text.get("1.0", END).strip() if self.image_task_prompt_text else "",
                "生图提示词已复制。",
            ),
        ).pack(side=RIGHT, padx=4)
        Button(task_actions, text="刷新提示词", command=self.generate_image_task_package).pack(side=RIGHT, padx=4)
        self.image_task_prompt_text = Text(prompt_box, height=7, wrap="word")
        self.image_task_prompt_text.pack(fill=BOTH, expand=True, pady=(8, 0))
        self.image_task_prompt_text.insert(
            "1.0",
            "这里会显示由产品标题、描述、卖点和原图生成的生图提示词。\n"
            "先选择平台/国家和图片语言，再点“生成图片提示词”或“打开GPT并发送”。\n"
            "如果要消耗 OpenAI API 自动生图，点“通过文案API生图”。",
        )

        source_box = LabelFrame(left, text="1688 原生图", padx=8, pady=8)
        source_box.pack(fill=BOTH, expand=True)
        source_toolbar = Frame(source_box)
        source_toolbar.pack(fill=X)
        Button(source_toolbar, text="全选", command=lambda: self.select_all_gallery("source")).pack(side=LEFT, padx=4)
        Button(source_toolbar, text="反选", command=lambda: self.invert_gallery_selection("source")).pack(side=LEFT, padx=4)
        Button(source_toolbar, text="选择参考图片", command=self.choose_images).pack(side=LEFT, padx=4)
        Button(source_toolbar, text="打开原图文件夹", command=self.open_source_images_folder).pack(side=LEFT, padx=4)
        Button(source_toolbar, text="清除参考图片", command=self.clear_source_images).pack(side=LEFT, padx=4)
        self.source_image_preview_frame = Frame(source_box)
        self.source_image_preview_frame.pack(fill=BOTH, expand=True, pady=(8, 0))
        self.refresh_source_image_preview()

        ai_box = LabelFrame(right, text="AI 生成图", padx=8, pady=8)
        ai_box.pack(fill=BOTH, expand=True, pady=(0, 8))
        ai_toolbar = Frame(ai_box)
        ai_toolbar.pack(fill=X)
        Button(ai_toolbar, text="全选", command=lambda: self.select_all_gallery("ai")).pack(side=LEFT, padx=4)
        Button(ai_toolbar, text="反选", command=lambda: self.invert_gallery_selection("ai")).pack(side=LEFT, padx=4)
        Button(ai_toolbar, text="上传本地图片", command=self.add_local_images_to_listing).pack(side=LEFT, padx=4)
        Button(ai_toolbar, text="导入GPT下载图", command=self.import_generated_images).pack(side=LEFT, padx=4)
        self.ai_image_preview_frame = Frame(ai_box)
        self.ai_image_preview_frame.pack(fill=BOTH, expand=True, pady=(8, 0))
        self.refresh_ai_image_preview()

        translate_bar = LabelFrame(right, text="图片文字一键翻译", padx=8, pady=8)
        translate_bar.pack(fill=X)
        Label(translate_bar, text="选择目标语言").pack(side=LEFT)
        Combobox(
            translate_bar,
            textvariable=self.image_translate_language,
            values=("西班牙语", "俄语", "英语"),
            state="readonly",
            width=10,
        ).pack(side=LEFT, padx=8)
        Button(translate_bar, text="一键翻译图中文字并导出", command=self.translate_generated_images_prompt).pack(side=LEFT)

        image_log_box = LabelFrame(parent, text="生图日志 / 失败原因", padx=8, pady=8)
        image_log_box.pack(fill=X, padx=12, pady=(0, 12))
        self.image_log = Text(image_log_box, height=4, wrap="word")
        self.image_log.pack(fill=X)

    def generate_image_task_package(self) -> str:
        prompts = self.build_manual_image_prompts()
        out_dir = self.output_dir_path()
        out_dir.mkdir(parents=True, exist_ok=True)
        image_out_dir = out_dir / "chatgpt_images"
        image_out_dir.mkdir(parents=True, exist_ok=True)
        task_text = (
            "这是给 Codex CLI / ChatGPT 的生图任务包。\n"
            f"请读取随任务附带的原始产品图，生成独立图片文件，并把结果保存到：{image_out_dir}\n"
            "如果使用 ChatGPT 手动生成，请生成后下载图片，再点击软件里的“导入GPT结果”。\n\n"
            + "\n\n---\n\n".join(f"{item['label']}\n{item['prompt']}" for item in prompts)
        )
        task_path = out_dir / "codex_task_package.md"
        task_path.write_text(task_text, encoding="utf-8")
        run_path = out_dir / "run_codex_image_task.cmd"
        command = self.build_codex_image_command(task_path)
        run_path.write_text(
            "@echo off\n"
            "chcp 65001 >nul\n"
            f"cd /d {subprocess.list2cmdline([str(APP_DIR)])}\n"
            "echo 正在调用 Codex CLI 生成商品图...\n"
            f"{command}\n"
            "echo.\n"
            "echo 完成后请回到软件点击“导入GPT结果”或刷新预览。\n"
            "pause\n",
            encoding="utf-8",
        )
        if self.image_task_prompt_text:
            self.image_task_prompt_text.delete("1.0", END)
            self.image_task_prompt_text.insert("1.0", task_text)
        self.image_task_status.set(f"任务包已生成: {task_path.name}")
        self.write_log(f"生图任务包已生成: {task_path}，运行脚本: {run_path}")
        return task_text

    def send_image_task_to_gpt(self) -> None:
        prompt = ""
        if self.image_task_prompt_text:
            prompt = self.image_task_prompt_text.get("1.0", END).strip()
        if not prompt:
            prompt = self.generate_image_task_package()
        self.open_chatgpt_and_try_send(prompt)

    def build_codex_image_command(self, task_path: Path) -> str:
        args = ["codex", "exec", "--skip-git-repo-check", "-C", str(APP_DIR)]
        for image_path in [Path(path) for path in self.source_images if Path(path).exists()][:7]:
            args.extend(["-i", str(image_path)])
        args.append("-")
        return f"type {subprocess.list2cmdline([str(task_path)])} | {subprocess.list2cmdline(args)}"

    def run_codex_image_task(self) -> None:
        self.write_log("Codex CLI 当前不能稳定生图，已自动改用 ChatGPT 上传原图并发送提示词。")
        self.send_image_task_to_gpt()
        return
        self.generate_image_task_package()
        run_path = self.output_dir_path() / "run_codex_image_task.cmd"
        if not run_path.exists():
            self.show_error("Codex任务不存在", "请先生成任务包。")
            return
        try:
            subprocess.Popen(["cmd", "/c", "start", "", str(run_path)], cwd=str(APP_DIR), shell=False)
            self.write_log("已打开 Codex CLI 生图任务窗口。")
            self.show_info(
                "已调用 Codex",
                "Codex 会读取任务包和前 7 张原图。生成完成后，如果图片已保存到输出文件夹，点击导入图片即可。",
            )
        except Exception as exc:
            self.show_error("调用Codex失败", str(exc))

    def build_listing_page(self, parent: Frame) -> None:
        left = Frame(parent, padx=8, pady=8, width=360)
        left.pack(side=LEFT, fill=Y)
        left.pack_propagate(False)
        right = Frame(parent, padx=8, pady=8)
        right.pack(side=LEFT, fill=BOTH, expand=True)

        calc = LabelFrame(left, text="核价计算器", padx=8, pady=8)
        calc.pack(fill=X, pady=(0, 8))
        dim_row = Frame(calc, pady=3)
        dim_row.pack(fill=X)
        Label(dim_row, text="尺寸 cm", width=14, anchor="w").pack(side=LEFT)
        Entry(dim_row, textvariable=self.length_cm, width=6).pack(side=LEFT)
        Label(dim_row, text="x").pack(side=LEFT, padx=2)
        Entry(dim_row, textvariable=self.width_cm, width=6).pack(side=LEFT)
        Label(dim_row, text="x").pack(side=LEFT, padx=2)
        Entry(dim_row, textvariable=self.height_cm, width=6).pack(side=LEFT)
        self.add_calc_entry(calc, "重量 kg", self.weight_kg)
        self.add_calc_entry(calc, "采购成本 CNY", self.cost_cny)
        self.add_calc_entry(calc, "贴单打包 CNY", self.ml_prep_fee_cny)
        self.add_calc_entry(calc, "俄罗斯运费/kg", self.russia_freight_rate)
        self.add_calc_entry(calc, "俄罗斯运费 CNY", self.russia_freight_cny)
        ml_ship_row = Frame(calc, pady=3)
        ml_ship_row.pack(fill=X)
        Label(ml_ship_row, text="美客多运费 USD", width=14, anchor="w").pack(side=LEFT)
        Entry(ml_ship_row, textvariable=self.ml_shipping_usd, width=8).pack(side=LEFT)
        Button(ml_ship_row, text="运费查询", command=self.show_ml_shipping_table).pack(side=LEFT, padx=(6, 0))
        Button(ml_ship_row, text="刷新运费", command=self.refresh_ml_shipping_cost).pack(side=LEFT, padx=(6, 0))
        self.add_calc_entry(calc, "目标利润 %", self.margin_percent)
        self.add_calc_entry(calc, "美客多佣金 %", self.ml_commission_percent)
        self.add_calc_entry(calc, "WB佣金 %", self.wb_commission_percent)
        rate_row = Frame(calc, pady=3)
        rate_row.pack(fill=X)
        Label(rate_row, text="USD/CNY汇率", width=14, anchor="w").pack(side=LEFT)
        Entry(rate_row, textvariable=self.usd_cny_rate, width=10).pack(side=LEFT, fill=X, expand=True)
        Button(rate_row, text="刷新汇率", command=self.refresh_exchange_rates).pack(side=LEFT, padx=(6, 0))
        self.add_calc_entry(calc, "MXN/USD汇率", self.mxn_rate)
        self.add_calc_entry(calc, "RUB汇率", self.rub_rate)
        self.add_result_row(calc, "计费重量", self.billable_weight_text)
        self.add_profit_input_row(calc)
        self.add_net_proceeds_input_row(calc)
        self.add_result_row(calc, "墨西哥利润USD", self.ml_profit_usd)
        self.add_result_row(calc, "实际利润率", self.ml_profit_percent_text)
        ship_row = Frame(calc, pady=3)
        ship_row.pack(fill=X)
        Label(ship_row, text="美客多邮编", width=14, anchor="w").pack(side=LEFT)
        Entry(ship_row, textvariable=self.ml_zip_from, width=8).pack(side=LEFT)
        Label(ship_row, text="到").pack(side=LEFT, padx=3)
        Entry(ship_row, textvariable=self.ml_zip_to, width=8).pack(side=LEFT)
        Button(ship_row, text="刷新运费", command=self.refresh_ml_shipping_cost).pack(side=LEFT, padx=6)
        Button(calc, text="计算并填入价格", command=self.calculate_listing_prices).pack(anchor="e", pady=(6, 0))

        listing = LabelFrame(right, text="上架设置", padx=8, pady=8)
        listing.pack(fill=X, pady=(0, 8))
        identity_row = Frame(listing, pady=3)
        identity_row.pack(fill=X)
        for label, var, width in [
            ("SKU", self.sku_var, 10),
            ("货号/Model", self.model_var, 10),
            ("库存", self.stock_var, 8),
            ("UPC", self.upc_var, 16),
        ]:
            Label(identity_row, text=label, width=10, anchor="w").pack(side=LEFT)
            Entry(identity_row, textvariable=var, width=width).pack(side=LEFT, padx=(0, 8))
        Checkbutton(
            identity_row,
            text="自动填充合规UPC/货号",
            variable=self.auto_fill_listing_ids,
            onvalue=True,
            offvalue=False,
        ).pack(side=LEFT, padx=(2, 0))
        Button(identity_row, text="换UPC", command=self.refresh_upc_from_pool).pack(side=LEFT, padx=(8, 0))
        Button(identity_row, text="UPC池", command=self.show_upc_pool_status).pack(side=LEFT, padx=(4, 0))
        listing_type_row = Frame(listing, pady=3)
        listing_type_row.pack(fill=X)
        Label(listing_type_row, text="Listing type", width=14, anchor="w").pack(side=LEFT)
        Combobox(
            listing_type_row,
            textvariable=self.ml_listing_type,
            values=["gold_special", "gold_pro"],
            width=16,
            state="readonly",
        ).pack(side=LEFT)
        Label(listing_type_row, text="默认 Classic=gold_special，Premium=gold_pro", fg="#666").pack(side=LEFT, padx=(8, 0))
        attr_box = LabelFrame(listing, text="美客多类目属性（* 必填，选择分类后读取）", padx=6, pady=4)
        attr_box.pack(fill=X, pady=(2, 6))
        attr_actions = Frame(attr_box)
        attr_actions.pack(fill=X)
        Button(attr_actions, text="读取必填属性", command=self.load_ml_category_attributes).pack(side=RIGHT)
        # 带滚动条的属性区域，最大高度 180px，属性多时可滚动
        self._ml_attrs_canvas = Canvas(attr_box, highlightthickness=0)
        self._ml_attrs_scrollbar = Scrollbar(attr_box, orient="vertical", command=self._ml_attrs_canvas.yview)
        self._ml_attrs_canvas.configure(yscrollcommand=self._ml_attrs_scrollbar.set)
        self.ml_attrs_frame = Frame(self._ml_attrs_canvas)
        self._ml_attrs_window = self._ml_attrs_canvas.create_window((0, 0), window=self.ml_attrs_frame, anchor="nw")

        def _on_attrs_frame_configure(event: object) -> None:
            self._ml_attrs_canvas.configure(scrollregion=self._ml_attrs_canvas.bbox("all"))
            needed = self._ml_attrs_canvas.bbox("all")
            h = min(180, (needed[3] - needed[1]) if needed else 40)
            self._ml_attrs_canvas.configure(height=max(36, h))

        def _on_attrs_canvas_configure(event: object) -> None:
            self._ml_attrs_canvas.itemconfig(self._ml_attrs_window, width=self._ml_attrs_canvas.winfo_width())

        self.ml_attrs_frame.bind("<Configure>", _on_attrs_frame_configure)
        self._ml_attrs_canvas.bind("<Configure>", _on_attrs_canvas_configure)
        self._ml_attrs_canvas.pack(side=LEFT, fill=X, expand=True)
        self._ml_attrs_scrollbar.pack(side=RIGHT, fill=Y)
        self.render_ml_attribute_fields()
        ml_title_row = Frame(listing, pady=3)
        ml_title_row.pack(fill=X)
        Label(ml_title_row, text="美客多标题", width=14, anchor="w").pack(side=LEFT)
        self.ml_listing_title_entry = Entry(ml_title_row, textvariable=self.ml_listing_title)
        self.ml_listing_title_entry.pack(side=LEFT, fill=X, expand=True)
        Button(ml_title_row, text="同步", command=self.sync_listing_title_from_copy).pack(side=LEFT, padx=(6, 0))

        package_row = Frame(listing, pady=3)
        package_row.pack(fill=X)
        Label(package_row, text="包裹 cm/g", width=14, anchor="w").pack(side=LEFT)
        self.length_entry = Entry(package_row, textvariable=self.length_cm, width=7)
        self.length_entry.pack(side=LEFT)
        Label(package_row, text="x").pack(side=LEFT, padx=2)
        self.width_entry = Entry(package_row, textvariable=self.width_cm, width=7)
        self.width_entry.pack(side=LEFT)
        Label(package_row, text="x").pack(side=LEFT, padx=2)
        self.height_entry = Entry(package_row, textvariable=self.height_cm, width=7)
        self.height_entry.pack(side=LEFT)
        Label(package_row, text="重量kg").pack(side=LEFT, padx=(8, 2))
        self.weight_entry = Entry(package_row, textvariable=self.weight_kg, width=8)
        self.weight_entry.pack(side=LEFT)
        Label(package_row, text="发布时自动换算为 package_length/width/height/weight").pack(side=LEFT, padx=(8, 0))

        ml_row = Frame(listing, pady=3)
        ml_row.pack(fill=X)
        Label(ml_row, text="美客多类目ID", width=14, anchor="w").pack(side=LEFT)
        self.mx_category_entry = Entry(ml_row, textvariable=self.mx_category, width=14)
        self.mx_category_entry.pack(side=LEFT)
        Button(ml_row, text="查询分类", command=self.search_ml_category).pack(side=LEFT, padx=(6, 0))
        Button(ml_row, text="同步类目库", command=self.sync_ml_category_tree).pack(side=LEFT, padx=(4, 0))
        Label(ml_row, textvariable=self.mx_category_path, anchor="w", fg="#00468b").pack(side=LEFT, padx=(8, 0), fill=X, expand=True)
        wb_row = Frame(listing, pady=3)
        wb_row.pack(fill=X)
        Label(wb_row, text="WB Subject ID", width=14, anchor="w").pack(side=LEFT)
        Entry(wb_row, textvariable=self.ru_subject).pack(side=LEFT, fill=X, expand=True)
        Button(wb_row, text="查询分类", command=self.search_wb_subject).pack(side=LEFT, padx=(6, 0))
        ml_price_row = Frame(listing, pady=3)
        ml_price_row.pack(fill=X)
        Label(ml_price_row, text="墨西哥价格", width=14, anchor="w").pack(side=LEFT)
        self.mx_price_entry = Entry(ml_price_row, textvariable=self.mx_price, width=14)
        self.mx_price_entry.pack(side=LEFT, fill=X, expand=True)
        Label(ml_price_row, textvariable=self.ml_price_usd, width=16, anchor="w", fg="#00468b").pack(
            side=LEFT, padx=(6, 0)
        )
        self.add_calc_entry(listing, "俄罗斯价格", self.ru_price)
        logistic_row = Frame(listing, pady=3)
        logistic_row.pack(fill=X)
        Label(logistic_row, text="物流模式", width=14, anchor="w").pack(side=LEFT)
        self.ml_logistic_type_widget = Combobox(
            logistic_row,
            textvariable=self.ml_logistic_type,
            values=["remote", "fulfillment", "cross_docking", "me1"],
            state="readonly",
            width=16,
        )
        self.ml_logistic_type_widget.pack(side=LEFT)
        Label(logistic_row, text="Global Selling 常用 remote；如类目要求可改").pack(side=LEFT, padx=(8, 0))
        actions = Frame(listing, pady=8)
        actions.pack(fill=X)
        Button(actions, text="预检上架信息", command=self.run_listing_draft).pack(side=LEFT, padx=4)
        Button(actions, text="上架美客多", command=lambda: self.run_publish_listing("mercadolibre")).pack(side=LEFT, padx=4)
        Button(actions, text="上架WB", command=lambda: self.run_publish_listing("wildberries")).pack(side=LEFT, padx=4)
        Button(actions, text="上架 Ozon", command=lambda: self.show_platform_preview("Ozon")).pack(side=LEFT, padx=4)
        Button(actions, text="上架 Yandex", command=lambda: self.show_platform_preview("Yandex")).pack(side=LEFT, padx=4)
        Button(actions, text="全部上架", command=lambda: self.run_publish_listing("all")).pack(side=LEFT, padx=4)

        image_box = LabelFrame(right, text="图片URL / 预览信息（每行一个）", padx=8, pady=8)
        image_box.pack(fill=BOTH, expand=True)
        image_toolbar = Frame(image_box)
        image_toolbar.pack(fill=X)
        Button(image_toolbar, text="清空", command=self.clear_image_urls).pack(side=RIGHT)
        Button(image_toolbar, text="复制全部", command=self.copy_image_urls).pack(side=RIGHT, padx=4)
        Button(image_toolbar, text="导入GPT下载图片", command=self.import_generated_images).pack(side=RIGHT, padx=4)
        Button(image_toolbar, text="上传本地图片", command=self.add_local_images_to_listing).pack(side=RIGHT, padx=4)
        image_toolbar.winfo_children()[-1].pack_forget()
        self.image_urls_text = Text(image_box, height=8)
        self.image_urls_text.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.image_preview_frame = Frame(image_box)
        self.image_preview_frame.pack(fill=X, pady=(6, 0))

    def copy_text(self, value: str) -> None:
        self.copy_to_clipboard(value, "标题已复制。")

    def sync_listing_title_from_copy(self) -> None:
        title = ""
        if hasattr(self, "mx_title_var"):
            title = self.mx_title_var.get().strip()
        if not title and "name" in self.vars:
            title = self.vars["name"].get().strip()
        self.ml_listing_title.set(title[:60])
        self.write_log("已同步美客多上架标题。")

    def default_ml_attribute_value(self, attr_id: str, meta: dict) -> str:
        attr_id = str(attr_id or "").upper()
        if attr_id == "BRAND":
            return self.vars.get("brand").get().strip() if "brand" in self.vars else ""
        if attr_id in {"MODEL", "PART_NUMBER", "MPN"}:
            return self.model_var.get().strip() or self.sku_var.get().strip() or "100"
        if attr_id in {"GTIN", "UNIVERSAL_PRODUCT_CODE"}:
            return self.upc_var.get().strip()
        if attr_id in {"ITEM_CONDITION", "CONDITION"}:
            return self.store_config.get("listing", {}).get("condition", "new")
        if attr_id in {"EMPTY_GTIN_REASON", "GTIN_EXEMPTION_REASON"}:
            values = meta.get("values") or []
            preferred = ("Another reason", "Otra razón", "The product does not have a registered code")
            for name in preferred:
                for item in values:
                    item_name = str(item.get("name") or item.get("id") or "")
                    if item_name.lower() == name.lower():
                        return item_name
            if values:
                return str(values[0].get("name") or values[0].get("id") or "")
            return "Another reason"
        if attr_id == "PACKAGE_LENGTH":
            return f"{self.length_cm.get().strip()} cm".strip()
        if attr_id == "PACKAGE_WIDTH":
            return f"{self.width_cm.get().strip()} cm".strip()
        if attr_id == "PACKAGE_HEIGHT":
            return f"{self.height_cm.get().strip()} cm".strip()
        if attr_id == "PACKAGE_WEIGHT":
            weight = self.number_value(self.weight_kg.get())
            return f"{int(round(weight * 1000))} g" if weight else ""
        if attr_id == "VEHICLE_TYPE":
            values = meta.get("values") or []
            if values:
                return str(values[0].get("name") or values[0].get("id") or "")
            return "Car/Truck"
        saved = self.store_config.get("listing", {}).get("mercadolibre_attributes", {})
        return str(saved.get(attr_id) or "")

    def render_ml_attribute_fields(self) -> None:
        if not self.ml_attrs_frame:
            return
        for child in self.ml_attrs_frame.winfo_children():
            child.destroy()
        if not self.ml_attribute_meta:
            Label(self.ml_attrs_frame, text="选择美客多分类后点击读取属性。", fg="#666").pack(anchor="w")
            return
        old_vars = dict(self.ml_attribute_vars)
        self.ml_attribute_vars = {}
        self.ml_attribute_widgets = {}
        required_attrs = [item for item in self.ml_attribute_meta if item.get("required")]
        optional_attrs = [item for item in self.ml_attribute_meta if not item.get("required")]
        # 必填全部显示；可选属性最多显示 20 个（足够覆盖常用字段，不至于撑爆界面）
        visible = required_attrs + optional_attrs[:20]
        for idx, meta in enumerate(visible):
            attr_id = str(meta.get("id") or "")
            required_mark = "* " if meta.get("required") else "  "
            label_text = required_mark + str(meta.get("name") or attr_id)
            row = Frame(self.ml_attrs_frame, pady=2)
            row.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0, 8))
            label = Label(row, text=label_text[:26], width=26, anchor="w")
            label.pack(side=LEFT)
            var = old_vars.get(attr_id)
            if var is None:
                var = StringVar(value=self.default_ml_attribute_value(attr_id, meta))
            values = [str(item.get("name") or item.get("id") or "") for item in (meta.get("values") or [])]
            values = [v for v in values if v]
            widget = None
            if values:
                widget = Combobox(row, textvariable=var, values=values, width=16)
                widget.pack(side=LEFT)
            else:
                widget = Entry(row, textvariable=var, width=18)
                widget.pack(side=LEFT)
            self.ml_attribute_vars[attr_id] = var
            self.ml_attribute_widgets[attr_id] = {"row": row, "label": label, "widget": widget, "required": bool(meta.get("required"))}
            missing = attr_id in self.ml_missing_attributes or (bool(meta.get("required")) and not str(var.get()).strip())
            if missing:
                self._highlight_ml_attribute_widget(attr_id, True)
        self.ml_attrs_frame.grid_columnconfigure(0, weight=1)
        self.ml_attrs_frame.grid_columnconfigure(1, weight=1)
        # 强制触发高度重算
        self.ml_attrs_frame.update_idletasks()
        if hasattr(self, "_ml_attrs_canvas"):
            needed = self._ml_attrs_canvas.bbox("all")
            h = min(180, (needed[3] - needed[1]) if needed else 40)
            self._ml_attrs_canvas.configure(height=max(36, h))

    def _highlight_ml_attribute_widget(self, attr_id: str, missing: bool) -> None:
        info = self.ml_attribute_widgets.get(attr_id) or {}
        row = info.get("row")
        label = info.get("label")
        widget = info.get("widget")
        bg = "#fff0f0" if missing else row.cget("bg") if row else self.ml_attrs_frame.cget("bg")
        fg = "#b00020" if missing else "#111"
        if row:
            try:
                row.configure(bg=bg)
            except Exception:
                pass
        if label:
            try:
                label.configure(bg=bg, fg=fg)
            except Exception:
                pass
        if widget and hasattr(widget, "configure"):
            try:
                if widget.winfo_class() == "Entry":
                    widget.configure(bg=bg)
            except Exception:
                pass

    def _set_ml_missing_fields(self, missing_attributes: list[str] | set[str] = (), missing_fields: list[str] | set[str] = ()) -> None:
        self.ml_missing_attributes = {str(item).strip() for item in missing_attributes if str(item).strip()}
        self.ml_missing_fields = {str(item).strip() for item in missing_fields if str(item).strip()}
        if self.ml_attribute_meta:
            self.render_ml_attribute_fields()

    def _highlight_ml_publish_fields(self, fields: list[str] | set[str], missing: bool = True) -> None:
        targets = {str(item).strip().lower() for item in fields if str(item).strip()}
        controls = {
            "title": getattr(self, "ml_listing_title_entry", None),
            "category_id": getattr(self, "mx_category_entry", None),
            "price": getattr(self, "mx_price_entry", None),
            "package_length": getattr(self, "length_entry", None),
            "package_width": getattr(self, "width_entry", None),
            "package_height": getattr(self, "height_entry", None),
            "package_weight": getattr(self, "weight_entry", None),
            "listing_type_id": getattr(self, "ml_listing_type_widget", None),
            "logistic_type": getattr(self, "ml_logistic_type_widget", None),
        }
        bg = "#fff0f0" if missing else "white"
        for key, widget in controls.items():
            if widget is None or not hasattr(widget, "configure"):
                continue
            if key in targets:
                try:
                    widget.configure(bg=bg)
                except Exception:
                    pass

    def _reset_ml_publish_field_highlights(self) -> None:
        self._highlight_ml_publish_fields(
            [
                "title",
                "category_id",
                "price",
                "package_length",
                "package_width",
                "package_height",
                "package_weight",
                "listing_type_id",
                "logistic_type",
            ],
            False,
        )

    def load_ml_category_attributes(self) -> None:
        category_id = self.mx_category.get().strip()
        if not category_id:
            self.show_error("缺少分类", "请先选择或填写美客多类目 ID。")
            return
        try:
            cache = {}
            if ML_ATTR_CACHE_PATH.exists():
                cache = json.loads(ML_ATTR_CACHE_PATH.read_text(encoding="utf-8"))
            if category_id in cache:
                attrs = cache[category_id]
            else:
                self.sync_store_config_from_fields()
                config = publisher.load_store_config(STORE_CONFIG_PATH)
                config = self.ensure_mercadolibre_token(config)
                attrs = publisher.mercadolibre_category_attributes(
                    category_id,
                    config.get("mercadolibre", {}).get("access_token", ""),
                )
                cache[category_id] = attrs
                ML_ATTR_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            self.ml_attribute_meta = attrs
            self.render_ml_attribute_fields()
            required_count = len([item for item in attrs if item.get("required")])
            self.write_log(f"已读取美客多类目属性 {len(attrs)} 个，其中必填 {required_count} 个。")
        except Exception as exc:
            self.show_error("读取美客多属性失败", str(exc))

    def copy_to_clipboard(self, value: str, message: str = "已复制。") -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.write_log(message)

    def get_image_urls(self) -> list[str]:
        if not self.image_urls_text:
            return list(self.product.get("source_image_urls", []))
        return split_lines(self.image_urls_text.get("1.0", END))

    def get_public_image_urls(self) -> list[str]:
        return [
            url
            for url in self.get_image_urls()
            if url.lower().startswith(("http://", "https://", "ml-id:"))
        ]

    def get_local_image_paths(self) -> list[str]:
        paths: list[str] = []
        for value in self.get_image_urls():
            raw = value.strip()
            if not raw:
                continue
            if raw.lower().startswith("file://"):
                parsed = urlparse(raw)
                path = Path(url2pathname(parsed.path))
                if parsed.netloc:
                    path = Path(f"//{parsed.netloc}{url2pathname(parsed.path)}")
            else:
                path = Path(raw)
            if path.exists() and path.is_file():
                paths.append(str(path))
        return paths

    def image_url_to_local_path(self, raw: str) -> Path | None:
        raw = raw.strip()
        if not raw:
            return None
        if raw.lower().startswith("file://"):
            parsed = urlparse(raw)
            path = Path(url2pathname(parsed.path))
            if parsed.netloc:
                path = Path(f"//{parsed.netloc}{url2pathname(parsed.path)}")
        else:
            path = Path(raw)
        return path if path.exists() and path.is_file() else None

    def local_image_items(self) -> list[tuple[int, str, Path]]:
        items: list[tuple[int, str, Path]] = []
        for index, url in enumerate(self.get_image_urls()):
            path = self.image_url_to_local_path(url)
            if not path and url.lower().startswith(("http://", "https://")):
                path = self.remote_preview_path(url)
            if path:
                items.append((index, url, path))
        return items

    def render_gallery_items(
        self,
        frame: Frame,
        items: list[tuple[int, str, Path]],
        selected: set[int],
        kind: str,
        store_name: str,
    ) -> None:
        for child in frame.winfo_children():
            child.destroy()
        setattr(self, store_name, [])
        if not items:
            Label(frame, text="暂无图片，抓取或导入后会在这里显示。", fg="#666").pack(anchor="w")
            return
        for display_index, (item_index, _url, path) in enumerate(items, start=1):
            cell = Frame(frame, padx=4, pady=4, relief="groove", bd=1)
            cell.pack(side=LEFT, anchor="n")
            label_text = f"{display_index}. {path.name[:14]}"
            if item_index in selected:
                label_text += " [选中]"
            try:
                if Image and ImageTk:
                    image = Image.open(path)
                    image.thumbnail((100, 100))
                    photo = ImageTk.PhotoImage(image)
                    getattr(self, store_name).append(photo)
                    Label(cell, image=photo).pack()
                else:
                    Label(cell, text=path.name[:12], width=12, height=5, relief="groove").pack()
            except Exception:
                Label(cell, text=path.name[:12], width=12, height=5, relief="groove").pack()
            Label(cell, text=label_text, wraplength=100).pack()
            Button(
                cell,
                text="切换选中",
                width=10,
                command=lambda idx=item_index, gallery=kind: self.toggle_gallery_item(gallery, idx),
            ).pack(pady=(2, 0))

    def toggle_gallery_item(self, gallery: str, index: int) -> None:
        selected = self.source_gallery_selection if gallery == "source" else self.ai_gallery_selection
        if index in selected:
            selected.remove(index)
        else:
            selected.add(index)
        self.refresh_gallery(gallery)

    def select_all_gallery(self, gallery: str) -> None:
        if gallery == "source":
            self.source_gallery_selection = {idx for idx, _path in enumerate(self.source_images) if Path(_path).exists()}
        else:
            self.ai_gallery_selection = {idx for idx, _url, _path in self.local_image_items() if Path(_path).exists()}
        self.refresh_gallery(gallery)

    def invert_gallery_selection(self, gallery: str) -> None:
        if gallery == "source":
            all_indexes = {idx for idx, _path in enumerate(self.source_images) if Path(_path).exists()}
            self.source_gallery_selection = all_indexes - self.source_gallery_selection
        else:
            all_indexes = {idx for idx, _url, _path in self.local_image_items() if Path(_path).exists()}
            self.ai_gallery_selection = all_indexes - self.ai_gallery_selection
        self.refresh_gallery(gallery)

    def refresh_gallery(self, gallery: str) -> None:
        if gallery == "source":
            self.refresh_source_image_preview()
        else:
            self.refresh_ai_image_preview()

    def refresh_source_image_preview(self) -> None:
        if not getattr(self, "source_image_preview_frame", None):
            return
        items = [(idx, path, Path(path)) for idx, path in enumerate(self.source_images) if Path(path).exists()]
        self.render_gallery_items(self.source_image_preview_frame, items, self.source_gallery_selection, "source", "source_preview_images")

    def remote_preview_path(self, url: str) -> Path | None:
        cache_dir = self.output_dir_path() / "thumb_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".jpg"
        parsed_suffix = Path(urlparse(url).path).suffix.lower()
        if parsed_suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = parsed_suffix
        cache_path = cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + suffix)
        try:
            if not cache_path.exists():
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    },
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    cache_path.write_bytes(response.read(2_000_000))
            return cache_path if cache_path.exists() else None
        except Exception:
            return None

    def remote_preview_image(self, url: str):
        if not Image or not ImageTk or not url.lower().startswith(("http://", "https://")):
            return None
        cache_dir = self.output_dir_path() / "thumb_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg")
        try:
            if not cache_path.exists():
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    },
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    cache_path.write_bytes(response.read(2_000_000))
            image = Image.open(cache_path)
            image.thumbnail((92, 92))
            photo = ImageTk.PhotoImage(image)
            self.preview_images.append(photo)
            return photo
        except Exception:
            return None

    def extract_mercadolibre_uploaded_picture_url(self, data: dict) -> str:
        if data.get("id"):
            return f"ml-id:{data.get('id')}"
        for key in ["secure_url", "url"]:
            if data.get(key):
                return str(data[key])
        for variation in data.get("variations", []) if isinstance(data.get("variations"), list) else []:
            if isinstance(variation, dict):
                for key in ["secure_url", "url"]:
                    if variation.get(key):
                        return str(variation[key])
        return ""

    def download_image_for_upload(self, url: str, index: int) -> Path:
        cache_dir = self.output_dir_path() / "ml_upload_images"
        cache_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        path = cache_dir / f"upload_{index:02d}_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:10]}{suffix}"
        if path.exists() and path.stat().st_size > 1024:
            return path
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.amazon.com/",
            },
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            path.write_bytes(response.read(8_000_000))
        if path.stat().st_size < 1024:
            raise RuntimeError(f"图片下载为空或过小: {url}")
        return path

    def ensure_mercadolibre_images_uploaded(self, token: str) -> list[str]:
        uploaded: list[str] = []
        raw_urls = self.get_image_urls()
        if not raw_urls:
            return uploaded
        total = min(len(raw_urls), 12)
        for index, raw in enumerate(raw_urls[:12], start=1):
            raw = raw.strip()
            if not raw:
                continue
            if raw.startswith("ml-id:"):
                uploaded.append(raw)
                continue
            self.set_progress(index - 1, total, f"正在上传图片到美客多 {index}/{total}...")
            path = self.image_url_to_local_path(raw)
            if path is None and raw.lower().startswith(("http://", "https://")):
                path = self.download_image_for_upload(raw, index)
            if path is None:
                raise RuntimeError(f"图片不能上传，请删除或替换: {raw}")
            data = publisher.upload_mercadolibre_picture(path, token)
            ml_url = self.extract_mercadolibre_uploaded_picture_url(data)
            if not ml_url:
                raise RuntimeError(f"美客多图片上传未返回图片ID: {raw}")
            uploaded.append(ml_url)
        if uploaded:
            self.set_image_urls(uploaded)
            self.product = self.collect_product()
            self.product = save_product_to_sqlite(self.product)
            self.write_log(f"已把 {len(uploaded)} 张图片上传到美客多图片库，并替换为平台图片ID。")
        return uploaded

    def upload_local_images_to_mercadolibre(self, token: str) -> list[str]:
        uploaded: list[str] = []
        local_paths = self.get_local_image_paths()
        if not local_paths:
            return uploaded
        total = len(local_paths)
        for index, path in enumerate(local_paths, start=1):
            self.set_progress(index - 1, total, f"正在上传图片到美客多 {index}/{total}...")
            data = publisher.upload_mercadolibre_picture(path, token)
            url = self.extract_mercadolibre_uploaded_picture_url(data)
            if url:
                uploaded.append(url)
        if uploaded:
            merged = self.get_public_image_urls() + uploaded
            self.set_image_urls(merged)
            self.product = self.collect_product()
            self.product = save_product_to_sqlite(self.product)
            self.write_log(f"已上传 {len(uploaded)} 张本地图片到美客多，并写入图片 URL 框。")
        return uploaded

    def set_image_urls(self, urls: list[str]) -> None:
        if not self.image_urls_text:
            return
        self.image_urls_text.delete("1.0", END)
        self.image_urls_text.insert("1.0", "\n".join(urls or []))
        self.refresh_image_preview()
        self.refresh_ai_image_preview()

    def render_image_url_placeholders(self, frame: Frame) -> None:
        urls = self.get_image_urls()[:10]
        if not urls:
            Label(frame, text="导入图片后，这里会显示缩略图。", fg="#666").pack(anchor="w")
            return
        for display_index, url in enumerate(urls, start=1):
            cell = Frame(frame, padx=3, pady=3)
            cell.pack(side=LEFT, anchor="n")
            short = Path(urlparse(url).path).name or url
            Label(cell, text=short[:12], width=12, height=5, relief="groove").pack()
            Label(cell, text=f"{display_index}. {short[:14]}", wraplength=92).pack()

    def refresh_image_preview(self) -> None:
        if not self.image_preview_frame:
            return
        for child in self.image_preview_frame.winfo_children():
            child.destroy()
        self.preview_images = []
        local_items = self.local_image_items()[:10]
        self.refresh_ai_image_preview()
        if not local_items:
            self.render_image_url_placeholders(self.image_preview_frame)
            return
        for display_index, (url_index, _url, path) in enumerate(local_items, start=1):
            cell = Frame(self.image_preview_frame, padx=3, pady=3)
            cell.pack(side=LEFT, anchor="n")
            cell.image_index = url_index
            cell.bind("<ButtonPress-1>", lambda event, idx=url_index: self.start_image_drag(idx))
            cell.bind("<ButtonRelease-1>", lambda event, idx=url_index: self.finish_image_drag(event, idx))
            try:
                if Image and ImageTk:
                    image = Image.open(path)
                    image.thumbnail((92, 92))
                    photo = ImageTk.PhotoImage(image)
                    self.preview_images.append(photo)
                    image_label = Label(cell, image=photo)
                    image_label.pack()
                else:
                    image_label = Label(cell, text=Path(path).name[:12], width=12, height=5, relief="groove")
                    image_label.pack()
            except Exception:
                image_label = Label(cell, text=Path(path).name[:12], width=12, height=5, relief="groove")
                image_label.pack()
            image_label.image_index = url_index
            image_label.bind("<ButtonPress-1>", lambda event, idx=url_index: self.start_image_drag(idx))
            image_label.bind("<ButtonRelease-1>", lambda event, idx=url_index: self.finish_image_drag(event, idx))
            Label(cell, text=f"{display_index}. {Path(path).name[:14]}", wraplength=92).pack()
            tools = Frame(cell)
            tools.pack()
            Button(tools, text="<", width=2, command=lambda idx=url_index: self.move_image_url(idx, -1)).pack(side=LEFT)
            Button(tools, text="换", width=3, command=lambda idx=url_index: self.replace_image_url(idx)).pack(side=LEFT)
            Button(tools, text="删", width=3, command=lambda idx=url_index: self.remove_image_url(idx)).pack(side=LEFT)
            Button(tools, text=">", width=2, command=lambda idx=url_index: self.move_image_url(idx, 1)).pack(side=LEFT)

    def refresh_ai_image_preview(self) -> None:
        if not getattr(self, "ai_image_preview_frame", None):
            return
        items = self.local_image_items()[:10]
        self.render_gallery_items(self.ai_image_preview_frame, items, self.ai_gallery_selection, "ai", "ai_preview_images")

    def add_local_images_to_listing(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择要加入上架的本地图片",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp"), ("All files", "*.*")],
        )
        if not paths:
            return
        urls = self.get_image_urls()
        for raw_path in paths:
            url = file_url(Path(raw_path))
            if url not in urls:
                urls.append(url)
        self.set_image_urls(urls)
        self.write_log(f"已加入 {len(paths)} 张本地图片到当前上架图片。")

    def move_image_url(self, index: int, delta: int) -> None:
        urls = self.get_image_urls()
        target = index + delta
        if index < 0 or index >= len(urls) or target < 0 or target >= len(urls):
            return
        urls[index], urls[target] = urls[target], urls[index]
        self.set_image_urls(urls)

    def remove_image_url(self, index: int) -> None:
        urls = self.get_image_urls()
        if 0 <= index < len(urls):
            removed = urls.pop(index)
            self.set_image_urls(urls)
            self.write_log(f"已删除图片: {removed}")

    def replace_image_url(self, index: int) -> None:
        paths = filedialog.askopenfilenames(
            title="选择替换图片",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp"), ("All files", "*.*")],
        )
        if not paths:
            return
        urls = self.get_image_urls()
        if 0 <= index < len(urls):
            urls[index] = file_url(Path(paths[0]))
            self.set_image_urls(urls)
            self.write_log(f"已替换第 {index + 1} 张图片。")

    def start_image_drag(self, index: int) -> None:
        self.drag_image_index = index

    def finish_image_drag(self, event, fallback_index: int) -> None:
        source = getattr(self, "drag_image_index", fallback_index)
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        target = None
        while widget is not None:
            if hasattr(widget, "image_index"):
                target = int(getattr(widget, "image_index"))
                break
            widget = getattr(widget, "master", None)
        if target is None or target == source:
            return
        urls = self.get_image_urls()
        if not (0 <= source < len(urls) and 0 <= target < len(urls)):
            return
        item = urls.pop(source)
        if source < target:
            target -= 1
        urls.insert(target, item)
        self.set_image_urls(urls)
        self.write_log(f"已拖动调整图片顺序: {source + 1} -> {target + 1}")

    def clear_image_urls(self) -> None:
        self.set_image_urls([])
        self.write_log("图片 URL 已清空。")

    def copy_image_urls(self) -> None:
        self.copy_to_clipboard("\n".join(self.get_image_urls()), "图片 URL 已复制。")

    def compact_gpt_image_prompt(self) -> str:
        product = self.collect_product()
        count = max(6, min(10, int(self.image_count.get() or "10")))
        platform_language = "美客多图片用西班牙语文字；WB图片用俄语文字。"
        points = "；".join(product.get("selling_points", [])[:6])
        package = "；".join(product.get("package_includes", [])[:5])
        source_count = len([path for path in self.source_images if Path(path).exists()])
        scenes = [
            "1. 白底主图，完整展示产品和套装数量",
            "2. 规格尺寸图，标注关键尺寸",
            "3. 材质细节图，突出泡沫、连接扣、颜色和做工",
            "4. 使用场景图，真实钓鱼户外场景",
            "5. 卖点信息图，强调醒目配色和容易观察鱼讯",
            "6. 安装/更换图，展示夹扣和使用方式",
            "7. 包装清单图，展示15件产品",
            "8. 适用鱼种/场景图，Santee Crappie、Bluegill、Bass、Trout",
            "9. 近景特写图，展示细节和质感",
            "10. 高转化详情图，干净商业风格",
        ][:count]
        return (
            f"我已经上传/粘贴了 {source_count or 5} 张原始产品参考图。请先读取这些图，只能以原图里的产品外观、结构、颜色、材质和比例为准。\n"
            f"产品名: {product.get('name', '')}\n"
            f"品牌: {product.get('brand', '')}\n"
            f"品类: {product.get('category', '')}\n"
            f"目标买家: {product.get('target_customer', '')}\n"
            f"尺寸/规格: {product.get('dimensions', '')}\n"
            f"卖点: {points}\n"
            f"包装清单: {package}\n\n"
            f"请直接生成一整套 {count} 张独立电商图片，不要只生成一张，不要拼成长图，不要把多张图合在一张图里。"
            f"生成后右侧应能逐张选择/查看。{platform_language}\n"
            "每张图真实、清晰、高清、无水印、无平台Logo、无二维码、无夸张认证，不要添加原图没有的配件。\n\n"
            + "\n".join(scenes)
        )

    def open_source_images_folder(self) -> None:
        folder = self.output_dir_path() / "source_images"
        if not folder.exists():
            messagebox.showwarning("没有原图文件夹", "请先通过产品链接获取产品信息，软件会下载前 7 张原产品图。")
            return
        os.startfile(str(folder.resolve()))

    def open_chatgpt_and_try_send(self, prompt: str) -> None:
        source_files = [str(Path(path).resolve()) for path in self.source_images if Path(path).exists()]
        prompt_path = self.output_dir_path() / "chatgpt_prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        self.copy_to_clipboard(prompt, "图片提示词已复制，正在尝试打开 ChatGPT、上传原图并自动发送。")
        try:
            files_path = self.output_dir_path() / "chatgpt_source_files.json"
            files_path.write_text(json.dumps(source_files[:7], ensure_ascii=False), encoding="utf-8")
            result_path = self.output_dir_path() / "chatgpt_auto_result.json"
            try:
                result_path.unlink()
            except FileNotFoundError:
                pass
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--chatgpt-auto", str(prompt_path), str(files_path), str(result_path), str(self.output_dir_path())]
            else:
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--chatgpt-auto",
                    str(prompt_path),
                    str(files_path),
                    str(result_path),
                    str(self.output_dir_path()),
                ]
            subprocess.Popen(command, cwd=str(APP_DIR), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self.watch_chatgpt_auto_result, args=(result_path,), daemon=True).start()
            self.set_progress(5, 100, "ChatGPT 后台自动化已启动")
            self.write_log("ChatGPT 后台自动化已启动：会使用专用 Chrome 上传原图、发送提示词，并在生成后自动导入图片。")
            self.show_info(
                "正在自动发送",
                "已启动 ChatGPT 专用 Chrome 后台自动化，不是控制桌面版 ChatGPT。\n\n第一次使用时请在自动打开的 Chrome 里登录 ChatGPT；登录后再次点击本按钮，就会自动上传原图、发送提示词，并在生成完成后回传图片到软件。",
            )
            return
        except Exception as exc:
            self.write_log(f"ChatGPT 浏览器自动化启动失败，改用剪贴板方式: {exc}")
        opened = False
        try:
            os.startfile("chatgpt://")
            opened = True
            self.write_log("已尝试唤起 ChatGPT 桌面版。")
        except Exception:
            try:
                webbrowser.open("https://chatgpt.com/")
                opened = True
                self.write_log("未检测到 ChatGPT 桌面协议，已打开 ChatGPT 网页。")
            except Exception as exc:
                self.write_log(f"打开 ChatGPT 失败: {exc}")
        if not opened:
            return
        # Use the exact prompt selected by the user. Rebuilding a compact prompt here
        # can accidentally switch the platform/language back to a combined ML+WB task.
        compact_prompt = prompt
        compact_prompt_path = self.output_dir_path() / "chatgpt_short_prompt.txt"
        compact_prompt_path.write_text(compact_prompt, encoding="utf-8")
        safe_prompt_path = str(compact_prompt_path.resolve()).replace("'", "''")
        file_lines = ""
        for file_path in source_files[:7]:
            safe_path = file_path.replace("'", "''")
            file_lines += f"[void]$files.Add('{safe_path}'); "
        upload_block = ""
        if file_lines:
            upload_block = (
                "Add-Type -AssemblyName System.Collections.Specialized; "
                "$files = New-Object System.Collections.Specialized.StringCollection; "
                + file_lines +
                "[System.Windows.Forms.Clipboard]::SetFileDropList($files); "
                "Focus-ChatGPTWindow | Out-Null; "
                "Click-ChatGPTComposer; "
                "[System.Windows.Forms.SendKeys]::SendWait('^v'); "
                "Start-Sleep -Seconds 10; "
            )
        script = (
            "Start-Sleep -Seconds 6; "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type @'\n"
            "using System;\n"
            "using System.Runtime.InteropServices;\n"
            "public class Win32ChatGptFocus {\n"
            "  [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool SetCursorPos(int X, int Y);\n"
            "  [DllImport(\"user32.dll\")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);\n"
            "  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }\n"
            "}\n"
            "'@; "
            "$shell = New-Object -ComObject WScript.Shell; "
            "$script:chatTarget = $null; "
            "function Focus-ChatGPTWindow { "
            "$targets = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and ("
            "$_.ProcessName -match 'ChatGPT|chrome|msedge|msedgewebview2' -or "
            "$_.MainWindowTitle -match 'ChatGPT|OpenAI|Product|Fishing|Bobber|商品|图片|生成') } | "
            "Sort-Object @{Expression={ if ($_.ProcessName -match 'ChatGPT') {0} elseif ($_.MainWindowTitle -match 'ChatGPT|OpenAI') {1} else {2} }}, @{Expression='StartTime';Descending=$true}; "
            "foreach ($p in $targets) { "
            "$script:chatTarget = $p; "
            "[Win32ChatGptFocus]::ShowWindow($p.MainWindowHandle, 9) | Out-Null; "
            "[Win32ChatGptFocus]::SetForegroundWindow($p.MainWindowHandle) | Out-Null; "
            "Start-Sleep -Milliseconds 800; "
            "return $true } "
            "return $false }; "
            "function Click-ChatGPTComposer { "
            "if (-not $script:chatTarget) { Focus-ChatGPTWindow | Out-Null }; "
            "if (-not $script:chatTarget) { return }; "
            "$rect = New-Object Win32ChatGptFocus+RECT; "
            "[Win32ChatGptFocus]::GetWindowRect($script:chatTarget.MainWindowHandle, [ref]$rect) | Out-Null; "
            "$x = [int](($rect.Left + $rect.Right) / 2); "
            "$y = [int]($rect.Bottom - 72); "
            "[Win32ChatGptFocus]::SetCursorPos($x, $y) | Out-Null; "
            "Start-Sleep -Milliseconds 150; "
            "[Win32ChatGptFocus]::mouse_event(2,0,0,0,[UIntPtr]::Zero); "
            "[Win32ChatGptFocus]::mouse_event(4,0,0,0,[UIntPtr]::Zero); "
            "Start-Sleep -Milliseconds 400 }; "
            "Focus-ChatGPTWindow | Out-Null; "
            "Click-ChatGPTComposer; "
            + upload_block +
            f"$prompt = Get-Content -LiteralPath '{safe_prompt_path}' -Raw -Encoding UTF8; "
            "[System.Windows.Forms.Clipboard]::SetText($prompt); "
            "Focus-ChatGPTWindow | Out-Null; "
            "Click-ChatGPTComposer; "
            "[System.Windows.Forms.SendKeys]::SendWait('^v'); "
            "Start-Sleep -Seconds 1; "
            "[System.Windows.Forms.SendKeys]::SendWait('^{ENTER}'); "
            "Start-Sleep -Milliseconds 700; "
            "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')"
        )
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", script],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.show_info(
                "正在自动发送",
                "会先尝试把抓取到的前 7 张原图作为文件一次性上传到 ChatGPT，再写入当前平台提示词并发送。请让 ChatGPT 窗口保持在前台；如果浏览器不接受自动上传图片，请手动上传 output/source_images 里的原图。",
            )
        except Exception as exc:
            self.write_log(f"自动粘贴发送失败: {exc}")
            self.show_error("自动发送失败", "已复制提示词，请手动粘贴到 ChatGPT。")

    def import_generated_images(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择 ChatGPT 下载的图片",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp"), ("All files", "*.*")],
        )
        if not paths:
            return
        dest_dir = self.output_dir_path() / "chatgpt_images"
        dest_dir.mkdir(parents=True, exist_ok=True)
        urls = self.get_image_urls()
        imported = 0
        for index, raw_path in enumerate(paths, start=1):
            src = Path(raw_path)
            if not src.exists():
                continue
            suffix = src.suffix.lower() or ".png"
            target = dest_dir / f"chatgpt_{len(urls) + index:02d}{suffix}"
            shutil.copy2(src, target)
            urls.append(file_url(target))
            imported += 1
        self.set_image_urls(urls)
        self.product = self.collect_product()
        self.product = save_product_to_sqlite(self.product)
        self.write_log(f"已导入 {imported} 张 ChatGPT 下载图片，并填入图片 URL 框。本地 file URL 可用于记录/预览，正式上架仍需公网 URL。")
        self.show_info("导入完成", f"已导入 {imported} 张图片，并显示到图片预览/上架图片区域。")

    def import_generated_image_paths(self, paths: list[str], replace: bool = False) -> None:
        valid = [str(Path(path).resolve()) for path in paths if Path(path).exists()]
        if not valid:
            return
        urls = [] if replace else self.get_image_urls()
        for path in valid:
            url = file_url(Path(path))
            if url not in urls:
                urls.append(url)
        self.set_image_urls(urls)
        self.product = self.collect_product()
        self.product = save_product_to_sqlite(self.product)
        self.write_log(f"ChatGPT 后台生图已自动导入 {len(valid)} 张，已回填到图片框和预览区。")

    def watch_chatgpt_auto_result(self, result_path: Path) -> None:
        deadline = time.time() + 45 * 60
        announced = False
        while time.time() < deadline:
            if result_path.exists():
                try:
                    data = json.loads(result_path.read_text(encoding="utf-8"))
                except Exception:
                    time.sleep(2)
                    continue
                if data.get("ok"):
                    images = [str(path) for path in data.get("images", [])]
                    self.root.after(0, lambda imgs=images: self.import_generated_image_paths(imgs, replace=True))
                    self.root.after(0, lambda: self.set_progress(100, 100, "ChatGPT 图片已自动导入"))
                    return
                error = str(data.get("error") or "ChatGPT 后台生图未返回图片。")
                self.write_log("ChatGPT 自动导入失败: " + error)
                self.root.after(0, lambda msg=error: self.show_error("ChatGPT 自动导入失败", msg))
                return
            if not announced:
                announced = True
                self.write_log("已进入 ChatGPT 后台监听：生成完成后会自动抓取图片并导入。")
                self.root.after(0, lambda: self.set_progress(10, 100, "等待 ChatGPT 后台生成图片..."))
            time.sleep(3)
        self.write_log("ChatGPT 后台监听超时：没有检测到可导入的生成图片。")
        self.root.after(0, lambda: self.set_progress(100, 100, "ChatGPT 后台监听超时"))

    def split_price_currency(self) -> tuple[str, str]:
        value = self.detected_price.get().strip()
        if not value:
            return "", ""
        match = re.match(r"^(.+?)\s+(USD|MXN|RUB|CNY|EUR|GBP)$", value, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).upper()
        return value, ""

    def bind_calculator_traces(self) -> None:
        watched = [
            self.length_cm,
            self.width_cm,
            self.height_cm,
            self.weight_kg,
            self.cost_cny,
            self.freight_cny,
            self.ml_prep_fee_cny,
            self.russia_freight_rate,
            self.ml_shipping_usd,
            self.margin_percent,
            self.ml_commission_percent,
            self.wb_commission_percent,
            self.usd_cny_rate,
            self.mxn_rate,
            self.rub_rate,
        ]
        for var in watched:
            var.trace_add("write", lambda *_args: self.auto_update_calculator())

    def auto_update_calculator(self) -> None:
        if getattr(self, "_auto_calculating", False):
            return
        try:
            self._auto_calculating = True
            self.refresh_ml_shipping_cost(show_log=False)
            self.calculate_listing_prices(show_errors=False)
        finally:
            self._auto_calculating = False

    def number_value(self, value: str) -> float:
        text = (value or "").strip().replace(",", ".")
        return float(text) if text else 0.0

    def billable_weight_kg(self) -> float:
        length = self.number_value(self.length_cm.get())
        width = self.number_value(self.width_cm.get())
        height = self.number_value(self.height_cm.get())
        weight = self.number_value(self.weight_kg.get())
        volume_kg = (length * width * height) / 6000 if length and width and height else 0
        return max(weight, volume_kg)

    def estimate_ml_shipping_usd(self, billable_kg: float) -> float:
        billable_g = max(1, int(round(billable_kg * 1000)))
        tiers = [
            (100, 6.20),
            (300, 8.40),
            (500, 9.90),
            (1000, 11.80),
            (2000, 17.20),
            (3000, 24.00),
            (5000, 37.00),
            (10000, 68.00),
            (15000, 96.00),
            (20000, 128.00),
            (30000, 188.00),
        ]
        for limit_g, cost in tiers:
            if billable_g <= limit_g:
                return cost
        extra_kg = max(0, billable_kg - 30)
        return 188.00 + extra_kg * 6.5

    def refresh_ml_shipping_cost(self, show_log: bool = True) -> None:
        try:
            billable = self.billable_weight_kg()
            if billable <= 0:
                self.billable_weight_text.set("")
                return
            billable_g = int(round(billable * 1000))
            usd_cost = self.estimate_ml_shipping_usd(billable)
            self.billable_weight_text.set(f"{billable_g} g（体积重和实重取大）")
            self.ml_shipping_usd.set(f"{usd_cost:.2f}")
            mxn_rate = self.number_value(self.mxn_rate.get())
            if mxn_rate:
                self.ml_shipping_mxn.set(f"{usd_cost * mxn_rate:.2f}")
            if show_log:
                self.write_log(f"美客多运费已按计费重量 {billable_g} g 估算: {usd_cost:.2f} USD")
        except Exception as exc:
            if show_log:
                self.show_error("美客多运费计算失败", str(exc))

    def refresh_exchange_rates(self) -> None:
        try:
            request = urllib.request.Request(
                "https://open.er-api.com/v6/latest/USD",
                headers={"User-Agent": "MarketplaceMediaGenerator/1.0"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            rates = data.get("rates", {}) if isinstance(data, dict) else {}
            cny = float(rates.get("CNY") or 0)
            mxn = float(rates.get("MXN") or 0)
            rub = float(rates.get("RUB") or 0)
            if not cny or not mxn or not rub:
                raise RuntimeError(f"汇率接口未返回完整数据: {data}")
            self.usd_cny_rate.set(f"{cny:.4f}")
            self.mxn_rate.set(f"{mxn:.4f}")
            self.rub_rate.set(f"{rub / cny:.4f}")
            self.write_log(f"汇率已刷新: 1 USD = {cny:.4f} CNY, {mxn:.4f} MXN")
        except Exception as exc:
            self.show_error("刷新汇率失败", str(exc))

    def refresh_measurements_from_dimensions(self) -> None:
        text = (self.vars.get("dimensions").get() if "dimensions" in self.vars else "") or ""
        nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[,.]\d+)?", text)]
        lowered = text.lower()
        factor = 2.54 if any(mark in lowered for mark in ["inch", "inches", "英寸", "吋"]) else 1.0
        if len(nums) >= 3:
            self.length_cm.set(f"{nums[0] * factor:.1f}")
            self.width_cm.set(f"{nums[1] * factor:.1f}")
            self.height_cm.set(f"{nums[2] * factor:.1f}")
            self.vars["dimensions"].set(
                f"{nums[0] * factor:.1f} x {nums[1] * factor:.1f} x {nums[2] * factor:.1f} cm"
            )
        weight_match = re.search(r"(\d+(?:[,.]\d+)?)\s*(kg|g|克|千克|lb|lbs|磅)", text, re.I)
        if weight_match:
            value = float(weight_match.group(1).replace(",", "."))
            unit = weight_match.group(2).lower()
            if unit in {"g", "克"}:
                value = value / 1000
            elif unit in {"lb", "lbs", "磅"}:
                value = value * 0.453592
            self.weight_kg.set(f"{value:.3f}")

    def calculate_listing_prices(self, show_errors: bool = True) -> None:
        try:
            billable_kg = self.billable_weight_kg()
            russia_freight = billable_kg * self.number_value(self.russia_freight_rate.get())
            if russia_freight:
                self.russia_freight_cny.set(str(round(russia_freight, 2)))
            common_base = self.number_value(self.cost_cny.get()) + self.number_value(self.freight_cny.get())
            prep_fee = self.number_value(self.ml_prep_fee_cny.get())
            margin = self.number_value(self.margin_percent.get()) / 100
            ml_fee = self.number_value(self.ml_commission_percent.get()) / 100
            wb_fee = self.number_value(self.wb_commission_percent.get()) / 100
            usd_cny = self.number_value(self.usd_cny_rate.get())
            mxn_usd = self.number_value(self.mxn_rate.get())
            rub = self.number_value(self.rub_rate.get())
            ml_shipping_usd = self.number_value(self.ml_shipping_usd.get())
            ml_shipping_cny = ml_shipping_usd * usd_cny
            ml_base = common_base + prep_fee + ml_shipping_cny
            wb_base = common_base + self.number_value(self.russia_freight_cny.get())
            if common_base <= 0:
                raise RuntimeError("请先填写采购成本和物流成本。")
            ml_denominator = 1 - ml_fee - margin
            wb_denominator = 1 - wb_fee - margin
            if ml_denominator <= 0 or wb_denominator <= 0:
                raise RuntimeError("目标利润率 + 平台佣金不能大于等于 100%。")
            ml_price_usd = ml_base / ml_denominator / usd_cny if usd_cny else 0
            ml_price_mxn = ml_price_usd * mxn_usd
            wb_price = wb_base / wb_denominator * rub
            ml_revenue_cny = ml_price_usd * usd_cny
            ml_commission_cny = ml_revenue_cny * ml_fee
            ml_profit_cny = ml_revenue_cny - ml_commission_cny - ml_base
            ml_net_proceeds_cny = ml_revenue_cny - ml_commission_cny - ml_shipping_cny
            ml_profit_percent = (ml_profit_cny / ml_revenue_cny * 100) if ml_revenue_cny else 0
            self.mx_price.set(str(round(ml_price_mxn, 2)))
            self.ml_price_usd.set(f"≈ {ml_price_usd:.2f} USD")
            self.ml_profit_cny.set(f"{ml_profit_cny:.2f} CNY")
            self.ml_profit_usd.set(f"{(ml_profit_cny / usd_cny if usd_cny else 0):.2f} USD")
            self.ml_net_proceeds_usd.set(f"{(ml_net_proceeds_cny / usd_cny if usd_cny else 0):.2f} USD")
            self.ml_profit_percent_text.set(f"{ml_profit_percent:.2f}%")
            self.ru_price.set(str(int(round(wb_price, 0))))
            if show_errors:
                self.write_log("核价完成，已填入墨西哥价格和俄罗斯价格。")
        except Exception as exc:
            if show_errors:
                self.show_error("核价失败", str(exc))

    def reverse_price_from_profit(self) -> None:
        try:
            target_profit = self.number_value(re.sub(r"[^\d.,-]", "", self.ml_profit_cny.get()))
            common_base = self.number_value(self.cost_cny.get())
            prep_fee = self.number_value(self.ml_prep_fee_cny.get())
            usd_cny = self.number_value(self.usd_cny_rate.get())
            mxn_usd = self.number_value(self.mxn_rate.get())
            ml_fee = self.number_value(self.ml_commission_percent.get()) / 100
            ml_shipping_cny = self.number_value(self.ml_shipping_usd.get()) * usd_cny
            if not usd_cny or not mxn_usd:
                raise RuntimeError("请先填写 USD/CNY 和 MXN/USD 汇率。")
            denominator = 1 - ml_fee
            if denominator <= 0:
                raise RuntimeError("美客多佣金不能大于等于 100%。")
            revenue_cny = (common_base + prep_fee + ml_shipping_cny + target_profit) / denominator
            price_usd = revenue_cny / usd_cny
            self.mx_price.set(str(round(price_usd * mxn_usd, 2)))
            self.ml_price_usd.set(f"≈ {price_usd:.2f} USD")
            self.ml_profit_usd.set(f"{(target_profit / usd_cny):.2f} USD")
            self.ml_net_proceeds_usd.set(f"{((revenue_cny * (1 - ml_fee) - ml_shipping_cny) / usd_cny):.2f} USD")
            self.ml_profit_percent_text.set(f"{(target_profit / revenue_cny * 100 if revenue_cny else 0):.2f}%")
            self.write_log("已按输入利润倒推出墨西哥售价。")
        except Exception as exc:
            self.show_error("利润倒推失败", str(exc))

    def reverse_price_from_net_proceeds(self) -> None:
        try:
            target_net_usd = self.number_value(re.sub(r"[^\d.,-]", "", self.ml_net_proceeds_usd.get()))
            usd_cny = self.number_value(self.usd_cny_rate.get())
            mxn_usd = self.number_value(self.mxn_rate.get())
            ml_fee = self.number_value(self.ml_commission_percent.get()) / 100
            ml_shipping_usd = self.number_value(self.ml_shipping_usd.get())
            common_base = self.number_value(self.cost_cny.get())
            prep_fee = self.number_value(self.ml_prep_fee_cny.get())
            if not target_net_usd:
                raise RuntimeError("请先输入目标净收益 USD。")
            if not usd_cny or not mxn_usd:
                raise RuntimeError("请先填写 USD/CNY 和 MXN/USD 汇率。")
            denominator = 1 - ml_fee
            if denominator <= 0:
                raise RuntimeError("美客多佣金不能大于等于 100%。")
            price_usd = (target_net_usd + ml_shipping_usd) / denominator
            revenue_cny = price_usd * usd_cny
            ml_profit_cny = revenue_cny - revenue_cny * ml_fee - ml_shipping_usd * usd_cny - common_base - prep_fee
            self.mx_price.set(str(round(price_usd * mxn_usd, 2)))
            self.ml_price_usd.set(f"≈ {price_usd:.2f} USD")
            self.ml_profit_cny.set(f"{ml_profit_cny:.2f} CNY")
            self.ml_profit_usd.set(f"{(ml_profit_cny / usd_cny):.2f} USD")
            self.ml_profit_percent_text.set(f"{(ml_profit_cny / revenue_cny * 100 if revenue_cny else 0):.2f}%")
            self.write_log("已按美客多净收益 USD 倒推出墨西哥售价。")
        except Exception as exc:
            self.show_error("净收益定价失败", str(exc))

    def show_ml_shipping_table(self) -> None:
        window = Toplevel(self.root)
        window.title("美客多运费表（计费重量kg）")
        window.geometry("520x460")
        Label(window, text="按计费重量匹配标准运费，当前软件只使用 USD 运费计算。", anchor="w").pack(fill=X, padx=12, pady=8)
        table = Text(window, height=20)
        table.pack(fill=BOTH, expand=True, padx=12, pady=(0, 8))
        table.insert("1.0", "重量区间(kg)\t标准运费(USD)\n")
        table.insert(END, "-" * 42 + "\n")
        for weight_range, cost in [
            ("0-0.1", "6.20"),
            ("0.1-0.3", "8.40"),
            ("0.3-0.5", "9.90"),
            ("0.5-1.0", "11.80"),
            ("1.0-2.0", "17.20"),
            ("2.0-3.0", "24.00"),
            ("3.0-5.0", "37.00"),
            ("5.0-10.0", "68.00"),
            ("10.0-15.0", "96.00"),
            ("15.0-20.0", "128.00"),
            ("20.0-30.0", "188.00"),
        ]:
            table.insert(END, f"{weight_range}\t\t{cost}\n")
        table.config(state="disabled")
        Button(window, text="关闭", command=window.destroy).pack(anchor="e", padx=12, pady=(0, 12))

    def fetch_ml_shipping_cost(self) -> None:
        try:
            config = publisher.load_store_config(STORE_CONFIG_PATH)
            cost = publisher.estimate_mercadolibre_shipping(
                config.get("mercadolibre", {}).get("access_token", ""),
                self.ml_zip_from.get().strip(),
                self.ml_zip_to.get().strip(),
                self.length_cm.get(),
                self.width_cm.get(),
                self.height_cm.get(),
                self.weight_kg.get(),
                self.mx_price.get(),
            )
            mxn_rate = self.number_value(self.mxn_rate.get()) or 18
            usd_cost = round(float(cost) / mxn_rate, 2)
            self.ml_shipping_usd.set(str(usd_cost))
            self.ml_shipping_mxn.set("")
            self.write_log(f"美客多官网运费已获取: {cost} MXN")
        except Exception as exc:
            self.show_error("美客多运费查询失败", str(exc))

    def choose_search_result(self, title: str, options: list[tuple[str, str]]) -> tuple[str, str] | None:
        if not options:
            self.show_error("没有结果", "没有查询到可选择的分类。")
            return None
        window = Toplevel(self.root)
        window.title(title)
        window.geometry("720x360")
        choice = {"value": None}
        Label(window, text="优先显示本地缓存结果；中文为辅助映射，括号/竖线后保留平台原始英文路径。", fg="#666").pack(
            anchor="w", padx=10, pady=(8, 0)
        )
        listbox = __import__("tkinter").Listbox(window)
        listbox.pack(fill=BOTH, expand=True, padx=10, pady=10)
        for item_id, name in options:
            listbox.insert(END, f"{item_id}  {name}")

        def select() -> None:
            index = listbox.curselection()
            if index:
                choice["value"] = options[index[0]]
                window.destroy()

        Button(window, text="选择", command=select).pack(anchor="e", padx=10, pady=(0, 10))
        window.wait_window()
        return choice["value"]

    def search_ml_category(self) -> None:
        keyword = " ".join(
            part
            for part in [
                self.vars["name"].get().strip(),
                self.vars["category"].get().strip(),
                self.mx_title_var.get().strip(),
                self.mx_title_alt_var.get().strip(),
            ]
            if part
        )[:220]
        try:
            self.sync_store_config_from_fields()
            config = publisher.load_store_config(STORE_CONFIG_PATH)
            config = self.ensure_mercadolibre_token(config)
            token = config.get("mercadolibre", {}).get("access_token", "")
            options = publisher.search_mercadolibre_categories(keyword, token)
            selected = self.choose_search_result("选择美客多分类", options)
            if selected:
                self.mx_category.set(selected[0])
                self.mx_category_path.set(selected[1].split("  |  ", 1)[0])
                self.load_ml_category_attributes()
                self.write_log(f"已选择美客多分类: {selected[0]} {selected[1]}")
        except Exception as exc:
            self.show_error("美客多分类查询失败", str(exc))

    def sync_ml_category_tree(self) -> None:
        thread = threading.Thread(target=self._sync_ml_category_tree, daemon=True)
        self.reset_cancel()
        thread.start()

    def _sync_ml_category_tree(self) -> None:
        try:
            self.set_progress(0, 100, "正在同步美客多本地类目库...")
            self.sync_store_config_from_fields()
            config = publisher.load_store_config(STORE_CONFIG_PATH)
            token = config.get("mercadolibre", {}).get("access_token", "")
            count = publisher.sync_mercadolibre_category_tree(token)
            self.set_progress(100, 100, "美客多类目库已同步")
            self.write_log(f"美客多本地类目库已同步 {count} 个类目，之后查询会优先本地秒出。")
            self.show_info("同步完成", f"已同步 {count} 个美客多类目到本地。")
        except Exception as exc:
            self.write_log(f"同步美客多类目库失败: {exc}")
            self.show_error("同步类目库失败", str(exc))

    def search_wb_subject(self) -> None:
        keyword = self.vars["category"].get().strip() or self.vars["name"].get().strip()
        self.sync_store_config_from_fields()
        token = publisher.load_store_config(STORE_CONFIG_PATH).get("wildberries", {}).get("content_token", "")
        try:
            options = publisher.search_wildberries_subjects(keyword, token)
            selected = self.choose_search_result("选择 WB 分类", options)
            if selected:
                self.ru_subject.set(selected[0])
                self.write_log(f"已选择 WB Subject: {selected[0]} {selected[1]}")
        except Exception as exc:
            self.show_error("WB 分类查询失败", str(exc))

    def shorten_points(self, points: list[str]) -> list[str]:
        short = []
        for point in points[:6]:
            text = normalize_space(str(point))
            if len(text) > 90:
                text = text[:87].rstrip() + "..."
            if text:
                short.append(text)
        return short

    def simplify_points_cn(self, points: list[str]) -> list[str]:
        simplified = []
        replacements = {
            "portable": "便携",
            "waterproof": "防水",
            "rechargeable": "可充电",
            "easy to use": "易使用",
            "stainless steel": "不锈钢",
            "durable": "耐用",
            "lightweight": "轻便",
        }
        for point in points[:6]:
            text = normalize_space(str(point))
            lowered = text.lower()
            for en, zh in replacements.items():
                if en in lowered:
                    text = zh
                    break
            if len(text) > 36:
                text = text[:34].rstrip() + "…"
            if text and text not in simplified:
                simplified.append(text)
        return simplified

    def bind_api_key(self) -> None:
        key = self.api_key.get().strip()
        if not key:
            messagebox.showwarning("缺少 API Key", "请先输入 API Key。")
            return
        self.app_config["api_provider"] = "DeepSeek"
        self.app_config[self.provider_key_name()] = key
        self.app_config["deepseek_base_url"] = self.deepseek_base_url.get().strip() or "https://api.deepseek.com"
        self.app_config["deepseek_model"] = self.deepseek_model.get().strip() or "deepseek-chat"
        save_app_config(self.app_config)
        self.write_log("DeepSeek API Key 已绑定，下次打开会自动填入。")

    def clear_api_key(self) -> None:
        self.api_key.set("")
        self.app_config[self.provider_key_name()] = ""
        save_app_config(self.app_config)
        self.write_log("DeepSeek API Key 已清除。")

    def bind_openai_api_key(self) -> None:
        key = self.openai_api_key.get().strip()
        if not key:
            messagebox.showwarning("缺少 API Key", "请先输入图片 OpenAI API Key。")
            return
        self.app_config["openai_api_key"] = key
        self.app_config["openai_base_url"] = self.normalized_openai_base_url()
        self.app_config["openai_image_model"] = self.openai_image_model.get().strip() or "gpt-image-1.5"
        save_app_config(self.app_config)
        self.write_log(f"图片 OpenAI API 已绑定，Base URL: {self.app_config['openai_base_url']}")

    def clear_openai_api_key(self) -> None:
        self.openai_api_key.set("")
        self.app_config["openai_api_key"] = ""
        save_app_config(self.app_config)
        self.write_log("图片 OpenAI API Key 已清除。")

    def normalized_openai_base_url(self) -> str:
        base_url = (self.openai_base_url.get().strip() or "https://api.openai.com/v1").rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        return base_url

    def http_error_text(self, exc: Exception) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(exc)
            return f"HTTP {exc.code}: {body[:600]}"
        return str(exc)

    def test_deepseek_api_key(self) -> None:
        key = self.api_key.get().strip()
        if not key:
            messagebox.showwarning("缺少 API Key", "请先输入 DeepSeek API Key。")
            return
        base_url = (self.deepseek_base_url.get().strip() or "https://api.deepseek.com").rstrip("/")
        model = self.deepseek_model.get().strip() or "deepseek-chat"
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                json.loads(response.read().decode("utf-8", errors="replace"))
            self.show_info("检测成功", "DeepSeek Key 可用。")
            self.write_log("DeepSeek Key 检测成功。")
        except Exception as exc:
            detail = self.http_error_text(exc)
            self.write_log(f"DeepSeek Key 检测失败: {detail}")
            self.show_error("DeepSeek 检测失败", detail)

    def test_openai_api_key(self) -> None:
        key = self.openai_api_key.get().strip()
        if not key:
            messagebox.showwarning("缺少 API Key", "请先输入图片 OpenAI API Key。")
            return
        base_url = self.normalized_openai_base_url()
        model = self.openai_image_model.get().strip() or "gpt-image-1.5"
        payload = json.dumps(
            {
                "model": model,
                "prompt": "A simple white background ecommerce product image test, no text.",
                "size": "1024x1024",
                "n": 1,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/images/generations",
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                json.loads(response.read().decode("utf-8", errors="replace"))
            self.app_config["openai_base_url"] = base_url
            self.app_config["openai_api_key"] = key
            self.app_config["openai_image_model"] = self.openai_image_model.get().strip() or "gpt-image-1.5"
            save_app_config(self.app_config)
            self.show_info("检测成功", "图片生成接口可用，已保存。")
            self.write_log(f"OpenAI 图片生成接口检测成功: {base_url}, model={model}")
        except Exception as exc:
            detail = self.http_error_text(exc)
            self.app_config["openai_base_url"] = base_url
            self.app_config["openai_api_key"] = key
            self.app_config["openai_image_model"] = self.openai_image_model.get().strip() or "gpt-image-1.5"
            save_app_config(self.app_config)
            self.write_log(f"OpenAI 图片生成接口检测失败，但配置已保存: {detail}")
            self.show_error(
                "图片接口检测失败",
                "已保存 Base URL 和 Key。本次检测调用 /v1/images/generations。\n\n"
                f"接口返回:\n{detail}",
            )

    def run_url_import(self) -> None:
        url = self.product_url.get().strip()
        if not url:
            messagebox.showwarning("缺少网址", "请先输入产品网址。")
            return
        thread = threading.Thread(target=self._run_url_import, daemon=True)
        thread.start()

    def cancel_current_task(self) -> None:
        self.cancel_requested = True
        self.status_text.set("正在终止当前任务...")
        self.write_log("已请求终止当前任务。")

    def reset_cancel(self) -> None:
        self.cancel_requested = False

    def check_cancelled(self) -> None:
        if self.cancel_requested:
            raise RuntimeError("任务已终止。")

    def _run_url_import(self) -> None:
        try:
            self.reset_cancel()
            self.product = default_product()
            self.source_images = []
            self.latest_plan = None
            self.root.after(0, self.fill_fields)
            self.set_progress(0, 100, "正在打开网页...")
            page_url = self.product_url.get().strip()
            html = fetch_url_html(page_url, self.alibaba_cookie.get().strip())
            self.check_cancelled()
            self.set_progress(25, 100, "正在提取主图...")
            image_urls = extract_product_image_urls(html, page_url, limit=7)
            image_paths = []
            if image_urls:
                try:
                    image_paths = download_images(image_urls, self.output_dir_path() / "source_images")
                except Exception as exc:
                    self.write_log(f"主图下载失败，已保留图片 URL 并继续采集文字信息: {exc}")
            else:
                self.write_log("没有识别到商品主图，已继续采集标题、卖点、尺寸和重量；请稍后手动上传图片或重试链接。")
            self.check_cancelled()
            self.set_progress(70, 100, "正在提取亚马逊卖点...")
            bullets = extract_amazon_bullets(html) if "amazon." in page_url.lower() else []
            page_title = extract_page_title(html)
            price, currency = extract_price_currency(html)
            dimensions, weight_kg = extract_measurements(html)
            product = default_product()
            product["source_images"] = image_paths
            product["source_image_urls"] = image_urls
            product["source_url"] = page_url
            product["source_text"] = html_to_text(html)
            if not product["source_text"].strip() or "安全验证" in html or "slide.1688.com" in html:
                warning = "网页抓取失败：触发了1688滑块验证或被反爬拦截，请在设置中更新可用 Cookie 后重试！"
                self.write_log(warning)
                self.show_error("抓取失败", warning)
                return
            if page_title:
                product["name"] = page_title
                inferred = infer_product_from_title(page_title)
                for key, value in inferred.items():
                    if value:
                        product[key] = value
            if dimensions:
                product["dimensions"] = dimensions
            if weight_kg:
                product["weight_kg"] = weight_kg
            if bullets:
                product["selling_points"] = bullets
            if price:
                product["detected_price"] = price
                product["detected_currency"] = currency
            product = save_product_to_sqlite(product)
            self.product = product
            self.source_images = image_paths
            self.root.after(0, self.fill_fields)
            self.set_progress(100, 100, "网址获取完成")
            self.write_log(f"已下载 {len(image_paths)} 张商品主图。")
            if bullets:
                self.write_log(f"已抓取 {len(bullets)} 条亚马逊卖点。")
            if self.auto_ai_recognition.get() == "1":
                self.schedule_auto_ai_after_import()
            else:
                self.write_log("已关闭自动AI识别，仅完成主图/标题/卖点抓取。")
        except Exception as exc:
            detail = str(exc)
            self.write_log(f"获取网址产品信息失败: {detail}\n{traceback.format_exc()}")
            self.show_error("失败", f"获取网址产品信息失败:\n{detail}")

    def open_store_auth(self) -> None:
        window = Toplevel(self.root)
        window.title("授权店铺")
        window.geometry("1180x510")
        fields: dict[str, StringVar] = {}
        labels: dict[str, StringVar] = {}
        Label(
            window,
            text=(
                "说明：先复制美客多授权链接到当前店铺浏览器打开；授权后复制浏览器地址栏里包含 code= 的完整地址，"
                "粘到“授权后地址 / code”，再点换取Token。后台已授权页面不会显示 code。"
            ),
            anchor="w",
        ).grid(row=0, column=0, columnspan=5, sticky="w", padx=10, pady=(8, 2))

        def add_field(row: int, key: str, label: str, value: str, show: str = "") -> None:
            Label(window, text=label, anchor="w").grid(row=row + 1, column=0, sticky="w", padx=10, pady=6)
            var = StringVar(value=value)
            Entry(window, textvariable=var, show=show, width=50).grid(
                row=row + 1, column=1, sticky="ew", padx=10, pady=6
            )
            fields[key] = var

        ml = self.store_config.get("mercadolibre", {})
        wb = self.store_config.get("wildberries", {})
        ozon = self.store_config.get("ozon", {})
        add_field(0, "ml_app_id", "美客多 App ID", ml.get("app_id", ""))
        add_field(1, "ml_redirect_uri", "美客多 Redirect URI", ml.get("redirect_uri", ""))
        add_field(2, "ml_app_secret", "美客多 App Secret", ml.get("app_secret", ""), "*")
        add_field(3, "ml_code_verifier", "美客多 Code Verifier", ml.get("code_verifier", ""))
        add_field(4, "ml_auth_code", "含 code= 的授权后地址", "")
        add_field(5, "ml_token", "美客多 Access Token", ml.get("access_token", ""), "*")
        add_field(6, "wb_token", "WB Content API Token", wb.get("content_token", ""), "*")
        add_field(7, "ozon_client_id", "Ozon Client-Id", ozon.get("client_id", ""))
        add_field(8, "ozon_api_key", "Ozon Api-Key", ozon.get("api_key", ""), "*")

        def add_get_button(row: int, key: str, command) -> None:
            label_var = StringVar(value=self.store_config.get(key, {}).get("shop_name", "未获取"))
            labels[key] = label_var
            Button(window, text="获取", command=command).grid(row=row + 1, column=2, padx=6, pady=6)
            Label(window, textvariable=label_var, anchor="w").grid(row=row + 1, column=3, sticky="w", padx=6, pady=6)

        def get_ml() -> None:
            try:
                name = publisher.fetch_mercadolibre_shop_name(fields["ml_token"].get().strip())
                labels["mercadolibre"].set(name or "获取成功")
            except Exception as exc:
                labels["mercadolibre"].set("获取失败")
                self.write_log(f"获取美客多店铺失败: {exc}")
                messagebox.showerror("美客多获取失败", str(exc))

        def exchange_ml_token() -> None:
            try:
                token_data = publisher.exchange_mercadolibre_code(
                    fields["ml_app_id"].get().strip(),
                    fields["ml_app_secret"].get().strip(),
                    fields["ml_redirect_uri"].get().strip(),
                    fields["ml_auth_code"].get().strip(),
                    fields["ml_code_verifier"].get().strip(),
                )
                fields["ml_token"].set(token_data["access_token"])
                self.store_config.setdefault("mercadolibre", {})["refresh_token"] = token_data.get("refresh_token", "")
                name = publisher.fetch_mercadolibre_shop_name(token_data["access_token"])
                labels["mercadolibre"].set(name or "获取成功")
                self.write_log("美客多授权码已换取 Access Token。")
            except Exception as exc:
                labels["mercadolibre"].set("换取失败")
                self.write_log(f"美客多换取 Token 失败: {exc}")
                messagebox.showerror("美客多换取 Token 失败", str(exc))

        def get_wb() -> None:
            try:
                name = publisher.fetch_wildberries_shop_name(fields["wb_token"].get().strip())
                labels["wildberries"].set(name or "获取成功")
                if "限流" in (name or ""):
                    messagebox.showinfo(
                        "WB Token 已保存",
                        "WB 返回 429 Too Many Requests，这是接口限流，不是 Token 错误。\n"
                        "请点击“保存授权设置”，稍后再查询店铺名或分类。",
                    )
            except Exception as exc:
                labels["wildberries"].set("获取失败")
                self.write_log(f"获取 WB 店铺失败: {exc}")
                messagebox.showerror("WB 获取失败", str(exc))

        def get_ozon() -> None:
            try:
                name = publisher.fetch_ozon_shop_name(
                    fields["ozon_client_id"].get().strip(),
                    fields["ozon_api_key"].get().strip(),
                )
                labels["ozon"].set(name or "获取成功")
            except Exception as exc:
                labels["ozon"].set("获取失败")
                self.write_log(f"获取 Ozon 店铺失败: {exc}")
                messagebox.showerror("Ozon 获取失败", str(exc))

        add_get_button(5, "mercadolibre", get_ml)
        add_get_button(6, "wildberries", get_wb)
        add_get_button(8, "ozon", get_ozon)

        def copy_url(url: str) -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.write_log(f"链接已复制，请粘贴到当前指纹浏览器地址栏: {url}")

        def open_url(url: str) -> None:
            webbrowser.open(url)
            self.write_log(f"已打开链接: {url}")

        def copy_ml_oauth_url() -> None:
            app_id = fields["ml_app_id"].get().strip()
            redirect_uri = fields["ml_redirect_uri"].get().strip()
            if not app_id or not redirect_uri:
                messagebox.showwarning(
                    "缺少信息",
                    "请先填写美客多 App ID 和 Redirect URI。\n"
                    "这两个值在 Mercado Libre 开发者后台的应用设置里。",
                )
                return
            verifier, challenge = publisher.generate_pkce_pair()
            fields["ml_code_verifier"].set(verifier)
            url = (
                "https://global-selling.mercadolibre.com/authorization?"
                f"response_type=code&client_id={quote(app_id)}"
                f"&redirect_uri={quote(redirect_uri, safe='')}"
                f"&code_challenge={quote(challenge)}&code_challenge_method=S256"
            )
            copy_url(url)
            messagebox.showinfo(
                "下一步",
                "授权链接已复制。\n"
                "请粘贴到当前登录美客多店铺的浏览器地址栏。\n"
                "授权后会跳到 Redirect URI 页面，请复制地址栏里包含 code= 的完整地址。\n"
                "不要重新点复制授权链接，否则 Code Verifier 会变化。",
            )

        Button(
            window,
            text="复制美客多授权链接",
            command=copy_ml_oauth_url,
        ).grid(row=4, column=4, padx=6, pady=6)
        Button(
            window,
            text="换取美客多Token",
            command=exchange_ml_token,
        ).grid(row=5, column=4, padx=6, pady=6)
        Button(
            window,
            text="复制美客多应用管理器",
            command=lambda: copy_url("https://global-selling.mercadolibre.com/devcenter"),
        ).grid(row=1, column=4, padx=6, pady=6)
        Button(
            window,
            text="复制美客多开发文档",
            command=lambda: copy_url("https://global-selling.mercadolibre.com/devsite/manage-sales-global-selling/my-first-application-global-selling"),
        ).grid(row=2, column=4, padx=6, pady=6)
        Button(
            window,
            text="复制 WB Token 链接",
            command=lambda: copy_url("https://seller.wildberries.ru/api-integrations"),
        ).grid(row=7, column=4, padx=6, pady=6)
        Button(
            window,
            text="打开 WB Token 页",
            command=lambda: open_url("https://seller.wildberries.ru/api-integrations"),
        ).grid(row=8, column=4, padx=6, pady=6)
        Button(
            window,
            text="复制 Ozon API 链接",
            command=lambda: copy_url("https://seller.ozon.ru/app/settings/api-keys"),
        ).grid(row=9, column=4, padx=6, pady=6)

        def save() -> None:
            listing = self.store_config.get("listing", {})
            self.store_config = {
                "mercadolibre": {
                    "access_token": fields["ml_token"].get().strip(),
                    "app_id": fields["ml_app_id"].get().strip(),
                    "app_secret": fields["ml_app_secret"].get().strip(),
                    "code_verifier": fields["ml_code_verifier"].get().strip(),
                    "refresh_token": self.store_config.get("mercadolibre", {}).get("refresh_token", ""),
                    "redirect_uri": fields["ml_redirect_uri"].get().strip(),
                    "category_id": self.mx_category.get().strip(),
                    "category_path": self.mx_category_path.get().strip(),
                    "site_id": "MLM",
                    "shop_name": labels["mercadolibre"].get(),
                },
                "wildberries": {
                    "content_token": fields["wb_token"].get().strip(),
                    "subject_id": self.ru_subject.get().strip(),
                    "shop_name": labels["wildberries"].get(),
                },
                "ozon": {
                    "client_id": fields["ozon_client_id"].get().strip(),
                    "api_key": fields["ozon_api_key"].get().strip(),
                    "category_id": self.store_config.get("ozon", {}).get("category_id", ""),
                    "shop_name": labels["ozon"].get(),
                },
                "listing": {
                    "mercadolibre_price": self.mx_price.get().strip(),
                    "wildberries_price": self.ru_price.get().strip(),
                    "upc": self.upc_var.get().strip(),
                    "model": self.model_var.get().strip(),
                    "mercadolibre_attributes": {
                        attr_id: var.get().strip()
                        for attr_id, var in self.ml_attribute_vars.items()
                        if var.get().strip()
                    },
                    "mercadolibre_title": self.ml_listing_title.get().strip(),
                    "package_length_cm": self.length_cm.get().strip(),
                    "package_width_cm": self.width_cm.get().strip(),
                    "package_height_cm": self.height_cm.get().strip(),
                    "package_weight_kg": self.weight_kg.get().strip(),
                    "ozon_price": listing.get("ozon_price", ""),
                    "stock": listing.get("stock", "10"),
                    "sku": listing.get("sku", ""),
                    "usd_cny_rate": self.usd_cny_rate.get().strip(),
                    "mxn_usd_rate": self.mxn_rate.get().strip(),
                    "rub_cny_rate": self.rub_rate.get().strip(),
                    "russia_freight_rate": self.russia_freight_rate.get().strip(),
                    "russia_freight_cny": self.russia_freight_cny.get().strip(),
                    "ml_shipping_usd": self.ml_shipping_usd.get().strip(),
                    "ml_shipping_mxn": self.ml_shipping_mxn.get().strip(),
                    "ml_prep_fee_cny": self.ml_prep_fee_cny.get().strip(),
                    "currency_id": "MXN",
                    "condition": "new",
                    "listing_type_id": self.ml_listing_type.get().strip() or "gold_special",
                    "mercadolibre_logistic_type": self.ml_logistic_type.get().strip() or "remote",
                },
            }
            publisher.save_store_config(STORE_CONFIG_PATH, self.store_config)
            self.write_log("店铺授权/上架设置已保存。")
            window.destroy()

        Button(window, text="保存授权设置", command=save).grid(row=10, column=1, sticky="e", padx=10, pady=14)
        window.grid_columnconfigure(1, weight=1)

    def fill_fields(self) -> None:
        p = self.product
        for key in ["name", "brand", "category", "target_customer", "dimensions"]:
            self.vars[key].set(p.get(key, ""))
        for key in ["selling_points", "package_includes"]:
            self.set_text(key, join_lines(p.get(key, [])))
        supplemental = p.get("supplemental_info") or p.get("source_text", "")
        if p.get("materials"):
            self.materials_var.set("、".join(p.get("materials", [])))
        elif supplemental:
            match = re.search(r"材质[:：]\s*([^\n]+)", supplemental)
            self.materials_var.set(match.group(1).strip() if match else "")
        price = str(p.get("detected_price", "")).strip()
        currency = str(p.get("detected_currency", "")).strip()
        self.detected_price.set(" ".join(part for part in [price, currency] if part))
        self.set_image_urls(list(p.get("source_image_urls", [])))
        self.refresh_source_image_preview()
        self.update_chinese_copy_preview()
        if not self.ml_listing_title.get().strip():
            self.ml_listing_title.set(p.get("name", ""))
        if p.get("weight_kg"):
            self.weight_kg.set(str(p.get("weight_kg")))
        self.refresh_measurements_from_dimensions()

    def update_chinese_copy_preview(self) -> None:
        if not self.zh_copy_text:
            return
        product = self.collect_product() if self.vars else self.product
        bullets = product.get("selling_points", []) or []
        package = product.get("package_includes", []) or []
        mx_title = self.mx_title_var.get().strip() if hasattr(self, "mx_title_var") else ""
        mx_desc = self.mx_desc.get("1.0", END).strip() if hasattr(self, "mx_desc") else ""
        lines = [
            "墨西哥文案中文对比",
            f"西语标题：{mx_title}" if mx_title else "",
            f"中文标题理解：{product.get('name', '').strip()} / {product.get('category', '').strip()} / {product.get('dimensions', '').strip()}",
            "",
            "西语描述：",
            mx_desc,
            "",
            "中文卖点对比：",
        ]
        material = self.materials_var.get().strip()
        if material:
            lines.append(f"材质：{material}")
        if bullets:
            lines.extend(f"- {item}" for item in bullets[:6])
        if package:
            lines.append("")
            lines.append("包装清单：")
            lines.extend(f"- {item}" for item in package[:6])
        self.zh_copy_text.delete("1.0", END)
        self.zh_copy_text.insert("1.0", "\n".join(line for line in lines if line is not None))

    def run_translate_mx_copy(self) -> None:
        if not self.api_key.get().strip():
            messagebox.showwarning("需要 API Key", "翻译墨西哥文案需要先填写 DeepSeek API Key。")
            return
        mx_title = self.mx_title_var.get().strip() if hasattr(self, "mx_title_var") else ""
        mx_desc = self.mx_desc.get("1.0", END).strip() if hasattr(self, "mx_desc") else ""
        if not mx_title and not mx_desc:
            messagebox.showwarning("缺少墨西哥文案", "请先生成或填写墨西哥标题和描述。")
            return
        target = self.translation_language.get().strip() or "中文"
        thread = threading.Thread(target=self._run_translate_mx_copy, args=(mx_title, mx_desc, target), daemon=True)
        thread.start()

    def _run_translate_mx_copy(self, mx_title: str, mx_desc: str, target: str) -> None:
        old_env = self.set_provider_env()
        try:
            self.set_progress(0, 100, f"正在翻译墨西哥文案为 {target}...")
            prompt = f"""请把下面 Mercado Libre Mexico 的标题和描述翻译成 {target}，用于和原文对照。
要求:
- 只翻译当前给出的标题和描述，不新增不存在的卖点。
- 输出结构:
标题翻译:
...

描述翻译:
...

原始标题:
{mx_title}

原始描述:
{mx_desc}
"""
            translated = generator.deepseek_chat_text(prompt, self.deepseek_model.get().strip() or "deepseek-chat")
            self.root.after(0, self._set_translation_preview, target, mx_title, mx_desc, translated)
            self.set_progress(100, 100, "翻译完成")
            self.write_log(f"墨西哥文案已翻译为 {target}。")
        except Exception as exc:
            self.write_log(f"翻译墨西哥文案失败: {exc}")
            self.show_error("翻译失败", str(exc))
        finally:
            self.restore_provider_env(old_env)

    def _set_translation_preview(self, target: str, mx_title: str, mx_desc: str, translated: str) -> None:
        if not self.zh_copy_text:
            return
        self.zh_copy_text.delete("1.0", END)
        self.zh_copy_text.insert(
            "1.0",
            f"输出语言: {target}\n\n墨西哥标题:\n{mx_title}\n\n墨西哥描述:\n{mx_desc}\n\n翻译结果:\n{translated}",
        )

    def set_text(self, key: str, value: str) -> None:
        self.texts[key].delete("1.0", END)
        self.texts[key].insert("1.0", value)

    def get_text(self, key: str) -> str:
        return self.texts[key].get("1.0", END).strip()

    def collect_product(self) -> dict:
        existing = self.product or {}
        dimensions = self.vars["dimensions"].get().strip()
        calc_dims = [self.length_cm.get().strip(), self.width_cm.get().strip(), self.height_cm.get().strip()]
        if all(calc_dims):
            dimensions = f"{calc_dims[0]} x {calc_dims[1]} x {calc_dims[2]} cm"
        return {
            "name": self.vars["name"].get().strip(),
            "brand": self.vars["brand"].get().strip(),
            "category": self.vars["category"].get().strip(),
            "target_customer": self.vars["target_customer"].get().strip(),
            "source_images": self.source_images,
            "source_image_urls": self.get_image_urls(),
            "upc": self.upc_var.get().strip(),
            "model": self.model_var.get().strip(),
            "materials": existing.get("materials", []),
            "dimensions": dimensions,
            "weight_kg": self.weight_kg.get().strip(),
            "colors": existing.get("colors", []),
            "selling_points": split_lines(self.get_text("selling_points")),
            "package_includes": split_lines(self.get_text("package_includes")),
            "source_url": existing.get("source_url", ""),
            "source_text": existing.get("source_text", ""),
            "avoid_claims": [],
            "supplemental_info": "\n".join(
                value
                for value in [
                    f"材质: {self.materials_var.get().strip()}" if self.materials_var.get().strip() else "",
                    f"尺寸: {dimensions}" if dimensions else "",
                    f"价格: {self.detected_price.get().strip()}" if self.detected_price.get().strip() else "",
                ]
                if value
            ),
            "detected_price": self.split_price_currency()[0],
            "detected_currency": self.split_price_currency()[1],
            "marketplace_terms": existing.get("marketplace_terms", {}),
        }

    def sync_store_config_from_fields(self) -> None:
        listing = self.store_config.setdefault("listing", {})
        ml = self.store_config.setdefault("mercadolibre", {})
        wb = self.store_config.setdefault("wildberries", {})
        ml["category_id"] = self.mx_category.get().strip()
        ml["category_path"] = self.mx_category_path.get().strip()
        wb["subject_id"] = self.ru_subject.get().strip()
        listing["mercadolibre_price"] = self.mx_price.get().strip()
        listing["wildberries_price"] = self.ru_price.get().strip()
        listing["mercadolibre_net_proceeds_usd"] = re.sub(r"[^\d.,-]", "", self.ml_net_proceeds_usd.get()).strip()
        listing["upc"] = self.upc_var.get().strip()
        listing["model"] = self.model_var.get().strip()
        listing["mercadolibre_attributes"] = {
            attr_id: var.get().strip()
            for attr_id, var in self.ml_attribute_vars.items()
            if var.get().strip()
        }
        listing["mercadolibre_title"] = self.ml_listing_title.get().strip()
        listing["package_length_cm"] = self.length_cm.get().strip()
        listing["package_width_cm"] = self.width_cm.get().strip()
        listing["package_height_cm"] = self.height_cm.get().strip()
        listing["package_weight_kg"] = self.weight_kg.get().strip()
        listing["sku"] = self.sku_var.get().strip()
        listing["stock"] = self.stock_var.get().strip()
        listing["listing_type_id"] = self.ml_listing_type.get().strip() or "gold_special"
        listing["usd_cny_rate"] = self.usd_cny_rate.get().strip()
        listing["mxn_usd_rate"] = self.mxn_rate.get().strip()
        listing["rub_cny_rate"] = self.rub_rate.get().strip()
        listing["russia_freight_rate"] = self.russia_freight_rate.get().strip()
        listing["russia_freight_cny"] = self.russia_freight_cny.get().strip()
        listing["ml_shipping_usd"] = self.ml_shipping_usd.get().strip()
        listing["ml_shipping_mxn"] = self.ml_shipping_mxn.get().strip()
        listing["mercadolibre_commission_percent"] = self.ml_commission_percent.get().strip()
        listing["listing_type_id"] = self.ml_listing_type.get().strip() or "gold_special"
        listing["mercadolibre_logistic_type"] = self.ml_logistic_type.get().strip() or "remote"
        listing["ml_prep_fee_cny"] = self.ml_prep_fee_cny.get().strip()
        publisher.save_store_config(STORE_CONFIG_PATH, self.store_config)

    def ensure_mercadolibre_token(self, config: dict, force: bool = False) -> dict:
        ml = config.setdefault("mercadolibre", {})
        token = ml.get("access_token", "")
        if token and not force:
            try:
                publisher.fetch_mercadolibre_shop_name(token)
                return config
            except Exception as exc:
                if not publisher.is_mercadolibre_auth_error(exc):
                    raise
        token_data = publisher.refresh_mercadolibre_token(
            ml.get("app_id", ""),
            ml.get("app_secret", ""),
            ml.get("refresh_token", ""),
        )
        ml["access_token"] = token_data["access_token"]
        if token_data.get("refresh_token"):
            ml["refresh_token"] = token_data["refresh_token"]
        self.store_config = config
        publisher.save_store_config(STORE_CONFIG_PATH, config)
        self.write_log("美客多 Access Token 已自动刷新，并会用新 Token 继续本次操作。")
        return config

    def mercadolibre_payload_pictures(self, payload: dict) -> list:
        pictures = payload.get("pictures")
        if pictures:
            return pictures
        sites = payload.get("sites_to_sell")
        if isinstance(sites, list):
            for site in sites:
                if isinstance(site, dict) and site.get("pictures"):
                    return site.get("pictures") or []
        return []

    def choose_images(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择产品参考图片",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp"), ("All files", "*.*")],
        )
        if paths:
            self.source_images = list(paths)
            self.refresh_source_image_preview()
            self.write_log(f"已选择 {len(paths)} 张参考图片。")

    def clear_source_images(self) -> None:
        self.source_images = []
        self.source_gallery_selection.clear()
        product = self.load_product()
        product["source_images"] = []
        product["source_image_urls"] = []
        product = save_product_to_sqlite(product)
        self.product = product
        self.set_image_urls([])
        self.refresh_source_image_preview()
        self.write_log("参考图片已清除。")

    def choose_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择生成结果保存位置")
        if path:
            self.output_dir.set(path)
            self.write_log(f"保存位置: {path}")

    def output_dir_path(self) -> Path:
        return Path(self.output_dir.get().strip() or str(OUTPUT_DIR))

    def save_product(self) -> None:
        product = self.collect_product()
        self.product = save_product_to_sqlite(product)
        self.write_log("已保存到 SQLite 商品库。")

    def run_analysis(self) -> None:
        if not self.source_images and not self.has_current_product_context(self.collect_product()):
            messagebox.showwarning("缺少内容", "请先获取产品信息，或选择参考图片/填写产品名和卖点后再开始识别。")
            return
        self.save_product()
        self.reset_cancel()
        thread = threading.Thread(target=self._run_analysis, daemon=True)
        thread.start()

    def _run_analysis(self) -> None:
        if not self.api_key.get().strip():
            self.show_error("需要 API Key", "自动识别需要先填写 DeepSeek API Key。")
            return

        self.write_log("DeepSeek 通道会读取文字信息；参考图片像素暂不发送。")

        old_env = self.set_provider_env()
        try:
            self.set_progress(0, 100, "正在自动识别产品信息...")
            self.check_cancelled()
            self.write_log("开始自动识别产品信息...")
            product = self.load_product()
            source_text = self.collect_ai_source_text(product)
            if not source_text.strip():
                raise RuntimeError("没有可供 AI 识别的文本内容，请先获取网址内容或填写卖点/包装清单。")
            updated = generator.analyze_product_info(
                existing=product,
                source_text=source_text,
                image_paths=[Path(p) for p in self.source_images],
                provider=self.provider_code(),
                deepseek_model=self.deepseek_model.get().strip() or "deepseek-chat",
            )
            self.check_cancelled()
            updated = save_product_to_sqlite(updated)
            self.product = updated
            self.root.after(0, self.fill_fields)
            notes = updated.get("recognition_notes", [])
            if notes:
                self.write_log("需要人工确认: " + "；".join(notes))
            self.write_log("自动识别完成，已填入表单。请检查后再生成方案。")
            self.set_progress(100, 100, "自动识别完成")
            self.show_info("完成", "自动识别完成，已填入表单。请检查后再生成方案。")
        except Exception as exc:
            detail = traceback.format_exc()
            self.write_log(f"自动识别失败: {exc}\n{detail}")
            self.show_error("自动识别失败", detail[:2000])
        finally:
            self.restore_provider_env(old_env)

    def run_copy_generation(self) -> None:
        if not self.api_key.get().strip():
            messagebox.showwarning("需要 API Key", "生成文案需要先填写 DeepSeek API Key。")
            return
        self.product = self.collect_product()
        if not self.has_current_product_context(self.product):
            messagebox.showwarning(
                "缺少当前商品",
                "请先粘贴产品链接并获取信息，或选择参考图片/填写产品名和卖点后再生成文案。这样可以避免把上一次商品误生成。",
            )
            self.write_log("已阻止生成文案：当前没有链接、参考图片、产品名或卖点。")
            return
        self.write_log(f"当前生成文案对象: {self.product.get('name') or self.product_url.get() or '手动填写商品'}")
        self.reset_cancel()
        thread = threading.Thread(target=self._run_copy_generation, daemon=True)
        thread.start()

    def has_current_product_context(self, product: dict) -> bool:
        name = str(product.get("name") or "").strip()
        bullets = product.get("selling_points") or []
        if isinstance(bullets, str):
            bullets = [bullets]
        has_bullets = any(str(item).strip() for item in bullets)
        has_images = bool(product.get("source_images") or product.get("source_image_urls"))
        has_url = bool(self.product_url.get().strip())
        has_category = bool(str(product.get("category") or "").strip())
        return bool(has_url or has_images or (name and (has_bullets or has_category)))

    def _build_plan(self) -> tuple[dict, dict]:
        product = self.collect_product()
        presets = generator.load_json(RESOURCE_DIR / "presets" / "platforms.json")
        keys = list(presets.keys()) if self.platform.get() == "all" else [self.platform.get()]
        platforms = [generator.PlatformPlan(key=k, preset=presets[k]) for k in keys]
        return product, generator.build_plan(product, platforms)

    def apply_listing_overrides(self, plan: dict) -> dict:
        platforms = plan.setdefault("platforms", {})
        ml_platform = platforms.setdefault("mercadolibre", {})
        ml_listing = ml_platform.setdefault("listing", {})
        ml_title = (
            self.ml_listing_title.get().strip()
            or getattr(self, "mx_title_var", StringVar()).get().strip()
            or self.vars["name"].get().strip()
        )
        if ml_title:
            ml_listing["title"] = self.capitalize_title(ml_title)
        if hasattr(self, "mx_desc"):
            ml_desc = self.mx_desc.get("1.0", END).strip()
            if ml_desc:
                ml_listing["description"] = ml_desc

        wb_platform = platforms.setdefault("wildberries", {})
        wb_listing = wb_platform.setdefault("listing", {})
        if hasattr(self, "ru_title_var"):
            wb_title = self.ru_title_var.get().strip()
            if wb_title:
                wb_listing["title"] = self.capitalize_title(wb_title)
        if hasattr(self, "ru_desc"):
            wb_desc = self.ru_desc.get("1.0", END).strip()
            if wb_desc:
                wb_listing["description"] = wb_desc
        return plan

    def _run_copy_generation(self) -> None:
        old_env = self.set_provider_env()
        plan = None
        try:
            out_dir = self.output_dir_path()
            self.set_progress(0, 100, "开始生成文案...")
            self.check_cancelled()
            self.write_log("开始生成文案...")
            _product, plan = self._build_plan()
            self.set_progress(10, 100, "基础方案已生成")
            generator.refine_listing_copy(
                plan,
                "gpt-5.4-mini",
                progress_callback=lambda current, total, message: self.set_progress(
                    10 + current,
                    10 + total,
                    message,
                ),
                provider=self.provider_code(),
                deepseek_model=self.deepseek_model.get().strip() or "deepseek-chat",
            )
            self.check_cancelled()

            self.latest_plan = plan
            self.show_plan_preview(plan)

            self.set_progress(100, 100, "生成完成")
            self.write_log("文案已生成并显示在页面中。")
            self.show_info("完成", "文案已生成并显示在下方。")
        except Exception as exc:
            detail = str(exc)
            self.write_log(f"生成文案失败: {detail}")
            if plan:
                self.latest_plan = plan
                self.show_plan_preview(plan)
                self.set_progress(100, 100, "API失败，已显示规则版文案")
                self.show_error(
                    "生成文案失败",
                    detail + "\n\n已先显示不调用 API 的规则版标题和描述，你可以继续编辑或稍后重试。",
                )
            else:
                self.show_error("生成文案失败", detail)
        finally:
            self.restore_provider_env(old_env)

    def run_image_generation(self) -> None:
        if self.image_generation_mode.get() == "ChatGPT提示词":
            self.run_manual_image_prompts()
            return
        if not self.openai_api_key.get().strip():
            messagebox.showwarning("需要 API Key", "API 生图需要先填写图片 OpenAI API Key。")
            return
        self.save_product()
        self.reset_cancel()
        thread = threading.Thread(target=self._run_image_generation, daemon=True)
        thread.start()

    def run_copy_based_api_image_generation(self) -> None:
        self.image_generation_mode.set("API生成")
        self.run_image_generation()

    def translate_generated_images_prompt(self) -> None:
        target = self.image_translate_language.get().strip() or "俄语"
        image_count = len(self.get_image_urls()) if hasattr(self, "get_image_urls") else 0
        prompt = (
            f"请基于我上传的 {image_count or '多张'} 张已生成电商图片，把图片里的文字翻译并替换为{target}。\n"
            "保持原有产品、构图、颜色、卖点层级和平台风格不变，只替换文字语言。\n"
            "不要改变产品外观，不要新增 Logo、水印、二维码、价格或虚假认证。\n"
            "请逐张输出独立图片，保持 1200x1200 方图，适合直接作为平台商品图。"
        )
        if self.image_task_prompt_text:
            self.image_task_prompt_text.delete("1.0", END)
            self.image_task_prompt_text.insert("1.0", prompt)
        self.image_task_status.set(f"已生成翻译修图提示词：{target}")
        self.open_chatgpt_and_try_send(prompt)

    def run_manual_image_prompts(self) -> None:
        try:
            self.reset_cancel()
            self.set_progress(0, 100, "正在生成 ChatGPT 图片提示词...")
            prompts = self.build_manual_image_prompts()
            if self.image_task_prompt_text and prompts:
                task_text = "\n\n---\n\n".join(f"{item['label']}\n{item['prompt']}" for item in prompts)
                self.image_task_prompt_text.delete("1.0", END)
                self.image_task_prompt_text.insert("1.0", task_text)
                self.image_task_status.set("生图提示词已生成")
            self.set_progress(100, 100, "ChatGPT 图片提示词已生成")
            self.open_manual_prompt_window(prompts)
            self.write_log(f"已生成 {len(prompts)} 条 ChatGPT 图片提示词。")
        except Exception as exc:
            self.write_log(f"生成 ChatGPT 图片提示词失败: {exc}")
            self.show_error("失败", "生成 ChatGPT 图片提示词失败，请查看运行日志。")

    def selected_image_market_info(self) -> dict[str, str]:
        selected = self.image_market.get().strip()
        mapping = {
            "美客多墨西哥": {
                "platform_key": "mercadolibre",
                "market": "Mercado Libre Mexico / 美客多墨西哥",
                "language": "Mexican Spanish",
                "aesthetic": "clean marketplace style, bright white background, practical Spanish text, mobile-first square images",
            },
            "WB俄罗斯": {
                "platform_key": "wildberries",
                "market": "Wildberries Russia / WB俄罗斯",
                "language": "Russian",
                "aesthetic": "Russian ecommerce style, clear benefits, high contrast text blocks, clean product detail layout",
            },
            "Ozon俄罗斯": {
                "platform_key": "wildberries",
                "market": "Ozon Russia / Ozon俄罗斯",
                "language": "Russian",
                "aesthetic": "Ozon-style Russian ecommerce images, clean blue-white layout, concise feature labels",
            },
            "全部平台": {
                "platform_key": "all",
                "market": "All selected marketplaces",
                "language": self.media_language.get().strip() or "Mexican Spanish",
                "aesthetic": "neutral cross-border ecommerce style, clean and reusable across marketplaces",
            },
        }
        return mapping.get(selected, mapping["美客多墨西哥"])

    def filter_plan_for_image_market(self, plan: dict) -> dict:
        info = self.selected_image_market_info()
        platform_key = info.get("platform_key", "mercadolibre")
        if platform_key == "all":
            return plan
        filtered = dict(plan)
        platforms = plan.get("platforms", {})
        if platform_key in platforms:
            filtered["platforms"] = {platform_key: platforms[platform_key]}
        return filtered

    def product_image_scenes(self, product: dict, count: int) -> list[tuple[str, str, str]]:
        text = " ".join(
            [
                str(product.get("name", "")),
                str(product.get("category", "")),
                " ".join(product.get("selling_points", []) or []),
            ]
        ).lower()
        if any(word in text for word in ["drawer", "organizer", "storage", "收纳", "抽屉", "整理"]):
            scenes = [
                ("01", "白底主图", "white background, full product set centered, no text"),
                ("02", "数量展示", "show all pieces and size variants clearly, no collage frame"),
                ("03", "尺寸规格", "clean size image with simple measurement arrows"),
                ("04", "材质细节", "close-up of transparent material, edges and divider structure"),
                ("05", "使用场景", "real home drawer organization scene, product visible in use"),
                ("06", "透明可见", "product in drawer with visible contents, neat cosmetic or desk items"),
                ("07", "调节方式", "hands adjusting divider or extending the organizer inside drawer"),
                ("08", "包装清单", "product set and included pieces on clean background"),
                ("09", "空间对比", "before and after drawer organization, one clean scene"),
                ("10", "移动端详情", "square mobile-style product detail image with one short benefit"),
            ]
        else:
            scenes = [
                ("01", "白底主图", "white background, full set, product centered, no text"),
                ("02", "数量展示", "show all pieces clearly on white background, no collage frame"),
                ("03", "尺寸规格", "one clean product size image, simple measurement arrows"),
                ("04", "材质细节", "close-up product detail, material and connector visible"),
                ("05", "使用场景", "realistic product lifestyle scene, product visible in use"),
                ("06", "核心卖点", "show one key benefit in a clean ecommerce composition"),
                ("07", "安装方式", "hands using or installing the product, one clean step scene"),
                ("08", "包装清单", "product set and included pieces on clean background"),
                ("09", "型号对比", "three sizes or types arranged cleanly, no grid collage"),
                ("10", "移动端详情", "square mobile-style product detail image with product and one short benefit"),
            ]
        return scenes[:count]

    def precise_image_prompt(
        self,
        product: dict,
        platform: dict,
        market: str,
        language: str,
        aesthetic: str,
        scenes: list[tuple[str, str, str]],
        reference_count: int,
    ) -> str:
        listing = platform.get("listing", {})
        title = listing.get("title") or self.vars.get("name", StringVar()).get()
        description = listing.get("description") or ""
        points = "\n".join(f"- {item}" for item in (product.get("selling_points") or [])[:8])
        package = "\n".join(f"- {item}" for item in (product.get("package_includes") or [])[:6])
        scene_lines = "\n".join(f"{idx}. {name}: {brief}" for idx, name, brief in scenes)
        return f"""请根据我上传的 {reference_count or 7} 张原始产品图，生成 {len(scenes)} 张独立电商图片。
平台/国家: {market}
图片文字语言: {language}
审美方向: {aesthetic}

先判断参考图中的真实商品是什么，再开始生成。必须保持参考图里的商品外观、结构、颜色、材质、比例和套装数量，不要换成别的产品，不要沿用旧商品记忆。

当前商品信息:
产品名: {product.get('name', '')}
品牌: {product.get('brand', '')}
品类: {product.get('category', '')}
目标买家: {product.get('target_customer', '')}
尺寸/规格: {product.get('dimensions', '')}
材质: {self.materials_var.get().strip()}
包装清单:
{package}

已生成的平台标题:
{title}

已生成的平台描述:
{description}

需要体现的卖点:
{points}

生成原则:
1. 输出 {len(scenes)} 张独立图片，不要拼图、不要九宫格、不要长图。
2. 主图保持干净真实；详情图要有少量清晰卖点文字，不要空白无信息。
3. 每张图围绕当前商品设计，可以按商品特点微调构图，不要机械套模板。
4. 不要生成平台 Logo、水印、二维码、价格、虚假认证、夸张促销标。
5. 不要新增参考图没有的配件、按钮、功能、结构。
6. 如果一次只能生成一张，请连续生成，直到完成 {len(scenes)} 张。

建议图片方向，可按产品实际情况灵活调整:
{scene_lines}
"""

    def build_manual_image_prompts(self) -> list[dict[str, str]]:
        count = max(1, min(10, int(self.image_count.get() or "10")))
        product = self.collect_product()
        if self.latest_plan:
            plan = json.loads(json.dumps(self.latest_plan, ensure_ascii=False))
            plan["product"] = product
            plan = self.apply_listing_overrides(plan)
        else:
            _product, plan = self._build_plan()
        plan = self.filter_plan_for_image_market(plan)
        market_info = self.selected_image_market_info()
        reference_count = len([path for path in product.get("source_images", []) if Path(path).exists()])
        points = "；".join(product.get("selling_points", [])[:5])
        package = "；".join(product.get("package_includes", [])[:3])
        scenes = self.product_image_scenes(product, count)
        prompts: list[dict[str, str]] = []
        for platform_key, platform in plan["platforms"].items():
            display_name = platform.get("display_name", platform_key)
            if market_info.get("platform_key") != "all":
                language = market_info["language"]
                market = market_info["market"]
                aesthetic = market_info["aesthetic"]
            elif platform_key == "wildberries":
                language = "Russian"
                market = "Wildberries / Ozon"
                aesthetic = "Russian ecommerce style, clean product cards and concise benefit labels"
            elif platform_key == "mercadolibre":
                language = "Mexican Spanish"
                market = "Mercado Libre Mexico"
                aesthetic = "Mexican marketplace style, bright clean square detail images"
            else:
                language = self.media_language.get().strip() or "Mexican Spanish"
                market = display_name
                aesthetic = "neutral ecommerce marketplace style"
            scene_lines = "\n".join(
                f"{num}. {name}: {brief}." for num, name, brief in scenes
            )
            prompt = (
                f"请连续生成 {len(scenes)} 张【独立商品图片】，不是一张拼图。\n"
                f"平台: {market}\n"
                f"图片文字语言: {language}\n\n"
                f"平台审美: {aesthetic}\n\n"
                "硬性规则:\n"
                f"1. 必须调用图片生成工具 {len(scenes)} 次，每次只生成 1 张独立图片。\n"
                f"2. 不要九宫格，不要10宫格，不要总览图，不要把多张图合到一个画布。\n"
                f"3. 如果系统一次只能出1张，请先生成第1张，完成后自动继续第2张，直到第{len(scenes)}张。\n"
                "4. 每张图都必须能单独下载。\n\n"
                f"参考图: 我已上传/会上传 {reference_count or 5} 张原产品图。只能参考原图里的产品外观、结构、颜色、材质和比例，不要换产品，不要新增配件。\n\n"
                f"产品: {product.get('name', '')}\n"
                f"品牌: {product.get('brand', '')}\n"
                f"品类: {product.get('category', '')}\n"
                f"卖点: {points}\n"
                f"包装: {package}\n\n"
                f"请按下面清单逐张生成，每张 1200x1200，商业高清、干净真实、无水印、无平台Logo、无二维码、无价格:\n"
                f"{scene_lines}\n\n"
                "再次确认: 输出必须是多张独立图片，不是一个包含10个版面的设计稿。"
            )
            prompt = self.precise_image_prompt(product, platform, market, language, aesthetic, scenes, reference_count)
            prompts.append(
                {
                    "label": f"{display_name} {len(scenes)}张独立图精简提示词",
                    "prompt": prompt,
                }
            )
        return prompts

    def open_manual_prompt_window(self, prompts: list[dict[str, str]]) -> None:
        if not prompts:
            messagebox.showwarning("无提示词", "没有生成可用的图片提示词。")
            return
        window = Toplevel(self.root)
        window.title("ChatGPT 手动生成图片提示词")
        window.geometry("980x680")

        Label(
            window,
            text="先在 ChatGPT 上传原始产品图片，再复制下面的提示词发送。这个模式不消耗 API 额度。",
            anchor="w",
        ).pack(fill=X, padx=10, pady=(10, 4))

        selector_row = Frame(window, padx=10, pady=6)
        selector_row.pack(fill=X)
        selected = StringVar(value=prompts[0]["label"])
        selector = Combobox(
            selector_row,
            textvariable=selected,
            values=[item["label"] for item in prompts],
            state="readonly",
            width=42,
        )
        selector.pack(side=LEFT)

        text = Text(window, height=24, wrap="word")
        text.pack(fill=BOTH, expand=True, padx=10, pady=(0, 8))

        def current_prompt() -> str:
            label = selected.get()
            for item in prompts:
                if item["label"] == label:
                    return item["prompt"]
            return prompts[0]["prompt"]

        def refresh_prompt(_event: object | None = None) -> None:
            text.delete("1.0", END)
            text.insert("1.0", current_prompt())

        def copy_current() -> None:
            self.copy_to_clipboard(current_prompt(), "当前图片提示词已复制。")

        def copy_all() -> None:
            all_text = "\n\n---\n\n".join(
                f"{item['label']}\n{item['prompt']}" for item in prompts
            )
            self.copy_to_clipboard(all_text, "全部图片提示词已复制。")

        selector.bind("<<ComboboxSelected>>", refresh_prompt)
        Button(selector_row, text="复制当前提示词", command=copy_current).pack(side=LEFT, padx=8)
        Button(selector_row, text="复制全部提示词", command=copy_all).pack(side=LEFT, padx=4)
        Button(
            selector_row,
            text="打开GPT并尝试发送",
            command=lambda: self.open_chatgpt_and_try_send(current_prompt()),
        ).pack(side=LEFT, padx=4)
        Button(
            selector_row,
            text="打开原图文件夹",
            command=self.open_source_images_folder,
        ).pack(side=LEFT, padx=4)
        Button(
            selector_row,
            text="导入生成图片",
            command=self.import_generated_images,
        ).pack(side=LEFT, padx=4)
        Button(
            selector_row,
            text="打开 ChatGPT",
            command=lambda: webbrowser.open("https://chatgpt.com/"),
        ).pack(side=LEFT, padx=4)
        refresh_prompt()

    def _media_progress(self, current: int, total: int, message: str) -> None:
        self.set_progress(current, total, message)
        self.check_cancelled()

    def _prepare_media_plan(self) -> tuple[dict, dict, Path]:
        out_dir = self.output_dir_path()
        product, plan = self._build_plan()
        if self.latest_plan:
            plan = self.latest_plan
        plan = self.filter_plan_for_image_market(plan)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "media_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        generator.write_storyboard(plan, out_dir)
        return product, plan, out_dir

    def _run_image_generation(self) -> None:
        old_key = os.environ.get("OPENAI_API_KEY")
        old_base_url = os.environ.get("OPENAI_BASE_URL")
        old_image_model = os.environ.get("OPENAI_IMAGE_MODEL")
        try:
            os.environ["OPENAI_API_KEY"] = self.openai_api_key.get().strip()
            os.environ["OPENAI_BASE_URL"] = self.normalized_openai_base_url()
            os.environ["OPENAI_IMAGE_MODEL"] = self.openai_image_model.get().strip() or "gpt-image-1.5"
            count = max(1, min(10, int(self.image_count.get() or "10")))
            self.set_progress(0, 100, "开始生成图片...")
            self.check_cancelled()
            product, plan, out_dir = self._prepare_media_plan()
            generator.generate_assets(
                plan,
                product,
                out_dir,
                progress_callback=self._media_progress,
                generate_images=True,
                generate_videos=False,
                image_count=count,
                prompt_language=self.media_language.get(),
            )
            self.check_cancelled()
            self.set_progress(100, 100, "图片生成完成")
            generated_paths = [str(path) for path in sorted(out_dir.glob("*/*.png"))]
            if generated_paths:
                self.root.after(0, lambda paths=generated_paths: self.import_generated_image_paths(paths, replace=True))
            self.write_log(f"图片已生成，保存位置: {out_dir}")
            self.show_info("完成", f"图片已生成。\n保存位置: {out_dir}")
        except Exception as exc:
            detail = self.http_error_text(exc)
            self.write_log(f"生成图片失败: {detail}")
            self.set_progress(100, 100, "生成图片失败")
            self.show_error("生成图片失败", detail)
        finally:
            if old_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_key
            if old_base_url is None:
                os.environ.pop("OPENAI_BASE_URL", None)
            else:
                os.environ["OPENAI_BASE_URL"] = old_base_url
            if old_image_model is None:
                os.environ.pop("OPENAI_IMAGE_MODEL", None)
            else:
                os.environ["OPENAI_IMAGE_MODEL"] = old_image_model

    def run_video_generation(self) -> None:
        if not self.openai_api_key.get().strip():
            messagebox.showwarning("需要 API Key", "生成视频需要先填写图片 OpenAI API Key。")
            return
        self.save_product()
        self.reset_cancel()
        thread = threading.Thread(target=self._run_video_generation, daemon=True)
        thread.start()

    def _run_video_generation(self) -> None:
        old_key = os.environ.get("OPENAI_API_KEY")
        old_base_url = os.environ.get("OPENAI_BASE_URL")
        old_image_model = os.environ.get("OPENAI_IMAGE_MODEL")
        try:
            os.environ["OPENAI_API_KEY"] = self.openai_api_key.get().strip()
            os.environ["OPENAI_BASE_URL"] = self.normalized_openai_base_url()
            os.environ["OPENAI_IMAGE_MODEL"] = self.openai_image_model.get().strip() or "gpt-image-1.5"
            self.set_progress(0, 100, "开始生成视频...")
            self.check_cancelled()
            product, plan, out_dir = self._prepare_media_plan()
            generator.generate_assets(
                plan,
                product,
                out_dir,
                progress_callback=self._media_progress,
                generate_images=False,
                generate_videos=True,
                prompt_language=self.media_language.get(),
            )
            self.check_cancelled()
            self.set_progress(100, 100, "视频生成完成")
            self.write_log(f"视频已生成，保存位置: {out_dir}")
            self.show_info("完成", f"视频已生成。\n保存位置: {out_dir}")
        except Exception as exc:
            detail = self.http_error_text(exc)
            self.write_log(f"生成视频失败: {detail}")
            self.set_progress(100, 100, "生成视频失败")
            self.show_error("生成视频失败", detail)
        finally:
            if old_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_key
            if old_base_url is None:
                os.environ.pop("OPENAI_BASE_URL", None)
            else:
                os.environ["OPENAI_BASE_URL"] = old_base_url
            if old_image_model is None:
                os.environ.pop("OPENAI_IMAGE_MODEL", None)
            else:
                os.environ["OPENAI_IMAGE_MODEL"] = old_image_model

    def run_listing_draft(self) -> None:
        self.auto_fill_listing_identity()
        self.product = self.collect_product()
        self.reset_cancel()
        thread = threading.Thread(target=self._run_listing_draft, daemon=True)
        thread.start()

    def _run_listing_draft(self) -> None:
        try:
            self.sync_store_config_from_fields()
            self.set_progress(0, 100, "正在预检上架信息...")
            self.check_cancelled()
            product, plan = self._build_plan()
            if self.latest_plan:
                plan = self.latest_plan
            plan = self.apply_listing_overrides(plan)

            config = publisher.load_store_config(STORE_CONFIG_PATH)
            if config.get("mercadolibre", {}).get("refresh_token"):
                try:
                    config = self.ensure_mercadolibre_token(config)
                except Exception as exc:
                    self.write_log(f"美客多 Token 自动刷新/验证失败: {exc}")
            ml_required = []
            wb_required = []
            try:
                ml_required = publisher.required_mercadolibre_attributes(
                    config["mercadolibre"].get("category_id", ""),
                    config["mercadolibre"].get("access_token", ""),
                )
            except Exception as exc:
                self.write_log(f"读取美客多必填属性失败: {exc}")
            self.set_progress(35, 100, "正在读取平台必填项...")
            self.check_cancelled()
            try:
                wb_required = publisher.required_wildberries_characteristics(
                    config["wildberries"].get("subject_id", ""),
                    config["wildberries"].get("content_token", ""),
                )
            except Exception as exc:
                self.write_log(f"读取 WB 必填特征失败: {exc}")

            draft = {
                "mercadolibre": {
                    "required_attributes": ml_required,
                    "payload": publisher.build_mercadolibre_payload(product, plan, config, self.get_public_image_urls()),
                },
                "wildberries": {
                    "required_characteristics": wb_required,
                    "payload": publisher.build_wildberries_payload(product, plan, config),
                },
                "ozon": {
                    "payload": publisher.build_ozon_payload(product, plan, config),
                    "notes": [
                        "Ozon 已接入授权校验和草稿结构。",
                        "Ozon 正式发布还需要类目属性映射、图片 URL、价格/库存接口校验后再开启确认发布。",
                    ],
                },
                "notes": [
                    "发布前请确认类目 ID、Subject ID、价格、库存、SKU、必填属性和图片 URL。",
                    "美客多图片发布通常需要公网可访问图片 URL；本地生成图片请先上传到可访问图床或平台媒体接口。",
                ],
            }
            issues = self.collect_listing_issues(draft, config)
            details = []
            for platform, items in issues.items():
                details.append(f"{platform}: " + "、".join(items))
            if ml_required:
                details.append("美客多必填属性: " + "、".join(str(item.get("name") or item.get("id")) for item in ml_required[:10]))
            if wb_required:
                details.append("WB必填特征: " + "、".join(str(item.get("name") or item.get("id")) for item in wb_required[:10]))
            self.set_progress(100, 100, "预检完成")
            message = "\n".join(details) if details else "预检通过，未发现缺项。"
            self.write_log("上架预检完成: " + message.replace("\n", " | "))
            self.show_info("上架预检", message)
        except Exception as exc:
            self.write_log(f"上架预检失败: {exc}")
            self.show_error("失败", f"上架预检失败:\n{exc}")

    def _handle_mercadolibre_publish_error(self, parsed: dict, original_exc: Exception) -> None:
        self.root.after(0, lambda: self._handle_mercadolibre_publish_error_ui(parsed, original_exc))

    def _handle_mercadolibre_publish_error_ui(self, parsed: dict, original_exc: Exception) -> None:
        """解析美客多发布失败错误，把缺失属性和表单字段高亮回填到界面。"""
        error_code = parsed.get("error", "")
        message = parsed.get("message", str(original_exc))
        missing_attrs = [str(item) for item in parsed.get("missing_attributes", []) if str(item).strip()]
        missing_fields = [str(item) for item in parsed.get("missing_fields", []) if str(item).strip()]
        cause_list = parsed.get("cause", [])

        self._set_ml_missing_fields(missing_attrs, missing_fields)
        self._highlight_ml_publish_fields(missing_fields, True)

        lines: list[str] = []
        if error_code:
            lines.append(f"错误码: {error_code}")
        lines.append(f"平台返回: {message}")

        if missing_fields:
            lines.append("")
            lines.append("缺失或不符合要求的表单字段:")
            lines.extend(f"  • {field}" for field in missing_fields[:20])

        if missing_attrs:
            lines.append("")
            lines.append("缺失或不符合要求的类目属性:")
            for attr_id in missing_attrs[:20]:
                in_ui = "（已在属性框中）" if attr_id in self.ml_attribute_vars else "（请重新读取属性后补填）"
                lines.append(f"  • {attr_id} {in_ui}")

        if cause_list:
            lines.append("")
            lines.append("平台原始原因:")
            for item in cause_list[:8]:
                if isinstance(item, dict):
                    field = item.get("field") or item.get("id") or item.get("code") or ""
                    msg = item.get("message") or item.get("error") or ""
                    if field or msg:
                        lines.append(f"  • {field}: {msg}" if field and msg else f"  • {field or msg}")

        friendly_msg = "\n".join(lines)
        self.write_log(f"美客多发布失败: {friendly_msg.replace(chr(10), ' | ')}")

        if (missing_attrs or missing_fields) and hasattr(self, "_ml_attrs_canvas"):
            self._ml_attrs_canvas.yview_moveto(0)

        messagebox.showerror("美客多发布失败", friendly_msg[:2000])

    def run_publish_listing(self, platform: str = "all") -> None:
        self.auto_fill_listing_identity()
        platform_name = {"mercadolibre": "美客多", "wildberries": "WB", "all": "全部平台"}.get(platform, platform)
        if not messagebox.askyesno(
            "确认上架",
            f"即将调用{platform_name}接口发布商品。请确认类目、价格、库存、SKU 和必填属性已经检查无误。是否继续？",
        ):
            return
        thread = threading.Thread(target=self._run_publish_listing, args=(platform,), daemon=True)
        self.reset_cancel()
        thread.start()

    def show_platform_preview(self, platform_name: str) -> None:
        self.show_info(f"{platform_name} 预留接口", f"{platform_name} 上架接口已预留，后续可继续接入发布流程。")

    def build_current_listing_draft(self, config: dict) -> dict:
        product, plan = self._build_plan()
        if self.latest_plan:
            plan = self.latest_plan
        plan = self.apply_listing_overrides(plan)
        return {
            "mercadolibre": {
                "payload": publisher.build_mercadolibre_payload(product, plan, config, self.get_public_image_urls()),
            },
            "wildberries": {
                "payload": publisher.build_wildberries_payload(product, plan, config),
            },
        }

    def collect_listing_issues(self, draft: dict, config: dict) -> dict[str, list[str]]:
        issues: dict[str, list[str]] = {}
        ml_payload = draft["mercadolibre"]["payload"]
        ml_missing = []
        if not config["mercadolibre"].get("access_token"):
            ml_missing.append("美客多 Access Token")
        if not config["mercadolibre"].get("category_id") or not ml_payload.get("category_id"):
            ml_missing.append("美客多类目 ID")
        if not ml_payload.get("title"):
            ml_missing.append("美客多标题 title")
        for attr_id, label in [
            ("BRAND", "Brand"),
            ("MODEL", "Model"),
            ("PACKAGE_HEIGHT", "Package height"),
            ("PACKAGE_WIDTH", "Package width"),
            ("PACKAGE_LENGTH", "Package length"),
            ("PACKAGE_WEIGHT", "Package weight"),
            ("GTIN", "Universal product code"),
        ]:
            if not any(item.get("id") == attr_id and item.get("value_name") for item in ml_payload.get("attributes", [])):
                ml_missing.append(label)
        if not ml_payload.get("price"):
            ml_missing.append("墨西哥价格")
        for field, label in [
            ("package_length", "包裹长 package_length"),
            ("package_width", "包裹宽 package_width"),
            ("package_height", "包裹高 package_height"),
            ("package_weight", "包裹重量 package_weight"),
        ]:
            if not ml_payload.get(field):
                ml_missing.append(label)
        if ml_payload.get("category_id") and not str(ml_payload.get("category_id", "")).startswith("CBT"):
            ml_missing.append("美客多 Global Selling 类目必须是 CBT 开头，请重新查询/选择分类")
        if not self.mercadolibre_payload_pictures(ml_payload):
            if self.get_image_urls():
                ml_missing.append("公网图片 URL（本地 file URL 不能发布）")
            else:
                ml_missing.append("图片 URL")
        if ml_missing:
            issues["美客多"] = ml_missing

        wb_payload = draft["wildberries"]["payload"]
        wb_missing = []
        if not config["wildberries"].get("content_token"):
            wb_missing.append("WB Token")
        if not config["wildberries"].get("subject_id") or str(config["wildberries"].get("subject_id")) == "0":
            wb_missing.append("WB Subject ID")
        first_variant = wb_payload[0]["variants"][0] if wb_payload else {}
        first_size = first_variant.get("sizes", [{}])[0] if first_variant else {}
        if not first_size.get("price"):
            wb_missing.append("俄罗斯价格")
        if wb_missing:
            issues["WB"] = wb_missing
        return issues

    def _run_publish_listing(self, platform: str = "all") -> None:
        try:
            self.sync_store_config_from_fields()
            out_dir = self.output_dir_path()
            config = publisher.load_store_config(STORE_CONFIG_PATH)
            if platform in {"all", "mercadolibre"}:
                config = self.ensure_mercadolibre_token(config)
            if platform in {"all", "mercadolibre"}:
                ml_token = config["mercadolibre"].get("access_token", "")
                if ml_token and self.get_image_urls():
                    self.ensure_mercadolibre_images_uploaded(ml_token)
                elif self.get_image_urls():
                    self.write_log("检测到图片，但缺少美客多 Access Token，无法自动上传图片。")
            draft = self.build_current_listing_draft(config)
            results: dict[str, object] = {}
            skipped: dict[str, list[str]] = {}
            self.set_progress(0, 100, "正在检查上架必填项...")
            self.check_cancelled()

            if platform in {"all", "mercadolibre"}:
                ml_token = config["mercadolibre"].get("access_token", "")
                ml_payload = draft["mercadolibre"]["payload"]
                ml_missing = []
                if not ml_token:
                    ml_missing.append("美客多 Access Token")
                if not config["mercadolibre"].get("category_id") or not ml_payload.get("category_id"):
                    ml_missing.append("美客多类目 ID")
                if not ml_payload.get("title"):
                    ml_missing.append("美客多标题 title")
                for attr_id, label in [
                    ("BRAND", "Brand"),
                    ("MODEL", "Model"),
                    ("PACKAGE_HEIGHT", "Package height"),
                    ("PACKAGE_WIDTH", "Package width"),
                    ("PACKAGE_LENGTH", "Package length"),
                    ("PACKAGE_WEIGHT", "Package weight"),
                    ("GTIN", "Universal product code"),
                ]:
                    if not any(item.get("id") == attr_id and item.get("value_name") for item in ml_payload.get("attributes", [])):
                        ml_missing.append(label)
                if not ml_payload.get("price"):
                    ml_missing.append("墨西哥价格")
                if not ml_payload.get("sale_terms"):
                    ml_missing.append("Sale terms / 保修条款")
                for field, label in [
                    ("package_length", "包裹长 package_length"),
                    ("package_width", "包裹宽 package_width"),
                    ("package_height", "包裹高 package_height"),
                    ("package_weight", "包裹重量 package_weight"),
                ]:
                    if not ml_payload.get(field):
                        ml_missing.append(label)
                if ml_payload.get("category_id") and not str(ml_payload.get("category_id", "")).startswith("CBT"):
                    ml_missing.append("美客多 Global Selling 类目必须是 CBT 开头，请重新查询/选择分类")
                if not self.mercadolibre_payload_pictures(ml_payload):
                    if self.get_image_urls():
                        ml_missing.append("公网图片 URL（本地 file URL 不能发布）")
                    else:
                        ml_missing.append("图片 URL")
                if ml_missing:
                    skipped["mercadolibre"] = ml_missing
                else:
                    self.set_progress(20, 100, "正在发布美客多商品...")
                    debug_payload_path = out_dir / "last_mercadolibre_payload.json"
                    debug_payload_path.write_text(json.dumps(ml_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.write_log(
                        "美客多发布请求已生成，包含字段: "
                        + ", ".join(sorted(str(key) for key in ml_payload.keys()))
                        + f"；调试文件: {debug_payload_path}"
                    )
                    try:
                        results["mercadolibre"] = publisher.publish_mercadolibre(
                            ml_payload,
                            ml_token,
                        )
                    except Exception as exc:
                        if not publisher.is_mercadolibre_auth_error(exc):
                            # 解析平台错误，回填缺失属性到界面
                            parsed = publisher.parse_mercadolibre_error(exc)
                            self._handle_mercadolibre_publish_error(parsed, exc)
                            return
                        self.write_log("美客多发布返回 Token 失效，正在刷新 Token 后重试一次。")
                        config = self.ensure_mercadolibre_token(config, force=True)
                        ml_token = config["mercadolibre"].get("access_token", "")
                        try:
                            results["mercadolibre"] = publisher.publish_mercadolibre(ml_payload, ml_token)
                        except Exception as exc2:
                            parsed = publisher.parse_mercadolibre_error(exc2)
                            self._handle_mercadolibre_publish_error(parsed, exc2)
                            return
            if platform in {"all", "wildberries"}:
                self.set_progress(50, 100, "美客多处理完成，正在处理 WB...")
            else:
                self.set_progress(50, 100, "美客多发布请求已完成")
            self.check_cancelled()

            if platform in {"all", "wildberries"}:
                wb_token = config["wildberries"].get("content_token", "")
                wb_payload = draft["wildberries"]["payload"]
                wb_subject = config["wildberries"].get("subject_id")
                wb_missing = []
                if not wb_token:
                    wb_missing.append("WB Token")
                if not wb_subject or str(wb_subject) == "0":
                    wb_missing.append("WB Subject ID")
                first_variant = wb_payload[0]["variants"][0] if wb_payload else {}
                first_size = first_variant.get("sizes", [{}])[0] if first_variant else {}
                if not first_size.get("price"):
                    wb_missing.append("俄罗斯价格")
                if wb_missing:
                    skipped["wildberries"] = wb_missing
                else:
                    self.set_progress(70, 100, "正在发布 WB 商品...")
                    results["wildberries"] = publisher.publish_wildberries(
                        wb_payload,
                        wb_token,
                    )

            if skipped:
                results["skipped"] = skipped
            (out_dir / "publish_result.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not any(key in results for key in ("mercadolibre", "wildberries")):
                raise RuntimeError(
                    "没有调用平台发布接口，因为必填项不完整："
                    + "; ".join(
                        f"{platform}: {', '.join(items)}" for platform, items in skipped.items()
                    )
                )
            self.set_progress(100, 100, "发布请求完成")
            summary = json.dumps(results, ensure_ascii=False, indent=2)
            self.write_log("发布接口返回: " + summary.replace("\n", " ")[:1200])
            self.show_info("发布请求完成", summary[:1800])
        except Exception as exc:
            self.write_log(f"确认上架失败: {exc}")
            self.show_error("失败", f"确认上架失败:\n{exc}")

    def write_log(self, message: str) -> None:
        self.root.after(0, self._write_log, message)

    def _write_log(self, message: str) -> None:
        self.log.insert(END, message + "\n")
        self.log.see(END)
        if self.image_log is not None:
            lower = message.lower()
            image_related = any(
                word in message
                for word in ["图片", "生图", "ChatGPT", "OpenAI", "视频"]
            ) or any(word in lower for word in ["image", "video", "openai"])
            if image_related:
                self.image_log.insert(END, message + "\n")
                self.image_log.see(END)

    def start_indeterminate(self, message: str) -> None:
        self.root.after(0, self._start_indeterminate, message)

    def _start_indeterminate(self, message: str) -> None:
        self.status_text.set(message)
        self.progress.config(mode="indeterminate")
        self.progress.start(12)
        if hasattr(self, "url_progress"):
            self.url_progress.config(mode="indeterminate")
            self.url_progress.start(12)

    def stop_progress(self, message: str) -> None:
        self.root.after(0, self._stop_progress, message)

    def _stop_progress(self, message: str) -> None:
        self.progress.stop()
        self.progress.config(mode="determinate", maximum=100, value=100)
        self.status_text.set(message)
        if hasattr(self, "url_progress"):
            self.url_progress.stop()
            self.url_progress.config(mode="determinate", maximum=100, value=100)

    def set_progress(self, current: int, total: int, message: str) -> None:
        self.root.after(0, self._set_progress, current, max(total, 1), message)

    def _set_progress(self, current: int, total: int, message: str) -> None:
        self.progress.stop()
        self.progress.config(mode="determinate", maximum=total, value=current)
        self.status_text.set(message)
        if hasattr(self, "url_progress"):
            self.url_progress.stop()
            self.url_progress.config(mode="determinate", maximum=total, value=current)

    def show_plan_preview(self, plan: dict) -> None:
        self.root.after(0, self._show_plan_preview, plan)

    def _show_plan_preview(self, plan: dict) -> None:
        mx_title = ""
        mx_desc = ""
        ru_title = ""
        ru_desc = ""
        for key, platform in plan["platforms"].items():
            listing = platform["listing"]
            title = self.capitalize_title(listing["title"])
            alt_title = self.capitalize_title(self.pick_no_brand_title(listing, plan["product"]))
            if key == "mercadolibre":
                mx_title = title
                mx_alt_title = alt_title
                mx_desc = listing["description"]
            elif key == "wildberries":
                ru_title = title
                ru_alt_title = alt_title
                ru_desc = listing["description"]

        self.mx_title_var.set(mx_title)
        self.ml_listing_title.set(mx_title)
        self.mx_title_alt_var.set(locals().get("mx_alt_title", ""))
        self.mx_desc.delete("1.0", END)
        self.mx_desc.insert("1.0", mx_desc)
        self.ru_title_var.set(ru_title)
        self.ru_title_alt_var.set(locals().get("ru_alt_title", ""))
        self.ru_desc.delete("1.0", END)
        self.ru_desc.insert("1.0", ru_desc)
        self.update_chinese_copy_preview()

    def capitalize_title(self, title: str) -> str:
        title = title.strip()
        if not title:
            return title
        return title[0].upper() + title[1:]

    def pick_no_brand_title(self, listing: dict, product: dict) -> str:
        brand = str(product.get("brand", "")).casefold()
        name = str(product.get("name", "")).casefold()
        for title in listing.get("alt_titles", []):
            folded = str(title).casefold()
            if brand and brand in folded:
                continue
            if name and name in folded:
                continue
            return title
        title = str(listing.get("title", ""))
        for word in [product.get("brand", ""), product.get("name", "")]:
            if word:
                title = title.replace(str(word), "").strip()
        return " ".join(title.split())

    def show_info(self, title: str, message: str) -> None:
        self.root.after(0, lambda: messagebox.showinfo(title, message))

    def show_error(self, title: str, message: str) -> None:
        self.root.after(0, lambda: messagebox.showerror(title, message))


class CdpWebSocket:
    def __init__(self, websocket_url: str) -> None:
        parsed = urlparse(websocket_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self.sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("连接 Chrome DevTools WebSocket 失败")
        self.next_id = 1

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass

    def _send_frame(self, payload: bytes) -> None:
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self.sock.recv(length - len(chunks))
            if not chunk:
                raise RuntimeError("Chrome DevTools 连接已关闭")
            chunks.extend(chunk)
        return bytes(chunks)

    def _recv_frame(self) -> dict:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            masked = bool(second & 0x80)
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 8:
                raise RuntimeError("Chrome DevTools WebSocket 已关闭")
            if opcode == 9:
                continue

    def call(self, method: str, params: dict | None = None, timeout: float = 20) -> dict:
        message_id = self.next_id
        self.next_id += 1
        self._send_frame(json.dumps({"id": message_id, "method": method, "params": params or {}}).encode("utf-8"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv_frame()
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})
        raise RuntimeError(f"{method} 超时")


def find_chrome_path() -> str:
    env_candidates = [
        os.environ.get("ERP_CHROME_PATH", ""),
        os.environ.get("CHROME_PATH", ""),
        os.environ.get("BROWSER_PATH", ""),
    ]
    candidates = [Path(value).expanduser() for value in env_candidates if value.strip()]
    command_candidates: list[str] = []

    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
                Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                Path.home() / "Applications" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path.home() / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge",
            ]
        )
        command_candidates = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge"]
    elif os.name == "nt":
        candidates.extend(
            [
                Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ]
        )
        command_candidates = ["chrome", "chrome.exe", "msedge", "msedge.exe"]
    else:
        candidates.extend(
            [
                Path("/usr/bin/google-chrome"),
                Path("/usr/local/bin/google-chrome"),
                Path("/usr/bin/chromium"),
                Path("/usr/bin/chromium-browser"),
                Path("/usr/bin/microsoft-edge"),
                Path("/usr/bin/msedge"),
            ]
        )
        command_candidates = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge"]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    for command in command_candidates:
        found = shutil.which(command)
        if found:
            return found
    raise RuntimeError("没有找到 Chrome 或 Edge 浏览器；可设置 ERP_CHROME_PATH / CHROME_PATH 指向浏览器可执行文件。")


def http_json(url: str) -> dict | list:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_cdp(port: int, timeout: int = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_json(f"http://127.0.0.1:{port}/json/version")
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Chrome 调试端口启动超时")


def open_chatgpt_target(port: int) -> dict:
    pages = http_json(f"http://127.0.0.1:{port}/json/list")
    for page in pages:
        if page.get("type") == "page" and "chatgpt.com" in page.get("url", ""):
            return page
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/new?{quote('https://chatgpt.com/', safe='')}", timeout=5):
            pass
    except Exception:
        pass
    time.sleep(2)
    pages = http_json(f"http://127.0.0.1:{port}/json/list")
    for page in pages:
        if page.get("type") == "page" and "chatgpt.com" in page.get("url", ""):
            return page
    for page in pages:
        if page.get("type") == "page":
            return page
    raise RuntimeError("没有找到可用的 Chrome 页面")


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def wait_for_composer(cdp: CdpWebSocket, timeout: int = 90) -> bool:
    check_js = """
Boolean(document.querySelector('#prompt-textarea, textarea, div[contenteditable="true"], .ProseMirror'))
"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = cdp.call("Runtime.evaluate", {"expression": check_js, "returnByValue": True})
        if result.get("result", {}).get("value"):
            return True
        time.sleep(1)
    return False


def chatgpt_uploaded_file_count(cdp: CdpWebSocket) -> int:
    script = """
(() => Math.max(0, ...[...document.querySelectorAll('input[type=file]')].map(input => input.files ? input.files.length : 0)))()
"""
    result = cdp.call("Runtime.evaluate", {"expression": script, "returnByValue": True}, timeout=20)
    value = result.get("result", {}).get("value", 0)
    return int(value or 0)


def wait_for_chatgpt_upload(cdp: CdpWebSocket, expected_count: int, timeout: int = 45) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if chatgpt_uploaded_file_count(cdp) >= expected_count:
            return True
        time.sleep(1)
    return False


def upload_files_to_chatgpt(cdp: CdpWebSocket, files: list[str]) -> None:
    if not files:
        return
    selectors = [
        'input#upload-photos',
        'input[data-testid="upload-photos-input"]',
        'input[type=file][accept*="image"]',
        'input[type=file][multiple]',
        'input[type=file]',
    ]
    tried: set[int] = set()
    for selector in selectors:
        root = cdp.call("DOM.getDocument", {"depth": -1, "pierce": True})
        root_id = root["root"]["nodeId"]
        nodes = cdp.call("DOM.querySelectorAll", {"nodeId": root_id, "selector": selector})["nodeIds"]
        for node_id in nodes:
            if node_id in tried:
                continue
            tried.add(node_id)
            try:
                cdp.call("DOM.setFileInputFiles", {"nodeId": node_id, "files": files}, timeout=60)
                if wait_for_chatgpt_upload(cdp, len(files), timeout=35):
                    time.sleep(6)
                    return
            except Exception:
                continue
    raise RuntimeError("没有把原图上传到 ChatGPT：未能命中可用的图片上传控件。")


def chatgpt_image_infos(cdp: CdpWebSocket) -> list[dict]:
    script = """
(() => {
  const imgs = [...document.querySelectorAll('img')];
  return imgs.map((img, index) => {
    const rect = img.getBoundingClientRect();
    const src = img.currentSrc || img.src || '';
    const role = img.closest('[data-message-author-role]')?.getAttribute('data-message-author-role') || '';
    return {
      index,
      src,
      marker: src.slice(0, 180),
      width: img.naturalWidth || Math.round(rect.width),
      height: img.naturalHeight || Math.round(rect.height),
      rectWidth: Math.round(rect.width),
      rectHeight: Math.round(rect.height),
      role,
      alt: img.alt || ''
    };
  }).filter(x =>
    x.src &&
    x.width >= 384 &&
    x.height >= 384 &&
    !/avatar|favicon|logo/i.test(x.alt + ' ' + x.src)
  );
})()
"""
    result = cdp.call("Runtime.evaluate", {"expression": script, "returnByValue": True}, timeout=30)
    value = result.get("result", {}).get("value")
    return value if isinstance(value, list) else []


def chatgpt_fetch_image_data_url(cdp: CdpWebSocket, src: str) -> str:
    script = f"""
(async () => {{
  const src = {js_string(src)};
  const response = await fetch(src);
  const blob = await response.blob();
  return await new Promise((resolve, reject) => {{
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(String(reader.error || 'read image failed'));
    reader.readAsDataURL(blob);
  }});
}})()
"""
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": script, "awaitPromise": True, "returnByValue": True},
        timeout=90,
    )
    value = result.get("result", {}).get("value", "")
    if not isinstance(value, str) or not value.startswith("data:image/"):
        raise RuntimeError("ChatGPT 图片数据读取失败")
    return value


def save_data_url_image(data_url: str, target: Path) -> Path:
    header, encoded = data_url.split(",", 1)
    if "image/jpeg" in header or "image/jpg" in header:
        target = target.with_suffix(".jpg")
    elif "image/webp" in header:
        target = target.with_suffix(".webp")
    else:
        target = target.with_suffix(".png")
    target.write_bytes(base64.b64decode(encoded))
    return target


def poll_chatgpt_generated_images(
    cdp: CdpWebSocket,
    baseline: set[str],
    output_dir: Path,
    expected_count: int = 10,
    timeout: int = 45 * 60,
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    last_markers: list[str] = []
    stable_since: float | None = None
    final_infos: list[dict] = []
    while time.time() < deadline:
        infos = [
            info
            for info in chatgpt_image_infos(cdp)
            if info.get("marker") not in baseline and info.get("src")
        ]
        unique: list[dict] = []
        seen: set[str] = set()
        for info in infos:
            marker = str(info.get("marker") or info.get("src"))
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(info)
        markers = [str(info.get("marker")) for info in unique]
        if markers != last_markers:
            last_markers = markers
            stable_since = time.time()
        if len(unique) >= expected_count or (unique and stable_since and time.time() - stable_since > 90):
            final_infos = unique[:expected_count]
            break
        time.sleep(5)
    if not final_infos:
        final_infos = [
            info
            for info in chatgpt_image_infos(cdp)
            if info.get("marker") not in baseline and info.get("src")
        ][:expected_count]
    saved: list[str] = []
    for index, info in enumerate(final_infos, start=1):
        try:
            data_url = chatgpt_fetch_image_data_url(cdp, str(info["src"]))
            target = output_dir / f"chatgpt_auto_{index:02d}.png"
            saved_path = save_data_url_image(data_url, target)
            saved.append(str(saved_path.resolve()))
        except Exception:
            continue
    return saved


def write_chatgpt_auto_result(path: Path, ok: bool, images: list[str] | None = None, error: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ok": ok, "images": images or [], "error": error}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def chatgpt_auto_send(prompt_path: Path, files_path: Path, result_path: Path | None = None, output_dir: Path | None = None) -> None:
    prompt = prompt_path.read_text(encoding="utf-8")
    files = json.loads(files_path.read_text(encoding="utf-8")) if files_path.exists() else []
    files = [str(Path(path).resolve()) for path in files if Path(path).exists()][:7]
    result_path = result_path or (prompt_path.parent / "chatgpt_auto_result.json")
    output_dir = output_dir or prompt_path.parent
    port = 9223
    profile = output_dir / "chatgpt_chrome_profile"
    profile.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome_path()
    try:
        http_json(f"http://127.0.0.1:{port}/json/version")
    except Exception:
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--disable-popup-blocking",
                "--start-minimized",
                "https://chatgpt.com/",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_cdp(port)
    target = open_chatgpt_target(port)
    cdp = CdpWebSocket(target["webSocketDebuggerUrl"])
    try:
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("DOM.enable")
        cdp.call("Page.bringToFront")
        if not wait_for_composer(cdp, timeout=90):
            raise RuntimeError("没有找到 ChatGPT 输入框，请先在自动打开的 Chrome 里登录 ChatGPT 后重试")
        if files:
            upload_files_to_chatgpt(cdp, files)
        if False and files:
            root = cdp.call("DOM.getDocument", {"depth": -1, "pierce": True})
            root_id = root["root"]["nodeId"]
            nodes = cdp.call("DOM.querySelectorAll", {"nodeId": root_id, "selector": "input[type=file]"})["nodeIds"]
            if not nodes:
                cdp.call(
                    "Runtime.evaluate",
                    {
                        "expression": """
(() => {
  const buttons = [...document.querySelectorAll('button')];
  const attach = buttons.find(b => /attach|upload|file|上传|附件|添加|plus|add/i.test((b.getAttribute('aria-label') || '') + ' ' + b.innerText));
  if (attach) attach.click();
})()
""",
                        "awaitPromise": False,
                    },
                )
                time.sleep(1)
                root = cdp.call("DOM.getDocument", {"depth": -1, "pierce": True})
                root_id = root["root"]["nodeId"]
                nodes = cdp.call("DOM.querySelectorAll", {"nodeId": root_id, "selector": "input[type=file]"})["nodeIds"]
            if nodes:
                cdp.call("DOM.setFileInputFiles", {"nodeId": nodes[0], "files": files})
                time.sleep(8)
        baseline = {str(info.get("marker") or info.get("src")) for info in chatgpt_image_infos(cdp)}
        focus_js = """
(() => {
  const el = document.querySelector('#prompt-textarea, textarea, div[contenteditable="true"], .ProseMirror');
  if (!el) return false;
  el.focus();
  return true;
})()
"""
        cdp.call("Runtime.evaluate", {"expression": focus_js, "returnByValue": True})
        prompt_literal = json.dumps(prompt)
        paste_js = f"""
(() => {{
  const text = {prompt_literal};
  const el = document.querySelector('#prompt-textarea, textarea, div[contenteditable="true"], .ProseMirror');
  if (!el) return false;
  el.focus();
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {{
    el.value = text;
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    return true;
  }}
  document.execCommand('selectAll', false, null);
  document.execCommand('insertText', false, text);
  el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: text }}));
  return (el.innerText || el.textContent || '').length > 20;
}})()
"""
        inserted = cdp.call("Runtime.evaluate", {"expression": paste_js, "returnByValue": True}, timeout=60).get("result", {}).get("value")
        if not inserted:
            cdp.call("Input.insertText", {"text": prompt}, timeout=60)
        time.sleep(1)
        send_js = """
(() => {
  const buttons = [...document.querySelectorAll('button')];
  const send = buttons.find(b => /send|发送|提交/i.test((b.getAttribute('aria-label') || '') + ' ' + (b.dataset.testid || '') + ' ' + b.innerText));
  if (send && !send.disabled) { send.click(); return true; }
  return false;
})()
"""
        sent = cdp.call("Runtime.evaluate", {"expression": send_js, "returnByValue": True}).get("result", {}).get("value")
        if not sent:
            cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter"})
            cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter"})
        count_match = re.search(r"(\d+)\s*(?:张|寮|images?)", prompt, re.I)
        expected_count = max(1, min(10, int(count_match.group(1)) if count_match else 10))
        image_dir = output_dir / "chatgpt_images" / time.strftime("%Y%m%d_%H%M%S")
        images = poll_chatgpt_generated_images(cdp, baseline, image_dir, expected_count=expected_count)
        if images:
            write_chatgpt_auto_result(result_path, True, images=images)
        else:
            write_chatgpt_auto_result(result_path, False, error="没有从 ChatGPT 页面检测到新生成图片。")
    finally:
        cdp.close()


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--chatgpt-auto":
        prompt_arg = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT_DIR / "chatgpt_prompt.txt"
        files_arg = Path(sys.argv[3]) if len(sys.argv) > 3 else OUTPUT_DIR / "chatgpt_source_files.json"
        result_arg = Path(sys.argv[4]) if len(sys.argv) > 4 else OUTPUT_DIR / "chatgpt_auto_result.json"
        output_arg = Path(sys.argv[5]) if len(sys.argv) > 5 else OUTPUT_DIR
        try:
            chatgpt_auto_send(prompt_arg, files_arg, result_arg, output_arg)
        except Exception as exc:
            error_path = result_arg
            error_path.parent.mkdir(parents=True, exist_ok=True)
            write_chatgpt_auto_result(error_path, False, error=str(exc))
        return
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

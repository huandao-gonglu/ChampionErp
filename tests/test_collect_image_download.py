from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image
import pytest

from erp_web.runtime_units.source_collect_parsers import (
    collect_product_image_urls, extract_1688_detail_images, parse_1688_product,
)
from erp_web.runtime_units.collect_helpers import normalize_collect_source_images
from erp_web.product_model.draft_image_model import draft_image_refs_from_pool
from erp_web.product_model.sku_model import collected_skus
from erp_web.services import html_extract_service, image_service


def _png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_collect_product_image_urls_prefers_html_product_images_over_dom_icons() -> None:
    html = """
    <html><head>
      <meta property="og:image" content="https://cbu01.alicdn.com/img/ibank/O1CN-product-main.jpg">
    </head><body>
      <img src="https://img.alicdn.com/imgextra/i1/ui-icon.svg">
    </body></html>
    """

    urls = collect_product_image_urls(
        html,
        "https://detail.1688.com/offer/799002636435.html",
        ["https://img.alicdn.com/imgextra/i1/ui-icon.svg"],
        limit=5,
    )

    assert urls[0] == "https://cbu01.alicdn.com/img/ibank/O1CN-product-main.jpg"
    assert all(not url.lower().endswith(".svg") for url in urls)


def test_download_images_skips_svg_and_tiny_icons(monkeypatch, tmp_path: Path) -> None:
    payloads = {
        "https://example.test/fake-jpg.svg.jpg": ("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
        "https://example.test/tiny.png": ("image/png", _png_bytes(20, 20)),
        "https://example.test/main.png": ("image/png", _png_bytes(800, 800)),
    }

    class FakeResponse:
        def __init__(self, content_type: str, data: bytes) -> None:
            self.headers = {"Content-Type": content_type}
            self._data = data

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _size: int = -1) -> bytes:
            return self._data

    def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
        url = request.full_url  # type: ignore[attr-defined]
        content_type, data = payloads[url]
        return FakeResponse(content_type, data)

    monkeypatch.setattr(html_extract_service.urllib.request, "urlopen", fake_urlopen)

    paths = html_extract_service.download_images(list(payloads), tmp_path)

    assert len(paths) == 1
    saved = Path(paths[0])
    assert saved.name == "url_main_1.png"
    with Image.open(saved) as image:
        assert image.size == (800, 800)


def _gallery_html(main_images: list[str], detail_url: str = "") -> str:
    return '<script>' + json.dumps({
        "gallery": {"fields": {"subject": "宠物玩具", "mainImage": main_images}},
        "description": {"fields": {"detailUrl": detail_url}},
        "dataJson": {"skuModel": {
            "skuProps": [{"prop": "颜色", "value": [{"name": "黄色", "imageUrl": main_images[0]}]}],
            "skuInfoMap": {"黄色": {"skuId": 1, "specAttrs": "黄色", "price": "3", "canBookCount": 10}},
        }},
    }) + '</script>'


def test_1688_main_gallery_precedes_many_sku_thumbnails_and_recommendations() -> None:
    main = [f"https://cbu01.alicdn.com/main-{i}.jpg" for i in range(5)]
    noise = ''.join(f'<img src="https://cbu01.alicdn.com/recommended-{i}.jpg">' for i in range(50))
    html = noise + _gallery_html(main)
    assert collect_product_image_urls(html, "https://detail.1688.com/offer/1.html", limit=5) == main


def test_1688_collects_full_detail_and_downloads_shared_sku_image_once(monkeypatch) -> None:
    main = [f"https://cbu01.alicdn.com/main-{i}.jpg" for i in range(5)]
    details = [f"https://cbu01.alicdn.com/detail-{i}.jpg" for i in range(25)]
    detail_url = "https://itemcdn.tmall.com/1688offer/example"
    payload = 'var offer_details=' + json.dumps({"content": ''.join(f'<img src="{url}">' for url in details)}) + ';'
    fetched, downloaded = [], []

    def fetch(url):
        fetched.append(url)
        return payload

    def download(_app, url, _product, index):
        downloaded.append(url)
        return {"id": f"download-{index}", "url": url, "status": "ready"}

    monkeypatch.setattr(html_extract_service, "fetch_1688_detail_html", fetch)
    monkeypatch.setattr(image_service, "download_remote_image", download)
    monkeypatch.setattr(html_extract_service, "download_images", lambda *_args: pytest.fail("解析阶段不得预下载图片"))
    parsed = parse_1688_product({"html": _gallery_html(main, detail_url), "url": "https://detail.1688.com/offer/1.html"})
    source = normalize_collect_source_images(parsed["source"], "1688", "browser")
    assert fetched == [detail_url]
    assert downloaded == main + details
    assert len(source["image_pool"]) == 30
    assert len(draft_image_refs_from_pool({"source": source})) == 30
    assert source["image_pool"][0]["raw"]["source_url"] == main[0]
    assert collected_skus(source)[0]["image_asset_id"] == source["image_pool"][0]["id"]


def test_1688_detail_reads_lazy_images_and_deduplicates_without_executing_script() -> None:
    html = '<img src="//cbu01.alicdn.com/a.jpg"><img src="placeholder.gif" data-src="//cbu01.alicdn.com/b.jpg"><img src="//cbu01.alicdn.com/a.jpg">'
    payload = 'var offer_details=' + json.dumps({"content": html}) + '; throw new Error("不得执行");'
    assert extract_1688_detail_images(payload, "https://itemcdn.tmall.com/detail") == [
        "https://cbu01.alicdn.com/a.jpg", "https://cbu01.alicdn.com/b.jpg",
    ]


def test_1688_detail_failure_does_not_report_complete_collection(monkeypatch) -> None:
    def fetch(_url):
        raise RuntimeError("1688 详情图片读取失败，请重试采集")

    monkeypatch.setattr(html_extract_service, "fetch_1688_detail_html", fetch)
    with pytest.raises(RuntimeError, match="详情图片读取失败"):
        parse_1688_product({"html": _gallery_html(["https://cbu01.alicdn.com/main.jpg"], "https://itemcdn.tmall.com/1688offer/example")})
    with pytest.raises(ValueError, match="有效图片内容"):
        extract_1688_detail_images("访问被拒绝", "https://itemcdn.tmall.com/detail")


def test_1688_detail_url_rejects_non_official_host_before_request(monkeypatch) -> None:
    monkeypatch.setattr(html_extract_service, "fetch_url_html", lambda *_args: pytest.fail("不得请求非官方详情地址"))
    with pytest.raises(ValueError, match="官方域名"):
        html_extract_service.fetch_1688_detail_html("https://localhost/private")

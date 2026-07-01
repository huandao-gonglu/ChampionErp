from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from erp_web.services import html_extract_service


def _png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_collect_product_image_urls_prefers_html_product_images_over_dom_icons() -> None:
    from erp_web import runtime as erp_web_app

    html = """
    <html><head>
      <meta property="og:image" content="https://cbu01.alicdn.com/img/ibank/O1CN-product-main.jpg">
    </head><body>
      <img src="https://img.alicdn.com/imgextra/i1/ui-icon.svg">
    </body></html>
    """

    urls = erp_web_app.collect_product_image_urls(
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

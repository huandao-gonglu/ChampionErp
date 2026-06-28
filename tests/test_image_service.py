from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from conftest import assert_no_old_path
from services import image_service, image_translate_service


def _make_png(path: Path, color: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (8, 6), color)
    image.save(path, format="PNG")


def test_image_root_is_created_under_data_images(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    root = image_service.images_root(app_dir)
    assert root.exists()
    assert root.is_dir()
    assert root.resolve().is_relative_to((app_dir / "data" / "images").resolve())
    assert_no_old_path(root, old_path_markers)


def test_upload_delete_replace_main_and_sku_image(app_dir: Path, tmp_path: Path, old_path_markers: tuple[str, ...]) -> None:
    source = tmp_path / "test_image.png"
    replacement = tmp_path / "replacement.png"
    _make_png(source, (255, 0, 0))
    _make_png(replacement, (0, 0, 255))

    uploaded = image_service.upload_images(app_dir, [{"path": str(source), "platforms": ["mercadolibre"], "is_main": True}], "pytest-stage3a")
    assert len(uploaded) == 1
    item = uploaded[0]
    saved_path = app_dir / item["path"]
    assert saved_path.exists()
    assert saved_path.resolve().is_relative_to((app_dir / "data" / "images").resolve())
    assert item["preview_url"].startswith("/file?path=")
    assert item["status"] == "ready"
    assert item["width"] == 8
    assert item["height"] == 6

    pool = image_service.add_images([], uploaded, app_dir)
    pool = image_service.set_main_image(pool, item["id"], app_dir)
    assert pool[0]["is_main"] is True

    pool = image_service.set_sku_image(pool, item["id"], "SKU-BLUE", app_dir)
    assert pool[0]["is_sku"] is True
    assert pool[0]["sku"] == "SKU-BLUE"

    pool = image_service.replace_image(pool, item["id"], {"path": str(replacement), "product_id": "pytest-stage3a"}, app_dir)
    replaced_path = app_dir / pool[0]["path"]
    assert replaced_path.exists()
    assert pool[0]["is_main"] is True
    assert pool[0]["is_sku"] is True

    pool = image_service.delete_images(pool, [pool[0]["id"]], app_dir)
    assert pool == []
    assert_no_old_path(uploaded, old_path_markers)


def test_upload_data_url_returns_media_asset_shape(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    raw = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode("ascii")
    items = image_service.upload_images(
        app_dir,
        [{"filename": "tiny.png", "data_url": f"data:image/png;base64,{raw}", "platforms": ["mercadolibre"]}],
        "pytest-stage3a-dataurl",
    )
    assert items
    item = items[0]
    assert {"id", "asset_id", "path", "preview_url", "platforms", "status"}.issubset(item.keys())
    assert (app_dir / item["path"]).exists()
    assert item["status"] == "ready"
    assert_no_old_path(item, old_path_markers)


def test_safe_segment_truncates_long_source_url_with_stable_hash() -> None:
    long_url = (
        "https://detail.1688.com/offer/906909238760.html?"
        "topicCode=202603180010000000000015195837&"
        "optName=%E7%83%AD%E7%82%B9%E5%95%86%E6%9C%BA&"
        "topicName=%E7%8C%AB%E5%92%AA%E9%A3%9F%E5%85%B7%E6%8A%A4%E9%A2%88%E9%98%B2%E6%89%93%E7%BF%BB&"
        "item_id=906909238760&offerId=906909238760&object_id=906909238760&"
        "spm=a260k.29939364.recommend.0"
    )

    segment = image_service.safe_segment(long_url)

    assert len(segment) <= image_service.MAX_SAFE_SEGMENT_LENGTH
    assert segment == image_service.safe_segment(long_url)
    assert segment.startswith("https_detail.1688.com_offer_906909238760.html")
    assert segment.rsplit("_", 1)[-1].isalnum()


def test_upload_data_url_accepts_long_product_id_without_long_filename_error(app_dir: Path) -> None:
    raw = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode("ascii")
    long_product_id = (
        "https://detail.1688.com/offer/906909238760.html?"
        "topicCode=202603180010000000000015195837&"
        "optName=%E7%83%AD%E7%82%B9%E5%95%86%E6%9C%BA&"
        "topicName=%E7%8C%AB%E5%92%AA%E9%A3%9F%E5%85%B7%E6%8A%A4%E9%A2%88%E9%98%B2%E6%89%93%E7%BF%BB&"
        "item_id=906909238760&offerId=906909238760&object_id=906909238760&"
        "spm=a260k.29939364.recommend.0"
    )

    items = image_service.upload_images(
        app_dir,
        [{"filename": "tiny.png", "data_url": f"data:image/png;base64,{raw}", "platforms": ["mercadolibre"]}],
        long_product_id,
    )

    assert len(items) == 1
    item = items[0]
    saved_path = app_dir / item["path"]
    product_segment = Path(item["path"]).parts[2]
    assert saved_path.exists()
    assert len(product_segment) <= image_service.MAX_SAFE_SEGMENT_LENGTH
    assert saved_path.parent.name == product_segment


def test_image_translate_service_materializes_provider_output_into_pool_item(app_dir: Path, tmp_path: Path, old_path_markers: tuple[str, ...]) -> None:
    source_path = tmp_path / "source.png"
    generated_path = tmp_path / "translated.png"
    _make_png(source_path, (255, 0, 0))
    _make_png(generated_path, (0, 255, 0))

    source_item = image_service.upload_images(
        app_dir,
        [{"path": str(source_path), "platforms": ["mercadolibre"], "is_main": True, "selected": True}],
        "pytest-image-translate",
    )[0]
    product = {
        "product_id": "pytest-image-translate",
        "name": "Organizer with Chinese text",
        "source": {"title": "Organizer with Chinese text", "image_pool": [source_item]},
    }
    calls: list[dict] = []

    def fake_provider(config: dict, request: dict) -> list[dict]:
        calls.append({"config": config, "request": request})
        assert request["target_language"] == "Spanish (Mexico)"
        assert request["image_ids"] == [source_item["id"]]
        assert "Only replace or localize text" in request["prompt"]
        return [{"path": str(generated_path), "provider": "fake-image-ai"}]

    result = image_translate_service.translate_images(
        app_dir,
        product,
        {
            "ai_models": [
                {
                    "id": "image_model",
                    "provider": "OpenAI-Compatible",
                    "api_key": "test-key",
                    "base_url": "http://example.test",
                    "model": "fake-image",
                    "capabilities": ["image_edit", "image_generate"],
                }
            ]
        },
        target_language="Spanish (Mexico)",
        platform="mercadolibre",
        image_ids=[source_item["id"]],
        provider=fake_provider,
    )

    assert result["ok"] is True
    assert result["generated_count"] == 1
    assert calls
    item = result["imagePoolItems"][0]
    assert item["origin"] == "ai_generated"
    assert item["target_language"] == "Spanish (Mexico)"
    assert item["translated_from_id"] == source_item["id"]
    assert item["path"].replace("\\", "/").startswith("data/images/pytest-image-translate/translated/")
    assert item["preview_url"].startswith("/file?path=")
    assert (app_dir / item["path"]).exists()
    assert item["width"] == 8
    assert item["height"] == 6
    assert_no_old_path(result, old_path_markers)


def test_image_translate_service_returns_configuration_warning_without_provider_output(app_dir: Path, tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    _make_png(source_path, (255, 0, 0))
    source_item = image_service.upload_images(app_dir, [{"path": str(source_path), "selected": True}], "pytest-image-translate-warning")[0]
    product = {
        "product_id": "pytest-image-translate-warning",
        "name": "Warning item",
        "source": {"title": "Warning item", "image_pool": [source_item]},
    }

    result = image_translate_service.translate_images(
        app_dir,
        product,
        {
            "ai_models": [
                {
                    "id": "image_model",
                    "provider": "OpenAI",
                    "api_key": "",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-image-1",
                    "capabilities": ["image_edit", "image_generate"],
                }
            ]
        },
        target_language="Russian",
        provider=lambda _config, _request: [],
    )

    assert result["ok"] is False
    assert "未配置图片翻译模型" in result["message"]
    assert result["imagePoolItems"] == []
    assert "Target language: Russian" in result["prompt"]


def test_image_translate_service_uses_default_openai_provider(app_dir: Path, tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    _make_png(source_path, (255, 0, 0))
    source_item = image_service.upload_images(app_dir, [{"path": str(source_path), "selected": True}], "pytest-openai-image-provider")[0]
    product = {
        "product_id": "pytest-openai-image-provider",
        "name": "Provider item",
        "source": {"title": "Provider item", "image_pool": [source_item]},
    }
    raw = base64.b64encode(source_path.read_bytes()).decode("ascii")
    calls: list[dict] = []

    class Images:
        @staticmethod
        def edit(**kwargs):
            calls.append(kwargs)
            return type("Response", (), {"data": [type("Item", (), {"b64_json": raw})()]})()

        @staticmethod
        def generate(**kwargs):
            raise AssertionError("edit should be used when a local source image exists")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})
            self.images = Images()

    with patch.dict("sys.modules", {"openai": type("OpenAIModule", (), {"OpenAI": FakeOpenAI})}):
        result = image_translate_service.translate_images(
            app_dir,
            product,
            {
                "ai_models": [
                    {
                        "id": "image_model",
                        "provider": "OpenAI-Compatible",
                        "api_key": "test-key",
                        "base_url": "http://example.test/v1",
                        "model": "fake-image",
                        "capabilities": ["image_edit", "image_generate"],
                    }
                ]
            },
            target_language="Spanish (Mexico)",
            platform="mercadolibre",
            image_ids=[source_item["id"]],
        )

    assert result["ok"] is True
    assert result["generated_count"] == 1
    assert calls[0]["client"]["timeout"] == image_translate_service.IMAGE_AI_TIMEOUT_SECONDS
    assert calls[0]["client"]["default_headers"]["User-Agent"] == image_translate_service.ai_model_config.AI_HTTP_USER_AGENT
    assert calls[1]["model"] == "fake-image"
    assert calls[1]["image"].closed is True
    item = result["imagePoolItems"][0]
    assert item["origin"] == "ai_generated"
    assert item["provider"] == "OpenAI-Compatible"
    assert (app_dir / item["path"]).exists()

from __future__ import annotations

import base64
import time

import requests


def post_json(base_url: str, path: str, payload: dict, expected_status: int = 200) -> dict:
    response = requests.post(f"{base_url}{path}", json=payload, timeout=20)
    assert response.status_code == expected_status, response.text[:500]
    data = response.json()
    assert isinstance(data, dict)
    assert data
    return data


def get_json(base_url: str, path: str, params: dict | None = None, expected_status: int = 200) -> dict:
    response = requests.get(f"{base_url}{path}", params=params, timeout=20)
    assert response.status_code == expected_status, response.text[:500]
    data = response.json()
    assert isinstance(data, dict)
    assert data
    return data


def test_collect_api_manual_import_returns_product(backend_server: str) -> None:
    data = post_json(
        backend_server,
        "/api/collect-extension-payload",
        {
            "source_url": "https://detail.1688.com/offer/pytest-stage3a.html",
            "platform": "1688",
            "title": "Stage 3A API collect product",
            "price": "30",
            "description": "Manual import for API test.",
            "raw_html_optional": "标题：Stage 3A API collect product\n材质：PP\n尺寸：20x15x10 cm\n重量：0.5 kg",
            "platforms": ["mercadolibre"],
        },
    )

    assert data["ok"] is True
    assert data["product"]["source"]["title"]
    assert data["product"]["drafts"]["mercadolibre"]["title"]


def test_image_upload_and_delete_api(backend_server: str, sample_product: dict) -> None:
    saved = post_json(backend_server, "/api/save-product", {"product": sample_product})
    product_id = saved["product"]["product_id"]
    raw = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode("ascii")
    upload = post_json(
        backend_server,
        "/api/image-pool/upload",
        {
            "product_id": product_id,
            "uploads": [
                {
                    "filename": "api-stage3a.png",
                    "data_url": f"data:image/png;base64,{raw}",
                    "platforms": ["mercadolibre"],
                    "selected": True,
                    "is_main": True,
                }
            ],
        },
    )

    assert upload["ok"] is True
    assert upload["imagePool"]
    item = upload["imagePool"][0]
    assert item["path"].replace("\\", "/").startswith("data/images/")
    assert item["preview_url"].startswith("/file?path=")

    deleted = post_json(
        backend_server,
        "/api/image-pool/action",
        {"product_id": product_id, "action": "delete", "image_ids": [item["id"]]},
    )
    assert deleted["ok"] is True
    assert all(row["id"] != item["id"] for row in deleted.get("imagePool", []))


def test_image_translate_api_returns_configuration_warning_without_key(backend_server: str, sample_product: dict) -> None:
    sample_product["source"]["image_pool"] = [
        {
            "id": "source_1",
            "url": "https://example.com/source-1.jpg",
            "preview_url": "https://example.com/source-1.jpg",
            "origin": "1688",
            "usage": "main",
            "platforms": ["mercadolibre"],
            "is_main": True,
            "selected": True,
            "order": 0,
            "status": "ready",
        }
    ]
    saved = post_json(backend_server, "/api/save-product", {"product": sample_product})
    data = post_json(
        backend_server,
        "/api/image-translate",
        {
            "product_id": saved["product"]["product_id"],
            "platform": "mercadolibre",
            "language": "Spanish (Mexico)",
            "source_image_ids": ["source_1"],
        },
    )

    assert data["ok"] is False
    assert data.get("message")
    assert "图片翻译服务" in data.get("message", "")
    assert data["language"] == "Spanish (Mexico)"
    assert data["imagePoolItems"] == []
    assert "Target language: Spanish (Mexico)" in data["prompt"]
    assert data["product"]["product_id"]



def test_calculate_price_api_returns_frontend_fields(backend_server: str) -> None:
    data = post_json(
        backend_server,
        "/api/calculate-price",
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "purchase_cost": 30,
            "weight_kg": 0.5,
            "length_cm": 20,
            "width_cm": 15,
            "height_cm": 10,
            "target_margin_percent": 30,
            "commission_percent": 16,
            "usd_cny_rate": 7.2,
            "mxn_usd_rate": 18,
        },
    )

    assert data["suggested_price_mxn"] > 0
    assert data["suggested_price_usd"] > 0
    assert data["net_revenue_cny"] > 0
    assert data["profit_cny"] > 0
    assert data["shipping_cost_usd"] > 0
    assert isinstance(data["breakdown"], dict)


def test_generate_copy_api_returns_warning_without_key(backend_server: str, sample_product: dict) -> None:
    saved = post_json(backend_server, "/api/save-product", {"product": sample_product})
    product_id = saved["product"]["product_id"]
    data = post_json(
        backend_server,
        "/api/generate-copy",
        {
            "product_id": product_id,
            "platform": "mercadolibre",
        },
    )

    assert data["ok"] is True
    assert data["copy"]["title"]
    assert "product" in data
    assert "warning" in data


def test_generate_copy_api_requires_product_id(backend_server: str) -> None:
    data = post_json(
        backend_server,
        "/api/generate-copy",
        {"platform": "mercadolibre"},
        expected_status=400,
    )

    assert data["ok"] is False
    assert "product_id" in data["error"]


def test_save_product_and_publish_precheck_api_exist(backend_server: str, sample_product: dict) -> None:
    saved = post_json(backend_server, "/api/save-product", {"product": sample_product})
    assert saved["ok"] is True
    assert saved["product"]["product_id"]
    claimed = post_json(
        backend_server,
        "/api/claim-products",
        {"product_ids": [saved["product"]["product_id"]], "platform": "mercadolibre"},
    )
    draft_id = claimed["items"][0]["draft_ids"][0]

    precheck = post_json(
        backend_server,
        "/api/publish-precheck",
        {"draft_id": draft_id, "platform": "mercadolibre", "site": "CBT"},
    )
    assert precheck["ok"] is True
    assert "mercadolibre" in precheck["platforms"]


def test_product_research_hot_product_api_returns_candidates(backend_server: str) -> None:
    data = post_json(
        backend_server,
        "/api/v1/product-research/hot-products/search",
        {
            "search_mode": "target_only",
            "markets": {
                "target_markets": ["amazon-us"],
                "reference_markets": [],
            },
            "result_options": {
                "limit": 5,
                "sort_by": "rank",
            },
        },
    )

    assert data["ok"] is True
    assert data["run"]["run_id"].startswith("prr_")
    assert data["run"]["description"]
    assert data["run"]["expires_at"]
    for _ in range(25):
        if data["run"]["status"] in {"completed", "failed"}:
            break
        time.sleep(0.2)
        data = get_json(
            backend_server,
            "/api/v1/product-research/hot-products/runs",
            {"run_id": data["run"]["run_id"]},
        )
    assert isinstance(data["items"], list)
    if data["run"]["status"] not in {"completed", "failed"}:
        assert data["run"]["status"] in {"queued", "running"}
        assert data["run"]["description"]
        return
    assert data["source_status"]
    if data["items"]:
        assert data["items"][0]["title"]
        assert data["items"][0]["rank"] >= 1
        assert data["items"][0]["source_url"].startswith("https://")
        assert any(row["status"] == "success" and row["items_found"] > 0 for row in data["source_status"])
    else:
        assert any(row["status"] in {"failed", "configuration_required", "empty"} for row in data["source_status"])


def test_product_research_search_provider_test_api(backend_server: str) -> None:
    data = post_json(
        backend_server,
        "/api/v1/product-research/search-providers/test",
        {
            "provider": {
                "id": "manual_test_import",
                "name": "Manual Test Import",
                "source_type": "manual_import",
                "platform": "manual_import",
                "enabled": True,
                "priority": 1,
                "supported_markets": ["US"],
                "supported_languages": ["en"],
                "supported_data_types": ["marketplace_products"],
                "auth_required": False,
                "config_json": {
                    "provider_strategy": "manual_import",
                    "items": [
                        {
                            "title": "Mahjong gift lamp",
                            "source_url": "https://example.com/mahjong-lamp",
                            "keyword": "mahjong gift",
                            "market": "US",
                        }
                    ],
                },
            },
            "options": {
                "market": "US",
                "language": "en",
                "keyword": "mahjong gift",
                "data_type": "marketplace_products",
            },
        },
    )

    assert data["ok"] is True
    assert data["status"] == "success"
    assert data["items_found"] == 1
    assert data["sample"]["keyword"] == "mahjong gift"
    assert data["sample"]["source_url"] == "https://example.com/mahjong-lamp"

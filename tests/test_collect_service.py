from __future__ import annotations

from pathlib import Path

from conftest import assert_no_old_path
from services import collect_service


SAMPLE_1688_TEXT = """
源链接：https://detail.1688.com/offer/123456.html
标题：可折叠收纳盒 家用塑料整理箱
价格：¥12.50
规格：大号 40x30x20 cm
SKU：蓝色大号, 白色小号
材质：PP塑料
重量：0.85 kg
图片：https://example.com/main.jpg
图片：https://example.com/detail.png
包装清单：收纳盒, 说明书
"""


def test_manual_1688_text_parses_core_fields(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    result = collect_service.clean_1688_text(SAMPLE_1688_TEXT, "https://detail.1688.com/offer/123456.html")

    assert result["ok"] is True
    assert result["source_url"] == "https://detail.1688.com/offer/123456.html"
    assert result.get("title")
    assert "收纳盒" in result["title"]
    assert float(result.get("source_price_cny")) == 12.5
    assert result.get("source_weight_kg") == "0.85"
    assert result.get("images") and len(result["images"]) == 2
    assert result.get("skus") or result.get("specs")
    assert_no_old_path(result, old_path_markers)


def test_verification_text_requires_manual_handling(old_path_markers: tuple[str, ...]) -> None:
    result = collect_service.clean_1688_text("请登录后继续，滑块验证，安全验证", "https://detail.1688.com/offer/verify.html")

    assert result["ok"] is True
    assert result["manual_required"] is True
    assert "人工" in result.get("message", "") or "手动" in result.get("message", "")
    assert_no_old_path(result, old_path_markers)


def test_collect_service_does_not_reference_legacy_runtime_paths(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    service_source = (app_dir / "services" / "collect_service.py").read_text(encoding="utf-8", errors="ignore")
    assert_no_old_path(service_source, old_path_markers)

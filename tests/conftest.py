from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest
import requests


APP_DIR = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("ERP_TEST_BASE_URL", "http://127.0.0.1:5000")
OLD_PATH_MARKERS = (
    r"C:\Users\miami\Documents\Codex\2026-05-23\wb-10",
    r"C:/Users/miami/Documents/Codex/2026-05-23/wb-10",
    r"D:\wb-10",
    r"D:/wb-10",
    r"D:\wb-10-web",
    r"D:/wb-10-web",
)

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


@pytest.fixture(scope="session")
def app_dir() -> Path:
    return APP_DIR


@pytest.fixture(scope="session")
def old_path_markers() -> tuple[str, ...]:
    return OLD_PATH_MARKERS


def _server_ready() -> bool:
    try:
        response = requests.get(f"{BASE_URL}/api/state", timeout=3)
        return response.status_code == 200
    except requests.RequestException:
        return False


@pytest.fixture(scope="session")
def backend_server() -> Iterator[str]:
    process: subprocess.Popen[str] | None = None
    if not _server_ready():
        env = os.environ.copy()
        env["ERP_SKIP_OPEN_BROWSER"] = "1"
        process = subprocess.Popen(
            [sys.executable, str(APP_DIR / "erp_web_app.py")],
            cwd=str(APP_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if _server_ready():
                break
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"ERP backend exited early: {stderr}")
            time.sleep(0.5)
        else:
            raise RuntimeError("ERP backend did not become ready on port 5000.")
    yield BASE_URL
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture()
def sample_product() -> dict:
    return {
        "name": "Stage 3A backend test product",
        "brand": "Generic",
        "model": "T-3A",
        "materials": ["ABS"],
        "selling_points": ["Reusable", "Easy to clean"],
        "package_includes": ["Main unit", "Manual"],
        "source": {
            "title": "Stage 3A backend test product",
            "source_url": "https://detail.1688.com/offer/123456.html",
            "source_platform": "1688",
            "description": "Manual imported product for backend tests.",
            "weight_kg": "0.5",
            "dimensions": {"length_cm": "20", "width_cm": "15", "height_cm": "10"},
            "image_pool": [],
        },
        "drafts": {
            "mercadolibre": {
                "enabled": True,
                "title": "Stage 3A backend test product",
                "description": "Manual imported product for backend tests.",
                "brand": "Generic",
                "model": "T-3A",
                "category_id": "MLM-100",
                "price": "199",
                "available_quantity": "3",
                "condition": "new",
                "listing_type_id": "gold_special",
                "images": [],
                "attributes": {"BRAND": "Generic", "MODEL": "T-3A"},
                "package_dimensions": {
                    "length_cm": "20",
                    "width_cm": "15",
                    "height_cm": "10",
                    "weight_kg": "0.5",
                },
            }
        },
    }


def assert_no_old_path(value: object, markers: tuple[str, ...] = OLD_PATH_MARKERS) -> None:
    text = str(value)
    matches = [marker for marker in markers if marker in text]
    assert not matches, f"found legacy path markers: {matches}"

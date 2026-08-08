from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

import pytest
import requests


APP_DIR = Path(__file__).resolve().parents[1]
EXTERNAL_BASE_URL = os.environ.get("ERP_TEST_BASE_URL", "").rstrip("/")
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


@pytest.fixture(autouse=True)
def _isolated_app_context(tmp_path: Path) -> Iterator[None]:
    """每个测试自动换到独立 AppContext（tmp 下的 SQLite/配置根）。

    context 挂载的有状态服务（db/products/config/research/ai_journal/
    exchange_rates）因此绝不会把测试数据写进真实仓库；需要把 context 绑定
    到测试自己目录的用例，再嵌套一层 temp_app_context 即可。
    """
    from tests.runtime_test_utils import temp_app_context

    with temp_app_context(tmp_path / "__ctx__"):
        yield


@pytest.fixture(scope="session")
def app_dir() -> Path:
    return APP_DIR


@pytest.fixture(scope="session")
def old_path_markers() -> tuple[str, ...]:
    return OLD_PATH_MARKERS


def _server_ready(base_url: str) -> bool:
    try:
        response = requests.get(f"{base_url}/api/state", timeout=3)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _available_local_port() -> int:
    """Reserve an ephemeral loopback port long enough to choose the test URL."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _prepare_backend_test_app_dir(app_dir: Path) -> None:
    """Copy non-secret runtime configuration needed by API integration tests."""
    source_config = APP_DIR / "config"
    target_config = app_dir / "config"
    if source_config.exists():
        shutil.copytree(
            source_config,
            target_config,
            ignore=shutil.ignore_patterns(
                "app_config.json",
                "store_config.json",
                "ai_config.snapshot.json",
                "local-backup",
            ),
        )


@pytest.fixture(scope="session")
def backend_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    if EXTERNAL_BASE_URL:
        base_url = EXTERNAL_BASE_URL
        parsed = urlsplit(base_url)
        port = int(parsed.port or os.environ.get("ERP_PORT", "5050"))
        server_app_dir = APP_DIR
    else:
        port = _available_local_port()
        base_url = f"http://127.0.0.1:{port}"
        server_app_dir = tmp_path_factory.mktemp("backend-server")
        _prepare_backend_test_app_dir(server_app_dir)

    process: subprocess.Popen[str] | None = None
    if not _server_ready(base_url):
        env = os.environ.copy()
        env["ERP_SKIP_OPEN_BROWSER"] = "1"
        env["ERP_NO_BROWSER"] = "1"
        env["ERP_PORT"] = str(port)
        env["ERP_APP_DIR"] = str(server_app_dir)
        process = subprocess.Popen(
            [sys.executable, "-m", "erp_web.server"],
            cwd=str(APP_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if _server_ready(base_url):
                break
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"ERP backend exited early: {stderr}")
            time.sleep(0.5)
        else:
            raise RuntimeError(f"ERP backend did not become ready on port {port}.")
    yield base_url
    if process:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


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
                "target_sites": [{"platform": "mercadolibre", "site": "MLM", "language": "es", "market_currency": "MXN", "listing_currency": "MXN"}],
                "pricing": {"targets": {"mercadolibre:mlm": {"listing_currency": "MXN", "applied_price": {"amount": "199", "currency": "MXN"}}}},
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

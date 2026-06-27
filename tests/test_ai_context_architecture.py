from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def python_files(*folders: str) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        files.extend((ROOT / folder).glob("*.py"))
    return sorted(files)


def test_route_and_facade_layers_do_not_import_runtime_star() -> None:
    banned = "import *"
    for path in python_files("erp_web/http_route_units", "erp_web/facades"):
        text = path.read_text(encoding="utf-8")
        assert banned not in text, f"{path.relative_to(ROOT)} should use explicit imports"


def test_runtime_publish_and_collect_aggregators_use_explicit_exports() -> None:
    for path in [
        ROOT / "erp_web/runtime_units/publish_runtime.py",
        ROOT / "erp_web/runtime_units/source_collect.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "import *" not in text, f"{path.relative_to(ROOT)} should list exported symbols explicitly"
        assert "__all__" in text, f"{path.relative_to(ROOT)} should document its public API"


def test_refactored_model_and_marketplace_units_do_not_use_wildcard_imports() -> None:
    for path in python_files("product_model_units", "marketplace_publish_units"):
        if path.name in {"__init__.py", "common.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "import *" not in text, f"{path.relative_to(ROOT)} should use explicit imports"


def test_refactored_runtime_units_do_not_use_wildcard_imports() -> None:
    for path in python_files("erp_web/runtime_units"):
        if path.name in {"__init__.py", "runtime_common.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "import *" not in text, f"{path.relative_to(ROOT)} should use explicit imports"


def test_image_pool_core_breaks_runtime_import_cycles() -> None:
    product_store = (ROOT / "erp_web/runtime_units/product_store.py").read_text(encoding="utf-8")
    image_pool = (ROOT / "erp_web/runtime_units/image_pool.py").read_text(encoding="utf-8")
    publish_mercadolibre = (ROOT / "erp_web/runtime_units/publish_mercadolibre.py").read_text(encoding="utf-8")

    assert "from .image_pool import" not in product_store
    assert "from .publish_mercadolibre import" not in image_pool
    assert "from .image_pool_core import" in product_store
    assert "from .image_pool_core import" in publish_mercadolibre


def test_context_map_mentions_runtime_compatibility_boundary() -> None:
    text = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    assert "compatibility aggregator" in text
    assert "Do not add new `from erp_web.runtime import *`" in text


def test_context_map_mentions_product_research_entry_points() -> None:
    text = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    assert "erp_web/http_route_units/product_research_routes.py" in text
    assert "erp_web/product_research_config.py" in text
    assert "services/product_research_service.py" in text
    assert "erp_web/schemas/product_research.py" in text

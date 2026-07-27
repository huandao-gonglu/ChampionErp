# -*- coding: utf-8 -*-
from __future__ import annotations

"""Application context: single home for path/port wiring.

``AppPaths`` collects every filesystem location and port the backend derives
from the application directory (previously 40+ loose constants in
``erp_web.runtime_units.runtime_common``). ``AppContext`` carries the
process-wide wiring; later stages will add the database handle, the publishing
bus, etc.

New code should call ``get_context().paths`` instead of importing path
constants from ``runtime_common`` (which now merely re-exports these values
for backward compatibility).
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from erp_web.db import DEFAULT_DB_NAME, ErpDatabase

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycles
    from erp_web.runtime_units.publishing_bus_core import PublishingBus
    from erp_web.runtime_units.pricing_runtime import ExchangeRateService
    from erp_web.services.ai_work_service import AiWorkJournal
    from erp_web.services.product_research_service import ProductResearchRunRegistry
    from erp_web.stores.config_store import ConfigStore
    from erp_web.stores.product_store import ProductStore

_DEFAULT_APP_DIR = Path(
    os.environ.get("ERP_APP_DIR", str(Path(__file__).resolve().parents[1]))
).expanduser().resolve()


@dataclass(frozen=True)
class AppPaths:
    """Absolute paths and ports derived from the application directory."""

    app_dir: Path
    dist_dir: Path
    data_dir: Path
    config_dir: Path
    cache_dir: Path
    logs_dir: Path
    images_dir: Path
    exports_dir: Path
    output_dir: Path
    store_config_path: Path
    app_config_path: Path
    removed_legacy_config_paths: tuple[Path, ...]
    legacy_store_config_paths: tuple[Path, ...]
    legacy_app_config_paths: tuple[Path, ...]
    db_path: Path
    task_dir: Path
    chatgpt_dir: Path
    source_dir: Path
    upload_dir: Path
    collect_debug_dir: Path
    browser_profile_dir: Path
    browser_debug_profile_dir: Path
    front_dir: Path
    front_dist_dir: Path
    front_dist_index_path: Path
    web_template_path: Path
    web_port: int
    browser_debug_port: int

    @classmethod
    def from_app_dir(cls, app_dir: Path) -> "AppPaths":
        """Derive the full path layout from ``app_dir``.

        Port convention — ERP_PORT is the single knob:
          default 5000 (production / Electron desktop, front/desktop/main.cjs
          follows it); scripts/dev.sh exports ERP_PORT=5050 and points the Vite
          proxy at it; tests/conftest.py builds its base URL from the same
          variable.
        """
        app_dir = Path(app_dir)
        data_dir = app_dir / "data"
        config_dir = app_dir / "config"
        cache_dir = data_dir / "cache"
        logs_dir = data_dir / "logs"
        images_dir = data_dir / "images"
        output_dir = logs_dir
        removed_legacy: tuple[Path, ...] = ()
        front_dir = app_dir / "front"
        front_dist_dir = app_dir / "erp_web" / "static" / "dist"
        return cls(
            app_dir=app_dir,
            dist_dir=app_dir / "dist",
            data_dir=data_dir,
            config_dir=config_dir,
            cache_dir=cache_dir,
            logs_dir=logs_dir,
            images_dir=images_dir,
            exports_dir=data_dir / "exports",
            output_dir=output_dir,
            store_config_path=config_dir / "store_config.json",
            app_config_path=config_dir / "app_config.json",
            removed_legacy_config_paths=removed_legacy,
            legacy_store_config_paths=removed_legacy,
            legacy_app_config_paths=removed_legacy,
            db_path=app_dir / DEFAULT_DB_NAME,
            task_dir=output_dir / "codex_tasks",
            chatgpt_dir=images_dir / "chatgpt",
            source_dir=images_dir / "source",
            upload_dir=images_dir / "uploads",
            collect_debug_dir=cache_dir / "collect_debug",
            browser_profile_dir=app_dir / "browser_profile" / "1688",
            browser_debug_profile_dir=Path(
                os.environ.get("ERP_BROWSER_PROFILE_DIR", str(app_dir / "browser_profile" / "debug"))
            ),
            front_dir=front_dir,
            front_dist_dir=front_dist_dir,
            front_dist_index_path=front_dist_dir / "index.html",
            web_template_path=front_dir / "index.html",
            web_port=int(os.environ.get("ERP_PORT", "5000")),
            browser_debug_port=int(os.environ.get("ERP_BROWSER_DEBUG_PORT", "9222")),
        )


class AppContext:
    """Process-wide application wiring: paths, database and the stateful services.

    The store/service attributes are created lazily on first access.  Their
    modules import runtime units which in turn import this module at import
    time (``runtime_common`` still derives its legacy path constants from
    ``get_context()``), so eager construction inside ``__init__`` would
    dead-lock the import graph.  Lazy construction happens strictly after the
    import phase, with a lock so each context owns exactly one instance.
    """

    def __init__(self, paths: AppPaths, db: ErpDatabase) -> None:
        self.paths = paths
        self.db = db
        # A lazy service may depend on another lazy service while it is being
        # constructed (the publishing bus needs both products and config).
        # Re-entrant locking keeps that dependency chain safe without
        # deadlocking the process during server startup.
        self._lazy_lock = threading.RLock()
        self._products: "ProductStore | None" = None
        self._config: "ConfigStore | None" = None
        self._research: "ProductResearchRunRegistry | None" = None
        self._ai_journal: "AiWorkJournal | None" = None
        self._exchange_rates: "ExchangeRateService | None" = None
        self._publishing_bus: "PublishingBus | None" = None

    @property
    def products(self) -> "ProductStore":
        if self._products is None:
            with self._lazy_lock:
                if self._products is None:
                    from erp_web.stores.product_store import ProductStore

                    self._products = ProductStore(self.db)
        return self._products

    @property
    def config(self) -> "ConfigStore":
        if self._config is None:
            with self._lazy_lock:
                if self._config is None:
                    from erp_web.stores.config_store import ConfigStore

                    self._config = ConfigStore(self.paths, self.db)
        return self._config

    @property
    def research(self) -> "ProductResearchRunRegistry":
        if self._research is None:
            with self._lazy_lock:
                if self._research is None:
                    from erp_web.services.product_research_service import ProductResearchRunRegistry

                    self._research = ProductResearchRunRegistry(self.db)
        return self._research

    @property
    def ai_journal(self) -> "AiWorkJournal":
        if self._ai_journal is None:
            with self._lazy_lock:
                if self._ai_journal is None:
                    from erp_web.services.ai_work_service import AiWorkJournal

                    self._ai_journal = AiWorkJournal(self.paths, self.db)
        return self._ai_journal

    @property
    def exchange_rates(self) -> "ExchangeRateService":
        if self._exchange_rates is None:
            with self._lazy_lock:
                if self._exchange_rates is None:
                    from erp_web.runtime_units.pricing_runtime import ExchangeRateService

                    self._exchange_rates = ExchangeRateService(self.db)
        return self._exchange_rates

    @property
    def publishing_bus(self) -> "PublishingBus":
        if self._publishing_bus is None:
            with self._lazy_lock:
                if self._publishing_bus is None:
                    from erp_web.runtime_units.publish_adapter import build_publishing_bus

                    self._publishing_bus = build_publishing_bus(self)
        return self._publishing_bus


_context_lock = threading.Lock()
_context: AppContext | None = None


def build_default_context() -> AppContext:
    """Build the context from the repository layout (current APP_DIR logic)."""
    paths = AppPaths.from_app_dir(_DEFAULT_APP_DIR)
    return AppContext(paths=paths, db=ErpDatabase(paths.db_path))


def get_context() -> AppContext:
    """Return the active context, lazily constructing the default one."""
    global _context
    context = _context
    if context is None:
        with _context_lock:
            context = _context
            if context is None:
                context = build_default_context()
                _context = context
    return context


def set_context(context: AppContext) -> None:
    """Install the process-wide context (server startup, tests)."""
    global _context
    with _context_lock:
        _context = context

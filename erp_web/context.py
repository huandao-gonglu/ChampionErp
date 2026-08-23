# -*- coding: utf-8 -*-
from __future__ import annotations

"""Application context: single home for path/port wiring.

``AppPaths`` collects every filesystem location and port the backend derives
from the application directory. ``AppContext`` carries the process-wide
wiring for the database and stateful services. Runtime compatibility path
constants have been removed; callers resolve paths through the active context.
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from erp_web.db import DEFAULT_DB_NAME, ErpDatabase

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycles
    from erp_web.runtime_units.category_catalog import CategoryCatalog
    from erp_web.runtime_units.publishing_bus_core import PublishingBus
    from erp_web.runtime_units.pricing_runtime import ExchangeRateService
    from erp_web.services.ai_chat_run_registry import AiChatRunRegistry
    from erp_web.services.ai_presentation_registry import (
        AiPresentationRegistry,
    )
    from erp_web.services.approval_session import ApprovalSession
    from erp_web.services.image_delivery_service import ImageDeliveryService
    from erp_web.services.ai_conversation_event_bus import AiConversationEventBus
    from erp_web.services.product_research_service import ProductResearchRunRegistry
    from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnClaimStore
    from erp_web.stores.config_store import ConfigStore
    from erp_web.stores.draft_query_snapshot_store import DraftQuerySnapshotStore
    from erp_web.stores.global_task_store import LocalGlobalTaskStore
    from erp_web.stores.product_store import ProductStore
    from erp_web.stores.pydantic_ai_event_outbox_store import (
        PydanticAiEventOutboxStore,
    )
    from erp_web.stores.pydantic_deferred_task_link_store import (
        PydanticDeferredTaskLinkStore,
    )
    from erp_web.stores.pydantic_message_store import PydanticMessageStore

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
    time, so eager construction inside ``__init__`` would risk an import
    cycle. Lazy construction happens strictly after the import phase, with a
    lock so each context owns exactly one instance.

    ``close()`` 是上下文所持资源的显式生命周期边界。它可幂等调用，且只会
    关闭已经实际创建的 lazy resource；关闭一个从未使用过的上下文不会为了
    销毁资源而反向构造 ``PublishingBus``。
    """

    def __init__(self, paths: AppPaths, db: ErpDatabase) -> None:
        self.paths = paths
        self.db = db
        # A lazy service may depend on another lazy service while it is being
        # constructed (the publishing bus needs both products and config).
        # Re-entrant locking keeps that dependency chain safe without
        # deadlocking the process during server startup.
        self._lazy_lock = threading.RLock()
        self._closed = False
        self._products: "ProductStore | None" = None
        self._config: "ConfigStore | None" = None
        self._research: "ProductResearchRunRegistry | None" = None
        self._pydantic_messages: "PydanticMessageStore | None" = None
        self._chat_turn_claims: "AiChatTurnClaimStore | None" = None
        self._chat_runs: "AiChatRunRegistry | None" = None
        self._draft_query_snapshots: "DraftQuerySnapshotStore | None" = None
        self._exchange_rates: "ExchangeRateService | None" = None
        self._image_delivery: "ImageDeliveryService | None" = None
        self._publishing_bus: "PublishingBus | None" = None
        self._global_tasks: "LocalGlobalTaskStore | None" = None
        self._deferred_task_links: "PydanticDeferredTaskLinkStore | None" = None
        self._ai_event_outbox: "PydanticAiEventOutboxStore | None" = None
        self._conversation_event_bus: "AiConversationEventBus | None" = None
        self._ai_presentations: "AiPresentationRegistry | None" = None
        self._approval_session: "ApprovalSession | None" = None
        self._category_catalog: "CategoryCatalog | None" = None

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
    def pydantic_messages(self) -> "PydanticMessageStore":
        if self._pydantic_messages is None:
            with self._lazy_lock:
                if self._pydantic_messages is None:
                    from erp_web.stores.pydantic_message_store import (
                        PydanticMessageStore,
                    )

                    self._pydantic_messages = PydanticMessageStore(self.db)
        return self._pydantic_messages

    @property
    def chat_turn_claims(self) -> "AiChatTurnClaimStore":
        if self._chat_turn_claims is None:
            with self._lazy_lock:
                if self._chat_turn_claims is None:
                    from erp_web.stores.ai_chat_turn_claim_store import (
                        AiChatTurnClaimStore,
                    )

                    self._chat_turn_claims = AiChatTurnClaimStore(self.db)
        return self._chat_turn_claims

    @property
    def chat_runs(self) -> "AiChatRunRegistry":
        if self._chat_runs is None:
            with self._lazy_lock:
                if self._chat_runs is None:
                    from erp_web.services.ai_chat_run_registry import (
                        AiChatRunRegistry,
                    )

                    self._chat_runs = AiChatRunRegistry()
        return self._chat_runs

    @property
    def draft_query_snapshots(self) -> "DraftQuerySnapshotStore":
        if self._draft_query_snapshots is None:
            with self._lazy_lock:
                if self._draft_query_snapshots is None:
                    from erp_web.stores.draft_query_snapshot_store import (
                        DraftQuerySnapshotStore,
                    )

                    self._draft_query_snapshots = DraftQuerySnapshotStore(
                        self.db
                    )
        return self._draft_query_snapshots

    @property
    def exchange_rates(self) -> "ExchangeRateService":
        if self._exchange_rates is None:
            with self._lazy_lock:
                if self._exchange_rates is None:
                    from erp_web.runtime_units.pricing_runtime import ExchangeRateService

                    self._exchange_rates = ExchangeRateService(self.db)
        return self._exchange_rates

    @property
    def image_delivery(self) -> "ImageDeliveryService":
        if self._image_delivery is None:
            with self._lazy_lock:
                if self._image_delivery is None:
                    from erp_web.services.image_delivery_service import ImageDeliveryService

                    self._image_delivery = ImageDeliveryService(self.paths)
        return self._image_delivery

    @property
    def publishing_bus(self) -> "PublishingBus":
        with self._lazy_lock:
            if self._closed:
                raise RuntimeError("AppContext 已关闭，不能再访问 publishing_bus")
            if self._publishing_bus is None:
                from erp_web.runtime_units.publish_adapter import build_publishing_bus

                self._publishing_bus = build_publishing_bus(self)
            return self._publishing_bus

    @property
    def category_catalog(self) -> "CategoryCatalog":
        """统一类目读取入口；业务模块一律通过它获取平台类目事实。"""

        if self._category_catalog is None:
            with self._lazy_lock:
                if self._category_catalog is None:
                    from erp_web.runtime_units.category_catalog import (
                        build_category_catalog,
                    )

                    self._category_catalog = build_category_catalog()
        return self._category_catalog

    @property
    def global_tasks(self) -> "LocalGlobalTaskStore":
        if self._global_tasks is None:
            with self._lazy_lock:
                if self._global_tasks is None:
                    from erp_web.stores.global_task_store import LocalGlobalTaskStore

                    self._global_tasks = LocalGlobalTaskStore(self.db)
        return self._global_tasks

    @property
    def deferred_task_links(self) -> "PydanticDeferredTaskLinkStore":
        if self._deferred_task_links is None:
            with self._lazy_lock:
                if self._deferred_task_links is None:
                    from erp_web.stores.pydantic_deferred_task_link_store import (
                        PydanticDeferredTaskLinkStore,
                    )

                    self._deferred_task_links = PydanticDeferredTaskLinkStore(
                        self.db
                    )
        return self._deferred_task_links

    @property
    def ai_event_outbox(self) -> "PydanticAiEventOutboxStore":
        if self._ai_event_outbox is None:
            with self._lazy_lock:
                if self._ai_event_outbox is None:
                    from erp_web.stores.pydantic_ai_event_outbox_store import (
                        PydanticAiEventOutboxStore,
                    )

                    self._ai_event_outbox = PydanticAiEventOutboxStore(self.db)
        return self._ai_event_outbox

    @property
    def conversation_event_bus(self) -> "AiConversationEventBus":
        if self._conversation_event_bus is None:
            with self._lazy_lock:
                if self._conversation_event_bus is None:
                    from erp_web.services.ai_conversation_event_bus import (
                        AiConversationEventBus,
                    )

                    self._conversation_event_bus = AiConversationEventBus()
        return self._conversation_event_bus

    @property
    def ai_presentations(self) -> "AiPresentationRegistry":
        """进程内通用 presentation registry（reservation/claim/lease/chunk 缓冲）。"""

        if self._ai_presentations is None:
            with self._lazy_lock:
                if self._ai_presentations is None:
                    from erp_web.services.ai_presentation_registry import (
                        AiPresentationRegistry,
                    )

                    self._ai_presentations = AiPresentationRegistry()
        return self._ai_presentations

    @property
    def approval_session(self) -> "ApprovalSession":
        """进程级可信审批凭据；token 只随受信 UI bootstrap 下发，不进入模型上下文。"""

        if self._approval_session is None:
            with self._lazy_lock:
                if self._approval_session is None:
                    from erp_web.services.approval_session import ApprovalSession

                    self._approval_session = ApprovalSession()
        return self._approval_session

    @property
    def closed(self) -> bool:
        with self._lazy_lock:
            return self._closed

    def close(self) -> None:
        """幂等关闭当前上下文已经创建的资源。"""

        with self._lazy_lock:
            if self._closed:
                return
            self._closed = True
            publishing_bus = self._publishing_bus
        if publishing_bus is not None:
            publishing_bus.executor.shutdown(wait=True)


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


def peek_context() -> AppContext | None:
    """读取已安装上下文但不触发默认数据库构造，供生命周期编排使用。"""

    with _context_lock:
        return _context


def set_context(context: AppContext) -> None:
    """安装当前上下文，但不自动关闭此前的上下文。

    生命周期仍由调用方持有，嵌套临时上下文才能只关闭内层实例，再恢复外层
    上下文。
    """
    global _context
    with _context_lock:
        _context = context


def clear_context() -> None:
    """移除当前上下文但不关闭它；资源生命周期仍由调用方持有。"""

    global _context
    with _context_lock:
        _context = None

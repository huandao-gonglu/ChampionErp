# -*- coding: utf-8 -*-
from __future__ import annotations

"""Application context: single home for path/port wiring.

``AppPaths`` collects every filesystem location and port the backend derives
from the application directory. ``AppContext`` carries each workspace's
wiring for the database and stateful services. Runtime compatibility path
constants have been removed; callers resolve paths through the active context.
"""

import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from erp_web.db import DEFAULT_DB_NAME, ErpDatabase

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycles
    from erp_web.runtime_units.category_catalog import CategoryCatalog
    from erp_web.runtime_units.publishing_bus_core import PublishingBus
    from erp_web.runtime_units.pricing_runtime import ExchangeRateService
    from erp_web.services.ai_chat_run_registry import AiChatRunRegistry
    from erp_web.services.approval_session import ApprovalSession
    from erp_web.services.image_delivery_service import ImageDeliveryService
    from erp_web.services.product_research_service import ProductResearchRunRegistry
    from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnClaimStore
    from erp_web.stores.category_catalog_store import CategoryCatalogStore
    from erp_web.stores.config_store import ConfigStore
    from erp_web.stores.draft_query_snapshot_store import DraftQuerySnapshotStore
    from erp_web.stores.product_store import ProductStore
    from erp_web.stores.pydantic_message_store import PydanticMessageStore

_DEFAULT_APP_DIR = (
    Path(os.environ.get("ERP_APP_DIR", str(Path(__file__).resolve().parents[1])))
    .expanduser()
    .resolve()
)


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
    front_dir: Path
    front_dist_dir: Path
    front_dist_index_path: Path
    web_template_path: Path
    web_port: int

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
        # 前端属于程序资源；ERP_APP_DIR 只选择业务数据和配置根目录。
        package_dir = Path(__file__).resolve().parent
        front_dir = package_dir.parent / "front"
        front_dist_dir = package_dir / "static" / "dist"
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
            front_dir=front_dir,
            front_dist_dir=front_dist_dir,
            front_dist_index_path=front_dist_dir / "index.html",
            web_template_path=front_dir / "index.html",
            web_port=int(os.environ.get("ERP_PORT", "5000")),
        )


class AppContext:
    """个人业务空间的路径、数据库与有状态服务，由请求局部绑定选择。

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
        self.shared_paths = paths
        self.db = db
        self.user_id = ""
        self.model_key = None
        self.allow_environment_credentials = True
        self.account_store = None
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
        self._agent_calls = None
        self._chat_runs: "AiChatRunRegistry | None" = None
        self._draft_query_snapshots: "DraftQuerySnapshotStore | None" = None
        self._exchange_rates: "ExchangeRateService | None" = None
        self._image_delivery: "ImageDeliveryService | None" = None
        self._publishing_bus: "PublishingBus | None" = None
        self._approval_session: "ApprovalSession | None" = None
        self._category_catalog: "CategoryCatalog | None" = None
        self._category_catalog_store: "CategoryCatalogStore | None" = None
        self._online_products = None
        self._task_queue = None
        self._external_requests = None
        self._external_request_limiter = None
        self._order_notifications = None
        self._order_procurement = None
        self._alibaba_self_purchases = None
        self._alibaba_direct_purchases = None
        self._alibaba_read_cache = None
        self._onebound_quota = None
        self._fulfillment = None
        self._yandex_logistics = None
        self._pricing_cache = None
        self._conversation_titles = None
        self._image_batches = None
        self._desktop_connections = None
        self._desktop_handoffs = None
        self._collection_links = None
        self._collection_receipts = None
        self._wechat = None
        self._wechat_store = None

    @property
    def collection_links(self):
        with self._lazy_lock:
            if self._collection_links is None:
                from erp_web.stores.collection_links import CollectionLinks
                self._collection_links = CollectionLinks(self.paths.data_dir / 'collection-links.sqlite3')
            return self._collection_links

    @property
    def collection_receipts(self):
        with self._lazy_lock:
            if self._collection_receipts is None:
                from erp_web.services.collection_receipts import CollectionReceipts
                self._collection_receipts = CollectionReceipts()
            return self._collection_receipts

    @property
    def desktop_handoffs(self):
        with self._lazy_lock:
            if self._closed:
                raise RuntimeError("AppContext 已关闭，不能再访问桌面交接。")
            if self._desktop_handoffs is None:
                from erp_web.services.desktop_handoff_service import DesktopHandoffs
                self._desktop_handoffs = DesktopHandoffs(
                    session_validator=self.account_store.session_valid if self.account_store else None,
                )
            return self._desktop_handoffs

    @property
    def desktop_connections(self):
        with self._lazy_lock:
            if self._closed:
                raise RuntimeError("AppContext 已关闭，不能再访问桌面连接。")
            if self._desktop_connections is None:
                from erp_web.services.desktop_connection_service import DesktopConnections
                self._desktop_connections = DesktopConnections(
                    session_validator=self.account_store.session_valid if self.account_store else None,
                )
            return self._desktop_connections

    @property
    def wechat_store(self):
        with self._lazy_lock:
            if self._closed:
                raise RuntimeError("AppContext 已关闭，不能再访问微信存储。")
            if self._wechat_store is None:
                from erp_web.stores.wechat_channel_store import WechatChannelStore

                self._wechat_store = WechatChannelStore(self.paths.data_dir / "wechat-channel.sqlite3", self.config)
            return self._wechat_store

    @property
    def wechat(self):
        with self._lazy_lock:
            if self._closed:
                raise RuntimeError("AppContext 已关闭，不能再访问微信连接。")
            if self._wechat is None:
                from erp_web.facades.ai_chat_facade import build_conversation_service
                from erp_web.services.wechat_channel_service import WechatChannelService

                self._wechat = WechatChannelService(
                    store=self.wechat_store,
                    conversations=build_conversation_service(self),
                    conversation_factory=lambda: build_conversation_service(self),
                    forward_types_reader=lambda: self.config.system_settings()["wechat_forward_types"],
                    forward_types_writer=lambda values: self.config.save_system_settings({"wechat_forward_types": values}),
                )
            return self._wechat

    def _notify_conversation_changed(self, conversation_id: str) -> None:
        """仅唤醒已经创建的渠道；提交历史不反向构造后台资源。"""
        channel = self._wechat
        if channel is not None:
            channel.wake()

    @property
    def pricing_cache(self):
        if self._pricing_cache is None:
            with self._lazy_lock:
                if self._pricing_cache is None:
                    from erp_web.services.pricing_cache import PricingCache

                    self._pricing_cache = PricingCache()
        return self._pricing_cache

    @property
    def conversation_titles(self):
        if self._conversation_titles is None:
            with self._lazy_lock:
                if self._conversation_titles is None:
                    from erp_web.stores.conversation_titles import ConversationTitles
                    self._conversation_titles = ConversationTitles(self.paths.data_dir / "conversation-titles.sqlite3")
        return self._conversation_titles

    @property
    def image_batches(self):
        if self._image_batches is None:
            with self._lazy_lock:
                if self._image_batches is None:
                    from erp_web.stores.image_batch_store import ImageBatchStore

                    self._image_batches = ImageBatchStore(self.paths.app_dir / "data" / "image-batches.sqlite3")
        return self._image_batches

    @property
    def yandex_logistics(self):
        if self._yandex_logistics is None:
            config = self.config
            with self._lazy_lock:
                if self._yandex_logistics is None:
                    from erp_web.services.yandex_logistics_service import YandexLogisticsService

                    self._yandex_logistics = YandexLogisticsService(config, self.shared_paths.data_dir / 'tariffs', snapshot_loader=self.tariffs.load)
        return self._yandex_logistics

    @property
    def tariffs(self):
        from erp_web.services.tariff_settings_service import TariffSettingsService

        personal = self.paths.data_dir / ('personal-tariffs' if self.paths == self.shared_paths else 'tariffs')
        return TariffSettingsService(personal, self.shared_paths.data_dir / 'tariffs')

    @property
    def fulfillment(self):
        self.order_notifications
        if self._fulfillment is None:
            with self._lazy_lock:
                if self._fulfillment is None:
                    from erp_web.facades.fulfillment_facade import create_service

                    self._fulfillment = create_service(self)
        return self._fulfillment

    @property
    def order_procurement(self):
        # 初始化订单 worker 在锁外完成，避免跨领域装配重入同一把锁。
        self.order_notifications
        if self._order_procurement is None:
            with self._lazy_lock:
                if self._order_procurement is None:
                    from erp_web.facades.order_procurement_facade import create_service

                    self._order_procurement = create_service(self)
        return self._order_procurement

    @property
    def alibaba_self_purchases(self):
        if self._alibaba_self_purchases is None:
            with self._lazy_lock:
                if self._alibaba_self_purchases is None:
                    from erp_web.stores.alibaba_self_purchase_store import AlibabaSelfPurchaseStore

                    self._alibaba_self_purchases = AlibabaSelfPurchaseStore(self.paths.data_dir / "alibaba-self-purchases.sqlite3")
        return self._alibaba_self_purchases

    @property
    def alibaba_direct_purchases(self):
        if self._alibaba_direct_purchases is None:
            with self._lazy_lock:
                if self._alibaba_direct_purchases is None:
                    from erp_web.stores.alibaba_direct_purchase_store import AlibabaDirectPurchaseStore

                    self._alibaba_direct_purchases = AlibabaDirectPurchaseStore(
                        self.paths.data_dir / "alibaba-direct-purchases.sqlite3"
                    )
        return self._alibaba_direct_purchases

    @property
    def alibaba_read_cache(self):
        if self._alibaba_read_cache is None:
            with self._lazy_lock:
                if self._alibaba_read_cache is None:
                    from erp_web.services.alibaba_read_cache import AlibabaReadCache

                    self._alibaba_read_cache = AlibabaReadCache(
                        self.paths.data_dir / "alibaba-read-cache.sqlite3"
                    )
        return self._alibaba_read_cache

    @property
    def onebound_quota(self):
        if self._onebound_quota is None:
            with self._lazy_lock:
                if self._onebound_quota is None:
                    from erp_web.stores.onebound_quota_store import OneBoundQuotaStore

                    self._onebound_quota = OneBoundQuotaStore(self.shared_paths.data_dir / "onebound-quota.sqlite3")
        return self._onebound_quota

    @property
    def order_notifications(self):
        if self._order_notifications is None:
            with self._lazy_lock:
                if self._order_notifications is None:
                    from erp_web.facades.order_notification_facade import create_order_notification_service

                    self._order_notifications = create_order_notification_service(self)
        return self._order_notifications

    @property
    def external_requests(self):
        if self._external_requests is None:
            with self._lazy_lock:
                if self._external_requests is None:
                    from erp_web.schemas.external_requests import RequestFailure
                    from erp_web.services.external_request_context import credential_fingerprint
                    from erp_web.services.external_request_limiter import ExternalRequestLimiter
                    from erp_web.services.external_request_manager import ExternalRequestManager
                    from erp_web.stores.external_request_store import ExternalRequestStore

                    store = ExternalRequestStore(self.paths.data_dir / "external-requests.sqlite3")
                    account_keys = {"ozon": ("client_id", "api_key"), "yandex": ("business_id", "api_token"), "mercadolibre": ("user_id", "access_token")}

                    def _sync_account_block(platform: str, account_id: str, code: str, message: str, *, scope: str = "account", credential_id: str = "") -> None:
                        if platform not in account_keys:
                            return
                        if platform == "mercadolibre" and scope == "credential":
                            return
                        acc_key, secret_key = account_keys[platform]
                        saved = self.config.load_store_config().get(platform)
                        if not isinstance(saved, dict):
                            return
                        if str(saved.get(acc_key) or "").strip() != account_id:
                            return
                        if scope == "credential" and credential_id and credential_fingerprint(str(saved.get(secret_key) or "").strip()) != credential_id:
                            return
                        if str(saved.get("auth_status") or "").strip().lower() == "failed" and str(saved.get("auth_error_code") or "").strip() == code:
                            return
                        self.config.update_store_config_fields(platform, {
                            "auth_status": "failed",
                            "auth_error_code": code,
                            "auth_error_message": message,
                            "auth_next_action": "请在授权配置核对账号状态或更换凭据后重新测试连接",
                        })

                    for migrated in store.migrated_account_blocks:
                        _sync_account_block(
                            migrated["platform"],
                            migrated["account_id"],
                            migrated["code"],
                            migrated["message"] or "平台已停用该账号的 API，请在授权配置核对并重新测试连接",
                        )

                    def _on_auth_block(ctx, failure) -> None:
                        if ctx.bypass_auth_block:
                            return
                        _sync_account_block(
                            ctx.platform,
                            ctx.account_id,
                            failure.code,
                            failure.message,
                            scope=failure.scope,
                            credential_id=ctx.credential_id,
                        )

                    def _persisted_auth_check(ctx):
                        if ctx.platform not in account_keys:
                            return None
                        acc_key, _ = account_keys[ctx.platform]
                        saved = self.config.load_store_config().get(ctx.platform)
                        if not isinstance(saved, dict):
                            return None
                        if str(saved.get(acc_key) or "").strip() != ctx.account_id:
                            return None
                        status = str(saved.get("auth_status") or "").strip().lower()
                        code = str(saved.get("auth_error_code") or "").strip()
                        if status in {"failed", "error", "测试失败"} and code.upper().endswith("_ACCOUNT_DISABLED"):
                            message = str(saved.get("auth_error_message") or "").strip() or "平台已停用该账号的 API，请在授权配置核对并重新测试连接"
                            return RequestFailure(code, message, "account", status=403)
                        return None

                    limiter = self._external_request_limiter or ExternalRequestLimiter()
                    self._external_requests = ExternalRequestManager(
                        store,
                        limiter=limiter,
                        on_auth_block=_on_auth_block,
                        persisted_auth_check=_persisted_auth_check,
                    )
        return self._external_requests

    @property
    def task_queue(self):
        with self._lazy_lock:
            if self._task_queue is None:
                from erp_web.services.task_queue_service import TaskQueueService

                self._task_queue = TaskQueueService(self)
            return self._task_queue

    @property
    def online_products(self):
        if self._online_products is None:
            with self._lazy_lock:
                if self._online_products is None:
                    from erp_web.facades.online_product_factory import create_online_product_service

                    self._online_products = create_online_product_service(self)
        return self._online_products

    @property
    def agent_calls(self):
        if self._agent_calls is None:
            with self._lazy_lock:
                if self._agent_calls is None:
                    from erp_web.stores.agent_call_store import AgentCallStore

                    self._agent_calls = AgentCallStore(self.db, on_change=self._notify_conversation_changed)
        return self._agent_calls

    @property
    def products(self) -> "ProductStore":
        if self._products is None:
            with self._lazy_lock:
                if self._products is None:
                    from erp_web.stores.product_store import ProductStore

                    self._products = ProductStore(
                        self.db,
                        image_draft_guard=lambda draft_id: self.image_batches.assert_draft_idle(draft_id),
                        forbidden_categories_loader=lambda: self.config.forbidden_categories(),
                        category_ancestors_loader=lambda platform, category_id: self.category_catalog.cached_category_ancestor_ids(platform, category_id),
                    )
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
                    from erp_web.services.product_research_service import (
                        ProductResearchRunRegistry,
                    )

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

                    self._draft_query_snapshots = DraftQuerySnapshotStore(self.db)
        return self._draft_query_snapshots

    @property
    def exchange_rates(self) -> "ExchangeRateService":
        if self._exchange_rates is None:
            with self._lazy_lock:
                if self._exchange_rates is None:
                    from erp_web.runtime_units.pricing_runtime import (
                        ExchangeRateService,
                    )

                    self._exchange_rates = ExchangeRateService(self.db)
        return self._exchange_rates

    @property
    def image_delivery(self) -> "ImageDeliveryService":
        if self._image_delivery is None:
            with self._lazy_lock:
                if self._image_delivery is None:
                    from erp_web.services.image_delivery_service import (
                        ImageDeliveryService,
                    )

                    self._image_delivery = ImageDeliveryService(self.paths, self.config.load_app_config)
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
    def category_catalog_store(self) -> "CategoryCatalogStore":
        """统一类目元数据缓存与服务端翻译词典 SQLite 引擎（按共享库路径进程内共享）。"""

        if self._category_catalog_store is None:
            with self._lazy_lock:
                if self._category_catalog_store is None:
                    from erp_web.stores.category_catalog_store import (
                        CategoryCatalogStore,
                    )

                    self._category_catalog_store = CategoryCatalogStore.acquire_shared(
                        self.shared_paths.cache_dir / "category-catalog.sqlite3"
                    )
        return self._category_catalog_store

    @property
    def approval_session(self) -> "ApprovalSession":
        """个人空间的可信审批凭据；仅随受信 UI bootstrap 下发，不进入模型上下文。"""

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
            task_queue = self._task_queue
            online_products = self._online_products
            order_notifications = self._order_notifications
            fulfillment = self._fulfillment
            alibaba_self_purchases = self._alibaba_self_purchases
            alibaba_direct_purchases = self._alibaba_direct_purchases
            alibaba_read_cache = self._alibaba_read_cache
            category_catalog_store = self._category_catalog_store
            wechat = self._wechat
        if self._desktop_connections is not None:
            self._desktop_connections.close()
        if wechat is not None:
            wechat.close()
        if task_queue is not None:
            task_queue.close()
        if category_catalog_store is not None:
            from erp_web.stores.category_catalog_store import CategoryCatalogStore

            CategoryCatalogStore.release_shared(category_catalog_store)
        if alibaba_read_cache is not None:
            alibaba_read_cache.close()
        if alibaba_direct_purchases is not None:
            alibaba_direct_purchases.close()
        if alibaba_self_purchases is not None:
            alibaba_self_purchases.close()
        if fulfillment is not None:
            fulfillment.close()
        if order_notifications is not None:
            order_notifications.close()
        if online_products is not None:
            online_products.close()
        if publishing_bus is not None:
            publishing_bus.close()


_context_lock = threading.Lock()
_context: AppContext | None = None
_request_context: ContextVar[AppContext | None] = ContextVar("erp_app_context", default=None)


@contextmanager
def bind_context(context: AppContext):
    """绑定一次请求或后台执行的业务空间，退出时恢复原上下文。"""
    token = _request_context.set(context)
    try:
        yield context
    finally:
        _request_context.reset(token)


def build_default_context() -> AppContext:
    """Build the context from the repository layout (current APP_DIR logic)."""
    paths = AppPaths.from_app_dir(_DEFAULT_APP_DIR)
    if (paths.app_dir / "migration-incomplete").exists():
        raise RuntimeError("业务数据迁移尚未完成，请检查迁移报告后再启动。")
    return AppContext(paths=paths, db=ErpDatabase(paths.db_path))


def get_context() -> AppContext:
    """优先返回请求/后台执行绑定的空间；离线入口才使用进程默认上下文。"""
    active = _request_context.get()
    if active is not None:
        return active
    global _context
    context = _context
    if context is None:
        with _context_lock:
            context = _context
            if context is None:
                context = build_default_context()
                _context = context
    return context


def bound_context() -> AppContext | None:
    """账号凭据只使用显式绑定的请求或后台任务上下文。"""
    return _request_context.get()


def peek_context() -> AppContext | None:
    """读取已安装上下文但不触发默认数据库构造，供生命周期编排使用。"""

    active = _request_context.get()
    if active is not None:
        return active
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

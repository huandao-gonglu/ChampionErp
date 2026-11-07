"""共享账号服务、个人业务上下文与领域恢复的进程组合根。"""
from __future__ import annotations

from functools import partial
import logging
from pathlib import Path
from threading import Event, RLock, Thread
import shutil
import time

from erp_web.context import AppContext, AppPaths, bind_context
from erp_web.services.desktop_connection_service import server_worker_context
from erp_web.db import ErpDatabase
from erp_web.stores.account_store import AccountStore
from erp_web.services.account_service import AccountService
from erp_web.services.ai_model_config import managed_newapi_models
from erp_web.services.newapi_bridge import NewAPIBridge

logger = logging.getLogger(__name__)


class AccountRuntime:
    def __init__(self, root: AppContext):
        self.root = root
        self.store = AccountStore(root.paths.app_dir / "accounts.sqlite3")
        self.service = AccountService(self.store)
        self.newapi = NewAPIBridge(self.store, self.service)
        self._contexts = {}
        self._jobs = {}
        self._lock = RLock()
        self._stop = Event()
        self._worker = None

    def context_for(self, user):
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("账号服务已停止。")
            context = self._contexts.get(user["id"])
            if context is None:
                workspace = user["workspace"]
                if workspace == "." and user["role"] == "admin":
                    context = self.root
                else:
                    # 归属只读账号库的服务端标识，客户端字段不能选择目录。
                    if workspace != f"users/{user['id']}":
                        raise RuntimeError("账号业务空间标识无效。")
                    directory = (self.root.paths.app_dir / workspace).resolve()
                    if not directory.is_relative_to((self.root.paths.app_dir / "users").resolve()):
                        raise RuntimeError("账号业务空间越界。")
                    paths = AppPaths.from_app_dir(directory)
                    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    context = AppContext(paths, ErpDatabase(paths.db_path))
                defaults = Path(__file__).resolve().parents[1] / "assets" / "account-defaults"
                for source in defaults.rglob("*"):
                    if source.is_file():
                        destination = context.paths.config_dir / source.relative_to(defaults)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        if not destination.exists():
                            shutil.copyfile(source, destination)
                context.user_id = user["id"]
                context.shared_paths = self.root.paths
                context.config.shared_config = self.root.config
                context.allow_environment_credentials = workspace == "."
                context.account_store = self.store
                context.config.managed_models = managed_newapi_models(
                    self.newapi.internal_url, context.config.load_app_config()["ai_models"],
                )
                context.model_key = partial(self.newapi.model_key, user["id"], user["display_name"])
                self._contexts[user["id"]] = context
            return context

    def disconnect(self, user_id, token_hash=None):
        with self._lock:
            context = self._contexts.get(user_id)
        if context is not None and context._desktop_connections is not None:
            context._desktop_connections.revoke_sessions(token_hash)

    def start(self):
        """恢复已有空间；新注册账号在首次业务请求时才创建业务库。"""
        for user in self.store.users():
            directory = self.root.paths.app_dir / user["workspace"]
            if not (directory / "erp.sqlite3").exists():
                continue
            context = self.context_for(user)
            with bind_context(context):
                with context.db._connect() as db:
                    online = db.execute("SELECT 1 FROM online_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()
                    publish = db.execute("SELECT 1 FROM publish_jobs LIMIT 1").fetchone()
                    agent = db.execute("SELECT 1 FROM ai_deferred_requests LIMIT 1").fetchone()
                if publish:
                    from erp_web.runtime_units.publish_adapter import mark_interrupted_publish_jobs
                    mark_interrupted_publish_jobs()
                if online:
                    context.online_products
                if online or publish:
                    context.task_queue.start()
                if agent:
                    context.agent_calls.mark_interrupted_writes()
                if (context.paths.data_dir / "order-notifications.sqlite3").exists():
                    context.order_notifications
                    context.fulfillment
                if ((context.paths.data_dir / "wechat-channel.sqlite3").exists()
                        and context.wechat_store.state()["credentials"]):
                    context.wechat.start()
        self._worker = Thread(target=server_worker_context().run, args=(self._scan,), name="erp-account-jobs", daemon=True)
        self._worker.start()

    def _scan(self):
        next_newapi_sync = 0.0
        while not self._stop.wait(1):
            if time.monotonic() >= next_newapi_sync:
                try:
                    self.newapi.sync()
                except Exception:
                    logger.exception("New API 账号同步暂未完成。")
                next_newapi_sync = time.monotonic() + 30
            with self._lock:
                contexts = list(self._contexts.items())
            for identity, context in contexts:
                try:
                    with bind_context(context):
                        # 只为实际使用对话的空间装配领域 Job，不在注册时启动整套服务。
                        if context._agent_calls is not None:
                            service = self._jobs.get(identity)
                            if service is None:
                                from erp_web.facades.ai_chat_facade import build_job_service
                                service = self._jobs[identity] = build_job_service(context)
                            service.scan()
                        if context._publishing_bus is not None or context._online_products is not None:
                            context.task_queue.start()
                        if context._wechat is not None:
                            context.wechat.start()
                except Exception:
                    logger.exception("个人空间后台任务处理失败：用户=%s", identity)

    def close(self):
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=5)
        for service in self._jobs.values():
            service.close()
        for context in self._contexts.values():
            context.close()
        self.root.close()

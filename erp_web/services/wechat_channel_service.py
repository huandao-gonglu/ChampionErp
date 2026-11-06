"""iLink 文字转发：SDK 负责协议，ERP 负责持久接收与原生对话。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import threading
import time
from datetime import datetime
from urllib.parse import urlsplit

from aiohttp import ClientError
from pydantic_ai.messages import ModelResponse, TextPart
from wechatbot.errors import ApiError
from wechatbot.protocol import DEFAULT_BASE_URL, ILinkApi

from erp_web.schemas.wechat_channel import WECHAT_FORWARD_TYPES, WechatChannelStatus
from erp_web.services.ai_chat_detached_runner import get_detached_chat_runner
from erp_web.services.chat_conversation_service import ChatConversationError, ChatConversationRun
from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnAlreadyAcceptedError


def visible_reply(text: str) -> str:
    """与网页一致，只隐藏开头的思考标签；不改写原生历史。"""
    while text.lstrip().startswith("<think>"):
        text = text.lstrip()
        end = text.find("</think>", len("<think>"))
        if end < 0:
            return ""
        text = text[end + len("</think>"):]
    return text.strip()


def text_chunks(text: str, limit: int = 3500) -> list[str]:
    # 按 UTF-8 字节分段，不切开中文和 emoji；顺序由持久投递队列保证。
    chunks, current, size = [], [], 0
    for character in text:
        length = len(character.encode("utf-8"))
        if size + length > limit and current:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(character)
        size += length
    if current:
        chunks.append("".join(current))
    return chunks


def trusted_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or not (parsed.hostname or "").endswith(".weixin.qq.com")
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("微信服务地址无效，请重新绑定。")
    return value.rstrip("/")


class WechatChannelService:
    def __init__(self, *, store, conversations, api=None, conversation_factory=None,
                 forward_types_reader=None, forward_types_writer=None):
        self.store = store
        self.conversations = conversations
        self.conversation_factory = conversation_factory
        self._forward_types_reader = forward_types_reader or (lambda: list(WECHAT_FORWARD_TYPES))
        self._forward_types_writer = forward_types_writer
        self.api = api or ILinkApi(bot_agent="ChampionERP/1.0")
        self.lock = threading.RLock()
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._loop = None
        self._wake_event = None
        self._login_future = None
        self._login_serial = 0
        self._verification = ""
        self._qr_image = ""
        self._status = "connecting" if store.state()["credentials"] else "unbound"
        self._error = ""
        self._expired_generation = None

    def start(self):
        with self.lock:
            if self._closed.is_set():
                raise RuntimeError("微信连接已关闭。")
            if self._thread is not None:
                return
            from contextvars import copy_context
            self._thread = threading.Thread(target=copy_context().run, args=(lambda: asyncio.run(self._serve()),), name="erp-wechat", daemon=True)
            self._thread.start()
        if not self._ready.wait(3):
            raise RuntimeError("微信连接尚未就绪，请稍后重试。")

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        tasks = [asyncio.create_task(self._receive_loop()), asyncio.create_task(self._pump_loop())]
        self._ready.set()
        try:
            while not self._closed.is_set():
                await asyncio.sleep(0.2)
        finally:
            if self._login_future is not None:
                self._login_future.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def close(self):
        self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def wake(self):
        """跨线程通知只唤醒投递；内容仍从已提交历史与渠道队列读取。"""
        if self._loop is not None and self._wake_event is not None and not self._closed.is_set():
            try:
                self._loop.call_soon_threadsafe(self._wake_event.set)
            except RuntimeError:
                # 关闭期间不再投递，未发送记录留在持久队列中。
                pass

    def forward_types(self):
        return self._forward_types_reader()

    def save_forward_types(self, values):
        if not isinstance(values, list):
            raise ValueError("请提供要转发的消息类型列表；空列表表示全部关闭。")
        if any(value not in WECHAT_FORWARD_TYPES for value in values):
            raise ValueError("转发类型包含不支持的选项。")
        if self._forward_types_writer is None:
            raise RuntimeError("当前入口没有配置转发设置存储。")
        selected = [value for value in WECHAT_FORWARD_TYPES if value in values]
        self._forward_types_writer(selected)
        self.store.skip_disabled(selected)
        self.wake()

    def _set_status(self, status, error="", *, generation=None):
        with self.lock:
            if generation is None or self.store.state()["generation"] == generation:
                self._status, self._error = status, error

    def validate_conversation(self, conversation_id):
        service = self.conversations
        conversation_id = service._validate_conversation_id(conversation_id)
        service._verify_history_ownership(conversation_id)
        history = service.chat_service.message_store.get(conversation_id)
        if history is None:
            raise ChatConversationError(404, "WECHAT_CONVERSATION_MISSING", "绑定的对话已失效，请在微信发送 /new 新建对话。")
        return history

    def begin_login(self):
        self.start()
        with self.lock:
            old = self.store.state()["credentials"]
            self._login_serial += 1
            serial = self._login_serial
            if self._login_future is not None:
                self._login_future.cancel()
            self.store.configure(conversation_id="", history_position=0, unbind=True)
            self._qr_image, self._verification = "", ""
            self._set_status("logging_in")
            self._login_future = asyncio.run_coroutine_threadsafe(self._login(serial, old), self._loop)
            self.wake()

    def verify(self, code):
        with self.lock:
            if self._status != "need_verifycode" or not code.isascii() or not code.isdigit() or len(code) > 12:
                raise ValueError("请输入微信中显示的有效配对码。")
            self._verification = code

    def unbind(self):
        with self.lock:
            self._login_serial += 1
            if self._login_future is not None:
                self._login_future.cancel()
            self.store.configure(conversation_id="", history_position=0, unbind=True)
            self._qr_image = self._verification = ""
            self._set_status("unbound")
            self.wake()

    async def _login(self, serial, old):
        import qrcode

        try:
            qr = await asyncio.wait_for(self.api.get_qr_code(DEFAULT_BASE_URL, [old["token"]] if old else []), 20)
            image = qrcode.make(qr["qrcode_img_content"])
            output = io.BytesIO()
            image.save(output, format="PNG")
            with self.lock:
                if serial != self._login_serial:
                    return
                self._qr_image = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
                self._set_status("waiting_scan")
            base_url, deadline = DEFAULT_BASE_URL, time.monotonic() + 300
            while time.monotonic() < deadline:
                with self.lock:
                    if serial != self._login_serial:
                        return
                    code, self._verification = self._verification, ""
                try:
                    result = await asyncio.wait_for(self.api.poll_qr_status(base_url, qr["qrcode"], code or None), 40)
                except (TimeoutError, ClientError):
                    # 状态长轮询超时不代表二维码失效，保留已显示的二维码继续等待。
                    await asyncio.sleep(1)
                    continue
                status = result.get("status", "wait")
                with self.lock:
                    if serial != self._login_serial:
                        return
                    if status in ("confirmed", "binded_redirect"):
                        credentials = old if status == "binded_redirect" else {
                            "token": result.get("bot_token"), "account_id": result.get("ilink_bot_id"),
                            "user_id": result.get("ilink_user_id"),
                            "base_url": trusted_base_url(result.get("baseurl") or DEFAULT_BASE_URL),
                        }
                        if not credentials or not all(credentials.get(key) for key in ("token", "account_id", "user_id")):
                            raise ValueError("微信没有返回完整绑定信息。")
                        conversation_id = self.conversations.create_conversation()
                        self.store.configure(conversation_id=conversation_id, history_position=0, credentials=credentials)
                        self.wake()
                        self._qr_image = ""
                        self._set_status("connecting")
                        return
                    if status in ("expired", "verify_code_blocked"):
                        self._qr_image = ""
                        self._set_status("login_expired", "二维码已失效，请重新绑定。")
                        return
                    if status == "scaned_but_redirect":
                        base_url = trusted_base_url("https://" + result["redirect_host"])
                    elif status == "need_verifycode":
                        self._set_status("need_verifycode")
                    elif status == "scaned":
                        self._set_status("waiting_confirm")
                await asyncio.sleep(1)
            self._set_status("login_expired", "绑定超时，请重新绑定。")
        except asyncio.CancelledError:
            raise
        except Exception:
            # 上游异常可能包含令牌、二维码或配对码，不记录异常正文。
            with self.lock:
                if serial == self._login_serial:
                    self._qr_image = ""
                    self._set_status("error", "微信绑定失败，请检查网络后重新绑定。")

    async def _receive_loop(self):
        while True:
            state = self.store.state()
            creds = state["credentials"]
            if not creds or self._expired_generation == state["generation"]:
                await asyncio.sleep(1)
                continue
            try:
                updates = await asyncio.wait_for(self.api.get_updates(creds["base_url"], creds["token"], state["cursor"]), 50)
                self.ingest_updates(state, updates)
                self._set_status("connected", generation=state["generation"])
            except ApiError as exc:
                if exc.is_session_expired:
                    self._expired_generation = state["generation"]
                    self._set_status("expired", "微信授权已失效，请重新绑定。", generation=state["generation"])
                else:
                    self._set_status("reconnecting", "微信连接暂时中断，正在重试。", generation=state["generation"])
                await asyncio.sleep(3)
            except ChatConversationError:
                self._set_status("conversation_missing", "绑定的对话已失效，请在微信发送 /new。", generation=state["generation"])
                await asyncio.sleep(2)
            except Exception:
                self._set_status("reconnecting", "微信连接暂时中断，正在重试。", generation=state["generation"])
                await asyncio.sleep(3)

    def ingest_updates(self, state, updates):
        accepted = []
        for raw in updates.get("msgs", []):
            if (raw.get("message_type") != 1 or raw.get("from_user_id") != state["credentials"]["user_id"]
                    or raw.get("group_id") or raw.get("message_state", 2) != 2):
                continue
            message_id = raw.get("message_id") or raw.get("client_id")
            if not message_id:
                continue
            # 重新绑定或切换会话后，不执行服务端补送的旧消息。
            created = raw.get("create_time_ms")
            if isinstance(created, (int, float)) and created < state.get("accept_after_ms", 0):
                continue
            items = raw.get("item_list", [])
            text_only = bool(items) and all(item.get("type") == 1 for item in items)
            text = "\n".join(str(item.get("text_item", {}).get("text", "")) for item in items) if text_only else ""
            accepted.append({"message_id": str(message_id), "text": text,
                             "context_token": raw.get("context_token", "")})
        cursor = updates.get("get_updates_buf")
        self.store.ingest(state["generation"], cursor if isinstance(cursor, str) and cursor else state["cursor"], accepted)
        if accepted:
            self.wake()

    def transfer_inputs(self, state):
        messages = self.store.inbox(state["generation"])
        if not messages:
            return
        conversations = self.conversation_factory() if self.conversation_factory else self.conversations
        for message in messages:
            with self.lock:
                current = self.store.state()
                if current["generation"] != state["generation"]:
                    return
                state = current
                if message["text"].strip() == "/new":
                    # 同一命令重放使用相同会话 ID，崩溃恢复时不重复创建或覆盖历史。
                    identity = f"wechat-new:{state['credentials']['account_id']}:{message['message_id']}"
                    conversation_id = "conversation_global_chat_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
                    conversations.create_conversation(conversation_id)
                    self.store.new_conversation(state["generation"], conversation_id, message["message_id"],
                                                forward_command="command" in self.forward_types())
                    continue
                if not message["text"].strip():
                    self.store.observe(state["generation"], state["history_position"], [
                        ("error", "unsupported:" + message["message_id"], "目前只支持文字消息，请输入文字。")],
                        enabled_types=self.forward_types())
                else:
                    key = "wechat_" + hashlib.sha256((state["credentials"]["account_id"] + ":" + message["message_id"]).encode()).hexdigest()
                    try:
                        self.validate_conversation(state["conversation_id"])
                        run = conversations.accept_text(state["conversation_id"], key, message["text"])
                    except AiChatTurnAlreadyAcceptedError:
                        run = None
                    except ChatConversationError as exc:
                        if exc.code == "AI_CHAT_STOPPING":
                            return
                        self.store.observe(state["generation"], state["history_position"], [("error", "rejected:" + key, str(exc))],
                                           enabled_types=self.forward_types())
                        run = None
                    if isinstance(run, ChatConversationRun):
                        try:
                            get_detached_chat_runner().submit(run.run())
                        except Exception:
                            conversations.run_registry.release(run.conversation_id)
                            raise
                self.store.received(state["generation"], message["message_id"])

    def collect_replies(self, state):
        history = self.validate_conversation(state["conversation_id"])
        messages = history.model_messages()
        replies = []
        for index, message in enumerate(messages[state["history_position"]:], state["history_position"]):
            metadata = message.metadata or {}
            if isinstance(message, ModelResponse) and (metadata.get("chat_final") or metadata.get("chat_progress")):
                forward_type = "reply" if metadata.get("chat_final") else "progress"
                text = visible_reply("".join(part.content for part in message.parts if isinstance(part, TextPart)))
                # 沿用已有 final 投递键，升级后不重复发送已投递的回答。
                key = "final" if forward_type == "reply" else "progress"
                replies.extend((forward_type, f"{key}:{index}:{message.run_id}:{part}", chunk) for part, chunk in enumerate(text_chunks(text)))
            if metadata.get("ai_run_error"):
                replies.append(("error", f"error:{index}", "本轮运行失败，请在 ERP 网页查看结果。已完成的业务操作不会重做。"))
        pending = self.conversations.call_store.pending(state["conversation_id"])
        if pending:
            requests, results, _ = pending
            if any(call.tool_call_id not in results.approvals for call in requests.approvals):
                call_ids = ":".join(sorted(call.tool_call_id for call in requests.approvals))
                key = "approval:" + hashlib.sha256(call_ids.encode()).hexdigest()
                replies.append(("approval", key, "本轮操作需要审批，请在 ERP 网页确认或拒绝。"))
        claim = self.conversations.claim_store.find_for_conversation(state["conversation_id"])
        if (claim is not None and claim.status == "cancelled" and claim.finished_at
                and not self.conversations.run_registry.is_active(state["conversation_id"])):
            finished_ms = datetime.fromisoformat(claim.finished_at).timestamp() * 1000
            if finished_ms >= state["accept_after_ms"]:
                replies.append(("error", "cancelled:" + claim.claim_id, "本轮操作已停止。已提交的业务操作不会自动撤销，请在 ERP 网页查看结果。"))
        self.store.observe(state["generation"], len(messages), replies, enabled_types=self.forward_types())

    async def send_pending(self, state):
        while True:
            # 每次发送后重新取队列，避免网络等待期间已停发的记录因重新勾选而复活。
            self.store.skip_disabled(self.forward_types())
            pending = self.store.pending(state["generation"])
            if not pending:
                return
            row = pending[0]
            current = self.store.state()
            if current["generation"] != state["generation"] or not current["context_token"]:
                return
            creds = current["credentials"]
            msg = self.api.build_text_message(creds["user_id"], current["context_token"], row["text"])
            msg["client_id"] = row["client_id"]
            await asyncio.wait_for(self.api.send_message(creds["base_url"], creds["token"], msg), 20)
            self.store.delivered(row["sequence"])

    async def _pump_loop(self):
        while True:
            self._wake_event.clear()
            state = self.store.state()
            retry = False
            try:
                if state["credentials"] and self._expired_generation != state["generation"]:
                    self.transfer_inputs(state)
                    self.collect_replies(self.store.state())
                    await self.send_pending(self.store.state())
            except ChatConversationError:
                self._set_status("conversation_missing", "绑定的对话已失效，请在微信发送 /new。", generation=state["generation"])
            except ApiError as exc:
                if exc.is_session_expired:
                    self._expired_generation = state["generation"]
                    self._set_status("expired", "微信授权已失效，请重新绑定。", generation=state["generation"])
                else:
                    retry = True
                    self._set_status("reconnecting", "消息尚未交付，正在重试。", generation=state["generation"])
            except Exception:
                retry = True
                self._set_status("reconnecting", "消息尚未交付，正在重试。", generation=state["generation"])
            current = self.store.state()
            retry = retry or bool(self.store.inbox(current["generation"])) or bool(
                current["context_token"] and self.store.pending(current["generation"])
            )
            if retry:
                try:
                    await asyncio.wait_for(self._wake_event.wait(), 1)
                except TimeoutError:
                    pass
            else:
                await self._wake_event.wait()

    def status(self) -> WechatChannelStatus:
        with self.lock:
            state = self.store.state()
            return {"ok": True, "bound": bool(state["credentials"]), "connected": self._status == "connected",
                    "status": self._status, "conversation_id": state["conversation_id"],
                    "qr_image": self._qr_image, "error": self._error,
                    "pending_deliveries": len(self.store.pending(state["generation"])),
                    "forward_types": self.forward_types()}


__all__ = ["WechatChannelService", "text_chunks", "visible_reply"]

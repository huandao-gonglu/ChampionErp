# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import contextmanager
from contextvars import Context, copy_context
from dataclasses import dataclass
import base64
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from .operation import check_cancellation, remaining_seconds
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from erp_shared.browser_connection_config import normalize_browser_proxy
from desktop_runtime.browser_debug_http import browser_debug_json


@dataclass
class _OwnedBrowser:
    endpoint: str
    process: subprocess.Popen | None
    had_profile_lock: bool

    def exited(self, profile_dir: Path) -> bool:
        if (profile_dir / "SingletonLock").is_symlink():
            return False
        if self.process is not None:
            return self.process.poll() is not None
        return self.had_profile_lock


_browser_launch_lock = Lock()
_port_locks: dict[int, Lock] = {}
_owned_sessions: dict[tuple[int, Path], _OwnedBrowser] = {}
logger = logging.getLogger(__name__)
_SESSION_FILE = ".erp-browser-session.json"


def _browser_version(port: int) -> dict[str, Any]:
    version = browser_debug_json(port, "/json/version", timeout=2)
    return version if isinstance(version, dict) else {}


def browser_debug_connected(port: int) -> bool:
    try:
        return bool(_browser_version(port).get("webSocketDebuggerUrl"))
    except (OSError, ValueError):
        return False


@contextmanager
def browser_operation(port: int):
    """一个端口一次处理一个完整操作，收尾完成后才交给下一项。"""
    with _browser_launch_lock:
        lock = _port_locks.setdefault(port, Lock())
    while not lock.acquire(timeout=0.25):
        check_cancellation()
    try:
        check_cancellation()
        yield
    finally:
        lock.release()


def _session_record(profile_dir: Path) -> dict:
    try:
        record = json.loads((profile_dir / _SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _remember_session(port: int, profile_dir: Path, endpoint: str, process=None) -> _OwnedBrowser:
    key = (port, profile_dir)
    owned = _owned_sessions.get(key)
    if owned is None or owned.endpoint != endpoint:
        owned = _OwnedBrowser(endpoint, process, (profile_dir / "SingletonLock").is_symlink())
        _owned_sessions[key] = owned
    return owned


def _clear_profile_caches(profile_dir: Path) -> None:
    """仅删除专用资料目录内可重建的资源，不跟随链接或目录联接。"""
    root = profile_dir.resolve()
    for relative in (
        "Default/Cache", "Default/Code Cache", "Default/GPUCache",
        "Default/DawnGraphiteCache", "Default/DawnWebGPUCache",
        "GraphiteDawnCache", "GrShaderCache", "ShaderCache",
    ):
        path = profile_dir / relative
        try:
            if path.resolve() == root / relative and path.is_dir():
                shutil.rmtree(path)
        except OSError:
            logger.warning("未能删除浏览器缓存 %s", relative, exc_info=True)


def finish_browser_session(port: int, profile_dir: Path, *, completed_page: tuple[str, str] | None = None,
                           landing_page: tuple[str, str] | None = None) -> None:
    """完整动作结束后回收实例。有其他任务页时，仅关闭已消费的页面。"""
    try:
        # 操作本身即使已经取消或超时，仍需给关闭协议独立的有限时间。
        Context().run(_finish_browser_session, port, profile_dir, completed_page, landing_page)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        logger.warning("专用浏览器收尾未完成，端口 %s", port, exc_info=True)


def _release_page(port, endpoint, completed_page, landing_page) -> bool:
    """返回浏览器是否还有使用中的页面。人工导航后的同一标签属于新页面。"""
    targets = browser_debug_json(port, "/json/list")
    if not isinstance(targets, list):
        raise ValueError("无法确认采集浏览器的剩余标签页。")
    try:
        pages = {(tab["id"], tab["url"]) for tab in targets if tab["type"] == "page"}
    except (KeyError, TypeError) as exc:
        raise ValueError("采集浏览器返回了无法识别的标签页。") from exc
    remaining = pages - {completed_page, landing_page}
    if all(url in ("about:blank", "chrome://newtab/", "edge://newtab/") for _, url in remaining):
        return False
    if completed_page in pages:
        cdp = CdpWebSocket(endpoint)
        try:
            cdp.call("Target.closeTarget", {"targetId": completed_page[0]}, timeout=5)
        finally:
            cdp.close()
    return True


def _discard_closed_session(port: int, profile_dir: Path, owned: _OwnedBrowser) -> None:
    if _session_record(profile_dir).get("websocket") == owned.endpoint:
        _clear_profile_caches(profile_dir)
        (profile_dir / _SESSION_FILE).unlink(missing_ok=True)
    del _owned_sessions[port, profile_dir]


def _finish_browser_session(port, profile_dir, completed_page, landing_page) -> None:
    with _browser_launch_lock:
        key = (port, profile_dir)
        recorded = _session_record(profile_dir).get("websocket")
        # 重启执行器后，手动选择的原采集标签仍可按磁盘记录核对归属。
        owned = _owned_sessions.get(key)
        expected = owned.endpoint if owned else (recorded if completed_page and completed_page[0] else None)
        if not expected or recorded != expected:
            return
        try:
            version = _browser_version(port)
        except OSError:
            if owned and owned.exited(profile_dir):
                _discard_closed_session(port, profile_dir, owned)
                return
            raise
        if version.get("webSocketDebuggerUrl") != expected:
            return
        owned = _remember_session(port, profile_dir, expected)
        if completed_page is not None and _release_page(port, expected, completed_page, landing_page):
            return
        _close_managed_browser(port, profile_dir, version, 5)
        if owned.process is not None:
            owned.process.wait(timeout=5)
        _discard_closed_session(port, profile_dir, owned)


def close_runtime_browsers() -> None:
    """执行器退出时回收本次持有的实例，包括仍供人工操作的窗口。"""
    with _browser_launch_lock:
        sessions = list(_owned_sessions)
    for port, profile_dir in sessions:
        finish_browser_session(port, profile_dir)


def ensure_browser_debug_session(
    port: int, profile_dir: Path, timeout: int = 15, *, proxy_server: str = "",
    headless: bool | None = None,
) -> None:
    """复用本机浏览器，未启动时以独立资料目录打开；不复制其他资料目录的登录。"""
    proxy_server = normalize_browser_proxy(proxy_server)
    with _browser_launch_lock:
        if browser_debug_connected(port):
            version = _browser_version(port)
            session = _session_record(profile_dir)
            endpoint = version.get("webSocketDebuggerUrl")
            if not endpoint or session.get("websocket") != endpoint:
                raise RuntimeError("专用浏览器端口被其他会话占用，请关闭旧专用浏览器或更换调试端口后重试。")
            _remember_session(port, profile_dir, endpoint)
            if headless is None:
                return
            if session.get("headless") is headless and session.get("proxy_server", "") == proxy_server:
                return
            _close_managed_browser(port, profile_dir, version, timeout)
        chrome = find_chrome_path()
        profile_dir.mkdir(parents=True, exist_ok=True)
        arguments = [
            chrome,
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile_dir.resolve()}",
            "--no-first-run",
            "--disable-popup-blocking",
            *(["--headless", "--window-size=1280,900"] if headless else ["--start-maximized"]),
            *([f"--proxy-server={proxy_server}"] if proxy_server else []),
        ]
        app_bundle = Path(chrome).parents[2] if len(Path(chrome).parents) > 2 else Path(chrome)
        use_mac_launcher = headless is False and sys.platform == "darwin" and app_bundle.suffix == ".app"
        if use_mac_launcher:
            # 由系统激活这个独立实例，避免只激活用户日常使用的另一个 Chrome。
            arguments = ["/usr/bin/open", "-n", "-a", str(app_bundle), "--args", *arguments[1:]]
        check_cancellation()
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            # macOS 的 open 不是 Chrome 本身，须先完成有时限的握手以便取消时能关闭真实实例。
            handshake = Context() if use_mac_launcher else copy_context()
            deadline = time.monotonic() + max(1, timeout)
            while time.monotonic() < deadline:
                if handshake.run(browser_debug_connected, port):
                    version = handshake.run(_browser_version, port)
                    session_file = profile_dir / _SESSION_FILE
                    temporary = session_file.with_suffix(".tmp")
                    temporary.write_text(json.dumps({
                        "websocket": version["webSocketDebuggerUrl"],
                        "headless": bool(headless), "proxy_server": proxy_server,
                    }), encoding="utf-8")
                    temporary.replace(session_file)
                    _remember_session(port, profile_dir, version["webSocketDebuggerUrl"],
                                      None if use_mac_launcher else process)
                    return
                exit_code = process.poll()
                if exit_code is not None and (not use_mac_launcher or exit_code != 0):
                    raise RuntimeError("专用浏览器启动失败，请关闭使用同一资料目录的窗口后重试。")
                time.sleep(0.5)
            raise RuntimeError(f"浏览器调试端口 {port} 启动超时，请检查浏览器是否已启动。")
        except BaseException:
            # 握手失败时尚无可核对的端点，只回收本次启动的子进程。
            if not use_mac_launcher and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            raise


def _close_managed_browser(port: int, profile_dir: Path, version: dict[str, Any], timeout: int) -> None:
    """仅关闭身份已核对的专用实例，并等待资料目录锁释放。"""
    profile_lock = profile_dir / "SingletonLock"
    had_profile_lock = profile_lock.is_symlink()
    cdp = CdpWebSocket(version["webSocketDebuggerUrl"])
    try:
        try:
            cdp.call("Browser.close", timeout=5)
        except (OSError, RuntimeError):
            # 正常关闭可能先断开 WebSocket；下面仍须确认进程释放连接与资料目录。
            pass
    finally:
        cdp.close()
    deadline = time.monotonic() + max(1, timeout)
    while time.monotonic() < deadline:
        if not browser_debug_connected(port) and not (had_profile_lock and profile_lock.is_symlink()):
            return
        time.sleep(0.2)
    raise RuntimeError("专用浏览器未能退出，请手动关闭专用浏览器后重试。")


class CdpWebSocket:
    def __init__(self, websocket_url: str) -> None:
        parsed = urllib.parse.urlparse(websocket_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self.sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("连接 Chrome DevTools WebSocket 失败")
        self.next_id = 1

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass

    def _send_frame(self, payload: bytes) -> None:
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            check_cancellation()
            deadline = getattr(self, '_call_deadline', None)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Chrome DevTools 读取超时')
            wait = min(0.5, deadline - time.monotonic()) if deadline is not None else 0.5
            self.sock.settimeout(remaining_seconds(max(0.01, wait)))
            try:
                chunk = self.sock.recv(length - len(chunks))
            except socket.timeout:
                continue
            if not chunk:
                raise RuntimeError("Chrome DevTools 连接已关闭")
            chunks.extend(chunk)
        return bytes(chunks)

    def _recv_frame(self) -> dict[str, Any]:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            masked = bool(second & 0x80)
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 8:
                raise RuntimeError("Chrome DevTools WebSocket 已关闭")
            if opcode == 9:
                continue

    def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 20,
        *, session_id: str = "", on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        check_cancellation()
        timeout = remaining_seconds(timeout)
        message_id = self.next_id
        self.next_id += 1
        request = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            request["sessionId"] = session_id
        self._send_frame(json.dumps(request).encode("utf-8"))
        deadline = time.monotonic() + timeout
        previous_timeout = self.sock.gettimeout()
        previous_deadline = getattr(self, "_call_deadline", None)
        self._call_deadline = deadline
        try:
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.01, deadline - time.monotonic()))
                message = self._recv_frame()
                if message.get("id") == message_id:
                    if "error" in message:
                        raise RuntimeError(f"{method} 调用失败：{message['error']}")
                    return message.get("result", {})
                if on_event is not None and message.get("method"):
                    on_event(message)
        except TimeoutError:
            raise TimeoutError(f"{method} 超时") from None
        finally:
            self.sock.settimeout(previous_timeout)
            self._call_deadline = previous_deadline
        raise TimeoutError(f"{method} 超时")


def find_chrome_path() -> str:
    env_candidates = [
        os.environ.get("ERP_CHROME_PATH", ""),
        os.environ.get("CHROME_PATH", ""),
        os.environ.get("BROWSER_PATH", ""),
    ]
    candidates = [Path(value).expanduser() for value in env_candidates if value.strip()]
    command_candidates: list[str] = []

    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
                Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                Path.home() / "Applications" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path.home() / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge",
            ]
        )
        command_candidates = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge"]
    elif os.name == "nt":
        candidates.extend(
            [
                Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ]
        )
        command_candidates = ["chrome", "chrome.exe", "msedge", "msedge.exe"]
    else:
        candidates.extend(
            [
                Path("/usr/bin/google-chrome"),
                Path("/usr/local/bin/google-chrome"),
                Path("/usr/bin/chromium"),
                Path("/usr/bin/chromium-browser"),
                Path("/usr/bin/microsoft-edge"),
                Path("/usr/bin/msedge"),
            ]
        )
        command_candidates = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge"]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    for command in command_candidates:
        found = shutil.which(command)
        if found:
            return found
    raise RuntimeError("没有找到 Chrome 或 Edge 浏览器；可设置 ERP_CHROME_PATH / CHROME_PATH 指向浏览器可执行文件。")


def find_named_browser_path(browser: str) -> str | None:
    name = str(browser or "").strip().lower()
    if name == "chrome":
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    elif name == "edge":
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]
    else:
        return None
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def open_auth_link_in_browser(url: str, browser: str = "default") -> dict[str, Any]:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("授权链接无效，请先生成完整授权链接")
    browser_name = str(browser or "default").strip().lower()
    if browser_name in {"chrome", "edge"}:
        browser_path = find_named_browser_path(browser_name)
        if not browser_path:
            return {"ok": False, "opened": False, "browser": browser_name, "error": f"未检测到 {browser_name}，请复制链接到已登录店铺的浏览器手动打开"}
        subprocess.Popen([browser_path, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "opened": True, "browser": browser_name, "path": browser_path}
    webbrowser.open(url)
    return {"ok": True, "opened": True, "browser": "default"}


def browser_debug_commands(port: int, profile_dir: Path) -> dict[str, str]:
    profile = str(profile_dir)
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    powershell_command = f'Start-Process "{chrome}" -ArgumentList \'--remote-debugging-port={port} --user-data-dir="{profile}"\''
    cmd_command = f'start chrome --remote-debugging-port={port} --user-data-dir="{profile}"'
    return {
        "profile_dir": profile,
        "powershell_command": powershell_command,
        "cmd_command": cmd_command,
        "start_command": powershell_command,
        "full_path_command": f'"{chrome}" --remote-debugging-port={port} --user-data-dir="{profile}"',
    }


def browser_debug_next_action() -> str:
    return "请按 start_command 启动专用 Chrome，打开商品详情页后重试。"


__all__ = [
    "CdpWebSocket",
    "browser_debug_connected",
    "browser_debug_commands",
    "browser_debug_next_action",
    "find_chrome_path",
    "find_named_browser_path",
    "ensure_browser_debug_session",
    "browser_operation",
    "finish_browser_session",
    "close_runtime_browsers",
    "open_auth_link_in_browser",
]

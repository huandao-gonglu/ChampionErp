# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from erp_web.context import get_context
from erp_web.services.browser_connection_config import normalize_browser_proxy
from erp_web.services.external_request_manager import managed_urlopen

_browser_launch_lock = Lock()
_SESSION_FILE = ".erp-browser-session.json"


def _browser_version(port: int) -> dict[str, Any]:
    with managed_urlopen(
        f"http://127.0.0.1:{port}/json/version", timeout=2, source=__name__,
    ) as response:
        version = json.load(response)
    return version if isinstance(version, dict) else {}


def browser_debug_connected(port: int) -> bool:
    try:
        return bool(_browser_version(port).get("webSocketDebuggerUrl"))
    except (OSError, ValueError):
        return False


def ensure_browser_debug_session(
    port: int, profile_dir: Path, timeout: int = 15, *, proxy_server: str = "",
    headless: bool | None = None,
) -> None:
    """复用本机浏览器，未启动时以独立资料目录打开；不复制其他资料目录的登录。"""
    proxy_server = normalize_browser_proxy(proxy_server)
    with _browser_launch_lock:
        if browser_debug_connected(port):
            if headless is None:
                return
            version = _browser_version(port)
            try:
                session = json.loads((profile_dir / _SESSION_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                session = {}
            if not isinstance(session, dict):
                session = {}
            if session.get("websocket") != version.get("webSocketDebuggerUrl"):
                raise RuntimeError("专用浏览器端口被其他会话占用，请关闭旧专用浏览器或更换调试端口后重试。")
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
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + max(1, timeout)
        while time.monotonic() < deadline:
            if browser_debug_connected(port):
                version = _browser_version(port)
                session_file = profile_dir / _SESSION_FILE
                temporary = session_file.with_suffix(".tmp")
                temporary.write_text(json.dumps({
                    "websocket": version["webSocketDebuggerUrl"],
                    "headless": bool(headless), "proxy_server": proxy_server,
                }), encoding="utf-8")
                temporary.replace(session_file)
                return
            exit_code = process.poll()
            if exit_code is not None and (not use_mac_launcher or exit_code != 0):
                raise RuntimeError("专用浏览器启动失败，请关闭使用同一资料目录的窗口后重试。")
            time.sleep(0.5)
        raise RuntimeError(f"浏览器调试端口 {port} 启动超时，请检查浏览器是否已启动。")


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
    raise RuntimeError("专用浏览器未能退出，尚未切换显示状态；请关闭该专用浏览器后重试。")

def pick_web_port(preferred_port: int, attempts: int = 20) -> int:
    import socket

    for port in range(preferred_port, preferred_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"没有可用端口，已尝试从 {preferred_port} 开始的 {attempts} 个端口")


def file_url(path: Path) -> str:
    return f"/file?path={urllib.parse.quote(str(path), safe='')}"


def parse_cookie_header(cookie: str, url: str) -> list[dict[str, str]]:
    parsed = urllib.parse.urlparse(url)
    domain = parsed.hostname or ""
    cookies: list[dict[str, str]] = []
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return cookies


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
            chunk = self.sock.recv(length - len(chunks))
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
        message_id = self.next_id
        self.next_id += 1
        request = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            request["sessionId"] = session_id
        self._send_frame(json.dumps(request).encode("utf-8"))
        deadline = time.monotonic() + timeout
        previous_timeout = self.sock.gettimeout()
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


def browser_debug_commands(port: int | None = None) -> dict[str, str]:
    paths = get_context().paths
    port = paths.browser_debug_port if port is None else port
    profile = str(paths.browser_debug_profile_dir)
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
    "open_auth_link_in_browser",
    "pick_web_port",
]

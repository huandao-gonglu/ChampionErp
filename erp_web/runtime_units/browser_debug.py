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
from typing import Any

from .runtime_common import BROWSER_DEBUG_PORT, BROWSER_DEBUG_PROFILE_DIR

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
            raise RuntimeError("杩炴帴 Chrome DevTools WebSocket 澶辫触")
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

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 20) -> dict[str, Any]:
        message_id = self.next_id
        self.next_id += 1
        self._send_frame(json.dumps({"id": message_id, "method": method, "params": params or {}}).encode("utf-8"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv_frame()
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})
        raise RuntimeError(f"{method} 超时")


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


def browser_debug_commands(port: int = BROWSER_DEBUG_PORT) -> dict[str, str]:
    profile = str(BROWSER_DEBUG_PROFILE_DIR)
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
    "browser_debug_commands",
    "browser_debug_next_action",
    "find_chrome_path",
    "find_named_browser_path",
    "open_auth_link_in_browser",
    "pick_web_port",
]

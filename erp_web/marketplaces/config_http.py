from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from erp_web import http_client

from .publisher import PublishAdapterError

# 平台 HTTP 边界把远端失败分类为类型化 PublishAdapterError：
# PublishingBus 只对 retryable=True 的失败重试，确定性 4xx 不再被反复外发。
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 420, 423, 425, 429})


def _http_failure_retryable(status_code: int) -> bool:
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return False
    return code in _RETRYABLE_HTTP_STATUS_CODES or 500 <= code <= 599


def _typed_http_failure(
    *,
    platform_prefix: str,
    message: str,
    status_code: int,
) -> PublishAdapterError:
    details: dict[str, Any] = {"http_status": int(status_code)}
    if status_code in (401, 403):
        code = f"{platform_prefix}_AUTH_FAILED"
        retryable = False
    elif status_code == 404:
        code = f"{platform_prefix}_NOT_FOUND"
        retryable = False
    elif status_code in (420, 429):
        code = f"{platform_prefix}_RATE_LIMITED"
        retryable = True
    elif _http_failure_retryable(status_code):
        code = f"{platform_prefix}_SERVER_ERROR"
        retryable = True
    else:
        code = f"{platform_prefix}_REQUEST_INVALID"
        retryable = False
    return PublishAdapterError(code, message, retryable=retryable, details=details)


def _typed_network_failure(
    *,
    platform_prefix: str,
    method: str,
    url: str,
    exc: Exception,
    message_prefix: str = "",
) -> PublishAdapterError:
    # 连接失败/超时属于瞬时错误：保留可重试语义，消息沿用 "failed:" 格式
    # 以便既有字符串解析（错误码/字段提取）继续工作。
    reason = getattr(exc, "reason", None)
    summary = str(reason) if reason is not None and str(reason) else str(exc)
    is_timeout = isinstance(exc, TimeoutError) or "timed out" in summary.lower()
    code = f"{platform_prefix}_TIMEOUT" if is_timeout else f"{platform_prefix}_NETWORK"
    prefix = message_prefix or f"{method} {url}"
    message = (
        f"{prefix} failed: timeout {summary}"
        if is_timeout
        else f"{prefix} failed: {summary}"
    )
    return PublishAdapterError(code, message, retryable=True)

def load_store_config(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "mercadolibre": {
            "access_token": "",
            "app_id": "",
            "app_secret": "",
            "code_verifier": "",
            "auth_status": "",
            "auth_checked_at": "",
            "auth_masked_account": "",
            "auth_error_code": "",
            "auth_error_message": "",
            "auth_next_action": "",
            "refresh_token": "",
            "redirect_uri": "",
            "category_id": "",
            "site_id": "CBT",
            "shop_name": "",
        },
        "yandex": {
            "api_token": "",
            # Campaign ID 是用户输入的非敏感静态配置；token 与在线派生能力
            # 只存 SQLite store_auth。
            "campaign_id": "",
            "shop_name": "",
            "auth_status": "",
            "auth_checked_at": "",
            "auth_masked_account": "",
            "auth_error_code": "",
            "auth_error_message": "",
            "auth_next_action": "",
        },
        "ozon": {
            "client_id": "",
            "api_key": "",
            "category_id": "",
            "shop_name": "",
            "auth_status": "",
            "auth_checked_at": "",
            "auth_masked_account": "",
            "auth_error_code": "",
            "auth_error_message": "",
            "auth_next_action": "",
        },
        "listing": {
            "stock": "10",
            "sku": "",
            "condition": "new",
            "listing_type_id": "gold_special",
            "mercadolibre_logistic_type": "remote",
        },
    }


def save_store_config(path: Path, config: dict[str, Any]) -> None:
    # Atomic write (same-directory temp file + os.replace): store credentials must
    # never be left half-written by a crash.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        tmp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name != "nt":
            tmp_path.chmod(0o600)
        os.replace(tmp_path, path)
        if os.name != "nt":
            path.chmod(0o600)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def bearer_auth_value(token: str) -> str:
    value = token.strip()
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    value = "".join(value.split())
    return f"Bearer {value}" if value else ""


def request_json(
    method: str,
    url: str,
    token: str = "",
    payload: dict[str, Any] | list[Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any] | list[Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = bearer_auth_value(token)
    if extra_headers:
        headers.update(extra_headers)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        return http_client.request_json(url, method=method, headers=headers, data=data, timeout=30)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise _typed_http_failure(
            platform_prefix="MERCADOLIBRE",
            message=f"{method} {url} failed: {exc.code} {detail}",
            status_code=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _typed_network_failure(
            platform_prefix="MERCADOLIBRE",
            method=method,
            url=url,
            exc=exc,
        ) from exc


def request_form_json(method: str, url: str, payload: dict[str, str]) -> dict[str, Any]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        return http_client.request_json(url, method=method, headers=headers, data=data, timeout=30)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


def upload_mercadolibre_picture(path: str | Path, token: str) -> dict[str, Any]:
    image_path = Path(path)
    if not image_path.exists():
        raise RuntimeError(f"图片文件不存在: {image_path}")
    boundary = "----CodexBoundary" + secrets.token_hex(12)
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{image_path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    data = header + image_path.read_bytes() + footer
    request = urllib.request.Request(
        "https://api.mercadolibre.com/pictures/items/upload",
        data=data,
        headers={
            "Authorization": bearer_auth_value(token),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise _typed_http_failure(
            platform_prefix="MERCADOLIBRE",
            message=f"POST Mercado Libre picture upload failed: {exc.code} {detail}",
            status_code=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _typed_network_failure(
            platform_prefix="MERCADOLIBRE",
            method="POST",
            url="https://api.mercadolibre.com/pictures/items/upload",
            exc=exc,
            message_prefix="POST Mercado Libre picture upload",
        ) from exc


def extract_oauth_code(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("code"):
        return query["code"][0]
    if value.startswith("code="):
        return value.split("=", 1)[1].split("&", 1)[0]
    if parsed.scheme and parsed.netloc:
        return ""
    return value if len(value) > 20 and "/" not in value and " " not in value else ""


def generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def exchange_mercadolibre_code(
    app_id: str,
    app_secret: str,
    redirect_uri: str,
    code_or_url: str,
    code_verifier: str = "",
) -> dict[str, str]:
    code = extract_oauth_code(code_or_url)
    if not app_id or not app_secret or not redirect_uri or not code or not code_verifier:
        raise RuntimeError(
            "缺少 App ID、App Secret、Redirect URI、授权 code 或 Code Verifier。"
            "请粘贴授权后浏览器地址栏里包含 ?code= 的完整地址，"
            "并且必须先用软件里的“复制美客多授权链接”生成新的授权链接。"
        )
    data = request_form_json(
        "POST",
        "https://api.mercadolibre.com/oauth/token",
        {
            "grant_type": "authorization_code",
            "client_id": app_id,
            "client_secret": app_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )
    token = str(data.get("access_token") or "")
    if not token:
        raise RuntimeError(f"未返回 access_token: {data}")
    return {
        "access_token": token,
        "refresh_token": str(data.get("refresh_token") or ""),
        "user_id": str(data.get("user_id") or ""),
    }


def refresh_mercadolibre_token(app_id: str, app_secret: str, refresh_token: str) -> dict[str, str]:
    if not app_id or not app_secret or not refresh_token:
        raise RuntimeError("缺少美客多 App ID、App Secret 或 Refresh Token，无法自动刷新 Access Token。")
    data = request_form_json(
        "POST",
        "https://api.mercadolibre.com/oauth/token",
        {
            "grant_type": "refresh_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "refresh_token": refresh_token,
        },
    )
    token = str(data.get("access_token") or "")
    if not token:
        raise RuntimeError(f"美客多刷新 Token 未返回 access_token: {data}")
    return {
        "access_token": token,
        "refresh_token": str(data.get("refresh_token") or refresh_token),
        "user_id": str(data.get("user_id") or ""),
    }


def is_mercadolibre_auth_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in [
            "401",
            "unauthorized",
            "invalid access token",
            "invalid_token",
            "invalid grant",
            "invalid_grant",
        ]
    )


def number_or_zero(value: Any) -> float:
    try:
        text = str(value or "").replace(",", ".").strip()
        if not text:
            return 0.0
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else 0.0
    except Exception:
        return 0.0


def request_ozon_json(
    method: str,
    url: str,
    client_id: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise TimeoutError("Ozon API deadline 已耗尽")
    headers = {"Content-Type": "application/json", "Client-Id": client_id, "Api-Key": api_key}
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise _typed_http_failure(
            platform_prefix="OZON",
            message=f"{method} {url} failed: {exc.code} {detail}",
            status_code=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _typed_network_failure(
            platform_prefix="OZON",
            method=method,
            url=url,
            exc=exc,
        ) from exc


def fetch_mercadolibre_user_profile(token: str) -> dict[str, Any]:
    """读取 ``/users/me``：店铺稳定身份与账户站点。

    授权测试、token 刷新和发布预检共用这一个入口，不再各自复制用户信息
    更新逻辑。
    """

    data = request_json("GET", "https://api.mercadolibre.com/users/me", token)
    if not isinstance(data, dict):
        return {}
    return {
        "user_id": str(data.get("id") or "").strip(),
        "nickname": str(data.get("nickname") or "").strip(),
        "site_id": str(data.get("site_id") or "").strip(),
    }


def fetch_mercadolibre_shop_name(token: str) -> str:
    profile = fetch_mercadolibre_user_profile(token)
    return profile.get("nickname") or profile.get("user_id") or ""


def fetch_mercadolibre_site_listing(site_id: str) -> dict[str, Any]:
    """读取远端站点元数据（公开接口），用于店铺级发布币种发现。"""

    site = str(site_id or "").strip()
    if not site:
        return {}
    data = request_json(
        "GET",
        f"https://api.mercadolibre.com/sites/{urllib.parse.quote(site)}",
    )
    return data if isinstance(data, dict) else {}

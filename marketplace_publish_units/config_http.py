from __future__ import annotations

from .common import *

from .category_cache import *

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
            "site_id": "MLM",
            "shop_name": "",
        },
        "wildberries": {
            "content_token": "",
            "prices_token": "",
            "marketplace_token": "",
            "stocks_token": "",
            "subject_id": "",
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
            "mercadolibre_price": "",
            "wildberries_price": "",
            "ozon_price": "",
            "stock": "10",
            "sku": "",
            "currency_id": "MXN",
            "condition": "new",
            "listing_type_id": "gold_special",
            "mercadolibre_logistic_type": "remote",
        },
    }


def save_store_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


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
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


def request_form_json(method: str, url: str, payload: dict[str, str]) -> dict[str, Any]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
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
        raise RuntimeError(f"POST Mercado Libre picture upload failed: {exc.code} {detail}") from exc


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
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Client-Id": client_id, "Api-Key": api_key}
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


def fetch_mercadolibre_shop_name(token: str) -> str:
    data = request_json("GET", "https://api.mercadolibre.com/users/me", token)
    if not isinstance(data, dict):
        return ""
    return data.get("nickname") or data.get("first_name") or str(data.get("id") or "")

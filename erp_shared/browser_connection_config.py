"""浏览器连接参数的纯校验，不启动进程或读取登录资料。"""

from urllib.parse import urlsplit


def normalize_browser_proxy(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    message = "浏览器代理请填写 http://、https:// 或 socks5:// 地址及端口，不包含账号密码。"
    try:
        parsed = urlsplit(text)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https", "socks5"}
            or not parsed.hostname or not port
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or any(character.isspace() or character in ",;\\" for character in text)
        ):
            raise ValueError(message)
    except ValueError:
        raise ValueError(message) from None
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}:{port}"


__all__ = ["normalize_browser_proxy"]

from __future__ import annotations

"""Mercado Libre OAuth 凭据组的进程内串行边界。"""

import threading


# access_token、单次 refresh_token 与 PKCE code_verifier 必须作为
# 同一组凭据串行读改写。RLock 允许发布前检查在持锁时
# 复用统一的 token 刷新和 ConfigStore 持久化边界。
MERCADOLIBRE_AUTH_LOCK = threading.RLock()


__all__ = ["MERCADOLIBRE_AUTH_LOCK"]

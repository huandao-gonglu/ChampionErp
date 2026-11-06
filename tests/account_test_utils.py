"""业务 HTTP 测试的真实账号会话夹具，不在产品中提供认证旁路。"""
from functools import lru_cache

from argon2 import PasswordHasher

from erp_web.account_runtime import AccountRuntime
from erp_web.context import get_context
from erp_web.schemas.accounts import SESSION_COOKIE


@lru_cache(maxsize=1)
def _password_hash():
    return PasswordHasher().hash("仅用于隔离测试目录的管理员密码")


def authenticated_headers(server):
    runtime = AccountRuntime(get_context())
    user = runtime.store.create_user("test-admin@example.com", "测试管理员", _password_hash(), admin=True)
    token, session = runtime.store.create_session(user["id"], user["password_hash"])
    runtime.context_for(user)
    server.account_runtime = runtime
    return {"Cookie": f"{SESSION_COOKIE}={token}", "X-CSRF-Token": session.csrf_token}


class AuthenticatedPort(int):
    def __new__(cls, port, headers):
        instance = super().__new__(cls, port)
        instance.headers = headers
        return instance

"""验证 Turnstile 的站点与用途绑定，以及邮件限流在失败重试时的边界。"""
import httpx
import pytest

from erp_web.schemas.accounts import AccountError
from erp_web.services import registration_verification as verification


@pytest.mark.parametrize(("success", "hostname", "action", "valid"), [
    (True, "erp.example", "register_email", True),
    (True, "another.example", "register_email", False),
    (True, "erp.example", "login", False),
    (False, "erp.example", "register_email", False),
])
def test_turnstile_is_bound_to_public_registration(monkeypatch, success, hostname, action, valid):
    real_client = httpx.Client
    monkeypatch.setenv("ERP_PUBLIC_URL", "https://erp.example")
    monkeypatch.setenv("ERP_TURNSTILE_SECRET", "test-only")
    def reply(request):
        assert request.url == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        assert b"response=human" in request.content
        return httpx.Response(200, json={"success": success, "hostname": hostname, "action": action})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(reply), **kwargs))
    if valid:
        verification.verify_human("human", "127.0.0.1")
    else:
        with pytest.raises(AccountError):
            verification.verify_human("human", "127.0.0.1")

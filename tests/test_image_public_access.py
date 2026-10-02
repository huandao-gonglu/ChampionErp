"""匿名 GET 必须验证实际图片内容，不把 HEAD 或 HTML 当作成功。"""
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import HTTPError
import io

import pytest

from erp_web.services import image_public_access
from erp_web.context import get_context
from erp_web.services.external_request_manager import BufferedResponse
from tests.image_hosting_test_utils import image_bytes


@pytest.mark.parametrize(("content_type", "raw", "expected"), [("image/png", image_bytes(), "reachable"), ("text/html", image_bytes(), "failed"), ("image/png", b"broken", "failed")])
def test_checks_actual_image_once_and_requires_content(monkeypatch, content_type, raw, expected):
    calls = []
    @contextmanager
    def request(req, **kwargs):
        calls.append((req.full_url, req.method, kwargs["timeout"], dict(req.header_items())))
        yield SimpleNamespace(status=200, headers={"Content-Type": content_type}, read=lambda: raw)
    monkeypatch.setattr(image_public_access, "managed_urlopen", request)
    url = "https://images.example.test/assets/image.png"
    results = image_public_access.check_image_public_access([url, url, "/file?path=local"])
    assert len(calls) == 1
    assert calls[0][1:3] == ("GET", 10)
    assert not {"authorization", "cookie", "x-amz-security-token"} & {key.lower() for key in calls[0][3]}
    assert results[0].status == expected


@pytest.mark.parametrize("status", [301, 404, 503, 405, 501])
def test_http_failure_never_means_public_access_success(monkeypatch, status):
    def request(req, **kwargs):
        raise HTTPError(req.full_url, status, "测试响应", {}, None)
    monkeypatch.setattr(image_public_access, "managed_urlopen", request)
    result = image_public_access.check_image_public_access(["https://images.example.test/a.png"])[0]
    assert result.status == "failed"
    assert result.http_status == status


def test_private_bucket_can_be_explicitly_checked_again_after_becoming_public(monkeypatch):
    calls = []
    url = "https://images.example.test/products/test.png"
    def send(req, **kwargs):
        calls.append(req.method)
        if len(calls) == 1:
            raise HTTPError(req.full_url, 401, "需要认证", {}, io.BytesIO())
        return BufferedResponse(image_bytes(), headers={"Content-Type": "image/png"}, status=200, url=req.full_url)
    monkeypatch.setattr(image_public_access, "safe_image_urlopen", send)
    first = image_public_access._check_image(url, profile_id="images-main")
    second = image_public_access._check_image(url, profile_id="images-main")
    assert first.status == "failed" and first.http_status == 401
    assert second.status == "reachable"
    assert calls == ["GET", "GET"]
    audit = get_context().external_requests.store.query(platform="image_hosting:public")
    assert audit["stats"]["network_attempts"] == 2

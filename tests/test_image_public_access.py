"""实际图片 URL 探测与确定性预检分离，不将根目录或 HTML 响应当作图片成功。"""

from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from erp_web.services import image_public_access


@pytest.mark.parametrize(("content_type", "expected"), [("image/jpeg", "reachable"), ("text/html", "inconclusive")])
def test_checks_actual_image_once_and_requires_image_response(monkeypatch, content_type, expected):
    calls = []

    @contextmanager
    def request(req, **kwargs):
        calls.append((req.full_url, req.method, kwargs["timeout"]))
        yield SimpleNamespace(status=200, headers={"Content-Type": content_type})

    monkeypatch.setattr(image_public_access, "managed_urlopen", request)
    url = "https://images.example.test/assets/12/image.jpg"
    results = image_public_access.check_image_public_access([url, url, "/file?path=local"])
    assert calls == [(url, "HEAD", 8)]
    assert len(results) == 1
    assert results[0].status == expected


@pytest.mark.parametrize(("error", "expected"), [(404, "failed"), (503, "failed"), (405, "inconclusive"), (501, "inconclusive"), (None, "failed")])
def test_failed_or_unsupported_probe_is_reported_honestly(monkeypatch, error, expected):
    def request(req, **kwargs):
        if error is not None:
            raise HTTPError(req.full_url, error, "测试响应", {}, None)
        raise TimeoutError("测试超时")

    monkeypatch.setattr(image_public_access, "managed_urlopen", request)
    result = image_public_access.check_image_public_access(["https://images.example.test/a.jpg"])[0]
    assert result.status == expected
    assert result.http_status == error

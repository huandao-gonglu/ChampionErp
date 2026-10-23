"""公开官方资料仍通过统一请求管理器，并隐藏网络异常中的敏感上下文。"""
from contextlib import contextmanager
from io import BytesIO

import pytest

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.services import commission_sources


def test_official_source_handles_managed_request_failure_without_sensitive_details(monkeypatch):
    def blocked(*args, **kwargs):
        raise PublishAdapterError("EXTERNAL_REQUEST_BLOCKED", "secret-token", retryable=False)
    monkeypatch.setattr(commission_sources, "managed_urlopen", blocked)
    with pytest.raises(ValueError, match="EXTERNAL_REQUEST_BLOCKED") as error:
        commission_sources.read_official_content("https://global-help.ozon.com/")
    assert "secret-token" not in str(error.value)


def test_official_source_uses_managed_transport_and_public_referer(monkeypatch):
    @contextmanager
    def read(request, **kwargs):
        assert kwargs["cookie_jar"] is not None and kwargs["source"] == commission_sources.__name__
        assert request.get_header("Referer") == "https://global-selling.mercadolibre.com/landing/selling-fee"
        assert request.get_header("Authorization") is None
        yield BytesIO(b'{"official": true}')
    monkeypatch.setattr(commission_sources, "managed_urlopen", read)
    assert commission_sources.read_official_content("https://global-selling.mercadolibre.com/", referer="https://global-selling.mercadolibre.com/landing/selling-fee") == b'{"official": true}'

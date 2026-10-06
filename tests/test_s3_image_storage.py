"""真实 SDK 的离线签名、寻址、复用、审计与失败边界。"""
import hashlib
from pathlib import Path
from urllib.error import URLError

import pytest

from erp_web.context import get_context
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services import s3_image_storage
from erp_web.services.s3_image_storage import S3ImageStorage
from tests.image_hosting_test_utils import FakeS3, image_bytes, profile


@pytest.mark.parametrize("style", ["auto", "path", "virtual"])
def test_signed_delivery_reuses_object_and_audits_requests(tmp_path, monkeypatch, style):
    remote = FakeS3(monkeypatch)
    monkeypatch.setenv("AWS_PROFILE", "must-not-be-read")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "wrong-environment-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wrong-environment-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "must-not-be-forwarded")
    monkeypatch.setenv("AWS_REQUEST_CHECKSUM_CALCULATION", "when_supported")
    path = tmp_path / "fake.jpg"; path.write_bytes(image_bytes())
    storage = S3ImageStorage(profile(addressing_style=style))
    result = storage.deliver(source_path=path)
    assert result.storage_key.endswith(".png")
    assert storage.deliver(source_path=path) == result
    path.unlink()
    assert storage.deliver(source_path=None, storage_key=result.storage_key, content_sha256=result.content_sha256) == result
    assert len(remote.uploads) == 1
    put = remote.uploads[0]
    assert "AWS4-HMAC-SHA256" in put["headers"]["authorization"]
    assert "Credential=test-access-key/" in put["headers"]["authorization"]
    assert "x-amz-security-token" not in put["headers"]
    assert "x-amz-sdk-checksum-algorithm" not in put["headers"]
    assert "x-amz-trailer" not in put["headers"]
    assert put["headers"]["content-type"] == "image/png"
    assert put["headers"]["x-amz-meta-sha256"] == hashlib.sha256(image_bytes()).hexdigest()
    assert "immutable" in put["headers"]["cache-control"]
    assert ("product-images.s3.example.test" in put["url"]) == (style == "virtual")
    audit = get_context().external_requests.store.query(platform="image_hosting:s3")
    assert audit["stats"]["network_attempts"] == 4
    assert all(item["account_id"] == "images-main" for item in audit["items"])
    assert "test-secret-key" not in str(audit)
    assert "Authorization" not in str(audit)


@pytest.mark.parametrize("status", [301, 400, 403, 429, 503])
def test_head_failures_never_upload_or_retry(tmp_path, monkeypatch, status):
    remote = FakeS3(monkeypatch); remote.failure = status
    path = tmp_path / "main.png"; path.write_bytes(image_bytes())
    with pytest.raises(ImageHostingError):
        S3ImageStorage(profile()).deliver(source_path=path)
    assert len(remote.calls) == 1
    assert not remote.uploads


def test_metadata_conflict_never_overwrites(tmp_path, monkeypatch):
    remote = FakeS3(monkeypatch)
    path = tmp_path / "main.png"; path.write_bytes(image_bytes())
    storage = S3ImageStorage(profile())
    delivered = storage.deliver(source_path=path)
    key = "/product-images/" + delivered.storage_key
    remote.objects[key] = (image_bytes(), "wrong-hash")
    with pytest.raises(ImageHostingError, match="不匹配"):
        storage.deliver(source_path=path)
    assert len(remote.uploads) == 1


def test_upload_timeout_is_unknown_and_next_explicit_prepare_checks_object(tmp_path, monkeypatch):
    remote = FakeS3(monkeypatch)
    original_open = remote.open
    def interrupted(req, **kwargs):
        response = original_open(req, **kwargs)
        if req.method == "PUT":
            raise URLError("测试连接中断，包含不应泄露的签名")
        return response
    monkeypatch.setattr(s3_image_storage, "safe_image_urlopen", interrupted)
    path = tmp_path / "main.png"; path.write_bytes(image_bytes())
    with pytest.raises(ImageHostingError) as error:
        S3ImageStorage(profile()).deliver(source_path=path)
    assert error.value.code == "IMAGE_UPLOAD_OUTCOME_UNKNOWN"
    assert "签名" not in str(error.value)
    assert len(remote.uploads) == 1
    monkeypatch.setattr(s3_image_storage, "safe_image_urlopen", original_open)
    assert S3ImageStorage(profile()).deliver(source_path=path).public_url
    assert len(remote.uploads) == 1


def test_sdk_default_transport_is_never_used(tmp_path, monkeypatch):
    from botocore.httpsession import URLLib3Session
    monkeypatch.setattr(URLLib3Session, "send", lambda *a, **k: pytest.fail("SDK 绕过了统一审计"))
    FakeS3(monkeypatch)
    path = tmp_path / "main.png"; path.write_bytes(image_bytes())
    assert S3ImageStorage(profile()).deliver(source_path=path).storage_key


def test_pdf_delivery_shares_operation_for_probe_upload_verification_and_public_read(monkeypatch):
    from erp_web.services import fulfillment_label_service as labels
    from erp_web.services.external_request_context import request_operation
    from erp_web.services.external_request_manager import BufferedResponse
    from tests.image_hosting_test_utils import configure
    remote = FakeS3(monkeypatch)
    configure()
    pdf = b"%PDF-1.7\nlabel fixture\n%%EOF"
    monkeypatch.setattr(labels, "safe_image_urlopen", lambda request, **kw: BufferedResponse(pdf, headers={}, status=200, url=request.full_url))
    with request_operation("fulfillment:fetch-label", operation_id="same-pdf-operation"):
        result = labels.deliver_label_bytes(get_context().config.load_app_config(), "order-123", pdf)
        assert result["url"].endswith(".pdf")
        assert [call["method"] for call in remote.calls] == ["HEAD", "PUT", "HEAD"]
        assert labels.deliver_label_bytes(get_context().config.load_app_config(), "order-123", pdf) == result
    assert len(remote.uploads) == 1
    assert get_context().external_requests.store.blocks() == []
    audit = get_context().external_requests.store.query(operation_id="same-pdf-operation")
    assert audit["stats"]["network_attempts"] == 7
    assert all(item["result"]["outcome"] == "success" for item in audit["items"])


@pytest.mark.parametrize("stage", ["upload", "public"])
def test_label_error_reports_failed_stage_and_retains_safe_transport_reason(monkeypatch, stage):
    from erp_web.services import fulfillment_label_service as labels
    from erp_web.schemas.fulfillment import FulfillmentError
    from erp_web.schemas.external_requests import ExternalRequestNotSent, RequestFailure
    configure_profile = profile()
    monkeypatch.setattr(labels, "default_profile", lambda config: configure_profile)
    class Storage:
        def __init__(self, config):
            pass
        def deliver_pdf(self, **kwargs):
            if stage == "upload":
                raise ImageHostingError("IMAGE_UPLOAD_FAILED", "S3 请求返回 HTTP 403，请检查对象读写权限")
            return "https://images.example.test/label.pdf"
    monkeypatch.setattr(labels, "S3ImageStorage", Storage)
    def public_failed(*args, **kwargs):
        raise ExternalRequestNotSent(RequestFailure("IMAGE_DNS_NONPUBLIC", "域名解析到非公开地址，请检查 DNS"))
    monkeypatch.setattr(labels, "managed_urlopen", public_failed)
    with pytest.raises(FulfillmentError) as error:
        labels.deliver_label_bytes({}, "order-123", b"%PDF-1.7\nfixture\n%%EOF")
    assert ("S3 面单上传及对象校验" if stage == "upload" else "面单公开下载核验") in str(error.value)
    assert ("HTTP 403" if stage == "upload" else "检查 DNS") in str(error.value)

"""显式表单测试的上传、匿名读取、精确清理与版本失效。"""
import io
from urllib.error import HTTPError

import pytest

from erp_web.context import get_context
from erp_web.facades import image_hosting_facade
from erp_web.services import image_public_access
from erp_web.services import image_hosting_transport, s3_image_storage
from erp_web.services.external_request_manager import BufferedResponse
from erp_web.schemas.external_requests import ExternalRequestNotSent, RequestFailure
from tests.image_hosting_test_utils import FakeS3, configure


def test_form_test_uses_saved_secrets_and_never_changes_configuration(monkeypatch):
    context = get_context(); configure(context)
    remote = FakeS3(monkeypatch)
    anonymous = []
    def get(req, **kwargs):
        anonymous.append(req)
        return BufferedResponse(remote.uploads[-1]["body"], headers={"Content-Type": "image/png"}, status=200, url=req.full_url)
    monkeypatch.setattr(image_public_access, "safe_image_urlopen", get)
    before = context.config.load_app_config()
    response, status = image_hosting_facade.test_image_hosting({"profile": {"id": "images-main"}})
    assert status == 200 and response["ok"]
    result = response["result"]
    assert result["upload_ok"] and result["public_access_ok"]
    assert result["upload_attempted"] and result["public_access_attempted"]
    assert result["cleanup_status"] == "deleted"
    assert "/hosting-tests/" in result["storage_key"]
    assert len(anonymous) == 1
    assert not any(key.lower() in {"authorization", "cookie", "x-amz-security-token"} for key, _ in anonymous[0].header_items())
    assert anonymous[0].method == "GET"
    assert not remote.objects
    assert context.config.load_app_config() == before
    assert context.config.public_image_hosting()["profiles"][0]["last_test"] == result
    assert "test-secret-key" not in str(response) and "test-access-key" not in str(response)
    assert context.external_requests.store.query(platform="image_hosting:public")["stats"]["network_attempts"] == 1
    context.config.save_image_hosting_profile({"id": "images-main", "name": "改名"})
    assert context.config.public_image_hosting()["profiles"][0]["last_test"] == result
    context.config.save_image_hosting_profile({"id": "images-main", "secret_access_key": "rotated-secret"})
    assert context.config.public_image_hosting()["profiles"][0]["last_test"] is None


@pytest.mark.parametrize("public_ok", [False, True])
def test_public_failure_and_cleanup_failure_are_independent(monkeypatch, public_ok):
    configure()
    remote = FakeS3(monkeypatch)
    old = remote.open
    def storage(req, **kwargs):
        if req.method == "DELETE":
            remote.calls.append({"method": "DELETE", "url": req.full_url})
            raise HTTPError(req.full_url, 403, "禁止删除", {}, io.BytesIO())
        return old(req, **kwargs)
    from erp_web.services import s3_image_storage
    monkeypatch.setattr(s3_image_storage, "safe_image_urlopen", storage)
    def get(req, **kwargs):
        raw = remote.uploads[-1]["body"] if public_ok else b"<html>forbidden</html>"
        return BufferedResponse(raw, headers={"Content-Type": "image/png"}, status=200, url=req.full_url)
    monkeypatch.setattr(image_public_access, "safe_image_urlopen", get)
    response, _ = image_hosting_facade.test_image_hosting({"profile": {"id": "images-main"}})
    result = response["result"]
    assert result["upload_ok"] is True
    assert result["public_access_ok"] is public_ok
    assert response["ok"] is public_ok
    assert result["cleanup_status"] == "retained"
    assert result["storage_key"] in result["cleanup_message"]
    deletes = [call for call in remote.calls if call["method"] == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0]["url"].endswith(result["storage_key"])


def test_unsaved_form_test_does_not_save_target_or_default(monkeypatch):
    context = get_context(); configure(context)
    remote = FakeS3(monkeypatch)
    monkeypatch.setattr(image_public_access, "safe_image_urlopen", lambda req, **kw: BufferedResponse(remote.uploads[-1]["body"], headers={"Content-Type": "image/png"}, status=200, url=req.full_url))
    response, _ = image_hosting_facade.test_image_hosting({"profile": {"id": "images-main", "bucket": "unsaved-bucket"}})
    assert response["ok"]
    assert "/unsaved-bucket/" in remote.uploads[0]["url"]
    assert context.config.load_app_config()["image_hosting"]["profiles"][0]["bucket"] == "product-images"
    assert context.config.public_image_hosting()["profiles"][0]["last_test"] is None


def test_fake_ip_upload_does_not_read_delete_or_claim_a_residual_object(monkeypatch):
    context = get_context(); configure(context)
    monkeypatch.setattr(image_hosting_transport.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("198.18.0.6", 443))])
    monkeypatch.setattr(image_hosting_transport.socket, "create_connection", lambda *a, **kw: pytest.fail("Fake-IP 拦截后不得建连"))
    monkeypatch.setattr(image_public_access, "safe_image_urlopen", lambda *a, **kw: pytest.fail("上传未发送时不得执行匿名读取"))
    response, status = image_hosting_facade.test_image_hosting({"profile": {"id": "images-main"}})
    result = response["result"]
    assert status == 200 and not response["ok"]
    assert not result["upload_ok"] and not result["upload_attempted"]
    assert not result["public_access_ok"] and not result["public_access_attempted"]
    assert result["error_code"] == "IMAGE_DNS_NONPUBLIC" and "Fake-IP" in result["message"]
    assert result["cleanup_status"] == "not_needed" and "无需清理" in result["cleanup_message"]
    audit = context.external_requests.store.query(platform="image_hosting:s3")
    assert audit["stats"]["attempts"] == 1 and audit["stats"]["network_attempts"] == 0
    assert audit["items"][0]["method"] == "PUT"
    assert audit["items"][0]["result"]["outcome"] == "not_sent"
    assert "test-secret-key" not in str(response) and "test-access-key" not in str(audit)


def test_head_local_rejection_after_put_still_cleans_the_uploaded_object(monkeypatch):
    configure()
    remote = FakeS3(monkeypatch)
    def send(req, **kwargs):
        if req.method == "HEAD":
            raise ExternalRequestNotSent(RequestFailure("IMAGE_DNS_NONPUBLIC", "对象检查时 DNS 已变为非公开地址"))
        return remote.open(req, **kwargs)
    monkeypatch.setattr(s3_image_storage, "safe_image_urlopen", send)
    response, _ = image_hosting_facade.test_image_hosting({"profile": {"id": "images-main"}})
    result = response["result"]
    assert result["upload_attempted"] and not result["public_access_attempted"]
    assert result["cleanup_status"] == "deleted"
    assert [call["method"] for call in remote.calls] == ["PUT", "DELETE"]
    assert not remote.objects


@pytest.mark.parametrize("failed_method", ["PUT", "DELETE"])
def test_unknown_write_is_reported_without_leaking_transport_errors(monkeypatch, failed_method):
    configure()
    remote = FakeS3(monkeypatch)
    def send(req, **kwargs):
        response = remote.open(req, **kwargs)
        if req.method == failed_method:
            raise TimeoutError("不要泄露签名或凭据")
        return response
    monkeypatch.setattr(s3_image_storage, "safe_image_urlopen", send)
    monkeypatch.setattr(image_public_access, "safe_image_urlopen", lambda req, **kw: BufferedResponse(remote.uploads[-1]["body"], headers={"Content-Type": "image/png"}, status=200, url=req.full_url))
    response, _ = image_hosting_facade.test_image_hosting({"profile": {"id": "images-main"}})
    result = response["result"]
    assert "不要泄露" not in str(response)
    if failed_method == "PUT":
        assert result["error_code"] == "IMAGE_UPLOAD_OUTCOME_UNKNOWN"
        assert result["upload_attempted"] and not result["public_access_attempted"]
        assert result["cleanup_status"] == "deleted"
    else:
        assert response["ok"] and result["cleanup_status"] == "unknown"
    assert len([call for call in remote.calls if call["method"] == failed_method]) == 1

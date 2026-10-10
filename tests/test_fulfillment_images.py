"""原图交付与仓库跨站访问：内容不替换，图片准备失败不创建报单。"""

import io
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PIL import Image

from erp_web.schemas.fulfillment import FulfillmentError
from erp_web.services import fulfillment_image_service as images
from tests.test_fulfillment import domain, ready, command


def image_bytes(color="red"):
    result = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(result, format="PNG")
    return result.getvalue()


def test_delivery_preserves_platform_bytes_and_checks_cross_site_access(monkeypatch):
    original = image_bytes()
    reads, files = [], []
    monkeypatch.setattr(images, "default_profile", lambda config: {})

    class Storage:
        def __init__(self, profile): pass
        def deliver(self, *, source_path):
            assert source_path.read_bytes() == original
            files.append(source_path)
            return SimpleNamespace(public_url="https://host.example/image.png")

    def read(req, **kwargs):
        reads.append((req.full_url, req.get_header("Referer")))
        assert kwargs["transport"] is images.safe_image_urlopen
        return io.BytesIO(original)

    monkeypatch.setattr(images, "S3ImageStorage", Storage)
    monkeypatch.setattr(images, "managed_urlopen", read)
    assert images.deliver_platform_image({}, "https://platform.example/source.png") == "https://host.example/image.png"
    assert reads == [("https://platform.example/source.png", None), ("https://host.example/image.png", "https://www.kuajing84.com/")]
    assert not files[0].exists()


@pytest.mark.parametrize("failure", ["forbidden", "different-content"])
def test_delivery_rejects_inaccessible_or_changed_public_image(monkeypatch, failure):
    monkeypatch.setattr(images, "default_profile", lambda config: {})
    monkeypatch.setattr(images, "S3ImageStorage", lambda profile: SimpleNamespace(deliver=lambda **kwargs: SimpleNamespace(public_url="https://host.example/image.png")))

    def read(req, **kwargs):
        if req.get_header("Referer"):
            if failure == "forbidden":
                raise HTTPError(req.full_url, 403, "secret-response", {}, None)
            return io.BytesIO(image_bytes("blue"))
        return io.BytesIO(image_bytes())

    monkeypatch.setattr(images, "managed_urlopen", read)
    with pytest.raises(FulfillmentError) as error:
        images.deliver_platform_image({}, "https://platform.example/source.png")
    assert error.value.definitive and not error.value.unknown
    assert "secret-response" not in str(error.value)


def test_create_and_package_update_deliver_images_only_on_write(domain):
    service, bus, order = ready(domain)
    sources = []
    def deliver(url):
        sources.append(url)
        return "https://host.example/platform-copy.png"
    service.image_delivery = deliver
    service.detail(order.identity)
    assert not sources
    service.process_one(order)
    assert sources == ["https://platform.example/yandex-sku.jpg"]
    assert bus.calls[0][1]["order_data"][0]["package_list"][0]["img"] == "https://host.example/platform-copy.png"
    command(service, order, "parcels", parcels=[domain[3]])
    service.process_one(order)
    assert [body for path, body in bus.calls if path.endswith("updateOrderPackage")][0]["package_list"][0]["img"] == "https://host.example/platform-copy.png"


@pytest.mark.parametrize("failure", ["upload", "cancel"])
def test_image_preparation_failure_or_concurrent_cancel_never_sends_create(domain, failure):
    service, bus, order = ready(domain)
    def deliver(url):
        if failure == "cancel":
            command(service, order, "cancel")
            return "https://host.example/platform-copy.png"
        raise FulfillmentError("商品图片托管失败")
    service.image_delivery = deliver
    service.process_one(order)
    assert not any(path.endswith("createOrder") for path, _ in bus.calls)
    assert not service.store.get(order.identity)["create_unknown"]


def test_dns_block_keeps_actionable_reason_and_does_not_upload(monkeypatch):
    from erp_web.schemas.external_requests import ExternalRequestNotSent, RequestFailure
    def blocked(*args, **kwargs):
        raise ExternalRequestNotSent(RequestFailure("IMAGE_DNS_NONPUBLIC", "图片域名解析到 198.18.0.0/15 保留地址，请排除代理 Fake-IP"))
    monkeypatch.setattr(images, "managed_urlopen", blocked)
    with pytest.raises(FulfillmentError, match="Fake-IP") as error:
        images.deliver_platform_image({}, "https://platform.example/source.png")
    assert error.value.definitive and not error.value.unknown


def test_remote_formal_order_edit_rejection_is_explained(domain, monkeypatch):
    import json
    from erp_web.services import crossborderbus_client as bus
    response = {"code": 2, "message": "订单非草稿状态,不可修改 private-secret"}
    monkeypatch.setattr(bus, "managed_urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()))
    with pytest.raises(FulfillmentError, match="正式报单") as error:
        bus.CrossborderBusClient(domain[0].store)._request("/erpapi/order/updateOrderPackage", {})
    assert error.value.definitive and not error.value.unknown
    assert "private-secret" not in str(error.value)

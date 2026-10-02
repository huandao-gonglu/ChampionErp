"""离线存储响应：真实 SDK 签名、真实请求管理，仅替换网络传输。"""
import hashlib
import io
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit

from PIL import Image

from erp_web.context import get_context
from erp_web.services.external_request_manager import BufferedResponse
from erp_web.services import s3_image_storage


def profile(**updates):
    return {"id": "images-main", "name": "测试图片存储", "type": "s3_compatible", "endpoint_url": "https://s3.example.test",
            "region": "auto", "bucket": "product-images", "key_prefix": "products", "public_base_url": "https://images.example.test",
            "addressing_style": "path", "access_key_id": "test-access-key", "secret_access_key": "test-secret-key", **updates}


def configure(context=None, **updates):
    context = context or get_context()
    config = context.config.load_app_config()
    value = profile(**updates)
    config["image_hosting"] = {"default_profile_id": value["id"], "profiles": [value]}
    context.config.save_app_config(config)
    return value


def image_bytes(kind="PNG", color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, format=kind)
    return stream.getvalue()


class FakeS3:
    def __init__(self, monkeypatch):
        self.objects = {}
        self.calls = []
        self.failure = None
        self.on_put = None
        monkeypatch.setattr(s3_image_storage, "safe_image_urlopen", self.open)

    def open(self, req, **kwargs):
        headers = {key.lower(): value for key, value in req.header_items()}
        self.calls.append({"url": req.full_url, "method": req.method, "headers": headers, "body": req.data, **kwargs})
        if self.failure:
            status = self.failure
            raise HTTPError(req.full_url, status, "测试拒绝", {}, io.BytesIO(b"<Error><Code>AccessDenied</Code></Error>"))
        key = unquote(urlsplit(req.full_url).path)
        if req.method == "HEAD":
            if key not in self.objects:
                raise HTTPError(req.full_url, 404, "不存在", {}, io.BytesIO())
            data, metadata = self.objects[key]
            return BufferedResponse(b"", headers={"content-length": str(len(data)), "x-amz-meta-sha256": metadata, "content-type": "image/png"}, status=200, url=req.full_url)
        if req.method == "PUT":
            self.objects[key] = (req.data, headers.get("x-amz-meta-sha256"))
            if self.on_put:
                self.on_put()
        elif req.method == "DELETE":
            self.objects.pop(key, None)
        return BufferedResponse(b"", headers={}, status=200, url=req.full_url)

    @property
    def uploads(self):
        return [call for call in self.calls if call["method"] == "PUT"]

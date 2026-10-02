"""S3 SDK 唯一装配与传输边界；生产交付只使用 HeadObject/PutObject。"""
from __future__ import annotations

import io
import os
import re
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

from botocore.awsrequest import AWSResponse
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.session import Session

from erp_web.schemas.external_requests import ExternalRequestBlocked, ExternalRequestOutcomeUnknown
from erp_web.schemas.image_hosting import DeliveredImage, ImageHostingError
from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.services.image_content import MAX_IMAGE_BYTES, image_content, read_image_file
from erp_web.services.image_hosting_config import config_version, content_storage_key, public_url, require_ready, target_fingerprint
from erp_web.services.image_hosting_transport import safe_image_urlopen


class _SdkRawResponse(io.BytesIO):
    def stream(self, amt=1024, decode_content=False):
        while chunk := self.read(amt):
            yield chunk


class S3ImageStorage:
    name = "s3_compatible"

    def __init__(self, profile: dict):
        self.profile = require_ready(profile)
        self.test_object_may_exist = False
        session = Session(session_vars={"profile": (None, None, None, None),
                                        "config_file": (None, None, os.devnull, None),
                                        "credentials_file": (None, None, os.devnull, None)})
        # 不读取机器默认 profile 或配置；凭据、区域及协议选项均显式注入。
        self.client = session.create_client(
            "s3", endpoint_url=self.profile["endpoint_url"], region_name=self.profile["region"],
            aws_access_key_id=self.profile["access_key_id"], aws_secret_access_key=self.profile["secret_access_key"], aws_session_token="",
            config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=30,
                          retries={"total_max_attempts": 1, "mode": "standard"},
                          request_checksum_calculation="when_required", response_checksum_validation="when_required",
                          s3={"addressing_style": self.profile["addressing_style"], "payload_signing_enabled": True,
                              "use_accelerate_endpoint": False, "use_dualstack_endpoint": False},
                          ignore_configured_endpoint_urls=True, use_dualstack_endpoint=False, use_fips_endpoint=False, proxies={}),
        )
        self.client.meta.events.register("before-send.s3", self._send)

    def _send(self, request, **kwargs):
        body = request.body
        if hasattr(body, "read"):
            body = body.read(MAX_IMAGE_BYTES + 1)
        headers = {key: value.decode("utf-8") if isinstance(value, bytes) else str(value) for key, value in request.headers.items()}
        req = Request(request.url, data=body, headers=headers, method=request.method)
        ctx = request_context(request.url, method=request.method, data=body, timeout=30,
                              source=__name__, platform="image_hosting:s3", account_id=self.profile["id"],
                              semantics="read" if request.method == "HEAD" else "write")
        ctx = replace(ctx, credential_id=config_version(self.profile), quota_key=target_fingerprint(self.profile))
        try:
            with managed_urlopen(req, timeout=30, request_context=ctx, transport=safe_image_urlopen, source=__name__) as response:
                raw, status, response_headers = response.read(), response.status, dict(response.headers)
        except HTTPError as exc:
            raw, status, response_headers = exc.read(), exc.code, dict(exc.headers or {})
            exc.close()
        if status >= 300 and not (status == 404 and request.method == "HEAD"):
            # 在 SDK 看到区域重定向错误前终止，禁止额外 HeadBucket 或隐式重放。
            raise ImageHostingError("IMAGE_UPLOAD_FAILED", f"S3 请求返回 HTTP {status}，请检查 Endpoint、Region 和权限；不会自动重试或重定向")
        return AWSResponse(request.url, status, response_headers, _SdkRawResponse(raw))

    def _head(self, key: str, digest: str, length: int | None) -> bool:
        try:
            result = self.client.head_object(Bucket=self.profile["bucket"], Key=key)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404 and str(exc.response.get("Error", {}).get("Code")) in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        if result.get("Metadata", {}).get("sha256") != digest or not result.get("ContentLength") or (length is not None and result.get("ContentLength") != length):
            raise ImageHostingError("IMAGE_UPLOAD_FAILED", "对象已存在但内容元数据或长度不匹配，已停止上传", "检查目标对象冲突，不要覆盖业务对象")
        return True

    def _put_image(self, key: str, data: bytes, digest: str, content_type: str, *, cache_control: str) -> None:
        self.client.put_object(
            Bucket=self.profile["bucket"], Key=key, Body=data,
            ContentType=content_type, Metadata={"sha256": digest}, CacheControl=cache_control,
        )

    def _require_test_key(self, key: str) -> None:
        prefix = self.profile["key_prefix"]
        expected = f"{prefix + '/' if prefix else ''}hosting-tests/"
        if not key.startswith(expected) or not re.fullmatch(r"[0-9a-f]{32}\.png", key[len(expected):]):
            raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "只允许操作本次独立测试对象")

    def upload_test_image(self, key: str, data: bytes) -> str:
        self._require_test_key(key)
        digest, _, content_type = image_content(data)
        self.test_object_may_exist = True
        try:
            self._put_image(key, data, digest, content_type, cache_control="no-store")
        except ExternalRequestBlocked as exc:
            if exc.details.get("definitively_rejected"):
                self.test_object_may_exist = False
            raise
        if not self._head(key, digest, len(data)):
            raise ImageHostingError("IMAGE_UPLOAD_FAILED", "测试对象上传后不存在")
        return digest

    def delete_test_image(self, key: str) -> None:
        self._require_test_key(key)
        self.client.delete_object(Bucket=self.profile["bucket"], Key=key)

    def deliver(self, *, source_path: Path | None, storage_key: str = "", content_sha256: str = "") -> DeliveredImage:
        try:
            if source_path is None or not source_path.is_file():
                digest = content_sha256
                suffix = storage_key.rsplit(".", 1)[-1]
                if storage_key != content_storage_key(self.profile, digest, suffix) or not self._head(storage_key, digest, None):
                    raise ImageHostingError("IMAGE_SOURCE_MISSING", "源图片与可复用对象均不存在", "重新导入源图片后准备")
                key = storage_key
                return DeliveredImage(key, public_url(self.profile, key), digest, self.name, self.profile["id"], target_fingerprint(self.profile))
            data = read_image_file(source_path)
            digest, suffix, content_type = image_content(data)
            key = content_storage_key(self.profile, digest, suffix)
            if not self._head(key, digest, len(data)):
                self._put_image(key, data, digest, content_type, cache_control="public, max-age=31536000, immutable")
            return DeliveredImage(key, public_url(self.profile, key), digest, self.name,
                                  self.profile["id"], target_fingerprint(self.profile))
        except ImageHostingError:
            raise
        except ExternalRequestOutcomeUnknown:
            raise ImageHostingError("IMAGE_UPLOAD_OUTCOME_UNKNOWN", "图片上传结果未知，本次准备未成功", "稍后再次显式准备，先检查对象是否已完整上传") from None
        except ExternalRequestBlocked as exc:
            code = "IMAGE_UPLOAD_OUTCOME_UNKNOWN" if exc.details.get("outcome_unknown") else "IMAGE_UPLOAD_FAILED"
            raise ImageHostingError(exc.code if exc.details.get("definitively_rejected") else code, str(exc)) from None
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            raise ImageHostingError("IMAGE_UPLOAD_FAILED", f"S3 对象请求失败（HTTP {status}），请检查权限、配置或限流状态") from None
        except Exception:
            raise ImageHostingError("IMAGE_UPLOAD_FAILED", "图片对象检查或上传失败，请检查配置和网络") from None


__all__ = ["S3ImageStorage", "image_content"]

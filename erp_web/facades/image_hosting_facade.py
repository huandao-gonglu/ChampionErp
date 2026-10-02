"""图片托管设置与显式测试的应用编排。"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from uuid import uuid4

from PIL import Image

from erp_web.context import get_context
from erp_web.schemas.external_requests import ExternalRequestBlocked, ExternalRequestOutcomeUnknown
from erp_web.schemas.image_hosting import ImageHostingError, ImageHostingTestResult
from erp_web.services.image_hosting_config import config_version, public_url
from erp_web.services.image_public_access import _check_image
from erp_web.services.s3_image_storage import S3ImageStorage


def list_image_hosting() -> tuple[dict, int]:
    return {"ok": True, "image_hosting": get_context().config.public_image_hosting()}, 200


def mutate_image_hosting(body: dict, action: str) -> tuple[dict, int]:
    try:
        store = get_context().config
        if action == "save":
            identity = store.save_image_hosting_profile(body["profile"])
        elif action == "default":
            identity = body.get("id")
            if not isinstance(identity, str):
                raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "请提供默认配置 id；空字符串表示解除默认")
            store.set_image_hosting_default(identity)
        elif action == "delete":
            identity = body["id"]
            store.delete_image_hosting_profile(identity)
        else:
            raise ValueError("未知托管操作")
        return {"ok": True, "id": identity, "image_hosting": store.public_image_hosting()}, 200
    except (ImageHostingError, ValueError) as exc:
        return {"ok": False, "error_code": getattr(exc, "code", "IMAGE_HOSTING_CONFIG_INVALID"), "error": str(exc)}, 400


def test_image_hosting(body: dict) -> tuple[dict, int]:
    store = get_context().config
    try:
        profile = store.image_hosting_test_profile(body["profile"])
    except ImageHostingError as exc:
        return {"ok": False, "error_code": exc.code, "error": str(exc)}, 400
    result = ImageHostingTestResult(profile_id=str(body["profile"].get("id") or ""), config_version=config_version(profile), checked_at=datetime.now(timezone.utc).isoformat())
    prefix = profile["key_prefix"]
    key = f"{prefix + '/' if prefix else ''}hosting-tests/{uuid4().hex}.png"
    result.storage_key = key
    result.public_url = public_url(profile, key)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (43, 108, 176)).save(buffer, format="PNG")
    data = buffer.getvalue()
    storage = None
    try:
        storage = S3ImageStorage(profile)
        digest = storage.upload_test_image(key, data)
        result.upload_ok = True
        result.public_access_attempted = True
        access = _check_image(result.public_url, expected_sha256=digest, profile_id=profile["id"])
        result.public_access_ok = access.status == "reachable"
        result.message = access.message
        if not result.public_access_ok:
            result.error_code = "IMAGE_PUBLIC_ACCESS_FAILED"
    except Exception as exc:
        unknown = isinstance(exc, ExternalRequestOutcomeUnknown) or (isinstance(exc, ExternalRequestBlocked) and exc.details.get("outcome_unknown"))
        result.error_code = "IMAGE_UPLOAD_OUTCOME_UNKNOWN" if unknown else getattr(exc, "code", "IMAGE_UPLOAD_FAILED")
        if unknown:
            result.message = "图片测试上传结果未知，请先核查本次测试对象；未执行匿名读取"
        elif isinstance(exc, (ImageHostingError, ExternalRequestBlocked)):
            result.message = str(exc)
        else:
            result.message = "图片测试上传或对象检查失败，请检查配置、网络与外部请求状态"
    finally:
        result.upload_attempted = bool(storage and storage.test_object_may_exist)
        if result.upload_attempted:
            try:
                # 仅删除本次独立随机测试 key，绝不清理业务对象。
                storage.delete_test_image(key)
                result.cleanup_status = "deleted"
                result.cleanup_message = "本次测试对象已删除"
            except Exception as exc:
                unknown = isinstance(exc, ExternalRequestOutcomeUnknown) or (isinstance(exc, ExternalRequestBlocked) and exc.details.get("outcome_unknown"))
                result.cleanup_status = "unknown" if unknown else "retained"
                result.cleanup_message = f"未确认测试对象已删除，请在存储商后台处理：{key}；无需扩大生产凭据权限"
        else:
            result.cleanup_message = "测试上传未发送，未产生测试对象，无需清理"
    value = result.model_dump()
    store.record_image_hosting_test(value)
    return {"ok": result.upload_ok and result.public_access_ok, "result": value}, 200


__all__ = ["list_image_hosting", "mutate_image_hosting", "test_image_hosting"]

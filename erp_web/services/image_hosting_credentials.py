"""托管表单与已保存秘密的纯合并契约。"""
from copy import deepcopy
from uuid import uuid4

from erp_web.schemas.image_hosting import ImageHostingError, ImageHostingProfile
from erp_web.services.config_service import is_masked_secret_placeholder
from erp_web.services.image_hosting_config import normalize_profile

SECRET_FIELDS = ("access_key_id", "secret_access_key")


def merge_profile(saved: dict | None, incoming: dict, *, new_id: str = "") -> dict:
    raw = deepcopy(incoming)
    clear = raw.pop("clear_secrets", [])
    if not isinstance(clear, list) or any(field not in SECRET_FIELDS for field in clear):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "清空凭据字段无效")
    for field in SECRET_FIELDS:
        raw.pop(field + "_configured", None)
    raw.pop("last_test", None)
    raw.pop("config_version", None)
    unknown = set(raw) - set(ImageHostingProfile.model_fields)
    if unknown:
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "托管表单包含未知字段")
    merged = {**(saved or {}), **raw}
    merged["id"] = (saved or {}).get("id") or new_id or "images-" + uuid4().hex
    for field in SECRET_FIELDS:
        value = raw.get(field)
        if field in clear:
            if value and not is_masked_secret_placeholder(value):
                raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "不能同时替换和清空同一凭据")
            merged[field] = ""
        elif field not in raw or is_masked_secret_placeholder(value):
            merged[field] = (saved or {}).get(field, "")
        elif not value:
            raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "清空凭据请使用明确清空动作；留空输入不应提交")
    return normalize_profile(merged)


__all__ = ["SECRET_FIELDS", "merge_profile"]

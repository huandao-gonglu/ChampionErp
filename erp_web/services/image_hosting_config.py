"""纯字段校验、内容身份与公开地址拼接；不读取配置或访问网络。"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from pydantic import ValidationError

from erp_web.schemas.image_hosting import ImageHostingConfig, ImageHostingError, ImageHostingProfile

_TARGET_FIELDS = ("type", "endpoint_url", "region", "bucket", "key_prefix", "public_base_url", "addressing_style")


def validate_public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片地址的主机或端口无效") from None
    if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or port == 0 or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片地址必须是无凭据、查询参数和片段的 HTTPS 地址")
    if host.lower() in {"localhost", "metadata.google.internal"} or host.lower().endswith((".localhost", ".local", ".internal")):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片地址不能指向本机、内网或云元数据服务")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片地址不能指向非公开 IP")
    if any(part in {".", ".."} for part in unquote(parsed.path).replace("\\", "/").split("/")):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片地址路径不能包含路径逃逸")
    return urlunsplit(parsed._replace(scheme="https", netloc=parsed.netloc.lower(), path=parsed.path.rstrip("/")))


def normalize_prefix(value: str) -> str:
    parts = value.replace("\\", "/").strip("/").split("/")
    decoded = unquote(value).replace("\\", "/")
    if any(part in {".", ".."} for part in decoded.split("/")) or any(ord(c) < 32 for c in decoded):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "对象路径前缀不能包含 . 或 .. 等路径逃逸")
    return "/".join(part for part in parts if part)


def normalize_profile(value: dict) -> dict:
    try:
        profile = ImageHostingProfile.model_validate(value).model_dump()
    except ValidationError:
        # Pydantic 的默认错误会带 input_value，不能回显秘密。
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片托管字段或类型无效，请检查表单") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile["id"]):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片托管配置 id 无效")
    for field in ("endpoint_url", "public_base_url"):
        if profile[field]:
            profile[field] = validate_public_url(profile[field])
    if profile["bucket"] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,253}[A-Za-z0-9]", profile["bucket"]):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "Bucket 名称无效")
    if profile["region"] and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", profile["region"]):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "Region 格式无效")
    profile["key_prefix"] = normalize_prefix(profile["key_prefix"])
    return profile


def normalize_image_hosting(value: object) -> dict:
    try:
        config = ImageHostingConfig.model_validate(value if value is not None else {}).model_dump()
    except ValidationError:
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片托管配置包含未知字段或无效结构") from None
    config["profiles"] = [normalize_profile(profile) for profile in config["profiles"]]
    ids = [profile["id"] for profile in config["profiles"]]
    if len(set(ids)) != len(ids) or config["default_profile_id"] and config["default_profile_id"] not in ids:
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片托管配置 id 重复或默认配置不存在")
    if config["default_profile_id"]:
        require_ready(next(item for item in config["profiles"] if item["id"] == config["default_profile_id"]), credentials=False)
    return config


def require_ready(profile: dict, *, credentials: bool = True) -> dict:
    profile = normalize_profile(profile)
    fields = ("endpoint_url", "region", "bucket", "public_base_url") + (("access_key_id", "secret_access_key") if credentials else ())
    if any(not profile[field] for field in fields):
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "图片托管配置不完整，请填写上传地址、区域、桶、公开地址和两项凭据")
    return profile


def target_fingerprint(profile: dict) -> str:
    return hashlib.sha256(json.dumps({field: profile.get(field, "") for field in _TARGET_FIELDS}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def config_version(profile: dict) -> str:
    identity = [target_fingerprint(profile), profile.get("access_key_id", ""), profile.get("secret_access_key", "")]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def public_url(profile: dict, key: str) -> str:
    if key != normalize_prefix(key) or not key:
        raise ImageHostingError("IMAGE_HOSTING_CONFIG_INVALID", "对象 key 无效")
    return profile["public_base_url"].rstrip("/") + "/" + quote(key, safe="/")


def content_storage_key(profile: dict, digest: str, suffix: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or suffix not in {"jpg", "png", "webp"}:
        raise ImageHostingError("IMAGE_DELIVERY_STALE", "图片交付的内容身份或格式无效", "重新准备图片")
    prefix = profile["key_prefix"]
    return f"{prefix + '/' if prefix else ''}assets/{digest[:2]}/{digest}.{suffix}"


def default_profile(config: dict) -> dict:
    hosting = config.get("image_hosting", {})
    profile = next((item for item in hosting.get("profiles", []) if item["id"] == hosting.get("default_profile_id")), None)
    if profile is None:
        raise ImageHostingError("IMAGE_HOSTING_NOT_CONFIGURED", "需要配置图片托管", "在设置中配置 S3 兼容存储并设为默认")
    return require_ready(profile)


__all__ = ["normalize_image_hosting", "normalize_profile", "normalize_prefix", "require_ready",
           "target_fingerprint", "config_version", "public_url", "content_storage_key", "default_profile", "validate_public_url"]

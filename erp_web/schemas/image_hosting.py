"""图片托管的配置、交付与测试契约。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ImageHostingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = ""
    name: str = ""
    type: Literal["s3_compatible"] = "s3_compatible"
    endpoint_url: str = ""
    region: str = ""
    bucket: str = ""
    key_prefix: str = ""
    public_base_url: str = ""
    addressing_style: Literal["auto", "path", "virtual"] = "auto"
    access_key_id: str = Field(default="", repr=False)
    secret_access_key: str = Field(default="", repr=False)


class ImageHostingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_profile_id: str = ""
    profiles: list[ImageHostingProfile] = Field(default_factory=list)


class ImageDeliveryFields(TypedDict, total=False):
    hosting_profile_id: str
    delivery_fingerprint: str
    delivery_provider: str
    storage_key: str
    content_sha256: str
    url: str


DELIVERY_FIELDS = tuple(ImageDeliveryFields.__annotations__)


@dataclass(frozen=True)
class DeliveredImage:
    storage_key: str
    public_url: str
    content_sha256: str
    provider: str
    hosting_profile_id: str
    delivery_fingerprint: str

    def fields(self) -> ImageDeliveryFields:
        return {"storage_key": self.storage_key, "url": self.public_url,
                "content_sha256": self.content_sha256, "delivery_provider": self.provider,
                "hosting_profile_id": self.hosting_profile_id,
                "delivery_fingerprint": self.delivery_fingerprint}


class ImageHostingError(ValueError):
    def __init__(self, code: str, message: str, next_action: str = "检查图片托管配置后重新显式准备"):
        super().__init__(message)
        self.code = code
        self.next_action = next_action


class ImageHostingTestResult(BaseModel):
    profile_id: str = ""
    config_version: str
    checked_at: str
    upload_ok: bool = False
    upload_attempted: bool = False
    public_access_ok: bool = False
    public_access_attempted: bool = False
    cleanup_status: Literal["not_needed", "deleted", "retained", "unknown"] = "not_needed"
    storage_key: str = ""
    public_url: str = ""
    error_code: str = ""
    message: str = ""
    cleanup_message: str = ""


__all__ = ["ImageHostingProfile", "ImageHostingConfig", "ImageDeliveryFields", "DELIVERY_FIELDS",
           "DeliveredImage", "ImageHostingError", "ImageHostingTestResult"]

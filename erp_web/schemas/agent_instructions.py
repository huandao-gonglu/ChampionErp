"""用户自定义全局偏好的公开文件契约。"""

from typing import TypedDict
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_USER_AGENTS_BYTES = 32 * 1024


class UserAgentsDocument(TypedDict):
    content: str
    revision: str


class UserAgentsSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=MAX_USER_AGENTS_BYTES)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("content")
    @classmethod
    def validate_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_USER_AGENTS_BYTES:
            raise ValueError("userAgents.md 不能超过 32 KiB。")
        return value


__all__ = ["MAX_USER_AGENTS_BYTES", "UserAgentsDocument", "UserAgentsSaveRequest"]

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


class UserAgentsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_USER_AGENTS_BYTES, description="要保存的长期规则正文。")
    current_text: str | None = Field(
        default=None, min_length=1, max_length=MAX_USER_AGENTS_BYTES,
        description="要替换的现有规则原文，必须唯一匹配。新增时省略。",
    )

    @field_validator("text", "current_text")
    @classmethod
    def validate_rule(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(marker in value for marker in ("<!--", "-->", "\x00"))):
            raise ValueError("规则须为非空正文，不能包含注释边界或空字符。")
        return value


class UserAgentsUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changed: bool
    text: str
    revision: str


__all__ = [
    "MAX_USER_AGENTS_BYTES", "UserAgentsDocument", "UserAgentsSaveRequest",
    "UserAgentsUpdateRequest", "UserAgentsUpdateResult",
]

from __future__ import annotations

"""店铺授权状态（脱敏 checklist、授权是否有效）的 Capability 契约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class StoreAuthChecklistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StoreAuthChecklistResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText = "mercadolibre"
    checklist: dict[str, JsonValue] = Field(default_factory=dict)


class StoreAuthCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=80),
    ]
    scope: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""


class StoreAuthCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    ok: bool = False
    message: TrimmedText = ""
    details: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "StoreAuthCheckRequest",
    "StoreAuthCheckResult",
    "StoreAuthChecklistRequest",
    "StoreAuthChecklistResult",
]

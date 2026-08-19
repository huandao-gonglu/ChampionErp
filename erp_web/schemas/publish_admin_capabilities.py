from __future__ import annotations

"""发布管理（直接发布、真实终态、远端关闭）的 Capability 契约。

三者都是对外部平台产生真实影响的破坏性写入：审批摘要与规范化参数由
服务端快照函数生成，digest 绑定冻结参数、步骤、任务版本与 Capability
版本；执行时重算快照复核，模型既不能提供审批 payload，也不能在批准后
篡改发布目标。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class ProductPublishDirectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )


class ProductPublishDirectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    status: TrimmedText = ""
    platform: TrimmedText = ""
    product_id: TrimmedText = ""
    message: TrimmedText = ""
    result: dict[str, JsonValue] = Field(default_factory=dict)


class PublishRealConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]


class PublishRealConfirmResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    status: TrimmedText = ""
    product_id: TrimmedText = ""
    payload_path: TrimmedText = ""
    message: TrimmedText = ""
    result: dict[str, JsonValue] = Field(default_factory=dict)


class PlatformItemCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    item_id: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=160)]


class PlatformItemCloseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    platform: TrimmedText = ""
    item_id: TrimmedText = ""
    status: TrimmedText = ""
    message: TrimmedText = ""


__all__ = [
    "PlatformItemCloseRequest",
    "PlatformItemCloseResult",
    "ProductPublishDirectRequest",
    "ProductPublishDirectResult",
    "PublishRealConfirmRequest",
    "PublishRealConfirmResult",
]

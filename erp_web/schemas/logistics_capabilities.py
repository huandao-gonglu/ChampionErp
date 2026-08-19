from __future__ import annotations

"""云途物流运单预览与真实创建的 Capability 契约。

Capability 只读取已保存的可信配置；与 HTTP facade 不同，模型侧不接受
临时密钥覆盖（raw secret override 属于 excluded 配置入口）。真实创建
是破坏性写入：审批摘要与规范化参数由服务端快照函数生成，模型不提供
审批 payload。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class LogisticsShipmentPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shipment: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_shipment(self) -> "LogisticsShipmentPreviewRequest":
        if not self.shipment:
            raise ValueError("shipment 不能为空")
        return self


class LogisticsShipmentPreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: TrimmedText = "yunexpress"
    request_payload: dict[str, JsonValue] = Field(default_factory=dict)
    message: TrimmedText = ""
    next_action: TrimmedText = ""


class LogisticsShipmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shipment: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_shipment(self) -> "LogisticsShipmentCreateRequest":
        if not self.shipment:
            raise ValueError("shipment 不能为空")
        return self


class LogisticsShipmentCreateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: TrimmedText = "yunexpress"
    message: TrimmedText = ""
    next_action: TrimmedText = ""
    response: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "LogisticsShipmentCreateRequest",
    "LogisticsShipmentCreateResult",
    "LogisticsShipmentPreviewRequest",
    "LogisticsShipmentPreviewResult",
]

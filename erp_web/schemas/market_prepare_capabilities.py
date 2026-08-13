from __future__ import annotations

"""目标市场准备、类目匹配与属性填写的类型化 Capability 契约。"""

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
)

from erp_web.schemas.draft_capabilities import DraftPublishReadiness


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]
StableId = Annotated[
    TrimmedText,
    StringConstraints(min_length=1, max_length=160),
]
PlatformKey = Annotated[
    TrimmedText,
    StringConstraints(min_length=1, max_length=80),
]


class CategoryMatchRequest(BaseModel):
    """对稳定草稿的一个明确目标市场运行类目匹配。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    target_platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    # focused Agent 无法消歧时，用户可以提交平台真实 category_id 继续同一步骤。
    category_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""


class CategoryMatchCapabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: StableId
    category_path: Annotated[TrimmedText, StringConstraints(max_length=1000)] = ""
    query: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    model_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    conversation_id: Annotated[TrimmedText, StringConstraints(max_length=200)] = ""
    changed: bool


class ProductAttributesFillRequest(BaseModel):
    """规则填充和 focused Agent 运行前可合并明确的用户属性值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    target_platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    provided_attributes: dict[StableId, JsonValue] = Field(
        default_factory=dict,
        max_length=200,
    )


class ProductAttributesFillResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    attributes: dict[str, JsonValue] = Field(max_length=500)
    filled_attribute_ids: list[str] = Field(default_factory=list, max_length=500)
    need_review_attribute_ids: list[str] = Field(default_factory=list, max_length=500)
    fill_source: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    warning: Annotated[TrimmedText, StringConstraints(max_length=1000)] = ""
    conversation_id: Annotated[TrimmedText, StringConstraints(max_length=200)] = ""
    changed: bool


class DraftPrepareForMarketRequest(BaseModel):
    """把来源草稿准备为一个可继续执行正式发布校验的目标市场草稿。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    target_platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    provided_attributes: dict[StableId, JsonValue] = Field(
        default_factory=dict,
        max_length=200,
    )
    asset_ids: list[StableId] = Field(default_factory=list, max_length=100)
    # 只接受核价业务输入；平台、站点与发布币种仍从可信草稿目标注入。
    pricing_input: dict[str, JsonValue] = Field(default_factory=dict)
    regenerate_copy: bool = False


class DraftPrepareForMarketResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    source_draft_id: StableId
    target_platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    completed_parts: list[str] = Field(min_length=1, max_length=20)
    readiness: DraftPublishReadiness
    agent_execution_conversation_ids: list[
        Annotated[TrimmedText, StringConstraints(min_length=1, max_length=200)]
    ] = Field(default_factory=list, max_length=20)


__all__ = [
    "CategoryMatchCapabilityResult",
    "CategoryMatchRequest",
    "DraftPrepareForMarketRequest",
    "DraftPrepareForMarketResult",
    "ProductAttributesFillRequest",
    "ProductAttributesFillResult",
]

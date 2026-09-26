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
from erp_web.schemas.draft_pricing import PricingParameters


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
    category_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = Field(
        default="", description="自动匹配留空；仅用户明确选择时填写，并提供 source_message_id。",
    )
    source_message_id: str = ""


class CategoryMatchCapabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: StableId
    category_path: Annotated[TrimmedText, StringConstraints(max_length=1000)] = ""
    query: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    model_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    changed: bool












class DraftPrepareForMarketRequest(BaseModel):
    """把来源草稿准备为一个可继续执行正式发布校验的目标市场草稿。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    target_platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    asset_ids: list[StableId] = Field(default_factory=list, max_length=100)
    source_conversation_id: str = Field(
        default="",
        max_length=160,
        description="资料来源会话 ID；跨会话引用仍由服务端校验归属。",
    )
    source_message_id: str = Field(
        default="",
        max_length=200,
        description="conversation_facts_query 返回的真实用户消息 ID；不接受模型自报来源。",
    )
    sales_target: list[
        Annotated[TrimmedText, StringConstraints(min_length=1, max_length=120)]
    ] = Field(
        default_factory=list,
        max_length=100,
        description=(
            "Mercado Libre CBT 销售目标；仅使用已保存选择或带 source_message_id 的用户事实。每项格式为 "
            'SITE_ID:logistic_type，例如 ["MLM:remote", "MLB:remote"]；'
            "不要猜测用户销售目标。"
        ),
    )
    # 只接受核价业务输入；平台、站点与发布币种仍从可信草稿目标注入。
    pricing: PricingParameters = Field(default_factory=PricingParameters)
    regenerate_copy: bool = False


class DraftPrepareForMarketResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: StableId
    source_draft_id: StableId
    target_platform: PlatformKey
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    completed_parts: list[str] = Field(min_length=1, max_length=20)
    readiness: DraftPublishReadiness


__all__ = [
    "CategoryMatchCapabilityResult",
    "CategoryMatchRequest",
    "DraftPrepareForMarketRequest",
    "DraftPrepareForMarketResult",
]

"""在线商品工具的有界查询契约；详情与修改复用公开领域结构。"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from erp_web.schemas.online_products import (
    JobStatus, OnlineFeedbackSummary, OnlineProduct, Operation, Platform,
    PriceScope, SourceImageSelection, StockScope,
)


class OnlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OnlineReadRequest(OnlineRequest):
    id: str = Field(default="", description="列表返回的在线商品 id；提供时读取详情，其余筛选参数不生效。不是本地商品或草稿 ID。")
    platform: Platform = "mercadolibre"
    q: str = ""
    status: str = ""
    market: str = ""
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=25, ge=1, le=50, description="每页最多返回的摘要条数；查看第一项用 page=1、limit=1。")
    view: Literal["groups", "listings"] = Field(default="groups", description="groups 按页面顺序读取父节点及代表刊登；listings 按同序逐 SKU 分页，组合可以跨页。")
    group_id: str = Field(default="", description="列表返回的真实组合或独立节点 ID；用于限定该节点的成员，读取全部成员时使用 view=listings。")
    include_source_images: bool = Field(default=False, description="提供 id 时同时读取关联源草稿的图片资产与内容版本，供在线图片修改选择；不提供 id 时无效。")
    fields: list[str] = Field(default_factory=list, description=(
        "按需读取公开业务字段，支持对象点路径，如 buyer_links、content.attributes、content.title、prices、stocks。"
        "有 fields 时 records 返回身份、同步与详情状态，以及 values[字段路径]；缺失路径列入 missing_fields。"
        "数组须选择完整数组以保留币种、仓库、属性名和单位，不支持数组下标、通配符或条件表达式。"
        "不传时保留列表摘要/单件完整详情；批量取数用 view=listings 并按 next_page 分页。"
    ))

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, fields: list[str]) -> list[str]:
        return validate_online_fields(fields)


def validate_online_fields(fields: list[str]) -> list[str]:
    """字段投影只访问已有公开契约，不开放平台原始响应或查询表达式。"""
    result = []
    for path in fields:
        parts = path.split(".")
        if (not all(part and part == part.strip() for part in parts)
                or any(char in path for char in "[]*")):
            raise ValueError(f"在线商品字段路径无效：{path}；使用对象点路径，数组整体读取")
        if parts[0] not in OnlineProduct.model_fields:
            raise ValueError(f"不是在线商品公开字段：{parts[0]}")
        if path not in result:
            result.append(path)
    return result


class OnlineSyncRequest(OnlineRequest):
    platform: Platform


class OnlineJobRequest(OnlineRequest):
    job_id: str = Field(min_length=1)


class OnlineJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    platform: Platform
    account_id: str
    operation: Operation
    target_id: str
    status: JobStatus
    idempotency_key: str
    request: dict[str, Any]
    result: dict[str, Any]
    dispatched: int
    created_at: str
    updated_at: str


class OnlineListingSummary(BaseModel):
    """用于定位、计价和库存统计的刊登摘要；内容与修改能力按 id 读取详情。"""
    model_config = ConfigDict(extra="forbid")
    id: str
    group_id: str
    platform: Platform
    remote_id: str
    seller_sku: str
    title: str
    raw_status: str
    sale_state: str
    prices: list[PriceScope]
    stocks: list[StockScope]
    synced_at: str
    details_state: Literal["pending", "ready", "failed"]


class OnlineProductFields(BaseModel):
    """按需字段载荷；身份和读取状态固定保留，值不做业务筛选或计算。"""
    model_config = ConfigDict(extra="forbid")
    id: str
    group_id: str
    platform: Platform
    account_id: str
    remote_id: str
    seller_sku: str
    version: str
    synced_at: str
    status_checked_at: str
    details_state: Literal["pending", "ready", "failed"]
    errors: list[str]
    values: dict[str, Any]
    missing_fields: list[str] = Field(default_factory=list)


class OnlineGroupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    kind: Literal["group", "single"]
    total_count: int
    matched_count: int
    representative_id: str
    feedback_summary: OnlineFeedbackSummary


class OnlineSyncSummary(BaseModel):
    """同步可信度所需的固定字段；不携带每个 SKU 的结果或历史任务。"""
    model_config = ConfigDict(extra="forbid")
    id: str
    status: JobStatus
    updated_at: str
    discovered: int | None = None
    completed: int | None = None
    failed: int | None = None
    discovery_complete: bool | None = None
    error_code: str = ""


class OnlineReadResult(BaseModel):
    """默认列表/详情使用 items/item，按需字段使用 records；不返回历史任务明细。"""
    model_config = ConfigDict(extra="forbid")
    ok: bool
    item: OnlineProduct | None = None
    source_images: SourceImageSelection | None = None
    items: list[OnlineListingSummary] = Field(default_factory=list)
    records: list[OnlineProductFields] = Field(default_factory=list, description="指定 fields 时的字段记录；item/items 不重复返回数据。")
    groups: list[OnlineGroupSummary] = Field(default_factory=list)
    view: Literal["groups", "listings", "detail"] = "detail"
    platform: Platform | None = None
    account_id: str = ""
    total: int = 0
    listing_total: int = 0
    page: int = 1
    per_page: int = 25
    next_page: int | None = None
    summary: dict[str, int] = Field(default_factory=dict)
    markets: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    latest_sync: OnlineSyncSummary | None = None
    state: str = ""
    store_name: str = ""


class OnlineJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    job: OnlineJob


class OnlineSubmissionResult(BaseModel):
    """AI 修改的完成回执：领域任务已持久化，不承诺远端生效。"""
    model_config = ConfigDict(extra="forbid")
    accepted: Literal[True] = True
    job_id: str
    listing_id: str
    platform: Platform
    operation: Operation
    status: JobStatus
    summary: str

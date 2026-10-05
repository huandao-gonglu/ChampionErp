"""在线商品工具的有界查询契约；详情与修改复用公开领域结构。"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    """列表为分页摘要，详情使用 item；两种读取均不返回历史任务明细。"""
    model_config = ConfigDict(extra="forbid")
    ok: bool
    item: OnlineProduct | None = None
    source_images: SourceImageSelection | None = None
    items: list[OnlineListingSummary] = Field(default_factory=list)
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

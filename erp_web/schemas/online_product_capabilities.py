"""现有在线商品 HTTP 契约的类型化工具边界；不增加场景查询或写入动作。"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from erp_web.schemas.online_products import JobStatus, OnlineProduct, OnlineProductGroup, Operation, Platform, SourceImageSelection


class OnlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OnlineReadRequest(OnlineRequest):
    id: str = Field(default="", description="列表返回的在线商品 id；提供时读取详情，其余筛选参数不生效。不是本地商品或草稿 ID。")
    platform: Platform = "mercadolibre"
    q: str = ""
    status: str = ""
    market: str = ""
    page: int = Field(default=1, ge=1)
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


class OnlineReadResult(BaseModel):
    """同一个 GET 接口的列表/详情返回；详情使用 item，列表使用 items。"""
    model_config = ConfigDict(extra="forbid")
    ok: bool
    item: OnlineProduct | None = None
    source_images: SourceImageSelection | None = None
    items: list[OnlineProduct] = Field(default_factory=list)
    groups: list[OnlineProductGroup] = Field(default_factory=list)
    platform: Platform | None = None
    account_id: str = ""
    total: int = 0
    listing_total: int = 0
    page: int = 1
    per_page: int = 25
    summary: dict[str, int] = Field(default_factory=dict)
    markets: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    latest_sync: OnlineJob | None = None
    state: str = ""
    jobs: list[OnlineJob] = Field(default_factory=list)
    store_name: str = ""


class OnlineJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    job: OnlineJob

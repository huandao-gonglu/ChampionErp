"""在线商品工具的有界查询契约；详情与修改复用公开领域结构。"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from erp_web.schemas.online_products import (
    JobStatus, OnlineChange, OnlineFeedbackSummary, OnlineProduct, Operation, Platform,
    PriceScope, SourceImageSelection, StockScope, OnlineWarehouse,
)


class OnlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OnlineReadRequest(OnlineRequest):
    id: str = Field(default="", description="列表返回的在线商品 id；提供时自动读取该 SKU 当前完整详情，包含最新内容，再按 fields 投影。同轮与主动刷新共用读取结果；列表筛选参数不生效。不是本地商品或草稿 ID。")
    platform: Platform = "mercadolibre"
    q: str = ""
    status: str = ""
    market: str = ""
    warehouse_id: str = Field(default='', description='Yandex 仓库 ID；列表按此仓库库存及店铺状态展示，留空查看全部仓库。')
    catalog: Literal["current", "archived", "all"] = Field(default="current", description="current 为普通商品（默认排除归档），archived 仅归档商品，all 包含全部；归档与停售、库存无关。")
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=25, ge=1, le=50, description="每页最多返回的记录数，不是响应字节保证；长描述、图片、全部属性或完整对象应减小页大小。查看第一项用 page=1、limit=1。")
    view: Literal["groups", "listings"] = Field(default="groups", description="groups 按页面顺序读取父节点及代表刊登；listings 按同序逐 SKU 分页，组合可以跨页。")
    group_id: str = Field(default="", description="列表返回的真实组合或独立节点 ID；用于限定该节点的成员，读取全部成员时使用 view=listings。")
    include_source_images: bool = Field(default=False, description="提供 id 时同时读取关联源草稿的图片资产与内容版本，供在线图片修改选择；不提供 id 时无效。")
    fields: list[str] = Field(default_factory=list, description=(
        "只选择本次任务需要的公开业务字段，支持对象点路径。"
        "常用路径：title（商品名）、buyer_links（买家链接）、prices（价格及币种范围）、stocks（库存及仓库范围）；"
        "content.title、content.description、content.pictures、content.attributes 分别为内容标题、长描述、图片、全部属性；"
        "content.guarantee_period 为 Yandex 保修期限（timePeriod、timeUnit、可选 comment）。"
        "capabilities.stock、capabilities.content、capabilities.price、capabilities.sale_state、capabilities.archive "
        "分别返回对应操作的 enabled、reason、fields、scope，按实际操作选择，不必读取完整 capabilities。"
        "例如核对保修和库存用 ['content.guarantee_period','stocks','capabilities.content','capabilities.stock']。"
        "仅需子字段时不读取完整 content；不得同时选择父路径及其子路径，如 content 与 content.attributes。"
        "这些路径是常用示例，不限制其他公开字段。"
        "有 fields 时 records 固定返回身份、version、同步与详情状态，以及 values[字段路径]；缺失路径列入 missing_fields。"
        "本轮已取得单件当前字段和 version 时直接复用，无需再读完整详情或主动刷新。"
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
    selected = set(result)
    for path in result:
        parts = path.split(".")
        for length in range(1, len(parts)):
            parent = ".".join(parts[:length])
            if parent in selected:
                raise ValueError(
                    f"在线商品字段选择重叠：{parent} 已包含 {path}。"
                    f"仅需局部数据时删除 {parent} 并保留所需子路径；"
                    f"确需完整对象时删除 {path}，避免重复返回。"
                )
    return result


class OnlineSyncRequest(OnlineRequest):
    platform: Platform
    catalog: Literal["current", "archived"] = Field(default="current", description="只同步所选目录：current 普通商品，archived 归档商品（目前仅 Yandex）；跟随用户当前页面目录，不同时同步两类。")
    mode: Literal["full", "hot"] = Field(default="full", description="full 为全量目录与详情同步；hot 跳过全店目录扫描，直接并发刷新本地已有 SKU（Yandex 同时跳过目录图文与卡片反馈，仅刷新价格、库存与状态）。")


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
    """用于定位、计价和库存统计的刊登摘要；内容、修改能力与版本通过 fields 按需读取。"""
    model_config = ConfigDict(extra="forbid")
    id: str
    group_id: str
    platform: Platform
    remote_id: str
    seller_sku: str
    title: str
    raw_status: str
    sale_state: str
    archived: bool = False
    prices: list[PriceScope]
    stocks: list[StockScope]
    synced_at: str
    sales_checked_at: str = ""
    basic_synced_at: str = ""
    details_state: Literal["pending", "unloaded", "ready", "failed"]


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
    sales_checked_at: str = ""
    basic_synced_at: str = ""
    details_state: Literal["pending", "unloaded", "ready", "failed"]
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
    catalog: Literal["current", "archived", "all"] = "current"
    account_id: str = ""
    total: int = 0
    listing_total: int = 0
    page: int = 1
    per_page: int = 25
    next_page: int | None = None
    summary: dict[str, int] = Field(default_factory=dict)
    markets: list[str] = Field(default_factory=list)
    warehouses: list[OnlineWarehouse] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    latest_sync: OnlineSyncSummary | None = None
    state: str = ""
    store_name: str = ""


class OnlineJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    job: OnlineJob
    recovery: "OnlineJobRecovery"


class OnlineJobRecovery(BaseModel):
    """描述这笔任务本身，不承诺目标没有其他阻塞，也不产生写入授权。"""
    model_config = ConfigDict(extra="forbid")
    requires_resolution: bool
    can_reconcile: bool
    can_close: bool
    explanation: str


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


class OnlineChangesRequest(OnlineRequest):
    items: list[OnlineChange] = Field(min_length=1, max_length=100,
        description="本次全部目标的真实 ID、版本和局部修改。同一批只允许同一操作和仓库范围；不同 SKU 可有不同属性值。")
    group_id: str = Field(default="", description="单批覆盖整组时必填，服务端核对 items 恰好覆盖整组；局部选择、超过 100 项的子批及失败子集留空，分批后核对完整覆盖。")
    catalog: Literal["current", "archived", "all"] = "current"

    @model_validator(mode="after")
    def distinct_targets_and_scope(self):
        if len({item.listing_id for item in self.items}) != len(self.items):
            raise ValueError("同一批不能重复提交同一个 SKU")
        if len({(item.operation, item.scope_id) for item in self.items}) != 1:
            raise ValueError("同一批必须固定操作和仓库范围，不同操作或仓库分别提交")
        return self


class OnlineChangeReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_id: str
    accepted: bool
    job_id: str = ""
    status: str = ""
    error_code: str = ""
    error: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class OnlineChangesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    accepted_count: int
    failed_count: int
    items: list[OnlineChangeReceipt]
    summary: str

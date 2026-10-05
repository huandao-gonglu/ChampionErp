"""订单采购契约：来源、销售规格关联与实际采购记录各自保留身份。"""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from erp_web.schemas.orders import OrderLine, OrderView, Platform


def safe_purchase_url(value: str) -> str:
    if not value:
        return ""
    parsed = HttpUrl(value)
    if parsed.username or parsed.password:
        raise ValueError("采购地址不能含登录凭据")
    return str(parsed)


class ProcurementSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier: str = Field(default="", max_length=200)
    source_platform: str = Field(default="", max_length=100)
    product_url: str = Field(max_length=2000)
    source_sku_id: str = Field(default="", max_length=200)
    specification: str = Field(min_length=1, max_length=1000)
    sku_url: str = Field(default="", max_length=2000)
    sku_url_verified: bool = False

    @model_validator(mode="after")
    def validate_links(self):
        self.product_url = safe_purchase_url(self.product_url)
        if not self.product_url:
            raise ValueError("请填写采购商品地址")
        self.sku_url = safe_purchase_url(self.sku_url)
        if self.sku_url_verified and (not self.sku_url or not self.source_sku_id):
            raise ValueError("确认规格直达链接时须同时填写来源 SKU 编号和链接")
        if (
            self.sku_url
            and urlsplit(self.product_url).hostname != urlsplit(self.sku_url).hostname
        ):
            raise ValueError("规格链接与采购商品地址须属于同一网站")
        return self


class SalesSkuBinding(BaseModel):
    id: str = ""
    platform: Platform
    store_identity: str
    seller_sku: str
    remote_id: str = ""
    variant_id: str = ""
    product_id: str
    draft_id: str
    sku_id: str
    source: ProcurementSource
    publication_id: str

    @property
    def identity(self) -> str:
        # 发布记录号不参与身份；重复发布同一关联不会增加候选。
        data = self.model_dump(exclude={"publication_id", "id"})
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()


class SourceSelection(BaseModel):
    line_key: str
    revision: int = 0
    status: Literal["unmatched", "matched", "ambiguous", "confirmed"] = "unmatched"
    source: ProcurementSource | None = None
    candidates: list[SalesSkuBinding] = Field(default_factory=list)
    reason: str = ""


class PurchaseRecord(BaseModel):
    id: str
    line_key: str
    request_id: str
    quantity: int = Field(gt=0)
    purchase_order_number: str
    source: ProcurementSource
    created_at: str
    status: Literal["purchased", "cancelled"] = "purchased"
    cancelled_at: str = ""


class ProcurementLine(BaseModel):
    line: OrderLine
    selection: SourceSelection
    records: list[PurchaseRecord] = Field(default_factory=list)
    purchased_quantity: int = 0
    remaining_quantity: int = 0


class OrderDetail(BaseModel):
    ok: bool = True
    order: OrderView
    lines: list[ProcurementLine]


class SelectSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1)
    line_key: str = Field(min_length=1)
    revision: int = Field(ge=0)
    candidate_id: str = ""
    source: ProcurementSource | None = None

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.candidate_id) == bool(self.source):
            raise ValueError("请选择一个已发布来源，或明确填写人工采购来源")
        return self


class RecordPurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1)
    line_key: str = Field(min_length=1)
    revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, strict=True)
    purchase_order_number: str = Field(min_length=1, max_length=200)


class CancelPurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)


def order_line_key(line: OrderLine) -> str:
    # SKU 身份跨首次补全远端字段保持稳定；同订单重复 SKU 由服务明确拒绝猜测。
    parts = (
        [line.sku, line.variant_id]
        if line.sku
        else [line.line_id, line.remote_id, line.variant_id]
    )
    if not any(parts):
        return ""
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:24]

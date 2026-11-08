"""草稿中缺失包装边长的估算建议与试算结果。"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .product_facts import Identity, Measurement

Edge = Literal["length_cm", "width_cm", "height_cm"]


class EstimateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EdgeEstimate(EstimateModel):
    low: Measurement
    suggested: Measurement
    high: Measurement

    @model_validator(mode="after")
    def ordered(self):
        if not self.low <= self.suggested <= self.high:
            raise ValueError("建议值必须位于估算上下限之间。")
        return self


class SkuPackageEstimate(EstimateModel):
    sku_id: Identity
    sku_name: str = Field(default="", max_length=500, description="商品资料中的规格原名，提供后展示给用户。")
    dimensions: dict[Edge, EdgeEstimate] = Field(min_length=1, max_length=3)
    basis: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] = Field(
        description="写明此规格的资料来源、读到的数值及从商品到装箱外尺寸的假设。"
    )


class PackageEstimatesRequest(EstimateModel):
    draft_id: Identity
    expected_product_updated_at: Identity
    estimates: list[SkuPackageEstimate] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_skus(self):
        if len({item.sku_id for item in self.estimates}) != len(self.estimates):
            raise ValueError("同一 SKU 的边长建议必须合并。")
        return self


class PackageFreightPreviewRequest(PackageEstimatesRequest):
    target_keys: list[Identity] = Field(default_factory=list, max_length=20)


class FreightSample(EstimateModel):
    sku_id: str
    market: str
    route: str
    warehouse: str
    amount: float
    currency: str


class FreightScenario(EstimateModel):
    endpoint: Literal["low", "high"]
    quote_count: int
    quotes: list[FreightSample] = Field(max_length=40)
    error_count: int
    errors: list[str] = Field(max_length=20)


class PackageFreightPreview(EstimateModel):
    yandex_extra_small_checks: dict[str, str] = Field(default_factory=dict)
    scenarios: list[FreightScenario]

"""商品 SKU 资料局部写入，字段缺省时保持原值。"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Measurement = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]


class PackageDimensionsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    length_cm: Measurement | None = None
    width_cm: Measurement | None = None
    height_cm: Measurement | None = None
    weight_kg: Measurement | None = None

    @model_validator(mode="after")
    def check_fields(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("至少提供一个有效包装字段，不接受空值。")
        return self


class ProductSkuFactsChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sku_id: Identity
    cost_cny: Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)] | None = None
    package_dimensions: PackageDimensionsPatch | None = None
    name: str | None = None
    options: dict[str, str] | None = None
    barcode: str | None = None
    supplier_stock: Annotated[int, Field(ge=0, strict=True)] | None = None
    battery: bool | None = None
    liquid: bool | None = None

    @model_validator(mode="after")
    def check_fields(self):
        fields = self.model_fields_set - {"sku_id"}
        if not fields or any(getattr(self, key) is None for key in fields):
            raise ValueError("至少提供一个要修改的资料字段，不接受 null。")
        return self


class ProductSkuFactsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: Identity
    expected_updated_at: Identity
    changes: list[ProductSkuFactsChange] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def check_unique(self):
        if len({row.sku_id for row in self.changes}) != len(self.changes):
            raise ValueError("同一 SKU 的修改必须合并，不能重复提交。")
        return self


class ProductSkuFactsUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: Identity
    updated_at: Identity
    changed_count: int = Field(ge=0)
    sku_ids: list[Identity]

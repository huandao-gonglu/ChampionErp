"""逐 SKU 包装资料的局部写入契约；尺寸为 cm，重量为 kg。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Identity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Measurement = Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)]


class PackageDimensionsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    length_cm: Measurement | None = None
    width_cm: Measurement | None = None
    height_cm: Measurement | None = None
    weight_kg: Measurement | None = None

    @model_validator(mode="after")
    def require_measurements(self) -> "PackageDimensionsPatch":
        if not self.model_fields_set or any(
            getattr(self, key) is None or getattr(self, key) <= 0 for key in self.model_fields_set
        ):
            raise ValueError("至少提供一个包装字段；已提供的值必须是有限正数，不能为 null。")
        return self


class DraftSkuPackageUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Identity
    sku_ids: Annotated[tuple[Identity, ...], Field(min_length=1, max_length=100)]
    package_dimensions: PackageDimensionsPatch = Field(
        description="仅更新提供的包装字段，长度单位 cm、重量单位 kg；未提供的字段（尤其各 SKU 原有重量）保持不变。",
    )

    @model_validator(mode="after")
    def require_unique_skus(self) -> "DraftSkuPackageUpdateRequest":
        if len(set(self.sku_ids)) != len(self.sku_ids):
            raise ValueError("sku_ids 不得重复。")
        return self


class DraftSkuPackageUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Identity
    sku_ids: Annotated[tuple[Identity, ...], Field(min_length=1, max_length=100)]
    package_dimensions: dict[str, float] = Field(min_length=1, max_length=4)
    changed_count: int = Field(ge=0, le=100)
    changed: bool
    updated_at: str = Field(max_length=64)

"""一份草稿的成组字段修改；请求携带读取版本，结果对应实际一次提交。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from .draft_package import Identity, PackageDimensionsPatch


class DraftChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sku_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] = ""
    attributes: dict[Identity, JsonValue] = Field(default_factory=dict, max_length=200,
        description="要设置的类目属性；值为 null 表示清空。sku_id 为空时修改公共属性，否则修改该 SKU 的差异属性。")
    package_dimensions: PackageDimensionsPatch | None = Field(default=None,
        description="指定 SKU 的包装局部字段，cm/kg；省略字段保持原值。")
    stock: Annotated[int, Field(strict=True, ge=0)] | None = Field(default=None,
        description="指定 SKU 的非负整数库存；省略时不修改。")

    @model_validator(mode="after")
    def require_change(self) -> "DraftChange":
        if not self.attributes and self.package_dimensions is None and self.stock is None:
            raise ValueError("每项必须包含属性、包装或库存中的至少一种修改。")
        if not self.sku_id and (self.package_dimensions is not None or self.stock is not None):
            raise ValueError("包装和库存修改必须指定 sku_id。")
        return self


class DraftChangesApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Identity
    expected_updated_at: Identity = Field(description="本次 draft_attributes_read 返回的 updated_at；版本不符时整组不写入，先重新读取再计算。")
    platform: Identity
    site: Identity
    category_id: str = Field(default="", max_length=160, description="修改类目属性时必须提供读取时的类目 ID。仅改包装或库存可省略。")
    changes: list[DraftChange] = Field(min_length=1, max_length=1000,
        description="由脚本计算的全部差异，每个 SKU 至多一项；同项可以合并属性、包装和库存。全部校验通过后只保存草稿一次。")

    @model_validator(mode="after")
    def require_unique_targets(self) -> "DraftChangesApplyRequest":
        ids = [change.sku_id for change in self.changes]
        if len(ids) != len(set(ids)):
            raise ValueError("同一 SKU 或公共属性不得出现多项，请先在脚本中合并。")
        if any(change.attributes for change in self.changes) and not self.category_id.strip():
            raise ValueError("修改属性必须指定 category_id。")
        return self


class DraftChangeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sku_id: str = ""
    changed: bool
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    changed_keys: list[str] = Field(default_factory=list)
    package_dimensions: dict[str, str] = Field(default_factory=dict)
    stock: str | None = None
    draft_fields: dict[str, str] = Field(default_factory=dict)


class DraftChangesApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str
    platform: str
    site: str
    category_id: str
    changed: bool
    changed_count: int
    previous_updated_at: str
    updated_at: str
    changes: list[DraftChangeResult]

"""一份草稿的成组字段修改；请求携带读取版本，结果对应实际一次提交。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from .product_facts import Identity


class DraftChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sku_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] = ""
    attributes: dict[Identity, JsonValue] = Field(default_factory=dict, max_length=200,
        description="要设置的类目属性；值为 null 表示清空。sku_id 为空时修改公共属性，否则修改该 SKU 的差异属性。")
    stock: Annotated[int, Field(strict=True, ge=0)] | None = Field(default=None,
        description="指定 SKU 的非负整数库存；省略时不修改。")

    @model_validator(mode="after")
    def require_change(self) -> "DraftChange":
        if not self.attributes and self.stock is None:
            raise ValueError("每项必须包含属性或库存中的至少一种修改。")
        if not self.sku_id and self.stock is not None:
            raise ValueError("库存修改必须指定 sku_id。")
        return self


class DraftChangesApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Identity
    expected_updated_at: Identity = Field(description="读取时或本轮最近成功保存回执中的 updated_at；使用保存回执前须将实际值同步到脚本数据。版本不符时整组不写入，按本次任务范围重新读取并计算。")
    platform: Identity
    site: Identity
    category_id: str = Field(default="", max_length=160, description="修改类目属性时必须提供读取时的类目 ID。仅改库存可省略。")
    changes: list[DraftChange] = Field(min_length=1, max_length=1000,
        description="合并本轮已确定的全部属性差异，每个 SKU 至多一项；同项 attributes 同时包含颜色、规格等本次所需字段，可合并库存。不得按属性分轮提交整批 SKU。全部校验通过后只保存草稿一次。")

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
    stock: str | None = None
    draft_fields: dict[str, str] = Field(default_factory=dict)


class DraftChangesApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str
    platform: str
    site: str
    category_id: str
    changed: bool
    changed_count: int = Field(description="实际发生变化的修改项数量，不是属性数；0 表示提交值已一致，不代表保存失败。")
    previous_updated_at: str
    updated_at: str
    changes: list[DraftChangeResult] = Field(description="对应全部提交项的实际保存回执，包含未变化项；attributes 只含本次提交字段的实际值，清空字段不再出现。直接逐项核对，不为确认成功而重读整批 SKU。")

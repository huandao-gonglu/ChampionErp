from __future__ import annotations

"""类目搜索、属性定义/枚举查询与类目预检的 Capability 契约。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from erp_web.schemas.category import CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL

TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class CategorySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    product_type: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=100)] = Field(description="固定检索语言的简短实物通用名，将与 keywords 一起查询。")
    keywords: tuple[Annotated[TrimmedText, StringConstraints(min_length=1, max_length=300)], ...] = Field(
        min_length=1, max_length=CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
    )
    limit: int | None = Field(default=None, ge=1, description="可选的本页条数限制；省略时仅按输出字节预算分页。")
    offset: int = Field(default=0, ge=0, description="续页保持搜索参数不变，使用上次 next_offset。")


class CategorySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    site: TrimmedText = ""
    keywords: tuple[TrimmedText, ...] = ()
    source: TrimmedText = ""
    results: tuple[dict[str, JsonValue], ...] = ()
    errors: tuple[dict[str, JsonValue], ...] = ()
    truncated: bool = False
    has_more: bool = False
    next_offset: int | None = None
    total_candidate_count: int = 0
    returned_candidate_count: int = 0
    remaining_candidate_count: int = 0
    query_candidate_counts: dict[str, int] = Field(default_factory=dict)


class CategoryAttributesQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    cursor: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    limit: int = Field(default=50, ge=1, le=100)
    scope: Literal["common", "sku", "writable", "all"] = Field(
        default="common",
        description="common 只读可写公共属性；sku 只读可写 SKU 属性；writable 一次读全部可写公共与 SKU 属性；all 查看全部定义及 write_scope。分页游标沿用原始定义页，过滤后空页仍须检查 has_more。",
    )
    compact: bool = Field(
        default=False,
        description="true 时省略长说明与 excluded_attributes，只返回填写所需的紧凑可写字段摘要。",
    )


class CategoryAttributesQueryResult(BaseModel):
    """类目属性分页有界摘要；不含 raw/platform_binding/完整枚举。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    site: TrimmedText = ""
    category_id: TrimmedText = ""
    category_path: TrimmedText = ""
    limit: int = 50
    cursor: TrimmedText = ""
    attributes: tuple[dict[str, JsonValue], ...] = ()
    excluded_attributes: tuple[dict[str, JsonValue], ...] = ()
    next_cursor: TrimmedText = ""
    has_more: bool = False


class CategoryAttributesQueryBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_ids: list[
        Annotated[TrimmedText, StringConstraints(min_length=1, max_length=160)]
    ] = Field(
        min_length=1,
        max_length=30,
        description="待批量查询属性定义的类目 ID 列表（最多 30 个，自动去重保序）；服务端并发翻完各自分页并默认返回紧凑可写视图。",
    )
    scope: Literal["common", "sku", "writable", "all"] = Field(
        default="writable",
        description="writable（默认）一次返回可写公共与 SKU 属性并标注 write_scope/variation_role；也可选 common/sku/all。",
    )
    compact: bool = Field(
        default=True,
        description="true（默认）省略长描述与 excluded_attributes，仅保留填写和字典查询所需字段；需看完整说明时设 false。",
    )
    include_excluded: bool = Field(
        default=False,
        description="是否附带被排除的托管/只读属性摘要；默认 false 保持回执紧凑。",
    )
    max_options_per_attribute: int = Field(
        default=20,
        ge=0,
        le=50,
        description="紧凑模式下每个属性最多内联返回的 options 预览条数（0 表示仅返回字段元数据，完整大字典用 category_attribute_values_query）。",
    )


class CategoryAttributesQueryBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category_id: TrimmedText
    ok: bool
    category_path: TrimmedText = ""
    attribute_count: int = Field(default=0, ge=0)
    attributes: tuple[dict[str, JsonValue], ...] = ()
    excluded_attributes: tuple[dict[str, JsonValue], ...] = ()
    error_code: TrimmedText = ""
    error: TrimmedText = ""


class CategoryAttributesQueryBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    site: TrimmedText = ""
    total: int = Field(ge=0)
    ok_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    items: list[CategoryAttributesQueryBatchItem] = Field(default_factory=list, max_length=30)


class CategoryAttributeValuesQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    attribute_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    query: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    cursor: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    limit: int = Field(default=50, ge=1, le=200)


class CategoryAttributeValuesQueryResult(BaseModel):
    """枚举候选当前页；候选全集只能分页读取。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    category_id: TrimmedText = ""
    attribute_id: TrimmedText = ""
    query: TrimmedText = ""
    cursor: TrimmedText = ""
    values: tuple[dict[str, JsonValue], ...] = ()
    next_cursor: TrimmedText = ""
    has_more: bool = False


class CategoryPrecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    product_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]

    @model_validator(mode="after")
    def require_subject(self) -> "CategoryPrecheckRequest":
        if not self.draft_id and not self.product_id:
            raise ValueError("draft_id 与 product_id 至少填写一个")
        return self


class CategoryPrecheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    site: TrimmedText = ""
    category_id: TrimmedText = ""
    category_path: TrimmedText = ""
    missing_fields: tuple[str, ...] = ()


__all__ = [
    "CategoryAttributeValuesQueryRequest",
    "CategoryAttributeValuesQueryResult",
    "CategoryAttributesQueryBatchItem",
    "CategoryAttributesQueryBatchRequest",
    "CategoryAttributesQueryBatchResult",
    "CategoryAttributesQueryRequest",
    "CategoryAttributesQueryResult",
    "CategoryPrecheckRequest",
    "CategoryPrecheckResult",
    "CategorySearchRequest",
    "CategorySearchResult",
]

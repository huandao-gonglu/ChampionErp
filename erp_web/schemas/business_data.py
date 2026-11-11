"""页面与 Agent 共用的只读业务数据契约。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class DataModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NoDataParameters(DataModel):
    pass


class DraftDataParameters(DataModel):
    draft_id: str = Field(min_length=1, max_length=160)


class PricingDataParameters(DraftDataParameters):
    sku_id: str = Field(default='', max_length=160)
    target_key: str = Field(default='', max_length=80)
    preview_id: str = Field(default='', max_length=80)


class ProductDataParameters(DataModel):
    product_id: str = Field(min_length=1, max_length=160)


class DraftIndexParameters(DataModel):
    scope: Literal['active', 'archived', 'all'] = 'active'


class DataCatalogRequest(DataModel):
    keyword: str = Field(default='', max_length=100)
    resource: str = Field(default='', max_length=100, description='指定资源时返回其参数 Schema 和默认字段。')
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class DataReadRequest(DataModel):
    resource: str = Field(min_length=1, max_length=100)
    params: dict[str, JsonValue] = Field(default_factory=dict, description='资源定位参数；按目录 parameter_hints 可直接读取，只有参数不明确时才获取完整 Schema。')
    path: str = Field(default='', max_length=300, description='留空读取默认集合，$ 为根；点路径展开字段/下标，含点号的键用 JSON Pointer，例如 /global.chat/model_id。')
    fields: list[str] = Field(default_factory=list, max_length=30, description='返回字段，支持点分隔路径；留空使用默认摘要，path 非空时读取该处完整内容。')
    filters: dict[str, JsonValue] = Field(default_factory=dict, description='对集合按字段精确匹配，支持点分隔路径。')
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=50)


class DataResourceInfo(DataModel):
    name: str
    description: str
    default_path: str
    default_fields: list[str]
    permission: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    parameter_hints: dict[str, JsonValue] = Field(default_factory=dict)


class DataCatalogResult(DataModel):
    resources: list[DataResourceInfo]
    total: int = 0
    next_offset: int | None = None


class DataReadResult(DataModel):
    resource: str
    path: str
    data: JsonValue
    total: int
    offset: int
    next_offset: int | None = None
    available_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    limited: bool = False
    notice: str = ''
    max_chars: int = 12000


__all__ = ['DataCatalogRequest', 'DataCatalogResult', 'DataReadRequest', 'DataReadResult', 'DataResourceInfo',
           'DraftDataParameters', 'DraftIndexParameters', 'NoDataParameters', 'PricingDataParameters', 'ProductDataParameters']

"""远端 Yandex 发布仓与运输渠道配置，不引用本地报单仓。"""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from erp_web.schemas.logistics import Carrier


class WarehouseInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class YandexWarehouseBinding(WarehouseInput):
    warehouse_id: int = Field(gt=0, strict=True)
    carrier: Carrier
    shipping_mode: str = Field(max_length=200)
    enabled: bool = True


class StoredRouteBinding(WarehouseInput):
    """读取已保存的运价行关联；由服务按当前运费表解析为运输模式。"""
    warehouse_id: int = Field(gt=0, strict=True)
    carrier: Carrier
    route_ids: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @model_validator(mode='after')
    def unique_routes(self):
        if any(not value for value in self.route_ids) or len(set(self.route_ids)) != len(self.route_ids):
            raise ValueError('运输渠道不能为空或重复')
        return self


class YandexWarehouseAccount(WarehouseInput):
    revision: int = Field(default=0, ge=0, strict=True)
    bindings: list[YandexWarehouseBinding | StoredRouteBinding] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_warehouses(self):
        ids = [item.warehouse_id for item in self.bindings]
        if len(set(ids)) != len(ids):
            raise ValueError('同一发布仓库只能配置一次，不能关联多个物流商')
        return self


class SaveYandexWarehouses(YandexWarehouseAccount):
    bindings: list[YandexWarehouseBinding] = Field(default_factory=list, max_length=100)
    business_id: str = Field(min_length=1, max_length=100)
    campaign_id: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def selected_mode(self):
        if any(item.enabled and not item.shipping_mode for item in self.bindings):
            raise ValueError('请为参与核价的仓库选择运输模式')
        return self


class RemoteWarehouse(WarehouseInput):
    id: int = Field(gt=0, strict=True)
    name: str


class YandexWarehouseCatalog(WarehouseInput):
    identity: str = ''
    items: list[RemoteWarehouse] = Field(default_factory=list)
    checked_at: str = ''


class YandexWarehouseConfiguration(WarehouseInput):
    accounts: dict[str, YandexWarehouseAccount] = Field(default_factory=dict)
    catalog: YandexWarehouseCatalog = Field(default_factory=YandexWarehouseCatalog)


def account_key(account):
    return f"{account['business_id']}:{account['campaign_id']}"

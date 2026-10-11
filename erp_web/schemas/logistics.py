"""收货地址及承接规则；枚举与匹配逻辑不访问网络或持久化。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from erp_web.schemas.alibaba_self_purchase import PurchaseAddress

# ERP 内部枚举，不是 Yandex API 运单字段；目录参考官方交货中心及运输方式文档。
Carrier = Literal['CEL', 'UNI', 'GBS_GUOO', 'GUOO_GTA', 'URAL', 'ATC']
CARRIERS = {'CEL': 'CEL', 'UNI': 'UNI', 'GBS_GUOO': 'GBS(GUOO)', 'GUOO_GTA': 'GUOO(GTA)', 'URAL': 'Ural', 'ATC': 'ATC'}


class LogisticsInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ParseLogisticsAddressRequest(LogisticsInput):
    text: str = Field(min_length=1, max_length=4000)


class LogisticsRule(LogisticsInput):
    platform: Literal['yandex'] = 'yandex'
    carrier: Carrier


class LogisticsAddressRequest(LogisticsInput):
    id: str = Field(default='', max_length=100)
    revision: int = Field(ge=0, strict=True)
    warehouse_id: int = Field(gt=0, strict=True)
    name: str = Field(min_length=1, max_length=100)
    address: PurchaseAddress
    enabled: bool = True
    is_default: bool = False
    remark: str = Field(default='', max_length=1000)
    rules: list[LogisticsRule] = Field(default_factory=list, max_length=30)


class LogisticsAddress(LogisticsAddressRequest):
    warehouse_name: str
    updated_at: str
    migrated: bool = False

    @model_validator(mode='before')
    @classmethod
    def read_stored_rules(cls, value):
        if isinstance(value, dict):
            return {**value, 'rules': stored_rules(value.get('rules', []))}
        return value


def stored_rules(rules):
    """读取旧持久化规则，只保留已确认的平台和物流商，移除退役的范围字段。"""
    result = {}
    for rule in rules:
        if rule.get('confirmed', True):
            value = {'platform': rule.get('platform', 'yandex'), 'carrier': rule['carrier']}
            result[(value['platform'], value['carrier'])] = value
    return list(result.values())


def matching_rule(address, order, carrier):
    return next((r for r in stored_rules(address['rules'])
                 if r['platform'] == order.platform and r['carrier'] == carrier), None)

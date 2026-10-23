"""核价、发布单元测试使用的明确仓库方案；不调用真实接口。"""
from copy import deepcopy
from erp_web.context import get_context
from erp_web.services.yandex_logistics_service import fingerprint

PLAN = {
    'business_id': '222', 'campaign_id': '111', 'placement_type': 'FBS', 'stock_update_mode': 'business',
    'route_id': 'test-cel', 'route_name': 'CEL 陆运', 'carrier': 'CEL',
    'publish_warehouse_id': 31, 'publish_warehouse_name': 'CEL 发布仓',
    'configuration_revision': 1,
}
PLAN['fingerprint'] = fingerprint(PLAN)


class LogisticsFixture:
    def __init__(self, plans=None):
        self.current = deepcopy(plans if plans is not None else [PLAN])

    def plans(self):
        return deepcopy(self.current)

    def pricing_plans(self):
        return self.plans()

    def configuration_fingerprint(self):
        return fingerprint(self.current)

    def validate(self, plan):
        if plan not in self.current:
            raise ValueError('核价物流方案已失效，请重新核价')
        return deepcopy(plan)


def install_logistics(monkeypatch, plans=None):
    service = LogisticsFixture(plans)
    monkeypatch.setattr(get_context(), '_yandex_logistics', service)
    return service

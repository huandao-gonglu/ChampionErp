"""将采购测试的上游回执夹具接到业务接口。"""
from erp_web.services.alibaba_api_client import LOGISTICS_INFO, LOGISTICS_TRACE, ORDER_DETAIL, OpenApiAlibabaProvider


class AlibabaFixtureProvider(OpenApiAlibabaProvider):
    def _send(self, api, values, *, namespace, semantics):
        if api in {ORDER_DETAIL, LOGISTICS_INFO, LOGISTICS_TRACE}:
            return self.query(api, values["orderId"])
        return self.call(api, values)

    def _request(self, api, values, *, namespace="com.alibaba.trade", semantics="read"):
        return self._send(api, values, namespace=namespace, semantics=semantics)

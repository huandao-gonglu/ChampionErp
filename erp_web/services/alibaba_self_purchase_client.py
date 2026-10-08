"""自购专用白名单；写接口不重试，不开放任意 API 或自动扣款。"""
from erp_web.services.alibaba_api_client import AlibabaApiClient, AlibabaApiError

ADDRESSES = "alibaba.trade.receiveAddress.get"
PREVIEW = "alibaba.createOrder.preview"
CREATE = "alibaba.trade.fenxiaoOrder.create"
ORDER_LIST = "alibaba.trade.getBuyerOrderList"
PAY_URL = "alibaba.trade.grouppay.url.get"


class AlibabaSelfPurchaseClient(AlibabaApiClient):
    def call(self, api: str, values: dict) -> dict:
        if api not in {ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_URL}:
            raise AlibabaApiError("不支持的自购接口")
        return self._request(api, values, namespace="com.alibaba.trade", semantics="write" if api == CREATE else "read")

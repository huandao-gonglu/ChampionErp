"""自购专用白名单；下单和免密支付均按写请求处理，不自动重试。"""
from erp_web.services.alibaba_api_client import AlibabaApiClient, AlibabaApiError

ADDRESSES = "alibaba.trade.receiveAddress.get"
PREVIEW = "alibaba.createOrder.preview"
CREATE = "alibaba.trade.fastCreateOrder"
ORDER_LIST = "alibaba.trade.getBuyerOrderList"
PAY_URL = "alibaba.trade.grouppay.url.get"
PAY_STATUS = "alibaba.trade.pay.protocolPay.isopen"
PAY = "alibaba.trade.pay.protocolPay.preparePay"


class AlibabaSelfPurchaseClient(AlibabaApiClient):
    def call(self, api: str, values: dict) -> dict:
        if api not in {ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_URL, PAY_STATUS, PAY}:
            raise AlibabaApiError("不支持的自购接口")
        return self._request(api, values, namespace="com.alibaba.trade", semantics="write" if api in {CREATE, PAY} else "read")

"""1688 买家 API；固定网关、显式读写语义，凭据不进入结果或错误。"""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace

from erp_web.schemas.alibaba_provider import BuyerOrder, Cargo, CollectedProduct, PreviewGroup, Shipment, ShipmentTrace
from erp_web.schemas.alibaba_self_purchase import AlibabaPaymentAgreement, AlibabaPurchaseAddressCandidate, AlibabaPurchaseAddressFields
from erp_web.services.alibaba_api_errors import AlibabaApiError, AlibabaApiRejected
from erp_web.services.alibaba_order_payloads import order_result, preview_results, shipment_results, trace_results
from erp_web.services.alibaba_product_payload import parse_1688_api_product
from erp_web.services.alibaba_purchase_address import saved_addresses
from erp_web.services.alibaba_purchase_payment import payment_agreement, payment_receipt
from erp_web.services.external_request_context import credential_fingerprint, request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.onebound_api_transport import OneBoundApiError, OneBoundApiRejected, request_onebound


PRODUCT_DETAIL = "product.search.queryProductDetail"
SELF_PRODUCT_DETAIL = "alibaba.product.get"
ORDER_DETAIL = "alibaba.trade.get.buyerView"
LOGISTICS_INFO = "alibaba.trade.getLogisticsInfos.buyerView"
LOGISTICS_TRACE = "alibaba.trade.getLogisticsTraceInfo.buyerView"
BUYER_IDENTITY = 'alibaba.account.basic'
ADDRESSES = "alibaba.trade.receiveAddress.get"
PREVIEW = "alibaba.createOrder.preview"
CREATE = "alibaba.trade.fastCreateOrder"
ORDER_LIST = "alibaba.trade.getBuyerOrderList"
PAY_URL = "alibaba.trade.grouppay.url.get"
PAY_STATUS = "alibaba.trade.pay.protocolPay.isopen"
PAY = "alibaba.trade.pay.protocolPay.preparePay"
_NAMESPACES = {
    ORDER_DETAIL: "com.alibaba.trade",
    LOGISTICS_INFO: "com.alibaba.logistics",
    LOGISTICS_TRACE: "com.alibaba.logistics",
}


def _known_rejection(payload, api=""):
    if not isinstance(payload, dict):
        return None
    code = payload.get("errorCode") or payload.get("error_code")
    messages = {
        "gw.APIACLDecline": f"当前应用未获 1688 接口 {api or '（未识别）'} 的权限（gw.APIACLDecline），请核对应用解决方案与接口授权",
        "500_005": "购买数量不满足该交易流程的起批限制（500_005），请调整数量后重新预览",
    }
    return messages.get(code) if isinstance(code, str) else None


def validate_order_number(value: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,30}", value):
        raise AlibabaApiError("请填写正确的 1688 采购订单号（仅数字）")
    return value


def _response_error(api, payload, credentials):
    """只从买家查询的业务错误字段提取原因，不回显请求、网关地址或附加数据。"""
    rejection = _known_rejection(payload, api)
    if api not in _NAMESPACES or not isinstance(payload, dict):
        return AlibabaApiRejected(rejection) if rejection else None
    code = payload.get("errorCode") or payload.get("error_code")
    code = str(code) if isinstance(code, (str, int)) and not isinstance(code, bool) else ""
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}", code) or any(s and s in code for s in credentials):
        code = ""
    message = payload.get("errorMessage") or payload.get("error_message")
    message = message.strip() if isinstance(message, str) else ""
    for secret in credentials:
        if secret:
            message = message.replace(secret, "***")
    # 某些网关用消息字段回显整段请求或响应，只保留其错误码。
    if re.search(r"[\r\n{}<>]|https?://|(?:token|secret|signature|session|authorization)\s*[:=]", message, re.I):
        message = ""
    if rejection:
        return AlibabaApiRejected(f"{rejection}。{message[:300]}" if message else rejection)
    reason = " ".join(part for part in (code, message[:300]) if part)
    label = {ORDER_DETAIL: "订单详情", LOGISTICS_INFO: "运单", LOGISTICS_TRACE: "物流轨迹"}[api]
    return AlibabaApiError(f"1688 {label}查询失败：{reason}") if reason else None


def _parse_response(api, payload, *, credentials=()):
    # 订单详情实测返回字符串 "true"，物流接口返回 JSON 布尔值。
    if (api == PRODUCT_DETAIL and isinstance(payload, dict) and "success" not in payload
            and isinstance(payload.get("result"), dict)):
        payload = payload["result"]
    success = payload.get("success") if isinstance(payload, dict) else None
    # 企业自用商详按官方契约直接返回 productInfo，没有 success 字段。
    if (api == SELF_PRODUCT_DETAIL and isinstance(payload, dict) and success is None
            and isinstance(payload.get("productInfo"), dict) and not payload.get("errorCode")):
        return payload
    # 官方买家订单列表实测直接返回 result 和 totalRecord，不带 success。
    if (api == ORDER_LIST and isinstance(payload, dict) and success is None
            and not payload.get('errorCode') and not payload.get('error_code') and not payload.get('error')
            and isinstance(payload.get('result'), list) and type(payload.get('totalRecord')) is int
            and payload['totalRecord'] >= len(payload['result'])
            and all(isinstance(row, dict) and isinstance(row.get('baseInfo'), dict) for row in payload['result'])):
        return payload
    if not (success is True or success == "true"):
        # 官方物流接口用 500_2 表达尚未发货；保留原始业务码，并归一为空物流列表。
        if api == LOGISTICS_INFO and isinstance(payload, dict) and payload.get("errorCode") == "500_2":
            return {**payload, "result": []}
        error = _response_error(api, payload, credentials)
        if error:
            raise error
        raise AlibabaApiError("1688 未返回成功结果，请检查请求参数、账号归属及接口权限")
    return payload


class AlibabaProvider(ABC):
    """商品、采购、支付与物流的供应商业务契约。"""

    @abstractmethod
    def get_product(self, offer_id: str, source_url: str) -> CollectedProduct: ...

    @abstractmethod
    def get_buyer_id(self) -> str: ...

    @abstractmethod
    def get_order(self, number: str) -> BuyerOrder: ...

    @abstractmethod
    def find_order_numbers(self, external_id: str) -> list[str]: ...

    @abstractmethod
    def get_shipments(self, number: str) -> list[Shipment]: ...

    @abstractmethod
    def get_logistics_traces(self, number: str) -> list[ShipmentTrace]: ...

    @abstractmethod
    def list_addresses(self) -> list[tuple[AlibabaPurchaseAddressCandidate, AlibabaPurchaseAddressFields]]: ...

    @abstractmethod
    def preview_order(self, address: AlibabaPurchaseAddressFields, cargo: list[Cargo]) -> list[PreviewGroup]: ...

    @abstractmethod
    def create_order(self, address: AlibabaPurchaseAddressFields, cargo: list[Cargo], *, flow: str,
                     channel: str, external_id: str) -> list[str]: ...

    @abstractmethod
    def get_cashier_url(self, numbers: list[str]) -> str: ...

    @abstractmethod
    def get_payment_agreement(self, channel: str) -> AlibabaPaymentAgreement: ...

    @abstractmethod
    def pay_order(self, number: str, *, channel: str, amount_fen: int, request_id: str) -> tuple[str, str]: ...


class OpenApiAlibabaProvider(AlibabaProvider):
    """使用 1688 开放平台业务协议的共用适配。"""

    product_detail_method = PRODUCT_DETAIL

    @abstractmethod
    def _send(self, api, values, *, namespace, semantics): ...

    def _request(self, api, values, *, namespace="com.alibaba.trade", semantics="read"):
        payload = self._send(api, values, namespace=namespace, semantics=semantics)
        return _parse_response(api, payload, credentials=self._credentials)

    def get_product(self, offer_id, source_url):
        product = self._product_payload(offer_id)
        return {"product": parse_1688_api_product(product, source_url, offer_id), "http_status": 200,
                "request": {"method": self.product_detail_method, "offer_id": offer_id}}

    def _product_payload(self, offer_id):
        result = self._request(PRODUCT_DETAIL, {"offerDetailParam": {"offerId": int(offer_id), "country": "en"}},
                               namespace="com.alibaba.fenxiao.crossborder").get("result")
        return _checked_product(result, offer_id, "offerId")

    def get_buyer_profile(self) -> tuple[str, str]:
        payload = self._request(BUYER_IDENTITY, {}, namespace="com.alibaba.account")
        result = payload.get("result")
        if not isinstance(result, dict):
            result = payload
        return (str(result.get("memberId") or result.get("member_id") or ""),
                str(result.get("loginId") or result.get("login_id") or "").strip())

    def get_buyer_id(self):
        identity, _ = self.get_buyer_profile()
        if not identity:
            raise AlibabaApiError("1688 未返回有效的买家身份")
        return identity

    def _read_order(self, api, number):
        return self._request(api, {"orderId": validate_order_number(number), "webSite": "1688"}, namespace=_NAMESPACES[api])

    def get_order(self, number):
        return order_result(self._read_order(ORDER_DETAIL, number), number)

    def find_order_numbers(self, external_id):
        payload = self._request(ORDER_LIST, {"outOrderId": external_id, "page": 1, "pageSize": 20,
                                             "needBuyerAddressAndPhone": False})
        data = payload.get("result")
        rows = data.get("orderList", []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        numbers = []
        for row in rows:
            base = row.get("baseInfo", {})
            if str(row.get("exAttributes", {}).get("outOrderId") or base.get("outOrderId") or "") == external_id:
                numbers.append(base.get("idOfStr") or base.get("id"))
        return [validate_order_number(numbers[0])] if len(numbers) == 1 else []

    def get_shipments(self, number):
        return shipment_results(self._read_order(LOGISTICS_INFO, number))

    def get_logistics_traces(self, number):
        return trace_results(self._read_order(LOGISTICS_TRACE, number), number)

    def list_addresses(self):
        return saved_addresses(self._request(ADDRESSES, {}))

    def preview_order(self, address, cargo):
        return preview_results(self._request(PREVIEW, _purchase_arguments(address, cargo)))

    def create_order(self, address, cargo, *, flow, channel, external_id):
        payload = self._request(CREATE, {**_purchase_arguments(address, cargo), "flow": flow,
                                "tradeType": "assureTrade", "preSelectPayChannel": channel,
                                "outOrderId": external_id}, semantics="write")
        result = payload.get("result", payload)
        if not isinstance(result, dict):
            raise AlibabaApiError("创建响应未返回有效订单号，请核验原请求")
        numbers = result.get("orderIdList")
        if numbers is None and result.get("orderId"):
            numbers = [result["orderId"]]
        if not isinstance(numbers, list) or len(numbers) != 1:
            raise AlibabaApiError("创建响应未返回唯一订单号，请核验原请求")
        return [validate_order_number(numbers[0])]

    def get_cashier_url(self, numbers):
        payload = self._request(PAY_URL, {"orderIds": [int(n) for n in numbers], "payPlatformType": "PC"})
        return _cashier_url(payload)

    def get_payment_agreement(self, channel):
        return payment_agreement(self._request(PAY_STATUS, {}), channel)

    def pay_order(self, number, *, channel, amount_fen, request_id):
        payload = self._request(PAY, {"tradeWithholdPreparePayParam": {
            "orderId": int(number), "payChannel": channel, "payAmount": amount_fen, "opRequestId": request_id,
        }}, semantics="write")
        return payment_receipt(payload, channel)


def _purchase_arguments(address, cargo):
    return {"addressParam": address, "cargoParamList": [
        {"offerId": int(item["offer_id"]), "specId": item["spec_id"], "quantity": item["quantity"]}
        for item in cargo
    ]}


def _cashier_url(payload):
    url = payload.get("payUrl") or payload.get("url") or payload.get("result")
    if isinstance(url, dict):
        url = url.get("payUrl") or url.get("url")
    parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    if (not parsed or parsed.scheme != "https" or parsed.username or parsed.password
            or not any(parsed.hostname == h or (parsed.hostname or "").endswith("." + h) for h in ("1688.com", "alipay.com"))):
        raise AlibabaApiError("1688 未返回有效的官方收银台链接，请到 1688 已买到的货品中处理")
    return url


def _checked_product(product, offer_id, *identity_fields):
    if isinstance(product, dict):
        identity = next((product[field] for field in identity_fields if product.get(field)), None)
        if str(identity) == offer_id:
            return product
    raise AlibabaApiError("1688 返回的商品详情与请求不符")


class OfficialAlibabaProvider(OpenApiAlibabaProvider):

    def __init__(self, config, timeout):
        self.timeout = timeout
        self.app_key = str(config.get("app_key") or "").strip()
        self.app_secret = str(config.get("app_secret") or "").strip()
        self.access_token = str(config.get("access_token") or "").strip()
        self._credentials = (self.app_secret, self.access_token)
        if not (self.app_key and self.app_secret and self.access_token):
            raise AlibabaApiError("请先在授权配置的「1688 授权」中连接采购账号；服务未配置时请联系管理员")
        if not re.fullmatch(r"[0-9]+", self.app_key):
            raise AlibabaApiError("1688 AppKey 格式无效")

    def _send(self, api, values, *, namespace, semantics):
        path = f"param2/1/{namespace}/{api}/{self.app_key}"
        params = {key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                  if isinstance(value, (dict, list, bool)) else str(value) for key, value in values.items()}
        params.update(access_token=self.access_token, _aop_timestamp=str(int(time.time() * 1000)))
        signing = path + "".join(key + params[key] for key in sorted(params))
        params["_aop_signature"] = hmac.new(
            self.app_secret.encode(), signing.encode(), hashlib.sha1,
        ).hexdigest().upper()
        url = "https://gw.open.1688.com/openapi/" + path
        request = urllib.request.Request(
            url, data=urllib.parse.urlencode(params).encode(), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Accept": "application/json"},
        )
        context = request_context(
            url, data=request.data, method="POST", timeout=self.timeout,
            source="1688采购" + ("查询" if semantics == "read" else "支付" if api == PAY else "下单"), account_id="app:" + self.app_key, semantics=semantics,
        )
        context = replace(context, interface=api, credential_id=credential_fingerprint(self.access_token))
        try:
            with managed_urlopen(
                request, timeout=self.timeout, request_context=context,
                follow_redirects=False,
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read())
            except (ValueError, UnicodeDecodeError):
                payload = None
            finally:
                exc.close()
            error = _response_error(api, payload, (self.access_token, self.app_secret, params["_aop_signature"]))
            if error:
                raise error from None
            if exc.code == 401:
                raise AlibabaApiError("1688 用户授权无效或已过期，请重新连接账号后重试") from None
            if exc.code == 403:
                raise AlibabaApiError("1688 拒绝访问，请检查应用的接口权限和采购账号授权") from None
            raise AlibabaApiError(f"1688 查询失败（HTTP {exc.code}），请稍后手动重试") from None
        except ExternalRequestBlocked:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise AlibabaApiError("1688 网络请求失败，请稍后手动重试") from None
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise AlibabaApiError("1688 返回了无法解析的响应") from None
        return payload


class SelfUseAlibabaProvider(OfficialAlibabaProvider):
    product_detail_method = SELF_PRODUCT_DETAIL

    def _product_payload(self, offer_id):
        payload = self._request(SELF_PRODUCT_DETAIL, {"productID": int(offer_id), "webSite": "1688"},
                                namespace="com.alibaba.product")
        return _checked_product(payload.get("productInfo"), offer_id, "productID", "productId")


class OneBoundAlibabaProvider(OpenApiAlibabaProvider):
    def __init__(self, config, timeout):
        if not (config.get('api_key') and config.get('api_secret') and config.get('access_token')):
            raise AlibabaApiError('当前方案的 1688 账号尚未连接，请联系管理员')
        self.config = dict(config)
        self._credentials = tuple(str(config[key]) for key in ("api_key", "api_secret", "access_token"))
        self.timeout = timeout
        self.expected_buyer = str(config.get('member_id') or '')
        self._buyer_checked = False

    def create_order(self, address, cargo, *, flow, channel, external_id):
        raise AlibabaApiRejected("万邦创建采购订单的接口尚未确认")

    def pay_order(self, number, *, channel, amount_fen, request_id):
        raise AlibabaApiRejected("万邦免密支付的接口尚未确认")

    def get_cashier_url(self, numbers):
        return _cashier_url(self._request("alibaba.alipay.url.get", {"orderIdList": [int(n) for n in numbers]}))

    def _send(self, api, values, *, namespace, semantics):
        if semantics == "write":
            raise AlibabaApiRejected("万邦写入接口尚未确认")
        try:
            if api != BUYER_IDENTITY and self.expected_buyer and not self._buyer_checked:
                if self.get_buyer_id() != self.expected_buyer:
                    raise AlibabaApiError('当前接入的 1688 买家已变化，请重新核验')
                self._buyer_checked = True
            payload = request_onebound(self.config, api, values, namespace=namespace, semantics=semantics, timeout=self.timeout)
        except OneBoundApiRejected as exc:
            raise AlibabaApiRejected(str(exc)) from None
        except OneBoundApiError as exc:
            raise AlibabaApiError(str(exc)) from None
        return payload


_PROVIDERS = {
    'alibaba': SelfUseAlibabaProvider,
    'alibaba_crossborder': OfficialAlibabaProvider,
    'onebound': OneBoundAlibabaProvider,
}


def alibaba_provider(config: dict) -> AlibabaProvider:
    # 旧调用方未传应用类型时使用跨境商详契约。
    name = config.get("application_type", "alibaba_crossborder")
    if name not in _PROVIDERS:
        raise AlibabaApiError("不支持的 1688 接入方案")
    try:
        timeout = max(3, min(30, int(config.get("timeout_seconds") or 20)))
    except (ValueError, TypeError):
        timeout = 20
    return _PROVIDERS[name](config, timeout)

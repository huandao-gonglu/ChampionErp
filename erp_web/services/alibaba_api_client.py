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
_NAMESPACES = {
    ORDER_DETAIL: "com.alibaba.trade",
    LOGISTICS_INFO: "com.alibaba.logistics",
    LOGISTICS_TRACE: "com.alibaba.logistics",
}


class AlibabaApiError(ValueError):
    """可向用户展示的固定错误，不包含上游原文或请求凭据。"""


class AlibabaApiRejected(AlibabaApiError):
    """平台明确拒绝执行请求，不能与网络结果未知混为一谈。"""


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


def _parse_response(api, payload):
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
    if (api == 'alibaba.trade.getBuyerOrderList' and isinstance(payload, dict) and success is None
            and not payload.get('errorCode') and not payload.get('error_code') and not payload.get('error')
            and isinstance(payload.get('result'), list) and type(payload.get('totalRecord')) is int
            and payload['totalRecord'] >= len(payload['result'])
            and all(isinstance(row, dict) and isinstance(row.get('baseInfo'), dict) for row in payload['result'])):
        return payload
    if not (success is True or success == "true"):
        # 官方物流接口用 500_2 表达尚未发货；保留原始业务码，并归一为空物流列表。
        if api == LOGISTICS_INFO and isinstance(payload, dict) and payload.get("errorCode") == "500_2":
            return {**payload, "result": []}
        rejected = _known_rejection(payload, api)
        if rejected:
            raise AlibabaApiRejected(rejected)
        raise AlibabaApiError("1688 未返回成功结果，请检查请求参数、账号归属及接口权限")
    return payload


class AlibabaProvider(ABC):
    """供应商统一返回 ERP 使用的 1688 回执；商品详情按各自的接口协议提取。"""

    product_detail_method = PRODUCT_DETAIL

    @abstractmethod
    def request(self, api, values, *, namespace, semantics):
        """向所选网关发送一次请求。"""

    def product_detail(self, offer_id):
        raw = self.request(self.product_detail_method, {"offerDetailParam": {"offerId": int(offer_id), "country": "en"}},
                           namespace="com.alibaba.fenxiao.crossborder", semantics="read")
        product = _parse_response(self.product_detail_method, raw).get("result")
        return _checked_product(product, offer_id, "offerId")


def _checked_product(product, offer_id, *identity_fields):
    if isinstance(product, dict):
        identity = next((product[field] for field in identity_fields if product.get(field)), None)
        if str(identity) == offer_id:
            return product
    raise AlibabaApiError("1688 返回的商品详情与请求不符")


class OfficialAlibabaProvider(AlibabaProvider):

    def __init__(self, config, timeout):
        self.timeout = timeout
        self.app_key = str(config.get("app_key") or "").strip()
        self.app_secret = str(config.get("app_secret") or "").strip()
        self.access_token = str(config.get("access_token") or "").strip()
        if not (self.app_key and self.app_secret and self.access_token):
            raise AlibabaApiError("请先在授权配置的「1688 授权」中连接采购账号；服务未配置时请联系管理员")
        if not re.fullmatch(r"[0-9]+", self.app_key):
            raise AlibabaApiError("1688 AppKey 格式无效")

    def request(self, api, values, *, namespace, semantics):
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
            source="1688采购" + ("查询" if semantics == "read" else "支付" if api == "alibaba.trade.pay.protocolPay.preparePay" else "下单"), account_id="app:" + self.app_key, semantics=semantics,
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
                rejected = _known_rejection(json.loads(exc.read()), api)
            except (ValueError, UnicodeDecodeError):
                rejected = None
            finally:
                exc.close()
            if rejected:
                raise AlibabaApiRejected(rejected) from None
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

    def product_detail(self, offer_id):
        raw = self.request(self.product_detail_method, {"productID": int(offer_id), "webSite": "1688"},
                           namespace="com.alibaba.product", semantics="read")
        product = _parse_response(self.product_detail_method, raw).get("productInfo")
        return _checked_product(product, offer_id, "productID", "productId")


class OneBoundAlibabaProvider(AlibabaProvider):
    def __init__(self, config, timeout):
        if not (config.get('api_key') and config.get('api_secret') and config.get('access_token')):
            raise AlibabaApiError('当前方案的 1688 账号尚未连接，请联系管理员')
        self.config = dict(config)
        self.timeout = timeout
        self.expected_buyer = str(config.get('member_id') or '')
        self._buyer_checked = False

    def request(self, api, values, *, namespace, semantics):
        try:
            if api != BUYER_IDENTITY and self.expected_buyer and (not self._buyer_checked or semantics == 'write'):
                try:
                    identity = request_onebound(self.config, BUYER_IDENTITY, {}, namespace='com.alibaba.account', semantics='read', timeout=self.timeout)
                    returned = _parse_response(BUYER_IDENTITY, identity).get('result', {}).get('memberId')
                    if returned != self.expected_buyer:
                        raise AlibabaApiError('当前接入的 1688 买家已变化，请重新核验')
                    self._buyer_checked = True
                except Exception:
                    if semantics == 'write':
                        raise AlibabaApiRejected('1688 买家身份核验未通过，尚未发起下单或支付，请重新核验') from None
                    raise
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


def _provider_class(config):
    # 旧调用方未传应用类型时使用跨境商详契约。
    name = config.get('application_type', 'alibaba_crossborder')
    if name not in _PROVIDERS:
        raise AlibabaApiError("不支持的 1688 接入方案")
    return _PROVIDERS[name]


def product_detail_method(config):
    return _provider_class(config).product_detail_method


class AlibabaApiClient:
    def __init__(self, config: dict):
        try:
            timeout = max(3, min(30, int(config.get("timeout_seconds") or 20)))
        except (ValueError, TypeError):
            timeout = 20
        self.provider = _provider_class(config)(config, timeout)

    def query(self, api: str, order_number: str, *, logistics_id: str = "") -> dict:
        if api not in _NAMESPACES:
            raise AlibabaApiError("不支持的 1688 查询接口")
        order_number = validate_order_number(order_number)
        params = {"orderId": order_number, "webSite": "1688"}
        if logistics_id:
            params["logisticsId"] = logistics_id
        return self._request(api, params, namespace=_NAMESPACES[api], semantics="read")

    def buyer_identity(self):
        return self._request(BUYER_IDENTITY, {}, namespace='com.alibaba.account', semantics='read')

    def product_detail(self, offer_id: str) -> dict:
        return self.provider.product_detail(offer_id)

    def _request(self, api, values, *, namespace, semantics):
        payload = self.provider.request(api, values, namespace=namespace, semantics=semantics)
        return _parse_response(api, payload)

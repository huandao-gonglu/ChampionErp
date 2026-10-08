"""1688 买家 API；固定网关、显式读写语义，凭据不进入结果或错误。"""

from __future__ import annotations

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


ORDER_DETAIL = "alibaba.trade.get.buyerView"
LOGISTICS_INFO = "alibaba.trade.getLogisticsInfos.buyerView"
LOGISTICS_TRACE = "alibaba.trade.getLogisticsTraceInfo.buyerView"
_NAMESPACES = {
    ORDER_DETAIL: "com.alibaba.trade",
    LOGISTICS_INFO: "com.alibaba.logistics",
    LOGISTICS_TRACE: "com.alibaba.logistics",
}


class AlibabaApiError(ValueError):
    """可向用户展示的固定错误，不包含上游原文或请求凭据。"""


class AlibabaApiRejected(AlibabaApiError):
    """平台明确拒绝执行请求，不能与网络结果未知混为一谈。"""


def _known_rejection(payload):
    if not isinstance(payload, dict):
        return None
    code = payload.get("errorCode") or payload.get("error_code")
    messages = {
        "gw.APIACLDecline": "当前应用未获该 1688 接口权限（gw.APIACLDecline），请在开发者中心开通对应接口",
        "500_005": "购买数量不满足该交易流程的起批限制（500_005），请调整数量后重新预览",
    }
    return messages.get(code) if isinstance(code, str) else None


def validate_order_number(value: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,30}", value):
        raise AlibabaApiError("请填写正确的 1688 采购订单号（仅数字）")
    return value


class AlibabaApiClient:
    def __init__(self, config: dict):
        self.app_key = str(config.get("app_key") or "").strip()
        self.app_secret = str(config.get("app_secret") or "").strip()
        self.access_token = str(config.get("access_token") or "").strip()
        if not (self.app_key and self.app_secret and self.access_token):
            raise AlibabaApiError("请先在授权配置的「1688 授权」中保存 AppKey、AppSecret 和 Access Token")
        if not re.fullmatch(r"[0-9]+", self.app_key):
            raise AlibabaApiError("1688 AppKey 格式无效")
        try:
            self.timeout = max(3, min(30, int(config.get("timeout_seconds") or 20)))
        except (ValueError, TypeError):
            self.timeout = 20

    def query(self, api: str, order_number: str, *, logistics_id: str = "") -> dict:
        if api not in _NAMESPACES:
            raise AlibabaApiError("不支持的 1688 查询接口")
        order_number = validate_order_number(order_number)
        params = {"orderId": order_number, "webSite": "1688"}
        if logistics_id:
            params["logisticsId"] = logistics_id
        return self._request(api, params, namespace=_NAMESPACES[api], semantics="read")

    def _request(self, api, values, *, namespace, semantics):
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
            source="1688采购" + ("查询" if semantics == "read" else "下单"), account_id="app:" + self.app_key, semantics=semantics,
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
                rejected = _known_rejection(json.loads(exc.read()))
            except (ValueError, UnicodeDecodeError):
                rejected = None
            finally:
                exc.close()
            if rejected:
                raise AlibabaApiRejected(rejected) from None
            if exc.code == 401:
                raise AlibabaApiError("1688 用户授权无效或已过期，请更新 Access Token 后重试") from None
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
        # 订单详情实测返回字符串 "true"，物流接口返回 JSON 布尔值。
        success = payload.get("success") if isinstance(payload, dict) else None
        if not (success is True or success == "true"):
            rejected = _known_rejection(payload)
            if rejected:
                raise AlibabaApiRejected(rejected)
            raise AlibabaApiError("1688 未返回成功结果，请检查请求参数、账号归属及接口权限")
        return payload

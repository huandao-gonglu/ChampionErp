"""跨境巴士协议适配；企业 Token 自动刷新，写请求不隐式重放。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request

from erp_web.schemas.external_requests import ExternalRequestBlocked, ExternalRequestOutcomeUnknown
from erp_web.schemas.fulfillment import FulfillmentError, normalize_bus_catalog, normalize_bus_services
from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_manager import managed_urlopen

BASE_URL = "https://api.service.kuajing84.com"
READ_PATHS = frozenset({"/erpapi/storehouse/cooperationStorehouse", "/erpapi/storehouse/searchStorehouseSection", "/erpapi/orderlist/status", "/erpapi/orderlist/search", "/erpapi/user/queryauthorization"})


class CrossborderBusClient:
    def __init__(self, store):
        self.store = store
        self.token_lock = threading.RLock()
        self.cancel = None
        self.authorizing = threading.local()

    def credentials(self):
        if hasattr(self.authorizing, "credentials"):
            return self.authorizing.credentials
        return self.store.setting("credentials", {})

    def _save_credentials(self, credentials):
        if hasattr(self.authorizing, "credentials"):
            self.authorizing.credentials = credentials
        else:
            self.store.set_setting("credentials", credentials)

    def identity(self):
        value = self.credentials()
        if not value.get("app_uid") or not value.get("client_secret"):
            return ""
        return hashlib.sha256(f'{value["client_secret"]}:{value["app_uid"]}'.encode()).hexdigest()

    def _request(self, path, body, headers=None):
        if self.cancel is not None and self.cancel.is_set():
            raise FulfillmentError("履约后台已停止，本次请求尚未发送。")
        raw = json.dumps(body, ensure_ascii=False).encode()
        req = Request(BASE_URL + path, method="POST", data=raw, headers={"Content-Type": "application/json", **(headers or {})})
        ctx = request_context(req.full_url, req.headers, data=raw, method="POST", platform="crossborderbus", account_id=str(self.credentials().get("app_uid") or "enterprise"), source=__name__, semantics="read" if path in READ_PATHS else "write", timeout=25)
        ctx = replace(ctx, cancel=self.cancel, credential_id=hashlib.sha256(str(self.credentials().get("client_secret", "")).encode()).hexdigest()[:24] + ctx.credential_id)
        try:
            with managed_urlopen(req, timeout=25, request_context=ctx, source=__name__) as response:
                result = json.loads(response.read().decode())
        except ExternalRequestOutcomeUnknown:
            raise FulfillmentError("请求结果尚未确认，请先核实远端结果。", unknown=True, definitive=False) from None
        except ExternalRequestBlocked as exc:
            raise FulfillmentError("跨境巴士请求被阻止，请检查授权或请求管理中的阻断原因。", unknown=bool(exc.details.get("outcome_unknown")), definitive=bool(exc.details.get("definitively_rejected"))) from None
        except HTTPError as exc:
            raise FulfillmentError(f"跨境巴士接口返回 HTTP {exc.code}，请检查授权和接口权限。", unknown=exc.code >= 500, definitive=exc.code < 500) from None
        except Exception:
            raise FulfillmentError("跨境巴士请求失败，请检查网络后核实结果。", unknown=True, definitive=False) from None
        if not isinstance(result, dict) or result.get("code") != 1:
            message = str(result.get("message") or "") if isinstance(result, dict) else ""
            # 远端正文可能包含凭据，只将明确的业务原因映射为固定文案。
            error = next((text for word, text in (("余额", "余额不足，请在跨境巴士补足余额后重试。"), ("仓库", "合作仓库不可用，请重新核对履约方案。"), ("重复", "订单号已存在，请核实创建结果。"), ("授权", "跨境巴士授权失效，请重新授权。"), ("打包", "仓库已打包，当前操作不可执行。"), ("发货", "仓库已发货，当前操作不可执行。")) if word in message), "跨境巴士拒绝当前请求，请核对资料及合作配置。")
            raise FulfillmentError(error, unknown="重复" in message, definitive=isinstance(result, dict) and result.get("code") in (0, 2))
        return result

    def token(self):
        with self.token_lock:
            credentials = self.credentials()
            if not credentials.get("client_secret"):
                raise FulfillmentError("请在授权配置中填写跨境巴士企业密钥。")
            if credentials.get("access_token") and credentials.get("expires_at", 0) > time.time() + 3600:
                return credentials["access_token"]
            refresh = credentials.get("expires_token")
            result = self._request("/erpapi/token/refresh" if refresh else "/erpapi/token/index", {"client_secret": credentials["client_secret"], **({"expires_token": refresh} if refresh else {})})
            if not result.get("access_token") or not result.get("expires_token") or not isinstance(result.get("expires_in"), int):
                raise FulfillmentError("跨境巴士 Token 回执不完整，请重新验证授权。")
            credentials.update(access_token=result["access_token"], expires_token=result["expires_token"], expires_at=time.time() + result["expires_in"])
            self._save_credentials(credentials)
            return result["access_token"]

    def request(self, path, body, *, expected_identity=None):
        with self.token_lock:
            if expected_identity is not None and expected_identity != self.identity():
                raise FulfillmentError("跨境巴士授权账号已变化，本次操作停止。")
            credentials = self.credentials()
            if not credentials.get("app_uid"):
                raise FulfillmentError("跨境巴士账号尚未授权。")
            return self._request(path, body, {"authorization": self.token(), "k-client-secret": credentials["client_secret"], "k-app-uid": str(credentials["app_uid"])})

    def authorize(self, client_secret, user_name, password):
        with self.token_lock:
            old = self.credentials()
            secret = client_secret or old.get("client_secret", "")
            if not secret:
                raise FulfillmentError("请填写企业密钥。")
            if bool(user_name) != bool(password):
                raise FulfillmentError("请同时填写跨境巴士账号和密码。")
            if secret != old.get("client_secret") and not password:
                raise FulfillmentError("更换企业密钥时必须重新填写账号和密码完成授权。")
            # 候选凭据只在授权线程内可见，验证失败保留原授权。
            self.authorizing.credentials = ({**old} if secret == old.get("client_secret") else {"client_secret": secret})
            try:
                token = self.token()
                if secret == old.get("client_secret"):
                    self.store.set_setting("credentials", self.credentials())
                if password:
                    result = self._request("/erpapi/user/authorization", {"user_name": user_name, "password": password}, {"authorization": token, "k-client-secret": secret})
                    if type(result.get("app_uid")) is not int or result["app_uid"] <= 0:
                        raise FulfillmentError("用户授权回执缺少授权记录。")
                    self._save_credentials({**self.credentials(), "app_uid": result["app_uid"], "user_name": user_name})
                elif self.credentials().get("app_uid"):
                    self.request("/erpapi/user/queryauthorization", {"app_user_id": self.credentials()["app_uid"]})
                else:
                    raise FulfillmentError("请填写跨境巴士账号和密码完成授权。")
                catalog = self.catalog()
                self.store.set_setting("credentials", self.credentials())
                return catalog
            finally:
                del self.authorizing.credentials

    def catalog(self):
        with self.token_lock:
            result = self.request("/erpapi/storehouse/cooperationStorehouse", {})
            if not isinstance(result.get("data"), list):
                raise FulfillmentError("合作仓库回执不完整。")
            value = normalize_bus_catalog({"identity": self.identity(), "sections": result["data"], "checked_at": time.time()})
            self.store.set_setting("catalog", value)
            return value

    def services(self, section_id, warehouse_id):
        result = self.request("/erpapi/storehouse/searchStorehouseSection", {"section_id": section_id, "sid": warehouse_id}).get("data")
        if not isinstance(result, dict) or not isinstance(result.get("core_data"), list) or not isinstance(result.get("optional_data"), list):
            raise FulfillmentError("合作增值服务回执不完整。")
        return normalize_bus_services(result)

    def search(self, order_number, *, expected_identity=None):
        page, matches = 1, []
        while True:
            data = self.request("/erpapi/orderlist/search", {"order_number": order_number, "order_type": "all", "limit": 100, "page": page}, expected_identity=expected_identity).get("data")
            if not isinstance(data, dict) or not isinstance(data.get("list"), list) or not isinstance(data.get("count"), int):
                raise FulfillmentError("远端订单查询回执不完整，不能判断创建结果。", unknown=True)
            rows = data["list"]
            matches.extend(row for row in rows if (row.get("sheet_info") or {}).get("section_order") == order_number)
            if page * 100 >= data["count"]:
                break
            if not rows or page >= 100:
                raise FulfillmentError("远端订单分页未完成，不能判断创建结果。", unknown=True)
            page += 1
        if len(matches) > 1:
            raise FulfillmentError("发现重复的远端预报单，请人工核对。", unknown=True)
        return matches[0] if matches else None

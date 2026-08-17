# -*- coding: utf-8 -*-
"""Yandex Market Seller API 的 HTTP 边界。

职责固定：官方主机与 ``Api-Key`` 请求头、JSON 编解码、超时与脱敏错误、
同时处理 HTTP 错误与 HTTP 200 中的 ``status: ERROR``、解析 ``errors[]``
与 ``warnings[]``。业务字段解释留给上层（授权校验器、类目适配器和发布
状态机），本模块不写入任何配置或任务状态。

Yandex 当前只推荐 API-Key 授权；不实现 OAuth / Bearer 兼容分支。
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .publisher import PublishAdapterError

YANDEX_MARKET_API_HOST = "https://api.partner.market.yandex.ru"

# 商品编辑发布闭环所需的最小权限集合（ALL_METHODS 覆盖全部）。
# 仓库探测与库存写入属于库存/订单处理域，需要
# INVENTORY_AND_ORDER_PROCESSING（官方 ApiKeyScopeType 枚举）；
# 缺少该 scope 的 token 能通过商品/价格检查，但会在仓库接口收到 403。
YANDEX_PUBLISH_SCOPES = (
    "OFFERS_AND_CARDS_MANAGEMENT",
    "PRICING",
    "INVENTORY_AND_ORDER_PROCESSING",
)
YANDEX_ALL_METHODS_SCOPE = "ALL_METHODS"

_RETRYABLE_HTTP_STATUSES = frozenset({420, 423, 429, 500, 502, 503, 504})
_DEFAULT_TIMEOUT_SECONDS = 30.0


class YandexApiError(PublishAdapterError):
    """Yandex 远端失败的类型化错误；message 已脱敏，不含 API-Key。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        http_status: int | None = None,
        errors: list[dict[str, Any]] | None = None,
        warnings: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        if http_status is not None:
            merged.setdefault("http_status", http_status)
        if errors:
            merged.setdefault("errors", errors)
        if warnings:
            merged.setdefault("warnings", warnings)
        super().__init__(code, message, retryable=retryable, details=merged)
        self.http_status = http_status
        self.errors = list(errors or [])
        self.warnings = list(warnings or [])


def _mask_for_error(text: str, api_token: str) -> str:
    masked = str(text or "")
    token = str(api_token or "").strip()
    if token:
        masked = masked.replace(token, "***")
    return masked


def _masked_error_rows(
    rows: list[dict[str, Any]],
    api_token: str,
) -> list[dict[str, Any]]:
    """逐字段脱敏平台 errors[]/warnings[]，绝不携带 API-Key。"""

    masked: list[dict[str, Any]] = []
    for row in rows:
        masked.append(
            {
                key: (
                    _mask_for_error(str(value), api_token)
                    if isinstance(value, str)
                    else value
                )
                for key, value in row.items()
            }
        )
    return masked


def _platform_error_summary(
    errors: list[dict[str, Any]],
    masked_detail: str = "",
) -> str:
    """平台错误码/消息摘要（已脱敏），用于错误消息后缀。"""

    if errors:
        first = errors[0]
        code = str(first.get("code") or "").strip()
        message = str(first.get("message") or "").strip()
        summary = "、".join(part for part in (code, message) if part)
        if summary:
            return f"（平台错误：{summary[:300]}）"
    detail = str(masked_detail or "").strip()
    if detail:
        return f"（{detail[:200]}）"
    return ""


_CAMPAIGN_DETAIL_PATH = re.compile(r"^/v2/campaigns/[^/]+$")


def _http_error_classification(
    status_code: int,
    method: str,
    path: str,
    errors: list[dict[str, Any]],
    masked_detail: str = "",
) -> tuple[str, str, bool, str]:
    """返回 ``(code, message, retryable, next_action)``。

    403/404 不能一律判定为“API-Key 权限不足”：Campaign 端点的 403/404
    通常是 Campaign ID 填写错误（把 Business ID 当成 Campaign ID，或
    Campaign 不属于该 API-Key 所在柜台）；token/仓库/价格端点的 403
    才更可能是缺少方法权限。按请求上下文分别提示。
    """

    summary = _platform_error_summary(errors, masked_detail)
    if status_code == 401:
        return (
            "YANDEX_AUTH_INVALID",
            "Yandex API-Key 无效或已被撤销",
            False,
            "检查 API-Key 是否完整或已撤销",
        )
    if status_code == 403:
        if _CAMPAIGN_DETAIL_PATH.match(path):
            return (
                "YANDEX_CAMPAIGN_ACCESS_DENIED",
                f"Yandex Campaign ID 不属于当前 API-Key 所在柜台{summary}。"
                "请确认填写的是 Campaign ID（店铺 ID），而不是 Business ID（柜台 ID）",
                False,
                "在 Yandex 卖家后台 → 设置 → API 和模块中核对 Campaign ID 与 API-Key 的归属柜台",
            )
        if path.startswith("/v2/auth/"):
            return (
                "YANDEX_PERMISSION_DENIED",
                f"该 Yandex API-Key 缺少所请求方法的权限{summary}",
                False,
                "到卖家后台为 token 增加对应方法权限",
            )
        if "/warehouses" in path or "/offers/stocks" in path:
            return (
                "YANDEX_PERMISSION_DENIED",
                f"当前 API-Key 缺少仓库/库存相关权限{summary}，"
                "请为 token 增加 INVENTORY_AND_ORDER_PROCESSING 权限",
                False,
                "在卖家后台为 token 增加 INVENTORY_AND_ORDER_PROCESSING 权限后重试",
            )
        if "/offer-prices" in path or "/price-quarantine" in path:
            return (
                "YANDEX_PERMISSION_DENIED",
                f"当前 API-Key 缺少价格相关权限{summary}，"
                "请为 token 增加 PRICING 权限",
                False,
                "在卖家后台为 token 增加 PRICING 权限后重试",
            )
        return (
            "YANDEX_ACCESS_DENIED",
            f"Yandex 拒绝访问该资源{summary}。资源可能不属于该 API-Key 所在柜台，"
            "或 token 缺少方法权限",
            False,
            "核对资源是否属于 API-Key 所在柜台，并检查 token 权限是否齐全",
        )
    if status_code == 404:
        if _CAMPAIGN_DETAIL_PATH.match(path):
            return (
                "YANDEX_CAMPAIGN_NOT_FOUND",
                "Yandex Campaign 不存在。请确认填写的是 Campaign ID（店铺 ID），"
                "而不是 Business ID（柜台 ID）",
                False,
                "在 Yandex 卖家后台 → 设置 → API 和模块中核对 Campaign ID",
            )
        return (
            "YANDEX_NOT_FOUND",
            f"Yandex 请求的资源不存在（HTTP 404）{summary}",
            False,
            "检查请求中的资源标识",
        )
    if status_code == 420:
        return (
            "YANDEX_RATE_LIMITED",
            "Yandex 接口被限流",
            True,
            "等待后重试，并降低类目/状态轮询频率",
        )
    if status_code == 423:
        return (
            "YANDEX_RESOURCE_LOCKED",
            "Yandex 资源锁定，正在处理上一次请求",
            True,
            "等待 Yandex 完成当前价格或商品处理",
        )
    if status_code == 429:
        return (
            "YANDEX_RATE_LIMITED",
            "Yandex 接口被限流",
            True,
            "等待后重试，并降低类目/状态轮询频率",
        )
    if 500 <= status_code < 600:
        return (
            "YANDEX_SERVER_ERROR",
            f"Yandex 服务端错误（HTTP {status_code}）",
            True,
            "使用退避重试，不改变 offerId",
        )
    return (
        "YANDEX_HTTP_FAILED",
        f"Yandex 请求失败（HTTP {status_code}）{summary}",
        False,
        "按错误信息检查请求内容",
    )


def _network_error_classification(exc: Exception) -> tuple[str, str, bool, str]:
    if isinstance(exc, (TimeoutError, ssl.SSLError)):
        return (
            "YANDEX_NETWORK_TIMEOUT",
            "Yandex 接口请求超时",
            True,
            "检查网络后使用退避重试",
        )
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, ssl.SSLError)):
            return (
                "YANDEX_NETWORK_TIMEOUT",
                "Yandex 接口请求超时",
                True,
                "检查网络后使用退避重试",
            )
        return (
            "YANDEX_NETWORK_FAILED",
            "Yandex 接口网络连接失败",
            True,
            "检查本机网络、代理或防火墙后重试",
        )
    return (
        "YANDEX_REQUEST_FAILED",
        f"Yandex 请求失败：{exc}",
        False,
        "检查请求参数后重试",
    )


def _business_error_from_body(
    method: str,
    path: str,
    body: dict[str, Any],
) -> YandexApiError:
    """HTTP 200 但 ``status: ERROR`` 的平台业务失败（确定性，不重试）。"""

    errors = [
        item if isinstance(item, dict) else {"message": str(item)}
        for item in (body.get("errors") if isinstance(body.get("errors"), list) else [])
    ]
    warnings = [
        item if isinstance(item, dict) else {"message": str(item)}
        for item in (body.get("warnings") if isinstance(body.get("warnings"), list) else [])
    ]
    first = errors[0] if errors else {}
    code = str(first.get("code") or "").strip() or "YANDEX_BUSINESS_ERROR"
    message = str(first.get("message") or "").strip() or "Yandex 返回业务错误"
    return YandexApiError(
        code,
        f"Yandex {method} {path} 业务失败：{message}",
        retryable=False,
        http_status=200,
        errors=errors,
        warnings=warnings,
    )


def request_yandex_json(
    method: str,
    path: str,
    api_token: str,
    payload: dict[str, Any] | list[Any] | None = None,
    *,
    query: dict[str, Any] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    check_business_status: bool = True,
) -> dict[str, Any]:
    """向 Yandex Market Seller API 发起一次 JSON 请求。

    低层请求函数：只负责传输与错误分类，不写业务状态。返回解析后的
    JSON 对象；``status: ERROR`` 的业务失败转换为 :class:`YandexApiError`。
    """

    token = str(api_token or "").strip()
    if not token:
        raise YandexApiError(
            "YANDEX_CREDENTIALS_MISSING",
            "请先填写 Yandex API-Key Token。",
            retryable=False,
        )
    normalized_path = str(path or "") if str(path or "").startswith("/") else f"/{path or ''}"
    url = YANDEX_MARKET_API_HOST + normalized_path
    if query:
        encoded = urllib.parse.urlencode(
            {key: value for key, value in query.items() if value not in (None, "")}
        )
        if encoded:
            url = f"{url}?{encoded}"
    headers = {
        "Content-Type": "application/json",
        # Yandex 当前推荐方案：Api-Key 请求头；不使用 OAuth/Bearer。
        "Api-Key": token,
    }
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else (b"{}" if method.upper() in {"POST", "PUT"} else None)
    )
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    timeout = max(0.1, float(timeout_seconds))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            detail = ""
        masked_detail = _mask_for_error(detail, token)
        # HTTPError 响应体同样携带平台 errors[]/warnings[] 与错误码
        # （例如 403 FORBIDDEN / Access denied），必须解析并脱敏后保留，
        # 不能只按 HTTP 状态码粗分类后丢弃。
        error_body: dict[str, Any] = {}
        stripped = detail.strip()
        if stripped:
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    error_body = parsed
            except json.JSONDecodeError:
                error_body = {}
        errors = _masked_error_rows(_body_errors(error_body), token)
        warnings = _masked_error_rows(_body_warnings(error_body), token)
        code, message, retryable, next_action = _http_error_classification(
            int(exc.code),
            method.upper(),
            normalized_path,
            errors,
            masked_detail,
        )
        raise YandexApiError(
            code,
            _mask_for_error(message, token),
            retryable=retryable,
            http_status=int(exc.code),
            errors=errors,
            warnings=warnings,
            details={"next_action": next_action},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 网络层错误统一分类
        code, message, retryable, next_action = _network_error_classification(exc)
        raise YandexApiError(
            code,
            _mask_for_error(message, token),
            retryable=retryable,
            details={"next_action": next_action},
        ) from exc
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise YandexApiError(
            "YANDEX_INVALID_RESPONSE",
            "Yandex 接口返回了无效的 JSON 响应",
            retryable=True,
        ) from exc
    if not isinstance(body, dict):
        raise YandexApiError(
            "YANDEX_INVALID_RESPONSE",
            "Yandex 接口响应不是 JSON 对象",
            retryable=False,
        )
    status = str(body.get("status") or "").strip().upper()
    if check_business_status and status == "ERROR":
        raise _business_error_from_body(method.upper(), normalized_path, body)
    return body


def _body_errors(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item if isinstance(item, dict) else {"message": str(item)}
        for item in (body.get("errors") if isinstance(body.get("errors"), list) else [])
    ]


def _body_warnings(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item if isinstance(item, dict) else {"message": str(item)}
        for item in (body.get("warnings") if isinstance(body.get("warnings"), list) else [])
    ]


def fetch_yandex_token_info(
    api_token: str,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """读取 API-Key 信息与权限范围（``POST /v2/auth/token``）。

    官方响应：``result.apiKey.name`` 与 ``result.apiKey.authScopes``。
    返回值与 :class:`YandexTokenInfo` 字段一一对应，不附带原始响应。
    """

    body = request_yandex_json(
        "POST",
        "/v2/auth/token",
        api_token,
        {},
        timeout_seconds=timeout_seconds,
    )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    api_key = result.get("apiKey") if isinstance(result.get("apiKey"), dict) else {}
    scopes = api_key.get("authScopes") if isinstance(api_key.get("authScopes"), list) else []
    return {
        "name": str(api_key.get("name") or "").strip(),
        "auth_scopes": [str(scope or "").strip() for scope in scopes if str(scope or "").strip()],
    }


def yandex_scope_allows(auth_scopes: list[str], required: str) -> bool:
    scopes = {str(item or "").strip().upper() for item in auth_scopes}
    return YANDEX_ALL_METHODS_SCOPE in scopes or required.upper() in scopes


def yandex_missing_publish_scopes(auth_scopes: list[str]) -> list[str]:
    return [
        scope
        for scope in YANDEX_PUBLISH_SCOPES
        if not yandex_scope_allows(auth_scopes, scope)
    ]


def fetch_yandex_campaign(
    api_token: str,
    campaign_id: str,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """读取店铺信息（``GET /v2/campaigns/{campaignId}``）。

    官方响应中 ``campaign`` 是顶层字段（没有 ``result`` 包装）：
    ``{"campaign": {"id", "business": {"id", "name"}, "placementType",
    "apiAvailability", "domain"}}``。
    """

    body = request_yandex_json(
        "GET",
        f"/v2/campaigns/{urllib.parse.quote(str(campaign_id or '').strip())}",
        api_token,
        timeout_seconds=timeout_seconds,
    )
    campaign = body.get("campaign") if isinstance(body.get("campaign"), dict) else {}
    return campaign


def fetch_yandex_business_settings(
    api_token: str,
    business_id: str,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """读取 Business 设置（``POST /v2/businesses/{businessId}/settings``）。"""

    body = request_yandex_json(
        "POST",
        f"/v2/businesses/{urllib.parse.quote(str(business_id or '').strip())}/settings",
        api_token,
        {},
        timeout_seconds=timeout_seconds,
    )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    settings = result.get("settings") if isinstance(result.get("settings"), dict) else {}
    return settings


_WAREHOUSE_PAGE_LIMIT = 30
_WAREHOUSE_MAX_PAGES = 20


def fetch_yandex_warehouses(
    api_token: str,
    business_id: str,
    *,
    campaign_ids: list[str | int] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """探测 Business 仓库组（``POST /v2/businesses/{businessId}/warehouses``）。

    官方请求体可携带 ``campaignIds``；响应仓库项的 ``groupInfo`` 仅在仓库
    属于仓库组时返回，是仓库组判定的唯一依据。分页参数为 query
    （``limit`` 上限 30 / ``pageToken``），本函数按页读取全部仓库。
    """

    identifiers = sorted(
        {
            int(item)
            for item in (campaign_ids or [])
            if str(item or "").strip().isdigit() and int(str(item).strip()) > 0
        }
    )
    payload: dict[str, Any] = {}
    if identifiers:
        payload["campaignIds"] = identifiers
    warehouses: list[dict[str, Any]] = []
    page_token = ""
    for _ in range(_WAREHOUSE_MAX_PAGES):
        query: dict[str, Any] = {"limit": _WAREHOUSE_PAGE_LIMIT}
        if page_token:
            query["pageToken"] = page_token
        body = request_yandex_json(
            "POST",
            f"/v2/businesses/{urllib.parse.quote(str(business_id or '').strip())}/warehouses",
            api_token,
            payload,
            query=query,
            timeout_seconds=timeout_seconds,
        )
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        rows = (
            result.get("warehouses") if isinstance(result.get("warehouses"), list) else []
        )
        warehouses.extend(item for item in rows if isinstance(item, dict))
        paging = result.get("paging") if isinstance(result.get("paging"), dict) else {}
        page_token = str(paging.get("nextPageToken") or "").strip()
        if not page_token:
            break
    return warehouses


def fetch_yandex_partner_warehouses(
    api_token: str,
    business_id: str,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """探测无仓库组场景的 Business 仓库（``POST /v3/businesses/{businessId}/warehouses``）。

    响应仓库项的 ``id`` 即 Business 库存写入所需的 ``partnerWarehouseId``；
    ``models[]`` 携带 ``placementType`` 与该模型的 ``apiAvailability``。
    分页参数为 query（``limit`` 默认 15、上限 30 / ``pageToken``），响应
    ``result.paging.nextPageToken`` 指向下一页；本函数按页读取全部仓库，
    与 v2 探测保持一致。
    """

    warehouses: list[dict[str, Any]] = []
    page_token = ""
    for _ in range(_WAREHOUSE_MAX_PAGES):
        query: dict[str, Any] = {"limit": _WAREHOUSE_PAGE_LIMIT}
        if page_token:
            query["pageToken"] = page_token
        body = request_yandex_json(
            "POST",
            f"/v3/businesses/{urllib.parse.quote(str(business_id or '').strip())}/warehouses",
            api_token,
            {},
            query=query,
            timeout_seconds=timeout_seconds,
        )
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        rows = (
            result.get("warehouses") if isinstance(result.get("warehouses"), list) else []
        )
        warehouses.extend(item for item in rows if isinstance(item, dict))
        paging = result.get("paging") if isinstance(result.get("paging"), dict) else {}
        page_token = str(paging.get("nextPageToken") or "").strip()
        if not page_token:
            break
    return warehouses


def fetch_yandex_category_tree(
    api_token: str,
    *,
    language: str = "RU",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """读取类目树（``POST /v2/categories/tree``）。

    官方响应中 ``result`` 本身就是根节点（CategoryDTO：``id/name/children``，
    叶子判定为无 ``children``），不存在 ``result.categories`` 列表。返回
    根节点列表，供下游按 ``children`` 递归展平。
    """

    body = request_yandex_json(
        "POST",
        "/v2/categories/tree",
        api_token,
        {"language": str(language or "RU").strip().upper() or "RU"},
        timeout_seconds=timeout_seconds,
    )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    if not result:
        return []
    if isinstance(result.get("categories"), list) and not any(
        key in result for key in ("children", "id", "name")
    ):
        # 防御：兼容非官方的 {result: {categories: [...]}} 包装。
        return [item for item in result["categories"] if isinstance(item, dict)]
    return [result]


def fetch_yandex_category_parameters(
    api_token: str,
    category_id: str,
    business_id: str,
    *,
    language: str = "RU",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """读取类目属性（``POST /v2/category/{categoryId}/parameters``）。"""

    body = request_yandex_json(
        "POST",
        f"/v2/category/{urllib.parse.quote(str(category_id or '').strip())}/parameters",
        api_token,
        {"language": str(language or "RU").strip().upper() or "RU"},
        query={"businessId": str(business_id or "").strip()},
        timeout_seconds=timeout_seconds,
    )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    parameters = (
        result.get("parameters") if isinstance(result.get("parameters"), list) else []
    )
    return [item for item in parameters if isinstance(item, dict)]


def update_yandex_offer_mapping(
    api_token: str,
    business_id: str,
    offer: dict[str, Any],
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """创建或更新目录商品（``POST /v2/businesses/{businessId}/offer-mappings/update``）。

    官方请求体顶层为 ``offerMappings`` 数组，每个元素包含 ``offer`` 对象；
    不支持裸 ``offer`` 包装。
    """

    body = request_yandex_json(
        "POST",
        f"/v2/businesses/{urllib.parse.quote(str(business_id or '').strip())}/offer-mappings/update",
        api_token,
        {"offerMappings": [{"offer": offer}]},
        timeout_seconds=timeout_seconds,
    )
    return body


def fetch_yandex_offer_mapping(
    api_token: str,
    business_id: str,
    offer_ids: list[str],
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """回读业务商品与卡片状态（``POST /v2/businesses/{businessId}/offer-mappings``）。"""

    body = request_yandex_json(
        "POST",
        f"/v2/businesses/{urllib.parse.quote(str(business_id or '').strip())}/offer-mappings",
        api_token,
        {"offerIds": [str(item) for item in offer_ids if str(item or "").strip()]},
        timeout_seconds=timeout_seconds,
    )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    mappings = (
        result.get("offerMappings") if isinstance(result.get("offerMappings"), list) else []
    )
    return [item for item in mappings if isinstance(item, dict)]


def update_yandex_campaign_offer(
    api_token: str,
    campaign_id: str,
    offer_update: dict[str, Any],
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """写入上架条件（``POST /v2/campaigns/{campaignId}/offers/update``）。"""

    body = request_yandex_json(
        "POST",
        f"/v2/campaigns/{urllib.parse.quote(str(campaign_id or '').strip())}/offers/update",
        api_token,
        {"offers": [offer_update]},
        timeout_seconds=timeout_seconds,
    )
    return body


def fetch_yandex_campaign_offer(
    api_token: str,
    campaign_id: str,
    *,
    offer_ids: list[str] | None = None,
    limit: int = 50,
    page_token: str = "",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """回读店铺商品状态（``POST /v2/campaigns/{campaignId}/offers``）。

    官方契约：``offerIds`` 为请求体顶层字段（无 ``filter`` 包装）；
    ``limit/pageToken`` 是 query 参数；指定 SKU 时不得同时填写分页参数。
    """

    payload: dict[str, Any] = {}
    query: dict[str, Any] = {}
    identifiers = [str(item) for item in (offer_ids or []) if str(item or "").strip()]
    if identifiers:
        payload["offerIds"] = identifiers
    else:
        query["limit"] = max(1, min(200, int(limit or 50)))
        token = str(page_token or "").strip()
        if token:
            query["pageToken"] = token
    body = request_yandex_json(
        "POST",
        f"/v2/campaigns/{urllib.parse.quote(str(campaign_id or '').strip())}/offers",
        api_token,
        payload,
        query=query or None,
        timeout_seconds=timeout_seconds,
    )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    offers = result.get("offers") if isinstance(result.get("offers"), list) else []
    return [item for item in offers if isinstance(item, dict)]


def update_yandex_price(
    api_token: str,
    *,
    business_id: str = "",
    campaign_id: str = "",
    offers: list[dict[str, Any]],
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """写入价格；按店铺能力选择 Business 级或 Campaign 级接口。"""

    payload = {"offers": offers}
    if str(business_id or "").strip():
        path = f"/v2/businesses/{urllib.parse.quote(str(business_id).strip())}/offer-prices/updates"
    elif str(campaign_id or "").strip():
        path = f"/v2/campaigns/{urllib.parse.quote(str(campaign_id).strip())}/offer-prices/updates"
    else:
        raise YandexApiError(
            "YANDEX_PRICE_TARGET_MISSING",
            "价格写入缺少 business_id 或 campaign_id",
            retryable=False,
        )
    body = request_yandex_json(
        "POST",
        path,
        api_token,
        payload,
        timeout_seconds=timeout_seconds,
    )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    if str(result.get("status") or "").strip().upper() == "FAILED":
        errors = _body_errors(body) or [
            {
                "code": "YANDEX_PRICE_UPDATE_FAILED",
                "message": "Yandex 价格更新失败",
            }
        ]
        raise YandexApiError(
            str(errors[0].get("code") or "YANDEX_PRICE_UPDATE_FAILED"),
            f"Yandex 价格更新失败：{errors[0].get('message') or ''}",
            retryable=False,
            http_status=200,
            errors=errors,
            warnings=_body_warnings(body),
        )
    return body


def fetch_yandex_price_quarantine(
    api_token: str,
    *,
    business_id: str = "",
    campaign_id: str = "",
    offer_ids: list[str] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """读取价格隔离区（``POST .../price-quarantine``）。

    官方资源是 ``price-quarantine``（不是 ``offer-prices/changes``）；
    价格级别决定接口：Business 级价格查 ``/v2/businesses/{businessId}/
    price-quarantine``，Campaign 级价格查 ``/v2/campaigns/{campaignId}/
    price-quarantine``。``offerIds`` 为请求体顶层过滤字段，可避免全量翻页。
    """

    identifiers = [str(item) for item in (offer_ids or []) if str(item or "").strip()]
    payload: dict[str, Any] = {"offerIds": identifiers} if identifiers else {}
    if str(business_id or "").strip():
        path = f"/v2/businesses/{urllib.parse.quote(str(business_id).strip())}/price-quarantine"
    elif str(campaign_id or "").strip():
        path = f"/v2/campaigns/{urllib.parse.quote(str(campaign_id).strip())}/price-quarantine"
    else:
        raise YandexApiError(
            "YANDEX_QUARANTINE_TARGET_MISSING",
            "价格隔离区回读缺少 business_id 或 campaign_id",
            retryable=False,
        )
    body = request_yandex_json(
        "POST",
        path,
        api_token,
        payload,
        timeout_seconds=timeout_seconds,
    )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    offers = result.get("offers") if isinstance(result.get("offers"), list) else []
    return [item for item in offers if isinstance(item, dict)]


def update_yandex_stock(
    api_token: str,
    *,
    mode: str,
    campaign_id: str = "",
    business_id: str = "",
    warehouse_ids: list[str] | int | None = None,
    offer_id: str,
    count: int,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """写入库存；按授权探测出的 ``mode`` 选择官方接口与请求体。

    - ``campaign_warehouses``（存在仓库组）：``PUT /v2/campaigns/{campaignId}/
      offers/stocks``，官方请求体为 ``{"skus": [{"sku": <自有SKU>, "items":
      [{"count": N}]}]}``；不含 ``warehouseId``，``items`` 恰有一个元素。
    - ``business``（无仓库组）：``POST /v3/businesses/{businessId}/offers/
      stocks/update``，官方请求体为 ``{"skuItems": [{"sku": <自有SKU>,
      "partnerWarehouseId": N, "count": M}]}``。草稿只有单一库存数，因此
      本模式强制恰好一个发布仓库：把同一数量复制到多个仓库会使可售库存
      成倍放大。
    """

    resolved_count = max(0, int(count))
    resolved_offer_id = str(offer_id or "").strip()
    if not resolved_offer_id:
        raise YandexApiError(
            "YANDEX_STOCK_OFFER_MISSING",
            "库存写入缺少 offerId / 自有 SKU",
            retryable=False,
        )
    resolved_mode = str(mode or "").strip()
    if resolved_mode == "campaign_warehouses":
        if not str(campaign_id or "").strip():
            raise YandexApiError(
                "YANDEX_STOCK_TARGET_MISSING",
                "仓库组库存写入缺少 campaign_id",
                retryable=False,
            )
        return request_yandex_json(
            "PUT",
            f"/v2/campaigns/{urllib.parse.quote(str(campaign_id).strip())}/offers/stocks",
            api_token,
            {
                "skus": [
                    {
                        "sku": resolved_offer_id,
                        "items": [{"count": resolved_count}],
                    }
                ]
            },
            timeout_seconds=timeout_seconds,
        )
    if resolved_mode == "business":
        if not str(business_id or "").strip():
            raise YandexApiError(
                "YANDEX_STOCK_TARGET_MISSING",
                "Business 库存写入缺少 business_id",
                retryable=False,
            )
        resolved_warehouses = sorted(
            {
                int(item)
                for item in (warehouse_ids or [])
                if str(item or "").strip().isdigit() and int(str(item).strip()) > 0
            }
        )
        if len(resolved_warehouses) != 1:
            raise YandexApiError(
                "YANDEX_STOCK_TARGET_MISSING",
                "Business 库存写入必须恰好指定一个发布仓库（partnerWarehouseId），"
                "请重新测试授权以选定发布仓库",
                retryable=False,
            )
        return request_yandex_json(
            "POST",
            f"/v3/businesses/{urllib.parse.quote(str(business_id).strip())}/offers/stocks/update",
            api_token,
            {
                "skuItems": [
                    {
                        "sku": resolved_offer_id,
                        "partnerWarehouseId": resolved_warehouses[0],
                        "count": resolved_count,
                    }
                ]
            },
            timeout_seconds=timeout_seconds,
        )
    raise YandexApiError(
        "YANDEX_STOCK_TARGET_MISSING",
        f"库存写入方式不受支持：{resolved_mode or '未知'}",
        retryable=False,
    )


__all__ = [
    "YANDEX_ALL_METHODS_SCOPE",
    "YANDEX_MARKET_API_HOST",
    "YANDEX_PUBLISH_SCOPES",
    "YandexApiError",
    "fetch_yandex_business_settings",
    "fetch_yandex_campaign",
    "fetch_yandex_campaign_offer",
    "fetch_yandex_category_parameters",
    "fetch_yandex_category_tree",
    "fetch_yandex_offer_mapping",
    "fetch_yandex_partner_warehouses",
    "fetch_yandex_price_quarantine",
    "fetch_yandex_token_info",
    "fetch_yandex_warehouses",
    "request_yandex_json",
    "update_yandex_campaign_offer",
    "update_yandex_offer_mapping",
    "update_yandex_price",
    "update_yandex_stock",
    "yandex_missing_publish_scopes",
    "yandex_scope_allows",
]

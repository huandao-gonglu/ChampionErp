"""账号控制面的固定服务传输；不记录票据、凭据或业务请求回执。"""
import httpx

from erp_web.schemas.accounts import AccountError


_PATHS = frozenset({"/api/internal/erp/flow", "/api/internal/erp/authorize", "/api/internal/erp/sync"})


def post_newapi(base_url, secret, path, body):
    if path not in _PATHS:
        raise ValueError("New API 内部接口未登记。")
    try:
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            response = client.post(base_url + path, json=body, headers={"Authorization": "Bearer " + secret})
            response.raise_for_status()
            if len(response.content) > 8192:
                raise ValueError
            result = response.json()
            if not isinstance(result, dict) or result.get("success") is False:
                raise ValueError
            return result
    except (httpx.HTTPError, ValueError) as exc:
        raise AccountError("New API 暂不可用，ERP 账号仍可正常使用。", status_code=503, error_code="NEWAPI_UNAVAILABLE") from exc

"""1688 授权的 HTTP 装配；只返回状态或一次性的官方登录链接。"""
from erp_web.context import get_context
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService


def authorization_payload(action, body=None):
    context = get_context()
    service = AlibabaAuthorizationService(context.config, owner_id=context.user_id)
    try:
        if action == 'status':
            return service.status(), 200
        if action == 'start':
            return service.start(), 200
        if action == 'callback':
            return service.callback((body or {}).get('code', ''), (body or {}).get('state', '')), 200
        raise ValueError('不支持的 1688 授权操作')
    except ExternalRequestBlocked:
        return {'ok': False, 'error': '1688 授权请求已中断，请查看系统设置的「中断与恢复」'}, 409
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}, 400

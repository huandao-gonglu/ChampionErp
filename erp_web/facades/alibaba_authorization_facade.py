"""1688 授权的 HTTP 装配；转发各授权操作的结构化结果。"""
from erp_web.context import get_context
from erp_web.schemas.managed_settings import ALIBABA_APPLICATION_TYPES
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService


def authorization_payload(action, body=None):
    context = get_context()
    service = AlibabaAuthorizationService(context.config, owner_id=context.user_id)
    provider = (body or {}).get('provider')
    try:
        if provider is not None and provider not in ALIBABA_APPLICATION_TYPES:
            raise ValueError('不支持的 1688 接入方案')
        if action == 'status':
            return service.status(provider), 200
        if action == 'start':
            return service.start(provider), 200
        if action == 'complete':
            return service.complete_from_url(body['callback_url'], provider), 200
        if action == 'complete-onebound':
            return service.complete_from_url(body['callback_url'], 'onebound'), 200
        if action == 'self-use-token':
            return service.bind_self_use_token(body['access_token']), 200
        if action == 'callback':
            return service.callback((body or {}).get('code', ''), (body or {}).get('state', '')), 200
        raise ValueError('不支持的 1688 授权操作')
    except ExternalRequestBlocked as exc:
        return {'ok': False, 'error': f'1688 授权请求已暂停：{exc}，请检查授权配置后重试'}, 409
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}, 400

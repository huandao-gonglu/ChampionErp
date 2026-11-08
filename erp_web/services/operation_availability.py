"""从操作的实际模型绑定与当前请求的桌面连接计算平台限制。"""

from . import ai_model_config
from .chat_images import chat_image_reason
from .desktop_connection_service import desktop_unavailable_reason
from erp_web.schemas.requests import RequestValidationError
from erp_web.schemas.api import OperationAvailability


def model_platform_reason(config, use_case, *, desktop_reason=None):
    try:
        model = ai_model_config.resolve_ai_model(config, use_case)
    except RuntimeError:
        if use_case == 'image.plan':
            return '套图规划需要具备文本、JSON 和图片理解能力的 API 模型，请在模型设置中测试并绑定。'
        # 显式绑定的桌面模型即使尚未通过测试，在网页也不能运行。其余配置问题交回模型校验。
        bound_id = ai_model_config.ai_use_case_binding(config, use_case).get('model_id')
        if not bound_id:
            return ''
        models = ai_model_config.normalize_ai_models((config or {}).get('ai_models'))
        model = next((item for item in models if item.get('id') == bound_id), None)
        if not model:
            return ''
    if ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_API:
        return ''
    spec = ai_model_config.AI_USE_CASES[use_case]
    label = spec['label']
    capability = spec.get('desktop_capability')
    if not capability:
        return f'{label}需要 API 模型，请在模型设置中调整绑定。'
    reason = desktop_unavailable_reason() if desktop_reason is None else desktop_reason
    if reason:
        return f'{label}使用本机模型「{model.get("name") or model["id"]}」。{reason}'
    from .ai_gateway_providers import AI_PROVIDER_REGISTRY
    if not any(provider.supports(model, capability) for provider in AI_PROVIDER_REGISTRY):
        return f'所选本机模型不支持{label}，请在模型设置中调整绑定。'
    return ''


def require_model_platform(config, use_case):
    reason = model_platform_reason(config, use_case)
    if reason:
        raise RequestValidationError(reason, status_code=409, error_code='OPERATION_UNAVAILABLE')


def operation_availability(config, *, connection_id=None) -> OperationAvailability:
    desktop_reason = desktop_unavailable_reason(connection_id)
    blocked = {}
    for use_case in ai_model_config.AI_USE_CASES:
        if ai_model_config.AI_USE_CASES[use_case].get('global_binding') is False:
            continue
        reason = model_platform_reason(config, use_case, desktop_reason=desktop_reason)
        if reason:
            blocked[use_case] = reason
    image_reason = chat_image_reason(config)
    if image_reason:
        blocked["global.chat.images"] = image_reason
    return {'desktop_reason': desktop_reason, 'blocked_use_cases': blocked}

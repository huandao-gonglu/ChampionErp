"""从操作的实际模型绑定与当前请求的桌面连接计算平台限制。"""

from . import ai_model_config
from .chat_images import chat_image_reason
from .desktop_connection_service import current_desktop_connection, delegate_connection_id, desktop_unavailable_reason
from erp_web.schemas.requests import RequestValidationError
from erp_web.schemas.api import OperationAvailability


class ImageDesktopRequired(RequestValidationError):
    """图片模型已选定，须从可用的桌面执行器继续。"""

    def __init__(self, message, *, fingerprint=""):
        super().__init__(message, status_code=409, error_code="OPERATION_UNAVAILABLE")
        self.fingerprint = fingerprint


def model_platform_reason(config, use_case, *, desktop_reason=None, require_model=False):
    try:
        model = ai_model_config.resolve_ai_model(config, use_case)
    except RuntimeError as exc:
        inherited_default = (
            use_case in ai_model_config.GENERAL_MODEL_INHERITING_USE_CASES
            and bool(
                ai_model_config.ai_use_case_binding(
                    config, ai_model_config.DEFAULT_GENERAL_USE_CASE_ID
                ).get("model_id")
            )
        )
        if use_case == 'image.plan':
            if inherited_default or ai_model_config.ai_use_case_binding(config, use_case).get('model_id'):
                return str(exc)
            return '套图规划需要具备文本、JSON 和图片理解能力的 API 模型，请在模型设置中测试并绑定。'
        if require_model or ai_model_config.ai_use_case_binding(config, use_case).get('model_id') or inherited_default:
            return str(exc)
        return ''
    if ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_API:
        return ''
    spec = ai_model_config.AI_USE_CASES[use_case]
    label = spec['label']
    capability = spec.get('desktop_capability')
    if not capability:
        return f'{label}需要 API 模型，请在模型设置中调整绑定。'
    from .ai_gateway_providers import AI_PROVIDER_REGISTRY
    if not any(provider.supports(model, capability) for provider in AI_PROVIDER_REGISTRY):
        return f'所选本机模型不支持{label}，请在模型设置中调整绑定。'
    reason = desktop_unavailable_reason() if desktop_reason is None else desktop_reason
    if reason:
        return f'{label}使用本机模型「{model.get("name") or model["id"]}」。{reason}'
    return ''


def require_model_platform(config, use_case, *, require_model=False):
    reason = model_platform_reason(config, use_case, require_model=require_model)
    if reason:
        if (use_case == "image.translate" and desktop_unavailable_reason()
                and not model_platform_reason(config, use_case, desktop_reason="")):
            raise ImageDesktopRequired(reason)
        raise RequestValidationError(reason, status_code=409, error_code='OPERATION_UNAVAILABLE')


def delegated_use_cases(config, desktop_use_cases, *, connection_id=None):
    """网页手动发起时可由本账号唯一在线桌面代为执行的用例；只开放 Canvas 图片修改。"""
    key = current_desktop_connection() if connection_id is None else connection_id
    if key or 'image.translate' not in desktop_use_cases or not delegate_connection_id():
        return []
    from .gemini_canvas_runtime import is_canvas_model
    return ['image.translate'] if is_canvas_model(ai_model_config.resolve_ai_model(config, 'image.translate')) else []


def operation_availability(config, *, connection_id=None) -> OperationAvailability:
    desktop_reason = desktop_unavailable_reason(connection_id)
    blocked = {}
    desktop_use_cases = []
    for use_case in ai_model_config.AI_USE_CASES:
        if ai_model_config.AI_USE_CASES[use_case].get('global_binding') is False:
            continue
        reason = model_platform_reason(config, use_case, desktop_reason=desktop_reason)
        if reason:
            blocked[use_case] = reason
            if desktop_reason and not model_platform_reason(config, use_case, desktop_reason=''):
                desktop_use_cases.append(use_case)
    image_reason = chat_image_reason(config)
    if image_reason:
        blocked["global.chat.images"] = image_reason
    return {'desktop_reason': desktop_reason, 'blocked_use_cases': blocked, 'desktop_use_cases': desktop_use_cases,
            'delegated_use_cases': delegated_use_cases(config, desktop_use_cases, connection_id=connection_id)}

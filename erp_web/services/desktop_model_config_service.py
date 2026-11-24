"""共享模型绑定和本机连接配置分离；本机字段只交给当前桌面 owner。"""
from erp_shared.browser_ai import resolve_local_settings
from erp_shared.desktop import LOCAL_MODEL_FIELDS, local_configuration_fingerprint
from .desktop_connection_service import call_desktop, current_desktop_connection, DesktopUnavailable


def prepare_local_model(model):
    if model.get('connection_type') not in {'browser', 'cli'}:
        return model
    result = dict(model)
    reference = str(model.get('local_connection_id') or model.get('id') or '')
    if not reference:
        raise ValueError('本机模型需要稳定的模型标识。')
    if current_desktop_connection():
        saved = call_desktop('config.read', timeout=30)['models'].get(reference)
        # 另一台桌面可能尚未配置此连接，保存其他设置时保留已有绑定。
        if saved is not None or not result.get('local_config_fingerprint'):
            result['local_config_fingerprint'] = local_configuration_fingerprint(saved or {})
    elif not result.get('local_connection_id'):
        raise DesktopUnavailable('请在桌面端配置并测试本机模型连接。')
    result['local_connection_id'] = reference
    for key in LOCAL_MODEL_FIELDS:
        result.pop(key, None)
    return result


def local_models_view(models):
    if not current_desktop_connection():
        return models
    local = call_desktop('config.read', timeout=15)['models']
    result = []
    for model in models:
        if model.get('connection_type') not in {'browser', 'cli'}:
            result.append(model)
            continue
        reference = str(model.get('local_connection_id') or model.get('id') or '')
        saved = local.get(reference)
        resolved = resolve_local_settings(model, saved)
        merged = {**model, **resolved}
        if merged.get('canvas_managed') and merged.get('canvas_url') and (saved is not None or 'browser_url' in resolved):
            merged['browser_url'] = merged['canvas_url']
        result.append(merged)
    return result

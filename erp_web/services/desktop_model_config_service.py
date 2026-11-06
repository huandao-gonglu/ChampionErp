"""共享模型绑定和本机连接配置分离；本机字段只交给当前桌面 owner。"""
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
        saved = call_desktop('config.read', timeout=30)['models'].get(reference, {})
        result['local_config_fingerprint'] = local_configuration_fingerprint(saved)
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
    return [{**model, **local.get(str(model.get('local_connection_id') or model.get('id') or ''), {})}
            if model.get('connection_type') in {'browser', 'cli'} else model for model in models]

"""共享配置与个人运费版本的 HTTP 适配。"""
from pydantic import ValidationError

from erp_web.context import get_context
from erp_web.schemas.requests import RequestValidationError
from erp_web.business_data_composition import build_business_data_catalog
from erp_web.services.account_request import current_session


def _result(action):
    try:
        return action(), 200
    except RequestValidationError as exc:
        return {'ok': False, 'error': str(exc), 'error_code': exc.error_code}, exc.status_code
    except ValidationError:
        return {'ok': False, 'error': '设置参数无效，请检查文件、日期、地址和数值范围。'}, 400
    except FileNotFoundError:
        return {'ok': False, 'error': '运费版本文件缺失，请重新导入。'}, 400
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}, 400


def managed_settings_payload(body=None):
    store = get_context().config.shared_config
    return _result(lambda: store.managed_settings() if body is None else store.save_managed_settings(body))


def tariff_settings_payload(body=None, *, upgrade=False):
    service = get_context().tariffs
    if body is None:
        return _result(lambda: build_business_data_catalog(get_context()).load('shipping.tariff_versions'))
    if upgrade:
        return _result(lambda: service.upgrade(body))
    session = current_session()
    return _result(lambda: service.import_file(body, is_admin=bool(session and session.user['role'] == 'admin')))


def tariff_rules_payload(query):
    values = {key: items[0] if len(items) == 1 else items for key, items in query.items()}
    return _result(lambda: build_business_data_catalog(get_context()).load('shipping.tariff_rules', values))


__all__ = ['managed_settings_payload', 'tariff_settings_payload', 'tariff_rules_payload']

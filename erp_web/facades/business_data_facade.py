"""普通只读业务数据的页面适配，不执行模型或业务写入。"""
from pydantic import ValidationError

from erp_web.business_data_composition import build_business_data_catalog
from erp_web.context import get_context
from erp_web.schemas.business_data import DataCatalogRequest, DataReadRequest


def business_data_payload(body=None, *, query=None):
    try:
        catalog = build_business_data_catalog(get_context())
        values = {key: items[0] if len(items) == 1 else items for key, items in (query or {}).items()}
        result = catalog.describe(DataCatalogRequest.model_validate(values)) if body is None else catalog.read(DataReadRequest.model_validate(body))
        return {'ok': True, **result.model_dump(mode='json')}, 200
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        return {'ok': False, 'error': str(exc), 'error_code': 'BUSINESS_DATA_QUERY_INVALID'}, 400


__all__ = ['business_data_payload']

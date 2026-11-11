"""普通只读业务数据的页面适配，不执行模型或业务写入。"""
from pydantic import ValidationError

from erp_web.business_query_composition import BUSINESS_QUERIES, build_business_data_catalog
from erp_web.context import get_context
from erp_web.schemas.business_data import DataCatalogRequest, DataReadRequest


BUSINESS_QUERY_PATHS = BUSINESS_QUERIES.http_queries


def business_query_payload(name, query=None):
    """普通页面读取完整业务结果；参数与 AI 读取使用相同查询契约。"""
    try:
        values = {key: items[0] if len(items) == 1 else items for key, items in (query or {}).items()}
        return build_business_data_catalog(get_context()).load(name, values), 200
    except FileNotFoundError as exc:
        status = build_business_data_catalog(get_context()).resource(name).not_found_status
        return {'ok': False, 'error': str(exc), 'error_code': 'BUSINESS_DATA_QUERY_INVALID'}, status
    except (ValidationError, ValueError) as exc:
        return {'ok': False, 'error': str(exc), 'error_code': 'BUSINESS_DATA_QUERY_INVALID'}, 400


def business_data_payload(body=None, *, query=None):
    try:
        catalog = build_business_data_catalog(get_context())
        values = {key: items[0] if len(items) == 1 else items for key, items in (query or {}).items()}
        result = catalog.describe(DataCatalogRequest.model_validate(values)) if body is None else catalog.read(DataReadRequest.model_validate(body))
        return {'ok': True, **result.model_dump(mode='json')}, 200
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        return {'ok': False, 'error': str(exc), 'error_code': 'BUSINESS_DATA_QUERY_INVALID'}, 400


__all__ = ['BUSINESS_QUERY_PATHS', 'business_data_payload', 'business_query_payload']

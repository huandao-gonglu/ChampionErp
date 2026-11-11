"""设置读取覆盖、同源、敏感字段边界与上下文提示回归。"""
import json
from types import SimpleNamespace

import pytest

from erp_web.app_config import default_app_config
from erp_web.business_query_composition import BUSINESS_QUERIES, build_business_data_catalog
from erp_web.business_query_inventory import APP_CONFIG_READS, audit_query_coverage
from erp_web.context import get_context
from erp_web.facades.business_data_facade import business_query_payload
from erp_web.schemas.business_data import DataCatalogRequest, DataReadRequest


def test_every_saved_config_section_and_get_has_an_explicit_reading_owner():
    from erp_web.ai_capability_coverage import AI_CAPABILITY_COVERAGE_MANIFEST
    assert set(default_app_config()) == set(APP_CONFIG_READS)
    assert set(APP_CONFIG_READS.values()) <= {query.name for query in BUSINESS_QUERIES.definitions}
    paths = {entry.path for entry in AI_CAPABILITY_COVERAGE_MANIFEST if entry.method == 'GET' and entry.path.startswith('/api/')}
    rows = audit_query_coverage(paths, BUSINESS_QUERIES)
    assert len(rows) == len(paths) - 1
    from erp_web.http_routes import GET_API_ROUTES
    audit_query_coverage({path for path in GET_API_ROUTES if path.startswith('/api/')}, BUSINESS_QUERIES)
    with pytest.raises(ValueError, match='new-settings'):
        audit_query_coverage(paths | {'/api/new-settings'}, BUSINESS_QUERIES)


def test_settings_reads_share_the_page_payload_and_never_invoke_network(monkeypatch):
    app = get_context()
    from erp_web.services import s3_image_storage, desktop_connection_service
    monkeypatch.setattr(s3_image_storage, 'safe_image_urlopen', lambda *a, **k: pytest.fail('读取不得执行网络测试'))
    monkeypatch.setattr(desktop_connection_service, 'call_desktop', lambda *a, **k: pytest.fail('配置元数据不得连接桌面'))
    catalog = build_business_data_catalog(app)
    for name in ('settings.system', 'settings.forbidden_categories', 'settings.image_hosting', 'settings.models',
                 'settings.model_bindings', 'settings.product_research', 'settings.store', 'settings.collection',
                 'settings.pricing_defaults', 'settings.approval'):
        page, status = business_query_payload(name)
        assert status == 200, page
        result = catalog.read(DataReadRequest(resource=name, path='$'))
        assert not result.limited and result.data == page


def test_public_config_metadata_excludes_secrets_urls_and_local_commands():
    app = default_app_config()
    secret = '真实密钥仅用于测试不得返回'
    app['1688_api'].update(app_secret=secret, access_token=secret)
    app['alibaba_cookie'] = secret
    app['pricing_defaults']['exchange_rate_api_url'] = 'https://example.com/?token=' + secret
    app['product_research']['search_providers'][0]['config_json']['api_key'] = secret
    app['ai_models'][0].update(api_key=secret, cli_command=secret, base_url='https://example.com/?token='+secret)
    store = {'yandex': {'api_token': secret, 'shop_name': '测试店铺', 'auth_error_message': secret}}
    fake = SimpleNamespace(config=SimpleNamespace(load_app_config=lambda: app, load_store_config=lambda: store))
    catalog = build_business_data_catalog(fake)
    for name in ('settings.models', 'settings.model_bindings', 'settings.product_research', 'settings.store',
                 'settings.collection', 'settings.pricing_defaults'):
        value = catalog.load(name)
        assert secret not in json.dumps(value, ensure_ascii=False)
    assert catalog.load('settings.collection')['alibaba_cookie_configured'] is True
    assert catalog.load('settings.pricing_defaults')['exchange_rate_api_configured'] is True


def test_catalog_gives_required_parameters_and_enum_without_loading_data():
    catalog = BUSINESS_QUERIES.bind(None)
    brief = catalog.describe(DataCatalogRequest(keyword='shipping.tariff_rules')).resources[0]
    assert not brief.parameters
    assert brief.parameter_hints['platform']['enum'] == ['yandex', 'ozon']
    assert brief.parameter_hints['platform']['required'] is True
    row = catalog.describe(DataCatalogRequest(keyword='draft.pricing')).resources[0]
    assert row.parameter_hints['draft_id']['required']


def test_parameterized_binding_keys_are_expandable_without_reading_the_whole_configuration():
    from erp_web.services.business_data import BusinessDataCatalog, BusinessResource
    from erp_web.schemas.business_data import NoDataParameters
    catalog = BusinessDataCatalog((BusinessResource('binding', '测试', NoDataParameters,
        lambda _: {'global.chat': {'model_id': 'api'}, 'key/with~escape': '事实'}),))
    assert catalog.read(DataReadRequest(resource='binding', path='/global.chat/model_id')).data == 'api'
    assert catalog.read(DataReadRequest(resource='binding', path='/key~1with~0escape')).data == '事实'


def test_unified_publish_status_keeps_the_public_not_found_status(monkeypatch):
    from erp_web.facades import business_data_facade
    def missing(_):
        raise FileNotFoundError('任务不存在')
    context = SimpleNamespace(publishing_bus=SimpleNamespace(get_public_status=missing))
    monkeypatch.setattr(business_data_facade, 'get_context', lambda: context)
    assert business_query_payload('publish.job', {'job_id': ['missing']})[1] == 404
    assert business_query_payload('publish.job', {})[1] == 400


def test_cursor_and_other_root_fields_remain_visible_from_a_default_collection():
    from erp_web.services.business_data import BusinessDataCatalog, BusinessResource
    from erp_web.schemas.business_data import NoDataParameters
    catalog = BusinessDataCatalog((BusinessResource('jobs', '任务', NoDataParameters,
        lambda _: {'items': [{'id': 1}], 'next_cursor': 'next'}, 'items'),))
    result = catalog.read(DataReadRequest(resource='jobs'))
    assert result.data == [{'id': 1}]
    assert result.metadata['next_cursor'] == 'next'
    assert result.metadata['root_fields'] == ['items', 'next_cursor']

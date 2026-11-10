"""普通业务查询与 AI 共用数据源；按需展开、预算、参数和账号边界回归。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from erp_web.business_data_composition import build_business_data_catalog
from erp_web.context import get_context
from erp_web.facades.business_data_facade import business_data_payload
from erp_web.facades.managed_settings_facade import tariff_rules_payload
from erp_web.product_model import default_product_model
from erp_web.runtime_units.business_data_capabilities import BusinessDataScope, business_data_read
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.business_data import DataCatalogRequest, DataReadRequest, NoDataParameters
from erp_web.services.business_data import BusinessDataCatalog, BusinessResource


def execution():
    return AiExecutionContext.create(timeout_seconds=30, budget_profile='test', permissions={'business.read', 'draft.read', 'product.read', 'pricing.read'})


def read(catalog, **kw):
    return catalog.read(DataReadRequest(**kw))


def test_query_discovery_does_not_load_data_and_schema_is_on_demand():
    catalog = BusinessDataCatalog((BusinessResource('sample', '测试资料', NoDataParameters,
        lambda _: pytest.fail('发现目录不能读取资料')),))
    brief = catalog.describe(DataCatalogRequest())
    assert brief.resources[0].parameters == {}
    detail = catalog.describe(DataCatalogRequest(resource='sample'))
    assert detail.resources[0].parameters['additionalProperties'] is False
    assert catalog.describe(DataCatalogRequest(keyword='无匹配')).resources == []


def test_projection_filter_paging_and_full_detail_remain_accessible():
    rows = [{'id': n, 'group': 'a' if n % 2 else 'b', 'detail': {'fact': str(n)}} for n in range(30)]
    catalog = BusinessDataCatalog((BusinessResource('sample', '测试', NoDataParameters,
        lambda _: {'rows': rows}, 'rows', ('id',)),))
    result = read(catalog, resource='sample', filters={'group': 'a'}, limit=2)
    assert result.data == [{'id': 1}, {'id': 3}]
    assert result.total == 15 and result.next_offset == 2
    assert 'detail' in result.available_fields
    assert read(catalog, resource='sample', path='rows.1.detail').data == {'fact': '1'}
    assert read(catalog, resource='sample', fields=['detail.fact'], offset=29).data == [{'detail.fact': '29'}]
    with pytest.raises(ValueError):
        read(catalog, resource='sample', path='rows.-1')
    with pytest.raises(ValidationError):
        read(catalog, resource='sample', params={'owner': 'other'})


def test_large_records_are_not_silently_truncated_and_can_be_expanded():
    catalog = BusinessDataCatalog((BusinessResource('sample', '测试', NoDataParameters,
        lambda _: {'rows': [{'id': 1, 'text': '事实' * 20000}]}, 'rows'),))
    result = read(catalog, resource='sample')
    assert result.limited and result.data == [] and result.next_offset == 0
    assert len(result.model_dump_json()) <= result.max_chars
    assert read(catalog, resource='sample', fields=['id']).data == [{'id': 1}]
    start = read(catalog, resource='sample', path='rows.0.text')
    rest = read(catalog, resource='sample', path='rows.0.text', offset=start.next_offset)
    assert start.next_offset == 9000 and rest.data == '事实' * 4500
    assert len(start.model_dump_json()) <= start.max_chars


def test_page_and_ai_share_tariff_source_without_exposing_secrets(monkeypatch):
    app = get_context()
    data = {'ok': True, 'version': 'v1', 'rules': [{'name': '物流', 'max_edge_cm': '60'}]}
    monkeypatch.setattr(type(app), 'tariffs', property(lambda _: SimpleNamespace(rules=lambda _: deepcopy(data))))
    expected, status = tariff_rules_payload({'platform': ['yandex']})
    assert status == 200
    result = business_data_read(DataReadRequest(resource='shipping.tariff_rules', params={'platform': 'yandex'}),
                                BusinessDataScope(lambda: build_business_data_catalog(app)), execution())
    assert result.data == expected['rules'] and result.metadata['version'] == 'v1'
    body, status = business_data_payload({'resource': 'shipping.tariff_rules', 'params': {'platform': 'yandex'}})
    assert status == 200 and body['data'] == result.data
    for name in ('config', 'store_auth', 'runtime_secrets', '/etc/passwd'):
        assert business_data_payload({'resource': name})[1] == 400


def test_draft_details_read_saved_and_preview_without_repricing():
    app = get_context()
    product = default_product_model()
    product.update(product_id='data-product', sku_items=[{'id': 'sku-1', 'active': True,
        'package_dimensions': {'length_cm': '30', 'width_cm': '20', 'height_cm': '10', 'weight_kg': '.4'}}])
    product['drafts'] = {'yandex': {'draft_id': 'data-draft', 'sku_items': [{'sku_id': 'sku-1', 'selected': True}]}}
    app.products.save_product(product)
    catalog = build_business_data_catalog(app)
    assert read(catalog, resource='draft.content', params={'draft_id': 'data-draft'}).data['draft_id'] == 'data-draft'
    assert read(catalog, resource='product.skus', params={'product_id': 'data-product'}).data[0]['id'] == 'sku-1'
    quote = {'target_key': 'yandex:global', 'applied_price': {'amount': '1200', 'currency': 'RUB'},
             'calculation_basis': {'shipping_evidence': {'route': '物流', 'decision': {'selection': 'specified'}}},
             'shipping_candidates': [{'amount_cny': '10'}]}
    app.pricing_cache.previews.put('test-preview', {'draft_id': 'data-draft', 'batch': {'items': [
        {'sku_id': 'sku-1', 'result': {'results': [quote]}}]}})
    result = read(catalog, resource='draft.pricing', params={'draft_id': 'data-draft', 'preview_id': 'test-preview'})
    assert result.data[0]['shipping_summary']['decision_available'] is True
    assert read(catalog, resource='draft.pricing', params={'draft_id': 'data-draft', 'preview_id': 'test-preview'},
                path='rows.0.calculation_basis.shipping_evidence').data['decision']['selection'] == 'specified'
    assert read(catalog, resource='draft.pricing', params={'draft_id': 'data-draft', 'preview_id': 'test-preview'},
                path='rows.0.shipping_candidates').data == [{'amount_cny': '10'}]
    app.pricing_cache.previews.put('foreign-preview', {'draft_id': 'other-draft'})
    with pytest.raises(ValueError, match='不属于'):
        read(catalog, resource='draft.pricing', params={'draft_id': 'data-draft', 'preview_id': 'foreign-preview'})
    with pytest.raises(ValueError, match='不存在'):
        read(catalog, resource='draft.content', params={'draft_id': 'foreign-draft'})


def test_catalog_can_add_a_normal_business_resource_without_ai_tool_changes():
    catalog = BusinessDataCatalog((BusinessResource('new.feature', '新功能', NoDataParameters,
        lambda _: {'normal': '页面原有查询结果'}),))
    assert business_data_read(DataReadRequest(resource='new.feature'), BusinessDataScope(lambda: catalog), execution()).data == {'normal': '页面原有查询结果'}


def test_resource_permissions_filter_discovery_and_reject_read():
    catalog = BusinessDataCatalog((BusinessResource('private', '测试', NoDataParameters,
        lambda _: pytest.fail('无权限不能读取底层资料'), permission='product.read'),))
    assert catalog.describe(DataCatalogRequest(), permissions={'business.read'}).resources == []
    with pytest.raises(ValueError, match='无权'):
        catalog.read(DataReadRequest(resource='private'), permissions={'business.read'})


@pytest.mark.parametrize('use_python', [False, True])
def test_native_chat_discovers_and_reads_shared_data_with_bounded_context(tmp_path, monkeypatch, use_python):
    import asyncio
    import json
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset
    from tests.ai_code_mode_helpers import business_returns, discover_tools, python_call
    from tests.test_native_agent_integration import body, service

    app = get_context()
    rules = [{'id': str(n), 'max_edge_cm': '60', 'max_sum_cm': '90', 'max_g': '500'} for n in range(100)]
    monkeypatch.setattr(type(app), 'tariffs', property(lambda _: SimpleNamespace(rules=lambda _: {'version': 'test', 'rules': rules})))
    async def model(messages, info):
        if discovery := discover_tools(info, 'business_data_read'):
            yield {0: discovery}
            return
        previous = business_returns(messages)
        if previous:
            result = previous[-1].content
            assert result['total'] == 100 and len(result['data']) == 2
            assert len(json.dumps(result, ensure_ascii=False)) < 2000
            yield '轻小件最长边不超过60厘米。'
            return
        arguments = json.dumps({'resource': 'shipping.tariff_rules', 'params': {'platform': 'yandex'}, 'limit': 2})
        factory = python_call if use_python else DeltaToolCall
        yield {0: factory(name='business_data_read', json_args=arguments, tool_call_id='read-rules')}
    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    run = ui.prepare_run(body('查一下轻小件的尺寸限制'))
    chunks = []
    asyncio.run(run.stream(chunks.append))
    assert b'"type":"finish"' in b''.join(chunks)
    assert '60' in b''.join(chunks).decode()

"""普通业务查询与 AI 共用数据源；按需展开、预算、参数和账号边界回归。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from erp_web.business_query_composition import build_business_data_catalog
from erp_web.context import get_context
from erp_web.facades.business_data_facade import business_data_payload, business_query_payload
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
    assert result.limited and result.data == [] and result.next_offset is None and "同一 offset" in result.notice
    assert len(result.model_dump_json()) <= result.max_chars
    assert read(catalog, resource='sample', fields=['id']).data == [{'id': 1}]
    start = read(catalog, resource='sample', path='rows.0.text')
    rest = read(catalog, resource='sample', path='rows.0.text', offset=start.next_offset)
    assert start.next_offset == 9000 and rest.data == '事实' * 4500
    assert len(start.model_dump_json()) <= start.max_chars


def test_text_budget_includes_json_escaping_and_discovery_is_paginated():
    catalog = BusinessDataCatalog(tuple(BusinessResource(str(n), '测试', NoDataParameters,
        lambda _: {'text': '\n' * 50000}) for n in range(100)))
    page = catalog.describe(DataCatalogRequest(limit=5))
    assert len(page.resources) == 5 and page.total == 100 and page.next_offset == 5
    text = read(catalog, resource='0', path='text')
    assert len(text.model_dump_json()) <= text.max_chars and text.next_offset > 0


def test_page_and_ai_share_tariff_source_without_exposing_secrets(monkeypatch):
    app = get_context()
    data = {'ok': True, 'version': 'v1', 'rules': [{'name': '物流', 'max_edge_cm': '60'}]}
    monkeypatch.setattr(type(app), 'tariffs', property(lambda _: SimpleNamespace(rules=lambda _: deepcopy(data))))
    expected, status = business_query_payload('shipping.tariff_rules', {'platform': ['yandex']})
    assert status == 200
    result = business_data_read(DataReadRequest(resource='shipping.tariff_rules', params={'platform': 'yandex'}, path='rules'),
                                BusinessDataScope(lambda: build_business_data_catalog(app)), execution())
    assert result.data == expected['rules'] and result.metadata['version'] == 'v1'
    body, status = business_data_payload({'resource': 'shipping.tariff_rules', 'params': {'platform': 'yandex'}, 'path': 'rules'})
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
        assert {'business_data_read', 'business_data_catalog'} <= {tool.name for tool in info.function_tools}
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


def test_catalog_matches_separate_keywords_and_ai_errors_are_actionable():
    from erp_web.services.capability_errors import BusinessCapabilityError
    from erp_web.runtime_units.business_data_capabilities import business_data_catalog
    catalog = BusinessDataCatalog((BusinessResource('sample', '物流尺寸限制', NoDataParameters,
        lambda _: {'rows': []}),))
    assert catalog.describe(DataCatalogRequest(keyword='运费 尺寸 重量')).total == 1
    with pytest.raises(BusinessCapabilityError, match='可用字段'):
        business_data_read(DataReadRequest(resource='sample', path='$[0]'), BusinessDataScope(lambda: catalog), execution())
    with pytest.raises(BusinessCapabilityError, match='未知业务数据'):
        business_data_catalog(DataCatalogRequest(resource='wrong'), BusinessDataScope(lambda: catalog), execution())


def test_native_query_error_guides_recovery_instead_of_opaque_retry(tmp_path):
    import asyncio
    import json
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset
    from tests.ai_code_mode_helpers import business_returns
    from tests.test_native_agent_integration import body, service
    async def model(messages, info):
        previous = business_returns(messages)
        if not previous:
            yield {0: DeltaToolCall(name='business_data_read', json_args=json.dumps({'resource': 'unknown'}), tool_call_id='bad')}
        elif len(previous) == 1:
            error = previous[-1].content['error']
            assert error['code'] == 'BUSINESS_DATA_QUERY_INVALID'
            assert '目录' in error['message']
            yield {0: DeltaToolCall(name='business_data_catalog', json_args=json.dumps({'resource': 'pricing.templates'}), tool_call_id='recover')}
        else:
            assert previous[-1].content['resources'][0]['name'] == 'pricing.templates'
            yield '已通过目录找到正确资源。'
    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(get_context()))
    chunks = []
    asyncio.run(ui.prepare_run(body('查询模板')).stream(chunks.append))
    assert '正确资源' in b''.join(chunks).decode()


def test_code_mode_type_result_is_json_safe_without_changing_kernel_state(tmp_path, monkeypatch):
    """复现线上 type(...) 作为脚本结果导致原生消息编码失败的边界。"""
    import asyncio
    import json
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset
    from tests.ai_code_mode_helpers import business_returns
    from tests.test_native_agent_integration import body, service, CONVERSATION
    app = get_context()
    monkeypatch.setattr(type(app), 'tariffs', property(lambda _: SimpleNamespace(rules=lambda _: {'rules': [{'id': '1'}, {'id': '2'}]})))
    async def model(messages, info):
        returned = business_returns(messages)
        outer = [p for m in messages for p in m.parts if getattr(p, 'tool_name', '') == 'run_code' and p.part_kind == 'tool-return']
        if not outer:
            code = 'r = await business_data_read(resource="shipping.tariff_rules", params={"platform":"yandex"})\nlen(r["data"]), type(r["data"])'
            yield {0: DeltaToolCall(name='run_code', json_args=json.dumps({'code': code}), tool_call_id='types')}
        elif len(outer) == 1:
            assert outer[-1].content == [2, "<class 'list'>"]
            yield {0: DeltaToolCall(name='run_code', json_args=json.dumps({'code': 'len(r["data"])'}), tool_call_id='kernel')}
        else:
            assert outer[-1].content == 2 and returned
            yield '脚本结果与内核状态均正常。'
    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body('读取并核对类型')).stream(chunks.append))
    assert b'"type":"error"' not in b''.join(chunks)
    assert '内核状态均正常' in b''.join(chunks).decode()
    assert ui.chat_service.trusted_history(CONVERSATION)


def test_large_publish_job_returns_linked_draft_in_default_read():
    job = {'job_id': 'j1', 'draft_id': 'd1', 'status': 'completed', 'platforms': {'yandex': '详情' * 10000}}
    context = SimpleNamespace(publishing_bus=SimpleNamespace(get_public_status=lambda job_id: job))
    catalog = build_business_data_catalog(context)
    request = {'resource': 'publish.job', 'params': {'job_id': 'j1'}}
    result = read(catalog, **request)
    assert result.data['job.draft_id'] == 'd1' and result.data['job.status'] == 'completed'
    assert not result.limited and result.next_offset is None
    assert read(catalog, **request, path='job.draft_id').data == 'd1'
    assert catalog.load('publish.job', request['params']) == {'ok': True, 'job': job}


def test_filtered_oversized_row_after_first_page_can_resume_with_narrower_fields():
    rows = [{'id': 0, 'use': False}, {'id': 1, 'use': True},
            {'id': 2, 'use': True, 'text': '大' * 15000}, {'id': 3, 'use': True}]
    catalog = BusinessDataCatalog((BusinessResource('sample', '测试', NoDataParameters, lambda _: rows),))
    request = {'resource': 'sample', 'filters': {'use': True}}
    first = read(catalog, **request)
    assert first.next_offset == 1 and first.data == [rows[1]]
    blocked = read(catalog, offset=first.next_offset, **request)
    assert blocked.limited and blocked.next_offset is None and '位置 1' in blocked.notice
    assert read(catalog, offset=blocked.offset, fields=['id'], **request).data == [{'id': 2}, {'id': 3}]

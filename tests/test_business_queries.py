"""普通查询一次声明后的页面、目录、模型及账号范围回归。"""
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from pydantic import Field
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from erp_web import business_query_composition as composition
from erp_web.facades import business_data_facade as facade
from erp_web.http_route_units import business_query_routes as routes
from erp_web.runtime_units.business_data_capabilities import BusinessDataScope, business_data_read
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.business_data import DataCatalogRequest, DataModel, DataReadRequest, NoDataParameters
from erp_web.services.business_queries import BusinessQueries, BusinessQuery
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.test_fulfillment import domain
from tests.test_logistics_settings import configure


def execution(*permissions):
    return AiExecutionContext.create(timeout_seconds=30, budget_profile='test', permissions={'business.read', *permissions})


def test_one_normal_query_declaration_provides_http_and_ai_without_extra_registration(monkeypatch):
    class Query(DataModel):
        item_id: str = Field(min_length=1)

    queries = BusinessQueries((BusinessQuery('sample.detail', '普通功能详情', Query,
        lambda context, request: {'id': request.item_id, 'details': context.fact}, permission='sample.read'),))
    context = SimpleNamespace(fact='业务事实')
    monkeypatch.setattr(composition, 'BUSINESS_QUERIES', queries)
    monkeypatch.setattr(facade, 'get_context', lambda: context)
    monkeypatch.setattr(routes, 'GET_HANDLERS', routes.build_get_handlers(queries.http_queries))
    replies = []
    handler = SimpleNamespace(send_json=lambda *args: replies.append(args))
    assert routes.handle_get(handler, urlsplit('/api/business-queries/sample.detail?item_id=one'))
    assert replies[-1] == ({'id': 'one', 'details': '业务事实'}, 200)
    catalog = composition.build_business_data_catalog(context)
    assert catalog.describe(DataCatalogRequest()).resources[0].name == 'sample.detail'
    result = business_data_read(DataReadRequest(resource='sample.detail', params={'item_id': 'one'}),
                               BusinessDataScope(lambda: catalog), execution('sample.read'))
    assert result.data == replies[-1][0]
    assert routes.handle_get(handler, urlsplit('/api/business-queries/sample.detail?item_id=one&owner=other'))
    assert replies[-1][1] == 400
    assert routes.handle_get(handler, urlsplit('/api/business-queries/sample.detail?item_id=one&item_id=two'))
    assert replies[-1][1] == 400
    with pytest.raises(BusinessCapabilityError, match='无权'):
        business_data_read(DataReadRequest(resource='sample.detail', params={'item_id': 'one'}),
                           BusinessDataScope(lambda: catalog), execution())
    from erp_web.facades import agent_capability_facade
    monkeypatch.setattr(agent_capability_facade, 'BUSINESS_QUERIES', queries)
    assert 'sample.read' in agent_capability_facade.global_chat_permissions()


def test_query_definitions_reject_ambiguous_routes_and_names():
    def query(name, path=''):
        return BusinessQuery(name, '测试', NoDataParameters, lambda context, _: {}, http_path=path)
    for definitions in ((query('same'), query('same')), (query('a', '/api/same'), query('b', '/api/same')),
                        (query('a', '/outside'),)):
        with pytest.raises(ValueError):
            BusinessQueries(definitions)


def test_forwarder_configuration_is_same_public_fact_and_does_not_refresh_or_cross_accounts(domain, monkeypatch):
    service, bus, *_ = domain
    configured = configure(service.receiving, name='已配置收货点', is_default=True, rules=[{'carrier': 'CEL'}])
    context = SimpleNamespace(fulfillment=service)
    monkeypatch.setattr(facade, 'get_context', lambda: context)
    monkeypatch.setattr(bus, 'catalog', lambda: pytest.fail('普通查询不得刷新远端'))
    before = json.dumps(service.receiving.logistics.store.list(bus.identity()), sort_keys=True)
    catalog = composition.build_business_data_catalog(context)
    scope = BusinessDataScope(lambda: catalog)
    for name in ('logistics.settings', 'fulfillment.settings'):
        full, status = facade.business_query_payload(name)
        assert status == 200
        result = business_data_read(DataReadRequest(resource=name, path='$'), scope, execution('order.read'))
        assert result.data == full
        serialized = json.dumps(full)
        assert 'bus_identity' not in serialized and '"identity"' not in serialized
        assert bus.identity() not in serialized
    addresses = business_data_read(DataReadRequest(resource='logistics.settings', filters={'id': configured['id']}), scope, execution('order.read'))
    assert addresses.data[0]['rules'] == [{'platform': 'yandex', 'carrier': 'CEL'}]
    assert 'address' not in addresses.data[0]
    assert json.dumps(service.receiving.logistics.store.list(bus.identity()), sort_keys=True) == before
    bus.account = 'other-bus'
    assert business_data_read(DataReadRequest(resource='logistics.settings'), scope, execution('order.read')).data == []
    full = business_data_read(DataReadRequest(resource='fulfillment.settings', path='$'), scope, execution('order.read')).data
    assert full['rules'] == [] and full['catalog'] == {}
    names = {row.name for row in catalog.describe(DataCatalogRequest(keyword='报单'), permissions={'pricing.read'}).resources}
    assert names.isdisjoint({'logistics.settings', 'fulfillment.settings'})


@pytest.mark.parametrize('use_python', [False, True])
def test_native_chat_discovers_forwarder_config_without_specialized_ai_tools(domain, monkeypatch, tmp_path, use_python):
    from erp_web.context import get_context
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset
    from tests.ai_code_mode_helpers import business_returns, python_call
    from tests.test_native_agent_integration import body, service as chat_service

    fulfillment = domain[0]
    configure(fulfillment.receiving, name='义乌收货点', is_default=True, rules=[{'carrier': 'UNI'}])
    app = get_context()
    monkeypatch.setattr(type(app), 'fulfillment', property(lambda _: fulfillment))
    async def model(messages, info):
        previous = business_returns(messages)
        factory = python_call if use_python else DeltaToolCall
        if not previous:
            yield {0: factory(name='business_data_catalog', json_args=json.dumps({'keyword': '报单'}), tool_call_id='catalog')}
        elif len(previous) == 1:
            assert {'logistics.settings', 'fulfillment.settings'} <= {row['name'] for row in previous[-1].content['resources']}
            yield {0: factory(name='business_data_read', json_args=json.dumps({'resource': 'logistics.settings'}), tool_call_id='read')}
        else:
            assert any(row['name'] == '义乌收货点' and row['rules'] == [{'platform': 'yandex', 'carrier': 'UNI'}]
                       for row in previous[-1].content['data'])
            assert len(json.dumps(previous[-1].content, ensure_ascii=False)) < 3000
            yield '已配置义乌收货点，承接 UNI；可选枚举不等于已经配置。'
    ui = chat_service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body('我配置了哪些报单货代')).stream(chunks.append))
    assert b'"type":"error"' not in b''.join(chunks)
    assert '承接 UNI' in b''.join(chunks).decode()

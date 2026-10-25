"""在线属性编辑：缺失字段、枚举、多值、版本及发送前校验。"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.runtime_units.category_definition_support import project_attribute_page
from erp_web.runtime_units.online_product_capabilities import OnlineProductCapabilityScope, _change_snapshot
from erp_web.runtime_units.online_product_capabilities import online_products_attributes
from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
from erp_web.schemas.category_definition import (
    CategoryAttributeDefinition, CategoryAttributeOptionPreview, CategoryAttributeValue,
    CategoryAttributeValuePage, CategoryDefinition, CategoryUnitOption,
)
from erp_web.schemas.online_products import Capability, ChangeRequest, OnlineAttributeQuery, OnlineChange, OnlineListing
from erp_web.services.online_product_changes import confirmation
from erp_web.services.online_product_service import OnlineProductService
from erp_web.stores.online_product_store import OnlineConflict


class Catalog:
    def __init__(self):
        self.calls = []
        self.definition = CategoryDefinition(platform='yandex', category_id='100', site='global', required=(
            CategoryAttributeDefinition(id='10', name='Размер обуви', required=True, value_type='enum',
                value_mode='strict_enum', is_dictionary=True, has_more_values=True),
            CategoryAttributeDefinition(id='11', name='Размерная сетка', required=True, value_type='enum',
                value_mode='strict_enum', is_dictionary=True),
        ), optional=(
            CategoryAttributeDefinition(id='12', name='颜色', value_type='enum', is_dictionary=True, is_collection=True, max_value_count=2),
            CategoryAttributeDefinition(id='13', name='内长', value_type='numeric', constraints={'minValue':'1','maxValue':'50'},
                unit_options=(CategoryUnitOption(id='8',name='см'),), default_unit_id='8'),
            CategoryAttributeDefinition(id='14', name='说明', value_type='text', constraints={'maxLength':'20'}),
            CategoryAttributeDefinition(id='15', name='自定义选项', value_type='enum', is_dictionary=True, allow_custom_values=True),
            CategoryAttributeDefinition(id='16', name='只读字段', read_only=True),
            CategoryAttributeDefinition(id='200', name='分组'),
        ))
        self.options = {'10': [('101','42-43')], '11':[('111','EU')], '12':[('121','红'),('122','蓝')]}

    def attribute_definitions(self, platform, category, **kwargs):
        self.calls.append(('definitions', platform, category))
        return self.definition

    def public_attribute_page(self, platform, category, **kwargs):
        return project_attribute_page(self.attribute_definitions(platform, category), cursor=kwargs.get('cursor',''), limit=kwargs.get('limit',50))

    def attribute_values(self, platform, category, attribute, **kwargs):
        self.calls.append(('values', attribute, kwargs))
        return CategoryAttributeValuePage(platform=platform, category_id=category, attribute_id=attribute,
            values=tuple(CategoryAttributeValue(dictionary_value_id=id_,value=value) for id_,value in self.options.get(attribute,[])))


def row(id_, value, value_id=None, unit_id=None):
    return {'id': str(id_), 'parameterId': int(id_), 'value': value,
            **({'valueId':value_id} if value_id is not None else {}), **({'unitId':unit_id} if unit_id is not None else {})}


@pytest.fixture
def editing(monkeypatch):
    context = get_context()
    catalog = Catalog()
    context = SimpleNamespace(db=context.db, config=context.config, category_catalog=catalog)
    monkeypatch.setattr(context.config, 'load_store_config', lambda: {'yandex': {'business_id':'b','campaign_id':'c'}})
    listing = OnlineListing(id='listing', platform='yandex', account_id='b:c', remote_id='shoe', model='business_offer',
        content={'attributes':[row(12,'红',121),row(12,'蓝',122),row(14,'原说明')]},
        snapshot={'mapping':{'marketCategoryId':100}},
        capabilities={'content':Capability(enabled=True,fields=['attributes','title'])})
    writes = []
    remote = SimpleNamespace(account_id='b:c', read=lambda _: listing.model_copy(deep=True),
        write=lambda *args: writes.append(deepcopy(args[-1])) or {'status':'OK'})
    service = OnlineProductService(context, adapter_factories={'yandex':lambda config: remote}, start_worker=False)
    service.store.save(listing)
    yield service, catalog, listing, writes
    service.close()


def request(service, rows):
    return {'listing_id':'listing', 'version': service.store.get('listing').version, 'operation':'content',
            'scope_id':'global', 'changes':{'attributes':rows}, 'idempotency_key':uuid4().hex}


def test_ai_attribute_read_uses_actual_listing_category_and_shared_version_checks(editing):
    from erp_web.services.capability_errors import BusinessCapabilityError
    from pydantic import ValidationError

    service, catalog, _, writes = editing
    scope = OnlineProductCapabilityScope(lambda: service)
    version = service.store.get('listing').version
    query = OnlineAttributeQuery(listing_id='listing', version=version, limit=1)
    first = online_products_attributes(query, scope)
    assert first.model_dump(exclude_none=True) == service.attribute_options(query)
    assert first.page.category_id == '100'
    assert first.page.attributes[0].id == '10' and first.page.has_more
    second = online_products_attributes(query.model_copy(update={'cursor': first.page.next_cursor}), scope)
    assert second.page.attributes[0].id == '11'
    values = online_products_attributes(query.model_copy(update={'attribute_id': '10', 'query': '42'}), scope)
    assert values.page.values[0].dictionary_value_id == '101'
    assert all(call[2] == '100' for call in catalog.calls if call[0] == 'definitions')
    with pytest.raises(ValidationError):
        OnlineAttributeQuery.model_validate({**query.model_dump(), 'category_id': '猜测类目'})
    with pytest.raises(BusinessCapabilityError, match='快照已更新'):
        online_products_attributes(query.model_copy(update={'version': 'old'}), scope)
    assert not writes and not service.store.jobs('yandex', 'b:c')


@pytest.mark.parametrize('entry', ['direct', 'python'])
def test_native_agent_reads_online_attribute_definitions_and_candidates(editing, tmp_path, entry):
    import asyncio
    import json
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from tests.ai_code_mode_helpers import business_returns, python_call
    from tests.test_native_agent_integration import service as chat_service, body
    from erp_web.services.ai_tool_catalog import AiToolCatalog, AiToolBindingScope

    service, _, _, writes = editing
    version = service.store.get('listing').version
    toolset = AiToolCatalog.compile((online_products_attributes,)).bind(
        toolset_id='global.chat', allowed_tools=['online_products_attributes'],
        declared_permissions={'online_product.read'},
        scope=AiToolBindingScope.from_values(OnlineProductCapabilityScope(lambda: service)),
    )
    results = []

    async def model(messages, info):
        returned = business_returns(messages)
        if returned:
            results.append(returned[-1].content)
        if len(returned) == 2:
            yield '已核实实际类目与鞋码候选。'
            return
        arguments = {'listing_id': 'listing', 'version': version, 'limit': 1}
        if returned:
            arguments.update(attribute_id=returned[-1].content['page']['attributes'][0]['id'], query='42')
        call = dict(name='online_products_attributes', tool_call_id=f'attributes-{len(returned)}',
                    json_args=json.dumps(arguments))
        yield {0: python_call(**call) if entry == 'python' else DeltaToolCall(**call)}

    ui = chat_service(tmp_path, FunctionModel(stream_function=model), toolset)
    chunks = []
    asyncio.run(ui.prepare_run(body('核实鞋码候选')).stream(chunks.append))
    assert len(results) == 2, b''.join(chunks).decode()
    assert results[0]['page']['category_id'] == '100'
    assert results[1]['page']['values'][0]['dictionary_value_id'] == '101'
    assert not writes and not service.store.jobs('yandex', 'b:c')


def test_missing_required_fields_can_be_read_and_added_without_touching_other_attributes(editing):
    service, catalog, _, writes = editing
    version = service.store.get('listing').version
    first = service.attribute_options(OnlineAttributeQuery(listing_id='listing', version=version, limit=1))
    assert first['page']['attributes'][0]['name'] == 'Размер обуви'
    assert first['page']['has_more']
    second = service.attribute_options(OnlineAttributeQuery(listing_id='listing', version=version, cursor=first['page']['next_cursor'], limit=1))
    assert second['page']['attributes'][0]['id'] == '11'
    candidates = service.attribute_options(OnlineAttributeQuery(listing_id='listing',version=version,attribute_id='10',query='42'))
    assert candidates['page']['values'][0]['value'] == '42-43'
    body = request(service, [row(10,'42-43',101),row(11,'EU',111)])
    job = service.change(body)['job']
    assert service.change(body)['job']['id'] == job['id']
    assert not writes
    service.run_once()
    assert writes == [body['changes']]
    assert service.store.job(job['id'])['status'] == 'waiting_confirmation'
    assert service.store.get('listing').content['attributes'][0]['value'] == '红'
    assert len([call for call in catalog.calls if call[0] == 'values' and call[1] == '10']) == 3


@pytest.mark.parametrize('rows, message', [
    ([row(10,'42-43')], '必须选择'),
    ([row(10,'44',101)], '无法核实'),
    ([row(10,'42-43',999)], '无法核实'),
    ([row(999,'x')], '可编辑'),
    ([row(200,'改变分组')], '可编辑'),
    ([row(16,'只读')], '可编辑'),
    ([row(10,'42-43',101),row(10,'42-43',101)], '数量'),
    ([row(12,'红',121),row(12,'红',121)], '重复'),
    ([row(13,'51',unit_id=8)], '数值'),
    ([row(13,'20',unit_id=9)], '单位'),
    ([row(13,'NaN',unit_id=8)], '数值'),
    ([row(14,'x'*21)], '长度'),
    ([{**row(14,'文字'),'parameterId':15}], '身份'),
    ([{**row(10,'42-43',101),'name':'伪造名称'}], 'Extra inputs'),
    ([{**row(10,'42-43',101),'valueId':[]}], 'valid integer'),
])
def test_invalid_attributes_never_enqueue(editing, rows, message):
    service, _, _, writes = editing
    with pytest.raises(ValueError, match=message):
        service.change(request(service, rows))
    assert not service.store.jobs('yandex','b:c')
    assert not writes


def test_schema_and_versions_rechecked_before_dispatch(editing):
    service, catalog, _, writes = editing
    body = request(service,[row(10,'42-43',101)])
    job = service.change(body)['job']
    catalog.options['10'] = []
    service.run_once()
    assert not writes
    assert service.store.job(job['id'])['status'] == 'failed'
    assert not service.store.job(job['id'])['dispatched']
    with pytest.raises(OnlineConflict):
        service.attribute_options(OnlineAttributeQuery(listing_id='listing',version='old'))


def test_custom_values_units_multivalue_and_ai_approval_share_validation(editing):
    service, _, _, _ = editing
    rows = [row(12,'红',121),row(12,'蓝',122),row(13,'20',unit_id=8),row(15,'特别色')]
    body = request(service,rows)
    snapshot = _change_snapshot(OnlineChange.model_validate({k:v for k,v in body.items() if k != 'idempotency_key'}),
                                OnlineProductCapabilityScope(service=lambda:service))
    assert snapshot.canonical_payload['request']['changes']['attributes'] == rows


def test_yandex_payload_preserves_unedited_multivalues_and_replaces_changed_set(editing, monkeypatch):
    service, _, listing, _ = editing
    adapter = object.__new__(YandexOnlineAdapter)
    adapter.token, adapter.business = 'test', 'b'
    payloads = []
    monkeypatch.setattr('erp_web.runtime_units.online_yandex.api.update_yandex_offer_mapping', lambda *args:payloads.append(args[-1]) or {'status':'OK'})
    changes = [row(10,'42-43',101),row(11,'EU',111),row(14,'新说明')]
    before = deepcopy(listing.content)
    adapter.write(listing,'content','global',{'attributes':changes})
    assert payloads[0]['marketCategoryId'] == 100
    assert [r for r in payloads[0]['parameterValues'] if r['parameterId']==12] == [
        {'parameterId':12,'value':'红','valueId':121},{'parameterId':12,'value':'蓝','valueId':122}]
    assert [r['value'] for r in payloads[0]['parameterValues'] if r['parameterId']==14] == ['新说明']
    assert all('id' not in r and 'name' not in r for r in payloads[0]['parameterValues'])
    assert listing.content == before
    command = ChangeRequest.model_validate(request(service,[row(12,'蓝',122),row(12,'红',121)]))
    assert confirmation(listing,command)['attributes']
    listing.content['attributes'] = [row(12,'红',121)]
    assert not confirmation(listing,command)['attributes']


def test_empty_old_snapshot_exposes_attributes_without_rewriting_data(editing):
    service, _, listing, _ = editing
    listing.content['attributes'] = []
    listing.capabilities['content'].fields = ['title']
    service.store.save(listing)
    assert 'attributes' in service.detail('listing')['item']['capabilities']['content']['fields']
    assert service.attribute_options(OnlineAttributeQuery(listing_id='listing',version=service.store.get('listing').version))['page']['attributes']


def test_route_binds_options_to_listing_and_rejects_untrusted_category(editing, monkeypatch):
    from erp_web.facades import online_product_facade
    from erp_web.http_route_units.online_product_routes import GET_HANDLERS
    service, _, _, _ = editing
    monkeypatch.setattr(online_product_facade,'get_context',lambda:SimpleNamespace(online_products=service))
    query = {'listing_id':'listing','version':service.store.get('listing').version,'limit':'1'}
    assert GET_HANDLERS['/api/online-products/attributes'](query)['page']['category_id'] == '100'
    with pytest.raises(ValueError):
        GET_HANDLERS['/api/online-products/attributes']({**query,'category_id':'evil'})


def test_large_dictionary_ids_round_trip_without_javascript_rounding(editing, monkeypatch):
    service, catalog, listing, _ = editing
    big = '18446744073709551614'
    catalog.options['10'] = [(big,'42-43')]
    listing.content['attributes'].append(row(10,'42-43',int(big)))
    service.store.save(listing)
    result = service.attribute_options(OnlineAttributeQuery(listing_id='listing',version=service.store.get('listing').version))
    assert next(r for r in result['current_values'] if r['id']=='10')['valueId'] == big
    command = ChangeRequest.model_validate(request(service,[row(10,'42-43',big)]))
    service.validate_change(command)
    assert confirmation(listing, command)['attributes']
    adapter = object.__new__(YandexOnlineAdapter)
    adapter.token, adapter.business = 'test', 'b'
    payloads = []
    monkeypatch.setattr('erp_web.runtime_units.online_yandex.api.update_yandex_offer_mapping',lambda *args:payloads.append(args[-1]) or {})
    adapter.write(listing,'content','global',command.changes)
    assert next(r for r in payloads[0]['parameterValues'] if r['parameterId']==10)['valueId'] == int(big)


def test_stale_definitions_and_changed_account_prevent_editing(editing, monkeypatch):
    service, catalog, _, writes = editing
    catalog.definition = catalog.definition.model_copy(update={'cache':catalog.definition.cache.model_copy(update={'stale':True})})
    with pytest.raises(ValueError,match='过期'):
        service.change(request(service,[row(10,'42-43','101')]))
    monkeypatch.setattr(service.context.config,'load_store_config',lambda:{'yandex':{'business_id':'other','campaign_id':'c'}})
    with pytest.raises(OnlineConflict):
        service.attribute_options(OnlineAttributeQuery(listing_id='listing',version=service.store.get('listing').version))
    assert not writes

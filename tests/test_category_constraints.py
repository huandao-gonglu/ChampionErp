"""官方规则到编辑摘要、AI 写入、发布和在线修改的统一值限制。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from erp_web.runtime_units.category_attribute_updates import AttributeUpdateValidation
from erp_web.runtime_units.category_definition_support import definition_from_legacy_attributes, public_attribute_summary
from erp_web.runtime_units.publish_yandex import _compile_parameter_values, build_yandex_publish_payload
from erp_web.runtime_units.publish_validation import validate_yandex_draft
from erp_web.schemas.category_constraints import attribute_value_limits, category_attribute_constraint_error
from erp_web.schemas.online_products import OnlineListing
from erp_web.services.online_product_attributes import validate_yandex_attributes
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.test_yandex_publish import _config, _record, _validatable_product, _ready_store_currency  # noqa: F401


@pytest.fixture
def incident():
    return json.loads((Path(__file__).parent / 'fixtures/yandex_draft_version_limits.json').read_text())


def test_real_draft_open_enum_values_rejected_consistently_before_publish_and_online_write(incident, monkeypatch):
    """只使用已提取的属性夹具；不读取生产库、不发送网络请求。"""
    attr = incident['attribute']
    key = attr['id']
    record = _record({'required': [], 'optional': [attr]})
    record['category_id'] = incident['category_id']
    definition = definition_from_legacy_attributes(platform='yandex', site='global',
        category_id=incident['category_id'], required=[], optional=[attr])
    monkeypatch.setattr('erp_web.runtime_units.category_attribute_updates.fetch_category_record', lambda *a, **kw: record)
    validator = AttributeUpdateValidation('yandex', 'global', incident['category_id'])
    catalog = SimpleNamespace(attribute_definitions=lambda *a, **kw: definition)
    listing = OnlineListing(id='test', platform='yandex', account_id='test', remote_id='test', model='business_offer',
        snapshot={'mapping': {'marketCategoryId': int(incident['category_id'])}})
    rejected = []
    for item in incident['items']:
        value = item['value']
        text = value['values'][0]['value']
        product = _validatable_product(attributes={key: value})
        check = validate_yandex_draft(product, _config(), record)
        rows = [{'id': key, 'parameterId': int(key), 'value': text}]
        if len(text) > 100:
            rejected.append(item['sku_id'])
            assert not check['ok'], item['sku']
            assert any(error['field'] == f'attributes.{key}' for error in check['errors'])
            with pytest.raises(ValueError, match='最大长度 100'):
                build_yandex_publish_payload(product, _config(), record)
            with pytest.raises(BusinessCapabilityError, match='最大长度 100'):
                validator.validate({key: value}, sku_scope=True)
            with pytest.raises(ValueError, match='最大长度 100'):
                validate_yandex_attributes(listing, rows, catalog)
        else:
            assert check['ok'], check['errors']
            assert _compile_parameter_values({'attributes': {key: value}}, {key: attr})[0]['value'] == text
            validator.validate({key: value}, sku_scope=True)
            validate_yandex_attributes(listing, rows, catalog)
    assert len(incident['items']) == 74
    assert len(rejected) == 10


@pytest.mark.parametrize('shape', [lambda x: x, lambda x: {'value': x}, lambda x: {'values': [{'value': x}]}, lambda x: [x]])
def test_unicode_boundary_and_missing_rule_never_truncate_or_assume_100(shape):
    definition = {'constraints': {'max_length': '100'}, 'value_type': 'enum'}
    assert not category_attribute_constraint_error(definition, shape('🙂' * 100))
    assert '需减少 1' in category_attribute_constraint_error(definition, shape('🙂' * 101))
    assert not category_attribute_constraint_error({'constraints': {}}, shape('а' * 2001))


def test_multivalue_reports_bad_member_and_numeric_limits_keep_zero_and_precision():
    assert '第 2 个值' in category_attribute_constraint_error({'constraints': {'max_length': 2}}, {'values': [{'value': 'а'}, {'value': 'abc'}]})
    rule = {'value_type': 'numeric', 'constraints': {'min_value': '0', 'max_value': '0.123456789012345678'}}
    assert not category_attribute_constraint_error(rule, '0')
    assert '不能小于 0' in category_attribute_constraint_error(rule, '-1')
    assert '不能大于' in category_attribute_constraint_error(rule, '0.123456789012345679')
    assert '有限' in category_attribute_constraint_error(rule, 'NaN')
    assert attribute_value_limits(rule['constraints'])['min_value'] == '0'


def test_public_summary_retains_explicit_limits_without_platform_internals(incident):
    definition = definition_from_legacy_attributes(platform='yandex', site='global', category_id='70063695',
        required=[], optional=[incident['attribute'], {'id': 'numeric', 'constraints': {'min_value': 0, 'max_value': '1.5'}}])
    summary = public_attribute_summary(definition.optional[0], platform='yandex').model_dump()
    assert summary['max_length'] == 100
    assert summary['min_value'] is None
    assert 'constraints' not in summary and 'platform_binding' not in summary
    numeric = public_attribute_summary(definition.optional[1], platform='yandex')
    assert numeric.min_value == '0' and numeric.max_value == '1.5'


def test_official_wire_constraints_survive_provider_and_public_projection():
    from erp_web.runtime_units.yandex_category_api import _normalize_yandex_parameter
    from erp_web.runtime_units.category_providers import _yandex_parameter_definition
    wire = {'id': 23476910, 'name': 'Версия', 'type': 'ENUM', 'allowCustomValues': True,
            'constraints': {'maxLength': 100}}
    normalized = _yandex_parameter_definition(_normalize_yandex_parameter(wire))
    definition = definition_from_legacy_attributes(platform='yandex', category_id='70063695', site='global',
        required=[], optional=[normalized])
    assert definition.optional[0].constraints == {'max_length': '100'}
    assert public_attribute_summary(definition.optional[0], platform='yandex').max_length == 100
    assert category_attribute_constraint_error(normalized, {'values': [{'value': 'а' * 104}]})

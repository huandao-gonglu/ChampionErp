"""把已出现的配置幻觉作为反例；用页面事实校验答案，而非只检查工具成功。"""
from copy import deepcopy

import pytest

from scripts.business_query_acceptance import compare_answer, expected_facts, parse_answer, context_metrics


@pytest.fixture
def pages():
    return {
        'logistics.settings': {'addresses': [
            {'id': 'cel', 'enabled': True, 'is_default': False, 'rules': [{'carrier': 'CEL'}]},
            {'id': 'uni', 'enabled': True, 'is_default': False, 'rules': [{'carrier': 'UNI'}]},
            {'id': 'guoo', 'enabled': True, 'is_default': False, 'rules': [{'carrier': 'GUOO_GTA'}]},
            {'id': 'disabled', 'enabled': False, 'is_default': True, 'rules': [{'carrier': 'ATC'}]},
        ]},
        'fulfillment.settings': {'rules': []},
        'yandex.warehouses': {'pricing_options': [{'publish_warehouse_id': 12}, {'publish_warehouse_id': 34}]},
        'settings.system': {'orders_auto_sync_interval_hours': 8, 'watermark': {
            'text': '测试', 'opacity': 20, 'size_percent': 30, 'density': 5, 'color': '#737373'}},
        'settings.image_hosting': {'image_hosting': {'default_profile_id': 'main', 'profiles': [{'id': 'main'}]}},
        'settings.models': {'models': [{'id': 'api', 'enabled': True}, {'id': 'disabled', 'enabled': False}]},
        'settings.model_bindings': {'global.chat': {'model_id': 'api'}},
        'settings.product_research': {'search_defaults': {'search_mode': 'target_only', 'target_markets': ['amazon-us']},
                                     'search_providers': [{'id': 'source', 'enabled': True}]},
    }


def test_correct_complete_answers_are_checked_against_current_page_facts(pages):
    expected = expected_facts(pages)
    for answer in expected.values():
        assert compare_answer(answer, answer) == []
    assert expected['forwarders']['default_address_ids'] == []
    assert expected['forwarders']['enabled_carriers'] == ['CEL', 'GUOO_GTA', 'UNI']
    changed = deepcopy(pages)
    changed['logistics.settings']['addresses'][1]['enabled'] = False
    assert compare_answer(expected['forwarders'], expected_facts(changed)['forwarders'])


@pytest.mark.parametrize('case,field,bad', [
    ('forwarders', 'enabled_carriers', ['CEL']),
    ('forwarders', 'enabled_address_ids', ['cel', 'uni']),
    ('forwarders', 'no_forwarder_configured', True),
    ('forwarders', 'default_address_ids', ['disabled']),
    ('forwarders', 'realtime_verified', True),
    ('warehouse_roles', 'local_receiving_filters_pricing', True),
    ('hosting', 'realtime_verified', True),
    ('models', 'enabled_model_ids', ['api', 'disabled']),
    ('system', 'orders_auto_sync_interval_hours', 5),
])
def test_known_business_errors_cannot_pass_even_when_the_model_run_finishes(pages, case, field, bad):
    expected = expected_facts(pages)[case]
    answer = {**expected, field: bad}
    assert any(item['field'] == field for item in compare_answer(answer, expected))


def test_checker_rejects_missing_duplicate_and_mistyped_facts(pages):
    expected = expected_facts(pages)['forwarders']
    assert compare_answer({}, expected)
    assert compare_answer({**expected, 'fulfillment_rule_count': False}, expected)
    assert compare_answer({**expected, 'enabled_carriers': ['CEL', 'UNI', 'GUOO_GTA', 'CEL']}, expected)
    assert compare_answer({**expected, 'enabled_carriers': ['UNI', 'GUOO_GTA', 'CEL']}, expected) == []
    assert parse_answer('说明\n```json\n{"value": 1}\n```') == {'value': 1}
    with pytest.raises(ValueError):
        parse_answer('我已经读取成功')


def test_context_metrics_keep_actual_usage_separate_from_character_counts():
    metrics = context_metrics([
        {'kind': 'request', 'instructions': '固定说明', 'parts': [{'part_kind': 'user-prompt', 'content': '问题'}]},
        {'kind': 'response', 'usage': {'input_tokens': 100, 'output_tokens': 10}, 'parts': []},
        {'kind': 'request', 'instructions': '固定说明', 'parts': [{'part_kind': 'tool-return', 'content': {'data': [1]}}]},
        {'kind': 'response', 'usage': {'input_tokens': 120, 'output_tokens': 20}, 'parts': []},
    ])
    assert metrics['peak_input_tokens'] == 120 and metrics['sum_input_tokens'] == 220
    assert metrics['instruction_chars'] == 4 and metrics['model_requests'] == 2
    assert metrics['message_part_chars']['tool-return'] > 0

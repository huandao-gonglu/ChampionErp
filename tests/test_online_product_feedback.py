"""在线属性名称、平台反馈与业务版本的边界。"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from erp_web.runtime_units import online_yandex
from erp_web.runtime_units.online_change_confirmation import yandex_change
from erp_web.runtime_units.online_yandex_read import read_batch
from erp_web.schemas.online_products import ChangeRequest, OnlineListing, PlatformIssue, snapshot_version
from tests.test_yandex_online_sync import Platform, adapter, service_for


def with_card(platform, card):
    original = platform.request
    def request(path, body=None, **kwargs):
        if path.endswith('/offer-cards'):
            return {"result": {"offerCards": [{"offerId": sku, **card} for sku in body['offerIds']]}}
        return original(path, body, **kwargs)
    platform.request = request


def test_sync_resolves_attribute_names_once_per_category_and_exposes_feedback(monkeypatch):
    platform = Platform(2)
    for row in platform.rows:
        row['offer']['marketCategoryId'] = 76508560
    with_card(platform, {
        'cardStatus': 'HAS_CARD_CAN_UPDATE_ERRORS',
        'parameterValues': [{'parameterId': 57046341, 'value': 'Материал: Плюш\nТип: Игрушка'}],
        'errors': [{'message': 'Неверный формат', 'comment': 'Используйте название: значение'}],
        'warnings': [{'message': 'Не доставляется', 'comment': 'Проверьте размеры упаковки'}],
    })
    definitions = Mock(return_value=SimpleNamespace(required=[], optional=[SimpleNamespace(id='57046341', name='Другие параметры')]))
    monkeypatch.setattr(online_yandex, 'get_category_catalog', lambda: SimpleNamespace(attribute_definitions=definitions))
    a = adapter(platform)
    result = read_batch(a, platform.rows, set())
    assert not result.errors and len(result.listings) == 2
    definitions.assert_called_once_with('yandex', '76508560', site='global')
    for row in result.listings:
        assert row.content['attributes'][0]['name'] == 'Другие параметры'
        assert row.content['attributes'][0]['parameterId'] == 57046341
        assert row.content['attributes'][0]['value'] == 'Материал: Плюш\nТип: Игрушка'
        assert [(i.severity, i.message, i.comment) for i in row.platform_issues] == [
            ('error', 'Неверный формат', 'Используйте название: значение'),
            ('warning', 'Не доставляется', 'Проверьте размеры упаковки'),
        ]
        assert row.errors == [] and row.details_state == 'ready'
        public = row.model_dump(exclude={'snapshot'})
        assert public['platform_issues'][0]['message'] == 'Неверный формат'
        assert 'snapshot' not in public


@pytest.mark.parametrize('missing_category', [False, True])
def test_name_query_failure_keeps_attribute_values_and_card_feedback(monkeypatch, missing_category):
    platform = Platform(1)
    if not missing_category:
        platform.rows[0]['offer']['marketCategoryId'] = 76508560
    with_card(platform, {'parameterValues': [{'parameterId': 57046341, 'value': 'Исходный текст'}],
                         'warnings': [{'message': 'Предупреждение'}]})
    definitions = Mock(side_effect=TimeoutError('类目查询超时'))
    monkeypatch.setattr(online_yandex, 'get_category_catalog', lambda: SimpleNamespace(attribute_definitions=definitions))
    result = read_batch(adapter(platform), platform.rows, set())
    assert not result.errors
    row = result.listings[0]
    assert row.content['attributes'][0]['value'] == 'Исходный текст'
    assert row.content['attributes'][0]['name'] == ''
    assert row.content['attribute_names_error']
    assert row.platform_issues[0].message == 'Предупреждение'
    assert row.details_state == 'ready' and not row.errors
    assert definitions.call_count == (0 if missing_category else 1)


def test_display_names_and_feedback_do_not_conflict_with_business_content():
    platform = Platform(1)
    with_card(platform, {'parameterValues': [{'parameterId': 1, 'value': 'Плюш'}]})
    row = adapter(platform).read('sku-0')
    version = snapshot_version(row)
    row.content['attributes'][0]['name'] = 'Материал'
    row.content['attribute_names_error'] = '名称查询失败'
    row.platform_issues = [PlatformIssue(severity='warning', message='Предупреждение')]
    assert snapshot_version(row) == version
    row.content['attributes'][0]['value'] = 'Хлопок'
    assert snapshot_version(row) != version
    row.platform = 'mercadolibre'
    version = snapshot_version(row)
    row.content['attributes'][0]['name'] = 'Material'
    assert snapshot_version(row) != version


def test_old_persisted_snapshot_without_feedback_still_loads():
    row = adapter(Platform(1)).read('sku-0')
    data = row.model_dump(exclude={'platform_issues'})
    assert OnlineListing.model_validate(data).platform_issues == []


def test_sync_missing_target_card_is_a_read_failure_and_cannot_clear_saved_feedback():
    platform = Platform(2)
    original = platform.request
    def request(path, body=None, **kwargs):
        if path.endswith('/offer-cards'):
            return {'result': {'offerCards': [{'offerId': 'sku-0', 'warnings': [{'message': 'Предупреждение'}]}]}}
        return original(path, body, **kwargs)
    platform.request = request
    result = read_batch(adapter(platform), platform.rows, set())
    assert [row.remote_id for row in result.listings] == ['sku-0']
    assert result.listings[0].platform_issues[0].message == 'Предупреждение'
    assert set(result.errors) == {'sku-1'}
    assert '已保留原记录' in result.errors['sku-1']


def test_content_confirmation_updates_feedback_and_preserves_attribute_names():
    platform = Platform(1)
    with_card(platform, {'cardStatus': 'HAS_CARD_CAN_UPDATE',
                         'parameterValues': [{'parameterId': 1, 'value': 'Плюш'}],
                         'warnings': [{'message': 'Предупреждение'}]})
    a = adapter(platform)
    row = a.read('sku-0')
    row.content['attributes'][0]['name'] = 'Материал'
    with_card(platform, {'cardStatus': 'HAS_CARD_CAN_UPDATE_PROCESSING',
                         'parameterValues': [{'parameterId': 1, 'value': 'Хлопок'}],
                         'errors': [{'message': 'Обновление не принято'}]})
    a.request = platform.request
    request = ChangeRequest(listing_id=row.id, version=snapshot_version(row), operation='content', scope_id='global',
                            changes={'attributes': [{'id': '1', 'parameterId': 1, 'value': 'Хлопок'}]}, idempotency_key='feedback-test')
    fresh = yandex_change(a, row, request)
    assert fresh.content['attributes'][0]['name'] == 'Материал'
    assert fresh.content['attributes'][0]['value'] == 'Хлопок'
    assert fresh.raw_sub_status == ['HAS_CARD_CAN_UPDATE_PROCESSING']
    assert [(issue.severity, issue.message) for issue in fresh.platform_issues] == [('error', 'Обновление не принято')]
    assert row.platform_issues[0].severity == 'warning'


def test_active_product_with_warning_is_counted_as_needing_attention():
    platform = Platform(2)
    with_card(platform, {'warnings': [{'message': 'Проверьте размеры'}]})
    a = adapter(platform)
    service = service_for(a)
    try:
        row = a.read('sku-0')
        row.raw_status, row.sale_state = 'PUBLISHED', 'active'
        service.store.save(row)
        result = service.list('yandex')
        assert result['summary']['active'] == 1
        assert result['summary']['attention'] == 1
    finally:
        service.close()

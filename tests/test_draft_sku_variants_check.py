"""用延长杆和套装规格走主对话工具，核对不同类目配置下的实际保存结果。"""

import re
from dataclasses import replace
from unittest.mock import Mock

import pytest

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units import category_attribute_updates, product_capabilities
from erp_web.runtime_units.category_definition_support import definition_to_legacy_record
from erp_web.schemas.category_definition import CategoryAttributeDefinition, CategoryDefinition
from tests.test_draft_edit_capabilities import execution


@pytest.fixture
def variants(monkeypatch):
    app = get_context()
    names = [f'{drive}方头【{length}mm】' for drive, lengths in [
        ('1/4', [50, 75, 100, 150]), ('3/8', [75, 125, 150, 250]),
        ('1/2', [75, 125, 250, 300, 350, 395, 450, 500, 575]), ('3/4', [200]),
    ] for length in lengths] + ['14件套', '25件套', '41件套']
    product = {'product_id': 'extension-bars', 'name': '延长杆', 'sku_items': [
        {'id': f's{i}', 'active': True, 'name': name, 'options': {'规格': name}} for i, name in enumerate(names)
    ], 'drafts': {'yandex': {
        'title': '延长杆', 'category_id': '62149272', 'target_sites': [{'platform': 'yandex', 'site': 'global', 'category_id': '62149272'}],
        'grouping': {'mode': 'combined', 'name': '延长杆'},
        'sku_items': [{'sku_id': f's{i}', 'selected': True, 'sku': f'BAR-{i}'} for i in range(len(names))],
    }}}
    saved = app.products.save_product(product)
    draft_id = saved['drafts']['yandex']['draft_id']
    ctx = replace(execution(draft_id), permissions=frozenset({'product.read', 'product.write', 'draft.write'}))
    bindings = build_global_chat_toolset(app).bindings
    def call(tool, **args):
        return bindings[tool].executor({'draft_id': draft_id, 'platform': 'yandex', 'site': 'global', **args}, ctx)
    catalog = Mock()
    monkeypatch.setattr(product_capabilities, 'get_category_catalog', lambda: catalog)
    def set_fields(*, dimensions):
        def field(id, name, variant=False, required=False):
            return CategoryAttributeDefinition(id=id, name=name, variation_role='variant' if variant else '', required=required)
        length = field('23674610', '长度', dimensions, dimensions)
        catalog.attribute_definitions.return_value = CategoryDefinition(
            platform='yandex', category_id='62149272', required=(length,) if dimensions else (),
            optional=(field('200', '组名'), field('14805583', '每包数量', True), field('43994630', '方头', dimensions))
                     + (() if dimensions else (length,)),
        )
        catalog.refresh_yandex_variant_settings.return_value = catalog.attribute_definitions.return_value
        monkeypatch.setattr(category_attribute_updates, 'fetch_category_record',
                            lambda *a, **kw: definition_to_legacy_record(catalog.attribute_definitions.return_value))
    return app, draft_id, call, set_fields, catalog


def test_checked_saved_combinations_follow_target_fields_and_report_evidence_gaps(variants):
    app, draft_id, call, set_fields, _ = variants
    set_fields(dimensions=False)
    empty = call('draft_sku_variants_check')
    assert empty['stored_sku_count_by_field'] == {'14805583': 0}
    assert empty['issues'][0]['code'] == 'SKU_VARIATION_ATTRIBUTES_EMPTY'
    read = call('draft_attributes_read', scope='sku')
    # 模拟已确认单杆单支销售；套装组成数量没有对应的销售单位依据。
    rods = []
    for sku in read['skus']:
        match = re.fullmatch(r'(.+)方头【(\d+)mm】', sku['options']['规格'])
        if match:
            rods.append((sku['sku_id'], match[1], match[2]))
    assert len(rods) == 18
    receipt = call('draft_changes_apply', expected_updated_at=read['updated_at'], category_id='62149272',
                   changes=[{'sku_id': sku_id, 'attributes': {'14805583': '1'}} for sku_id, _, _ in rods])
    limited = call('draft_sku_variants_check')
    assert limited['updated_at'] == receipt['updated_at']
    assert limited['stored_sku_count_by_field'] == {'14805583': 18}
    assert limited['status'] == 'issues' and limited['sku_count'] == 21
    assert limited['writable_variant_fields'] == {'14805583': '每包数量'}
    assert [len(issue['affected_sku_ids']) for issue in limited['issues']] == [18, 3]
    set_fields(dimensions=True)
    second = call('draft_changes_apply', expected_updated_at=receipt['updated_at'], category_id='62149272',
                  changes=[{'sku_id': sku_id, 'attributes': {'23674610': length, '43994630': drive}} for sku_id, drive, length in rods])
    checked = call('draft_sku_variants_check')
    assert checked['updated_at'] == second['updated_at']
    assert [issue['affected_sku_ids'] for issue in checked['issues']] == [['s18', 's19', 's20']]
    assert checked['missing_required_by_field'] == {'23674610': ['s18', 's19', 's20']}
    draft = app.products.draft_record(draft_id)
    for row in draft['sku_items'][18:]:
        assert not row['attributes_by_target'].get('yandex:global')
        row['selected'] = False
    app.products.save_draft_content(draft)
    result = call('draft_sku_variants_check')
    assert result['sku_count'] == 18 and result['status'] == 'distinct' and not result['issues']


@pytest.mark.parametrize('case', ['single', 'separate'])
def test_skips_group_comparison_for_independent_listings(variants, case):
    app, draft_id, call, _, catalog = variants
    draft = app.products.draft_record(draft_id)
    if case == 'separate':
        draft['grouping']['mode'] = 'separate'
    else:
        for row in draft['sku_items'][1:]:
            row['selected'] = False
    app.products.save_draft_content(draft)
    assert call('draft_sku_variants_check')['status'] == 'not_applicable'
    catalog.attribute_definitions.assert_not_called()

def test_yandex_guidance_names_only_confirmed_common_fields_and_stops_after_save(variants):
    _, _, call, set_fields, catalog = variants
    set_fields(dimensions=False)
    candidate_ids = ['23674610', '43994630', '200', '14805583', 'unknown']
    assert not call('draft_sku_variants_check')['settings_guidance']
    catalog.refresh_yandex_variant_settings.assert_not_called()
    result = call('draft_sku_variants_check', yandex_variant_field_ids=candidate_ids)
    for expected in ['Настройки работы с карточками', '62149272', 'Добавить признак', '方头', '长度', 'Сохранить', '新旧变体组']:
        assert expected in result['settings_guidance']
    assert 'unknown' not in result['settings_guidance']
    assert '每包数量' not in result['settings_guidance']
    set_fields(dimensions=True)
    updated = call('draft_sku_variants_check', yandex_variant_field_ids=candidate_ids)
    assert updated['writable_variant_fields']['23674610'] == '长度'
    assert not updated['settings_guidance']
    assert catalog.refresh_yandex_variant_settings.call_count == 2


def test_guidance_uses_the_selected_category_and_requires_a_real_collision(variants):
    app, draft_id, call, _, catalog = variants
    draft = app.products.draft_record(draft_id)
    target = draft['target_sites'][0]
    target['category_id'] = draft['category_id'] = 'shoes'
    for i, row in enumerate(draft['sku_items']):
        row['selected'] = i < 2
        row['attributes_by_target']['yandex:global'] = {'size': '42'}
    app.products.save_draft_content(draft)
    def field(key, name, role=''):
        return CategoryAttributeDefinition(id=key, name=name, variation_role=role)
    catalog.refresh_yandex_variant_settings.return_value = CategoryDefinition(
        platform='yandex', category_id='shoes', category_path='Обувь',
        optional=(field('200', '组名'), field('size', 'Размер', 'variant'), field('color', 'Цвет')),
    )
    def check():
        return call('draft_sku_variants_check', yandex_variant_field_ids=['color'])
    guidance = check()['settings_guidance']
    assert 'Обувь' in guidance and 'Цвет' in guidance
    draft = app.products.draft_record(draft_id)
    draft['sku_items'][1]['attributes_by_target']['yandex:global']['size'] = '43'
    app.products.save_draft_content(draft)
    result = check()
    assert result['status'] == 'distinct' and not result['settings_guidance']

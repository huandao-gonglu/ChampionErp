"""采集入库与发货包装完整性分别判断，缺失资料不伪造。"""
from dataclasses import replace

import pytest

from erp_web.runtime_units import source_collect_workflows as workflows
from erp_web.runtime_units.collect_helpers import finalize_collect_diagnostics, snapshot_field_flags
from erp_web.runtime_units.source_sites import source_site


def test_1688_missing_package_is_not_a_collection_failure():
    flags = {'title_found': True, 'images_found_count': 5, 'dimensions_found': False, 'weight_found': False}
    spec = source_site('1688')
    assert spec.quality_reason(flags) == ''
    assert spec.quality_reason({**flags, 'images_found_count': 0}) == 'NO_IMAGES'
    assert spec.quality_reason({**flags, 'title_found': False}) == 'NO_TITLE'
    assert spec.quality_reason(flags, 'CAPTCHA') == 'CAPTCHA'


def test_diagnostics_check_all_sku_packages_without_copying_first_sku():
    package = {'length_cm': '30', 'width_cm': '20', 'height_cm': '10', 'weight_kg': '0.5'}
    source = {'skus': [{'id': 'one', 'package_dimensions': dict(package)}, {'id': 'two', 'package_dimensions': dict(package)}]}
    assert snapshot_field_flags(source)['dimensions_found'] is True
    assert snapshot_field_flags(source)['weight_found'] is True
    source['skus'][1]['package_dimensions']['height_cm'] = ''
    source['skus'][1]['package_dimensions']['weight_kg'] = '0'
    assert snapshot_field_flags(source)['dimensions_found'] is False
    assert snapshot_field_flags(source)['weight_found'] is False
    assert source['skus'][1]['package_dimensions']['height_cm'] == ''


@pytest.mark.parametrize('value', ['', '0', '-1', 'NaN', 'Infinity', '30-40'])
def test_incomplete_or_non_exact_source_package_remains_missing(value):
    source = {'dimensions': {'length_cm': value, 'width_cm': '20', 'height_cm': '10'}, 'weight_kg': value}
    flags = snapshot_field_flags(source)
    assert flags['dimensions_found'] is False
    assert flags['weight_found'] is False


def test_successful_collection_reports_package_followup_but_preserves_real_failures():
    source = {'title': '休闲鞋', 'images': ['https://example.test/shoe.jpg'], 'skus': [{'id': 'one'}]}
    success = finalize_collect_diagnostics({'success': True, 'error_code': ''}, source, '1688')
    assert {'dimensions', 'weight'}.issubset(success['missing_fields'])
    assert 'SKU 页补齐后再核价、发布' in success['next_action']
    assert success['error_code'] == ''
    failed = finalize_collect_diagnostics({'success': False, 'error_code': '1688_CAPTCHA_REQUIRED'}, source, '1688')
    assert '验证' in failed['next_action']
    assert '采集已完成' not in failed['next_action']


def test_batch_saves_skus_without_package_and_returns_success_with_followup(monkeypatch):
    url = 'https://detail.1688.com/offer/1060853411600.html'
    source = {'title': '休闲鞋', 'source_url': url, 'source_platform': '1688', 'price': '20', 'currency': 'CNY', 'images': ['https://example.test/shoe.jpg'],
              'skus': [{'id': 'red-39', 'name': '红色 / 39', 'price': '20', 'package_dimensions': {}}, {'id': 'black-40', 'name': '黑色 / 40', 'price': '25', 'package_dimensions': {}}]}
    spec = replace(source_site('1688'), parser=lambda *_: {'source': source})
    monkeypatch.setattr(workflows, 'source_site', lambda _: spec)
    monkeypatch.setattr(workflows, 'fetch_page_snapshot_with_browser_session', lambda *_args, **_kwargs: {'html': '<html><body>休闲鞋</body></html>', 'html_snapshot_path': '/tmp/shoe.html', 'url': url})
    monkeypatch.setattr(workflows, 'normalize_collect_source_images', lambda source, *_args: source)
    result = workflows.collect_batch_products([url], platforms=[])
    row = result['items'][0]
    assert row['ok'] is True and row['status'] == 'success'
    assert row['error_code'] == ''
    assert '包装长宽高' in row['next_action'] and '包装重量' in row['next_action']
    assert len(row['product']['sku_items']) == 2
    assert all(not any(sku['package_dimensions'].values()) for sku in row['product']['sku_items'])

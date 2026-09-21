"""模板更新的离线回归：只在系统临时目录制造样例，不调用账号或平台。"""
import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from erp_web.international_shipping.models import Package
from erp_web.international_shipping.tables import ozon_shipping
from erp_web.international_shipping.tariff_store import OZ_HEADERS, YA_HEADERS, col_name, compare, import_template, load_active, parse_template, read_current, read_version

OZ = {'group': 'Extra Small', 'service': 'Economy', 'carrier': 'Xingyuan', 'name': 'Xingyuan Economy Extra Small', 'rate': '¥ 3.37 + ¥ 0.0281/1 g', 'battery': '允许', 'liquid': '允许', 'dimensions': '边长总和 ≤ 90 cm, 长边 ≤ 60 cm', 'min_g': 1, 'max_g': 500, 'price_cny': '0.01 - 135', 'weight_type': '实际重量', 'divisor': '-'}
YA = {'country': '中国大陆', 'transport': '陆运', 'service': 'Economy', 'group': 'Extra Small', 'carrier': 'CEL', 'battery': '是', 'rate': '.287', 'fixed': 78, 'weight_type': '实际重量', 'step_g': 1}
NOTE = '严禁通过 Extra Small 渠道邮寄价格超过 1500 卢布且重量超过 500 克的包裹'


def xlsx(path, platform='ozon', rows=None, order=None, row_shift=0, labels=None, note=NOTE):
    headers = OZ_HEADERS if platform == 'ozon' else YA_HEADERS
    keys = order or list(headers)
    labels = labels or {}
    rows = rows if rows is not None else [OZ if platform == 'ozon' else YA]
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    def cell(col, row, v):
        address = col_name(col) + str(row)
        if isinstance(v, dict):
            return f'<c r="{address}" t="str"><f>{escape(v["formula"])}</f><v>{escape(v.get("cached", ""))}</v></c>'
        return f'<c r="{address}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'
    header_row = row_shift + 1
    parts = [f'<row r="{header_row}">' + ''.join(cell(i, header_row, labels.get(k, headers[k][0])) for i, k in enumerate(keys, 1)) + '</row>']
    for n, row in enumerate(rows, header_row + 1):
        parts.append(f'<row r="{n}">' + ''.join(cell(i, n, row.get(k, '')) for i, k in enumerate(keys, 1)) + '</row>')
    if platform == 'yandex':
        n = header_row + len(rows) + 2
        parts.append(f'<row r="{n}">' + cell(len(keys) + 2, n, note) + '</row>')
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="新版费率表" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{ns}"><sheetData>{"".join(parts)}</sheetData></worksheet>')


class TariffUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pricing-tariff-tests-')
        self.root = Path(self.tmp.name)
        self.store = self.root / 'tariffs'
        self.oz = self.root / 'ozon.xlsx'; self.ya = self.root / 'yandex.xlsx'
        xlsx(self.oz); xlsx(self.ya, 'yandex')

    def tearDown(self):
        self.tmp.cleanup()

    def import_both(self):
        import_template(self.oz, 'ozon', self.store)
        import_template(self.ya, 'yandex', self.store)

    def test_rate_add_remove_change_and_preview_does_not_activate(self):
        old_other = {**OZ, 'carrier': 'ATC', 'name': 'ATC Economy Extra Small'}
        xlsx(self.oz, rows=[OZ, old_other]); first = import_template(self.oz, 'ozon', self.store)
        before = (self.store / 'current.json').read_bytes()
        rows = [{**OZ, 'rate': '¥ 5 + ¥ 0.0281/1 g'}, {**OZ, 'carrier': 'CEL', 'name': 'CEL Economy Extra Small'}]
        xlsx(self.oz, rows=rows)
        preview = import_template(self.oz, 'ozon', self.store, preview=True)
        self.assertEqual((len(preview['added']), len(preview['removed']), len(preview['changed'])), (1, 1, 1))
        self.assertEqual(preview['changed'][0]['fields']['fixed'], {'before': '3.37', 'after': '5'})
        self.assertEqual((self.store / 'current.json').read_bytes(), before)
        self.assertIn('上涨', Path(preview['report']).read_text())
        live = import_template(self.oz, 'ozon', self.store)
        self.assertEqual(live['after_version'], preview['after_version'])
        self.assertNotEqual(first['after_version'], live['after_version'])
        self.assertTrue((self.store / 'versions' / (first['after_version'] + '.json')).exists())

    def test_header_move_and_column_reorder_do_not_report_rate_change(self):
        old, _ = parse_template(self.oz, 'ozon')
        xlsx(self.oz, order=list(reversed(OZ_HEADERS)), row_shift=8)
        new, _ = parse_template(self.oz, 'ozon')
        diff = compare(old, new)
        self.assertFalse(diff['changed'] or diff['added'] or diff['removed'])
        self.assertNotEqual(old['version'], new['version'])

    def test_same_bytes_renamed_are_idempotent_and_keep_import_history(self):
        first = import_template(self.oz, 'ozon', self.store)
        renamed = self.root / 'renamed.xlsx'; renamed.write_bytes(self.oz.read_bytes())
        second = import_template(renamed, 'ozon', self.store)
        self.assertEqual(first['after_version'], second['after_version'])
        self.assertEqual(len(list((self.store / 'versions').glob('*.json'))), 1)
        self.assertNotEqual(first['report'], second['report'])
        self.assertTrue(Path(first['report']).exists())

    def test_unsupported_unit_cannot_replace_current(self):
        import_template(self.oz, 'ozon', self.store); current = (self.store / 'current.json').read_bytes()
        xlsx(self.oz, rows=[{**OZ, 'rate': '¥ 3 + ¥ 10/1 lb'}])
        with self.assertRaisesRegex(ValueError, '公式或单位'): import_template(self.oz, 'ozon', self.store)
        self.assertEqual((self.store / 'current.json').read_bytes(), current)
        self.assertEqual(len(list((self.store / 'versions').glob('*.json'))), 1)

    def test_recognized_kg_unit_is_converted_to_grams(self):
        xlsx(self.oz, rows=[{**OZ, 'rate': '¥ 3.37 + ¥ 28.1/1 kg'}])
        pack, _ = parse_template(self.oz, 'ozon')
        self.assertEqual(pack['content']['rules'][0]['per_g'], '0.0281')

    def test_volume_divisor_change_is_imported_and_changes_billing(self):
        row = {**OZ, 'group': 'Big', 'name': 'Xingyuan Economy Big', 'min_g': 2001, 'max_g': 30000, 'dimensions': '边长总和 ≤ 310 cm, 长边 ≤ 150 cm', 'weight_type': '最大实际重量与体积重量', 'divisor': '长 × 宽 × 高 (cm) ÷ 6000'}
        xlsx(self.oz, rows=[row]); pack, _ = parse_template(self.oz, 'ozon')
        route = pack['content']['rules'][0]
        self.assertEqual(route['volumetric_divisor_kg'], '6000')
        self.assertEqual(ozon_shipping(Package(3000, 80, 50, 30), route)[1], 20000)

    def test_new_fee_header_and_missing_header_are_rejected(self):
        xlsx(self.oz, labels={'rate': '燃油附加费'})
        with self.assertRaisesRegex(ValueError, '表头'): parse_template(self.oz, 'ozon')
        xlsx(self.ya, 'yandex', labels={'rate': '重量费/磅，卢布'})
        with self.assertRaisesRegex(ValueError, '表头'): parse_template(self.ya, 'yandex')

    def test_duplicate_route_and_reversed_range_are_rejected(self):
        xlsx(self.oz, rows=[OZ, OZ])
        with self.assertRaisesRegex(ValueError, '重复'): parse_template(self.oz, 'ozon')
        xlsx(self.oz, rows=[{**OZ, 'min_g': 600, 'max_g': 500}])
        with self.assertRaisesRegex(ValueError, '颠倒'): parse_template(self.oz, 'ozon')

    def test_formula_is_not_evaluated_except_known_carrier_expression(self):
        # 该合成表 name 在 D 列，数据是第 2 行。
        xlsx(self.oz, rows=[{**OZ, 'carrier': {'formula': 'LEFT(D2,SEARCH(" ",D2))', 'cached': '陈旧物流商'}}])
        pack, _ = parse_template(self.oz, 'ozon')
        self.assertEqual(pack['content']['rules'][0]['carrier'], 'Xingyuan')
        xlsx(self.oz, rows=[{**OZ, 'rate': {'formula': '1+2', 'cached': '¥ 3 + ¥ 1/1 g'}}])
        with self.assertRaisesRegex(ValueError, '公式'): parse_template(self.oz, 'ozon')

    def test_future_effective_date_is_stored_but_not_activated(self):
        initial = import_template(self.oz, 'ozon', self.store)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        future = import_template(self.oz, 'ozon', self.store, effective_date=tomorrow)
        self.assertEqual(read_current(self.store)['platforms']['ozon'], initial['after_version'])
        self.assertIn('生效日未到', future['status'])
        self.assertTrue((self.store / 'versions' / (future['after_version'] + '.json')).exists())

    def test_deleted_original_file_does_not_affect_loaded_rules(self):
        self.import_both(); self.oz.unlink(); self.ya.unlink()
        self.assertEqual(len(load_active(self.store, 'ozon')['content']['rules']), 1)
        self.assertEqual(len(list((self.store / 'sources').glob('*.xlsx'))), 2)

    def test_missing_or_tampered_rules_do_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, '尚未导入'):
            load_active(self.store, 'ozon')
        self.import_both()
        version = read_current(self.store)['platforms']['ozon']
        path = self.store / 'versions' / (version + '.json')
        pack = json.loads(path.read_text()); pack['content']['rules'][0]['fixed'] = '0'
        path.write_text(json.dumps(pack))
        with self.assertRaisesRegex(ValueError, '校验失败'):
            read_version(self.store, version)

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
from erp_web.international_shipping.tariff_store import YA_HEADERS, col_name, compare, import_template, load_active, parse_template, read_current, read_version

YA = {'country': '中国大陆', 'transport': '陆运', 'service': 'Economy', 'group': 'Extra Small', 'carrier': 'CEL', 'battery': '是', 'rate': '.287', 'fixed': 78, 'weight_type': '实际重量', 'step_g': 1}
NOTE = '严禁通过 Extra Small 渠道邮寄价格超过 1500 卢布且重量超过 500 克的包裹'


def xlsx(path, platform='yandex', rows=None, order=None, row_shift=0, labels=None, note=NOTE):
    headers = YA_HEADERS
    keys = order or list(headers)
    labels = labels or {}
    rows = rows if rows is not None else [YA]
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




def test_yandex_import_preview_and_version_integrity(tmp_path):
    source = tmp_path / 'source.xlsx'
    xlsx(source)
    first = import_template(source, 'yandex', tmp_path / 'rules')
    original = load_active(tmp_path / 'rules', 'yandex')
    xlsx(source, rows=[{**YA, 'fixed': 100}])
    preview = import_template(source, 'yandex', tmp_path / 'rules', preview=True)
    assert len(preview['changed']) == 1
    assert load_active(tmp_path / 'rules', 'yandex') == original
    latest = import_template(source, 'yandex', tmp_path / 'rules')
    assert latest['after_version'] != first['after_version']
    assert read_version(tmp_path / 'rules', first['after_version']) == original


def test_yandex_headers_units_and_formulas_are_checked(tmp_path):
    import pytest
    source = tmp_path / 'source.xlsx'
    xlsx(source, labels={'rate': '重量费/磅'})
    with pytest.raises(ValueError, match='表头'): parse_template(source, 'yandex')
    xlsx(source, rows=[{**YA, 'fixed': {'formula':'1+1','cached':'2'}}])
    with pytest.raises(ValueError, match='公式'): parse_template(source, 'yandex')


def test_yandex_reordered_headers_preserve_rules(tmp_path):
    source = tmp_path / 'source.xlsx'
    xlsx(source)
    original, _ = parse_template(source, 'yandex')
    xlsx(source, order=list(reversed(YA_HEADERS)), row_shift=8)
    changed, _ = parse_template(source, 'yandex')
    assert not compare(original, changed)['changed']


def test_future_tariff_does_not_activate(tmp_path):
    source = tmp_path / 'source.xlsx'
    xlsx(source)
    result = import_template(source, 'yandex', tmp_path / 'rules', (date.today()+timedelta(days=1)).isoformat())
    assert not read_current(tmp_path / 'rules')['platforms']
    assert read_version(tmp_path / 'rules', result['after_version'])

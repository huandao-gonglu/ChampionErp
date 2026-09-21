"""独立实验的 Excel 费率导入、差异比较与版本存储；仅依赖标准库。"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import posixpath
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

SCHEMA = 1
PARSER = 'cn-fbs-1'
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
IGNORED_DIFF_FIELDS = {'source_cell', 'source_row', 'id'}
GROUPS = {'Extra Small', 'Budget', 'Small', 'Big', 'Premium Small', 'Premium Big'}


def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


def dec(value, label, positive=False):
    try:
        n = Decimal(re.sub(r'\s+', '', str(value)))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label}不是有效数字：{value}') from None
    if not n.is_finite() or n < 0 or (positive and n == 0):
        raise ValueError(f'{label}必须是有限的{"正" if positive else "非负"}数')
    return n


def numeric(value):
    return format(Decimal(str(value)).normalize(), 'f')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def col_name(index):
    text = ''
    while index:
        index, n = divmod(index - 1, 26)
        text = chr(65 + n) + text
    return text


class Workbook:
    """只读 XLSX 表格单元格。不运行宏、外部链接或任意 Excel 公式。"""
    def __init__(self, path):
        self.path = Path(path)
        if self.path.suffix.lower() != '.xlsx':
            raise ValueError('目前导入只支持 .xlsx 文件')
        self.bytes = self.path.read_bytes()
        self.sha = hashlib.sha256(self.bytes).hexdigest()
        self.sheets = []
        self.strings = []
        try:
            from io import BytesIO
            with ZipFile(BytesIO(self.bytes)) as archive:
                if 'xl/sharedStrings.xml' in archive.namelist():
                    shared = ET.fromstring(archive.read('xl/sharedStrings.xml'))
                    self.strings = [''.join(n.itertext()) for n in shared.findall('s:si', NS)]
                rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
                targets = {r.attrib['Id']: r.attrib['Target'] for r in rels if r.attrib.get('TargetMode') != 'External'}
                book = ET.fromstring(archive.read('xl/workbook.xml'))
                for sheet in book.findall('s:sheets/s:sheet', NS):
                    rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
                    target = targets[rid]
                    location = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
                    if not location.startswith('xl/'):
                        raise ValueError('工作表路径无效')
                    root = ET.fromstring(archive.read(location))
                    rows = {}
                    for row in root.findall('s:sheetData/s:row', NS):
                        values = {}
                        for cell in row.findall('s:c', NS):
                            address = cell.attrib['r']; column = re.match(r'[A-Z]+', address)[0]
                            v = cell.find('s:v', NS); text = v.text if v is not None else ''
                            typ = cell.attrib.get('t')
                            if typ == 's': text = self.strings[int(text)]
                            elif typ == 'inlineStr': text = ''.join(cell.find('s:is', NS).itertext())
                            elif text and typ not in ('str', 'e', 'b'):
                                # Excel 的数值精度为 15 位，消除 XML 内的浮点表示尾差。
                                text = numeric(format(Decimal(text), '.15g'))
                            formula = cell.find('s:f', NS)
                            values[column] = {'value': text or '', 'formula': formula.text if formula is not None else None, 'address': address}
                        rows[int(row.attrib['r'])] = values
                    self.sheets.append({'name': sheet.attrib['name'], 'rows': rows})
        except (BadZipFile, ET.ParseError, KeyError, IndexError, InvalidOperation) as exc:
            raise ValueError(f'无法读取 XLSX：{type(exc).__name__}') from None


OZ_HEADERS = {
    'group': ['评分组'], 'service': ['服务等级'], 'carrier': ['第三方物流'], 'name': ['配送方式'],
    'rate': ['费率（PUDO揽收点揽收/快递员上门揽收）'], 'battery': ['电池'], 'liquid': ['液体'],
    'dimensions': ['尺寸限制，最大（厘米）'], 'min_g': ['货件重量限制 / 最小（克）'],
    'max_g': ['货件重量限制 / 最大（克）'], 'price_cny': ['货值限制/最低-最高（人民币）'],
    'weight_type': ['计费类型'], 'divisor': ['体积重量计算方式'],
}
YA_HEADERS = {
    'country': ['国家'], 'transport': ['配送方式'], 'service': ['配送时效'], 'group': ['包裹类别'],
    'carrier': ['物流商'], 'battery': ['是否允许带电池'],
    'rate': ['重量费/公斤，卢布 (Extra Small 包裹重量费/每克)', '重量费/每克，卢布'],
    'fixed': ['每个包裹处理费，卢布'], 'weight_type': ['计费重量'], 'step_g': ['计费重量单位，克'],
}


def find_table(book, headers):
    aliases = {norm(alias): key for key, names in headers.items() for alias in names}
    matches, closest = [], (0, '')
    for sheet in book.sheets:
        for row_number, row in sheet['rows'].items():
            if row_number > 100: continue
            mapping = {}
            for column, cell in row.items():
                key = aliases.get(norm(cell['value']))
                if key:
                    if key in mapping: raise ValueError(f'{sheet["name"]} 第 {row_number} 行表头重复：{key}')
                    mapping[key] = column
            if len(mapping) > closest[0]: closest = (len(mapping), f'{sheet["name"]} 第 {row_number} 行缺少：{", ".join(sorted(set(headers) - set(mapping)))}')
            if len(mapping) == len(headers): matches.append((sheet, row_number, mapping))
    if len(matches) != 1:
        raise ValueError('无法唯一识别完整费率表。' + closest[1] + '；表头或单位变化时需调整导入规则。')
    sheet, row_number, columns = matches[0]
    ignored = ['Ozon评级', '时效限制（从PUDO揽收点到Ozon分拣中心），最长天数', '货值限制/最低-最高（卢布）', '货值限制/最低-最高（美元）', '货值限制/最低-最高（欧元）', '丢失赔偿上限，卢布', '在揽收点未被取走的已取消货件', '进口海关申报开始前取消的货件', '进口海关申报开始后取消的货件'] if headers is OZ_HEADERS else ['总运费，卢布', 'Merged']
    allowed = set(aliases) | {norm(x) for x in ignored}
    to_index = lambda col: sum((ord(c) - 64) * 26 ** i for i, c in enumerate(reversed(col)))
    start = min(to_index(c) for c in columns.values())
    for col, cell in sheet['rows'][row_number].items():
        if to_index(col) >= start and cell['value'] and norm(cell['value']) not in allowed:
            raise ValueError(f'费率区出现未识别的新表头：{cell["value"]}；需确认是否新增费用或限制')
    return matches[0]


def value(row, columns, key, carrier=False):
    cell = row.get(columns[key], {'value': '', 'formula': None, 'address': columns[key]})
    formula = cell['formula']
    if formula:
        # 原版 Ozon 的 carrier 列只有这个明确的文本公式；依据配送名称重算，避免缓存陈旧。
        f = re.fullmatch(r'LEFT\(([A-Z]+)(\d+),SEARCH\(" ",\1\2\)\)', formula, re.I)
        if not carrier or not f or f[1] != columns.get('name') or cell['address'][len(columns[key]):] != f[2]:
            raise ValueError(f'{cell["address"]} 使用了不支持的公式：请提供明确费率值或更新解析器')
        name = str(row.get(f[1], {}).get('value', ''))
        if ' ' not in name: raise ValueError('物流商公式缺少可识别的配送名称')
        return name.split(' ', 1)[0].strip()
    if str(cell['value']).startswith('#'): raise ValueError(f'{cell["address"]} 含 Excel 错误')
    return str(cell['value']).strip()


def yes_no(text, label):
    if text in ('允许', '是'): return True
    if text in ('禁止', '否'): return False
    raise ValueError(f'{label}无法识别是否允许：{text}')


def parse_ozon(book):
    sheet, header, cols = find_table(book, OZ_HEADERS)
    rules = []
    for n, row in sheet['rows'].items():
        if n <= header: continue
        get = lambda key: value(row, cols, key, carrier=(key == 'carrier'))
        group, rate_text = get('group'), get('rate')
        if (not group and not rate_text) or (group == 'переход' and rate_text == 'переход'): continue
        if group not in GROUPS: raise ValueError(f'{sheet["name"]} 第 {n} 行存在未知评分组：{group}')
        service, carrier, name = get('service'), get('carrier'), get('name')
        if service not in ('Express', 'Standard', 'Economy') or not carrier or not name:
            raise ValueError(f'第 {n} 行缺少或使用未知的物流渠道/服务')
        match = re.fullmatch(r'¥\s*([\d.]+)\s*\+\s*¥\s*([\d.]+)\s*/\s*([\d.]+)\s*(g|kg)', unicodedata.normalize('NFKC', rate_text))
        if not match: raise ValueError(f'第 {n} 行费率公式或单位不受支持：{rate_text}')
        fixed, unit_fee = dec(match[1], '固定费'), dec(match[2], '重量费')
        unit_g = dec(match[3], '计价重量单位', True) * (1000 if match[4] == 'kg' else 1)
        dimensions = re.fullmatch(r'边长总和\s*≤\s*([\d.]+)\s*cm\s*,\s*长边\s*≤\s*([\d.]+)\s*cm\s*,?', unicodedata.normalize('NFKC', get('dimensions')))
        if not dimensions: raise ValueError(f'第 {n} 行尺寸限制格式或单位不受支持')
        span = re.fullmatch(r'([\d.]+)-([\d.]+)', re.sub(r'\s+', '', get('price_cny')))
        if not span: raise ValueError(f'第 {n} 行人民币货值区间无法识别')
        low, high = dec(span[1], '最低货值'), dec(span[2], '最高货值', True)
        min_g, max_g = dec(get('min_g'), '最小重量', True), dec(get('max_g'), '最大重量', True)
        if low > high or min_g > max_g: raise ValueError(f'第 {n} 行区间上下限颠倒')
        weight_type, divisor_text = get('weight_type'), get('divisor')
        divisor = None
        if weight_type == '最大实际重量与体积重量':
            d = re.fullmatch(r'长×宽×高\(cm\)÷([\d.]+)', re.sub(r'\s+', '', unicodedata.normalize('NFKC', divisor_text)))
            if not d: raise ValueError(f'第 {n} 行体积重量计算方式无法识别')
            divisor = numeric(dec(d[1], '体积重除数', True))
        elif weight_type != '实际重量' or divisor_text not in ('', '-'):
            raise ValueError(f'第 {n} 行计费重量类型无法识别：{weight_type}')
        rules.append({'name': name, 'carrier': carrier, 'group': group, 'service': service,
            'fixed': numeric(fixed), 'per_g': numeric(unit_fee / unit_g), 'battery': yes_no(get('battery'), '电池'),
            'liquid': yes_no(get('liquid'), '液体'), 'max_sum_cm': numeric(dec(dimensions[1], '三边和', True)),
            'max_edge_cm': numeric(dec(dimensions[2], '最长边', True)), 'min_g': numeric(min_g), 'max_g': numeric(max_g),
            'price_min_cny': numeric(low), 'price_max_cny': numeric(high), 'volumetric_divisor_kg': divisor,
            'source_cell': f'{sheet["name"]}!{cols["rate"]}{n}', 'source_row': n})
    return sheet['name'], rules, {}, []


def parse_yandex(book):
    sheet, header, cols = find_table(book, YA_HEADERS)
    # 价格/重量阈值在原表的提示文字中；厘米限制来自此前核对的官方规则，单独标明来源。
    notes = book.strings + [str(c['value']) for s in book.sheets for row in s['rows'].values() for c in row.values() if not c['formula']]
    limits = set()
    for note in notes:
        if 'Extra Small' not in note: continue
        found = re.search(r'价格超过\s*([\d.]+)\s*卢布.*重量超过\s*([\d.]+)\s*(克|公斤)', note)
        if found:
            limits.add((numeric(dec(found[1], '轻小件价格上限', True)), numeric(dec(found[2], '轻小件重量上限', True) * (1000 if found[3] == '公斤' else 1))))
    if len(limits) != 1: raise ValueError('无法唯一识别 Yandex 轻小件重量和货值提示，不能沿用旧阈值')
    price_max, weight_max = next(iter(limits))
    policy = {'extra_small': {'max_g': weight_max, 'price_max_rub': price_max, 'max_edge_cm': '60', 'max_sum_cm': '90', 'weight_price_source': '模板 Extra Small 提示文字', 'dimensions_source': '此前核对的官方规则；模板不含厘米限制，未从本次新表更新'}}
    mixed_unit = norm(sheet['rows'][header][cols['rate']]['value']) == norm(YA_HEADERS['rate'][0])
    rules = []
    for n, row in sheet['rows'].items():
        if n <= header: continue
        get = lambda key: value(row, cols, key)
        country = get('country')
        if country not in ('中国大陆', '中国', 'China'): continue
        group = get('group'); carrier = get('carrier'); service = get('service'); transport = get('transport')
        if group not in ('Extra Small', 'Other') or not carrier or not service or not transport:
            raise ValueError(f'第 {n} 行大陆渠道资料缺失或使用未知包裹类别')
        if get('weight_type') != '实际重量': raise ValueError(f'第 {n} 行大陆渠道出现未支持的体积重规则')
        step = dec(get('step_g'), '重量步长', True)
        per_g = dec(get('rate'), '重量单价') / (1000 if mixed_unit and group == 'Other' else 1)
        fixed = dec(get('fixed'), '每包处理费')
        transport_en = {'陆运': 'Land', '陆空联运': 'Land+Air', '空运': 'Air'}.get(transport, transport)
        rules.append({'name': f'China {transport_en} ({service} {group}) {carrier}', 'carrier': carrier,
            'group': group, 'service': service, 'transport': transport, 'fixed': numeric(fixed), 'per_g': numeric(per_g),
            'battery': yes_no(get('battery'), '电池'), 'step_g': numeric(step),
            'source_cell': f'{sheet["name"]}!{cols["rate"]}{n}:{cols["fixed"]}{n}', 'source_row': n})
    warnings = ['Yandex 只导入大陆渠道；不执行总运费公式。', '原表可见/隐藏费率可能不一致，本次以明确表头所在的可见费率表为准。', '60/90 cm 限制不在模板内，仍需单独核实官方政策变化。']
    return sheet['name'], rules, policy, warnings


def parse_template(path, platform, effective_date=None):
    if platform not in ('ozon', 'yandex'): raise ValueError('模板导入仅支持 ozon/yandex')
    if effective_date: date.fromisoformat(effective_date)
    book = Workbook(path)
    sheet, rules, policy, warnings = (parse_ozon if platform == 'ozon' else parse_yandex)(book)
    if not rules: raise ValueError('模板没有可用费率，拒绝覆盖当前规则')
    ids = set()
    for rule in rules:
        identity = [platform, rule['carrier'].casefold(), rule['service'].casefold(), rule['group'].casefold(), rule.get('transport', '')]
        rule['id'] = hashlib.sha256(canonical(identity)).hexdigest()[:16]
        if rule['id'] in ids: raise ValueError(f'渠道重复，无法唯一计价：{rule["name"]}')
        ids.add(rule['id'])
    rules.sort(key=lambda r: r['id'])
    content = {'schema_version': SCHEMA, 'parser_version': PARSER, 'platform': platform,
        'source_sha256': book.sha, 'sheet': sheet, 'effective_date': effective_date,
        'policies': policy, 'rules': rules, 'warnings': warnings}
    version = platform + '-' + digest(content)[:20]
    return {'version': version, 'imported_at': datetime.now(timezone.utc).isoformat(), 'source_path': str(Path(path).resolve()), 'content': content}, book.bytes


def compare(old, new):
    old_rules = {r['id']: r for r in (old or {}).get('content', {}).get('rules', [])}
    new_rules = {r['id']: r for r in new['content']['rules']}
    added = [new_rules[i] for i in sorted(new_rules.keys() - old_rules.keys())]
    removed = [old_rules[i] for i in sorted(old_rules.keys() - new_rules.keys())]
    changed = []
    for identity in sorted(old_rules.keys() & new_rules.keys()):
        before, after = old_rules[identity], new_rules[identity]
        fields = {k: {'before': before.get(k), 'after': after.get(k)} for k in sorted((before.keys() | after.keys()) - IGNORED_DIFF_FIELDS) if before.get(k) != after.get(k)}
        if fields: changed.append({'id': identity, 'name': after['name'], 'fields': fields})
    old_policy = (old or {}).get('content', {}).get('policies', {})
    return {'platform': new['content']['platform'], 'before_version': (old or {}).get('version'), 'after_version': new['version'], 'added': added, 'removed': removed, 'changed': changed, 'policy_change': {'before': old_policy, 'after': new['content']['policies']} if old_policy != new['content']['policies'] else None, 'effective_date': new['content']['effective_date']}


def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def read_current(root):
    path = Path(root) / 'current.json'
    if not path.exists(): return {'schema_version': SCHEMA, 'platforms': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA or not isinstance(data.get('platforms'), dict):
        raise ValueError('当前费率索引无效或版本不受支持')
    return data


def read_version(root, version):
    if not isinstance(version, str) or not re.fullmatch(r'(ozon|yandex)-[0-9a-f]{20}', version): raise ValueError('费率版本号无效')
    pack = json.loads((Path(root) / 'versions' / (version + '.json')).read_text(encoding='utf-8'))
    if not isinstance(pack, dict) or not isinstance(pack.get('content'), dict):
        raise ValueError('费率版本文件结构无效')
    content = pack['content']
    if content.get('schema_version') != SCHEMA or pack.get('version') != version or version != str(content.get('platform')) + '-' + digest(content)[:20]:
        raise ValueError('费率版本文件校验失败，请通过模板导入更新，勿直接改写版本文件')
    return pack


def diff_markdown(diff, pack, status):
    lines = ['# 模板费率导入差异', '', f'状态：{status}', f'平台：{diff["platform"]}', f'旧版本：{diff["before_version"] or "首次导入"}', f'新版本：{diff["after_version"]}', f'官方生效日：{diff["effective_date"] or "未提供，不从文件名猜测"}', f'来源：{pack["source_path"]}', f'文件 SHA-256：{pack["content"]["source_sha256"]}', '', f'新增 {len(diff["added"])} 条，删除 {len(diff["removed"])} 条，修改 {len(diff["changed"])} 条。', '', '| 渠道 | 字段 | 原值 | 新值 | 方向 |', '|---|---|---|---|---|']
    for entry in diff['changed']:
        for key, change in entry['fields'].items():
            direction = '变更'
            if key in ('fixed', 'per_g'):
                a, b = Decimal(str(change['before'])), Decimal(str(change['after']))
                direction = '上涨' if b > a else '下降'
            clean = lambda x: str(x).replace('|', '\\|').replace('\n', ' ')
            lines.append(f'| {clean(entry["name"])} | {key} | {clean(change["before"])} | {clean(change["after"])} | {direction} |')
    for kind, label in [('added', '新增'), ('removed', '删除')]:
        if diff[kind]: lines += ['', label + '渠道：'] + ['- ' + r['name'] for r in diff[kind]]
    if diff['policy_change']: lines += ['', '轻小件政策变化：', '```json', json.dumps(diff['policy_change'], ensure_ascii=False, indent=2), '```']
    lines += ['', '说明：固定费和每克单价分别比较；费率方向不保证每个重量的总价同方向。格式/来源变化可产生新版本，但不会误报为费率变化。']
    lines += ['- ' + warning for warning in pack['content']['warnings']]
    return '\n'.join(lines) + '\n'


def import_template(path, platform, root, effective_date=None, preview=False):
    pack, source_bytes = parse_template(path, platform, effective_date)
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    # 文件锁只覆盖本地版本切换，不包含网络请求。
    with (root / '.import.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_current(root)
        old_id = current['platforms'].get(platform)
        old = read_version(root, old_id) if old_id else None
        diff = compare(old, pack)
        future = effective_date and date.fromisoformat(effective_date) > date.today()
        unchanged = old_id == pack['version']
        status = '仅预览，未启用' if preview else '已保存，生效日未到，未启用' if future else '与当前版本一致，无需更新' if unchanged else '校验通过，已启用'
        attempt = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + pack['version']
        folder = root / ('previews' if preview else 'imports') / attempt
        if not preview:
            version_path = root / 'versions' / (pack['version'] + '.json')
            if version_path.exists(): read_version(root, pack['version'])
            else: atomic_write(version_path, json.dumps(pack, ensure_ascii=False, indent=2).encode())
            source_path = root / 'sources' / (pack['content']['source_sha256'] + '.xlsx')
            if not source_path.exists(): atomic_write(source_path, source_bytes)
        atomic_write(folder / 'diff.json', json.dumps({**diff, 'status': status}, ensure_ascii=False, indent=2).encode())
        atomic_write(folder / 'diff.md', diff_markdown(diff, pack, status).encode())
        if not preview and not future and not unchanged:
            current['platforms'][platform] = pack['version']
            atomic_write(root / 'current.json', json.dumps(current, ensure_ascii=False, indent=2).encode())
        return {**diff, 'status': status, 'report': str(folder / 'diff.md')}


def load_active(root, platform, version=None):
    """按平台固定一份规则快照；不要求无关平台也已导入。"""
    if platform not in ('ozon', 'yandex'):
        raise ValueError('模板仅支持 ozon/yandex')
    version = version or read_current(root)['platforms'].get(platform)
    if not version:
        raise ValueError(f'{platform} 尚未导入费率，请先使用模块的 --import-template 命令')
    pack = read_version(root, version)
    content = pack['content']
    if content['platform'] != platform:
        raise ValueError('当前索引指向了错误平台')
    if content['effective_date'] and date.fromisoformat(content['effective_date']) > date.today():
        raise ValueError('当前规则尚未到官方生效日')
    return copy.deepcopy(pack)

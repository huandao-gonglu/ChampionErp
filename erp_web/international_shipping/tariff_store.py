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
PARSER = 'cn-fbs-2-ogl'
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
    number = Decimal(str(value))
    if not number.is_finite() or (number and abs(number.adjusted()) > 18) or len(number.as_tuple().digits) > 50:
        raise ValueError('费率数值的大小或精度超出支持范围')
    return format(number.normalize(), 'f')


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
    def __init__(self, path=None, *, content=None):
        self.path = Path(path) if path is not None else None
        if self.path is not None and self.path.suffix.lower() != '.xlsx':
            raise ValueError('目前导入只支持 .xlsx 文件')
        if content is None and self.path is None:
            raise ValueError('缺少 XLSX 文件内容')
        self.bytes = content if content is not None else self.path.read_bytes()
        if len(self.bytes) > 8 * 1024 * 1024:
            raise ValueError('运费表不能超过 8 MiB')
        self.sha = hashlib.sha256(self.bytes).hexdigest()
        self.sheets = []
        self.strings = []
        try:
            from io import BytesIO
            with ZipFile(BytesIO(self.bytes)) as archive:
                entries = archive.infolist()
                if (len(entries) > 2000 or sum(item.file_size for item in entries) > 32 * 1024 * 1024
                        or len({item.filename for item in entries}) != len(entries)):
                    raise ValueError('运费表解压内容过大或包含重复文件')
                if any('vbaproject' in item.filename.lower() for item in entries):
                    raise ValueError('请上传不含宏的 XLSX 运费表')
                for item in entries:
                    if item.filename.endswith('.xml'):
                        xml = archive.read(item).upper()
                        if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
                            raise ValueError('运费表含不支持的 XML 声明')
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
        except (BadZipFile, ET.ParseError, KeyError, IndexError, InvalidOperation, TypeError, OverflowError, RuntimeError, NotImplementedError):
            raise ValueError('无法读取 XLSX 运费表，请核对文件格式和内容') from None


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
    ignored = ['总运费，卢布', 'Merged']
    allowed = set(aliases) | {norm(x) for x in ignored}
    to_index = lambda col: sum((ord(c) - 64) * 26 ** i for i, c in enumerate(reversed(col)))
    start = min(to_index(c) for c in columns.values())
    for col, cell in sheet['rows'][row_number].items():
        if to_index(col) >= start and cell['value'] and norm(cell['value']) not in allowed:
            raise ValueError(f'费率区出现未识别的新表头：{cell["value"]}；需确认是否新增费用或限制')
    return matches[0]


def value(row, columns, key):
    cell = row.get(columns[key], {'value': '', 'formula': None, 'address': columns[key]})
    formula = cell['formula']
    if formula:
        raise ValueError(f'{cell["address"]} 使用了不支持的公式：请提供明确费率值')
    if str(cell['value']).startswith('#'): raise ValueError(f'{cell["address"]} 含 Excel 错误')
    return str(cell['value']).strip()


def yes_no(text, label):
    if text in ('允许', '是'): return True
    if text in ('禁止', '否'): return False
    raise ValueError(f'{label}无法识别是否允许：{text}')


def parse_ozon(book):
    """只导入 OGL 官方英文页，核对自送与揽收的六档规则完全一致。"""
    sheets = [sheet for sheet in book.sheets if sheet['name'] == 'CHINA OGL']
    if len(sheets) != 1:
        raise ValueError('请导入 OGL 官方费率表；旧第三方物流表不再用于核价')
    sheet = sheets[0]
    rows = sheet['rows']
    expected = {'B': 'Handling Type', 'C': 'Scoring Group', 'E': 'Fix', 'F': 'Var',
                'G': 'Unit of tariffication', 'H': 'Batteries', 'I': 'Liquids',
                'J': 'Measurements, max cm', 'K': 'Shipment weight limits / min g',
                'L': 'Shipment weight limits / max g', 'N': 'Shipment cost limit / min-max CNY',
                'Q': 'Tarification type', 'R': 'Volume weight calculation'}
    headers = [n for n, row in rows.items() if all(norm(row.get(c, {}).get('value')) == norm(v) for c, v in expected.items())]
    if len(headers) != 1:
        raise ValueError('OGL 费率表头或单位发生变化，请核实官方格式')
    methods = {'Pick-up': {}, 'Self-Drop': {}}
    for n, row in rows.items():
        if n <= headers[0]: continue
        def get(column):
            cell = row.get(column, {})
            if cell.get('formula'): raise ValueError('OGL 费率必须是明确数值，不执行公式')
            return str(cell.get('value', '')).strip()
        method, group = get('B'), get('C')
        if not method and not group: continue
        if method not in methods or group not in GROUPS or group in methods[method]:
            raise ValueError('OGL 发运方法或评分组无效、重复')
        if get('G') != '1 gramm': raise ValueError('OGL 重量计费单位必须是克')
        dimensions = re.fullmatch(r'length\s*≤\s*([\d.]+)', get('J'))
        span = re.fullmatch(r'([\d.]+)-([\d.]+)', re.sub(r'\s+', '', get('N')))
        if not dimensions or not span: raise ValueError('OGL 尺寸或货值范围无法识别')
        low, high = dec(span[1], '最低货值', True), dec(span[2], '最高货值', True)
        min_g, max_g = dec(get('K'), '最小重量', True), dec(get('L'), '最大重量', True)
        if low > high or min_g > max_g: raise ValueError('OGL 区间上下限颠倒')
        divisor = None
        if get('Q') == 'Max weight between the physical and volume ones':
            found = re.fullmatch(r'L×W×H\(cm\)÷([\d.]+)', re.sub(r'\s+', '', get('R')))
            if not found: raise ValueError('OGL 体积重公式无法识别')
            divisor = numeric(dec(found[1], '体积重除数', True))
        elif get('Q') != 'Physical weight' or get('R') != '-':
            raise ValueError('OGL 计费重量类型无法识别')
        if get('H') not in ('Allowed', 'Prohibited') or get('I') not in ('Allowed', 'Prohibited'):
            raise ValueError('OGL 电池或液体规则无法识别')
        methods[method][group] = {'name': 'OGL ' + group, 'carrier': 'OGL', 'service': 'OGL', 'group': group,
            'fixed': numeric(dec(get('E'), '固定费')), 'per_g': numeric(dec(get('F'), '每克费')),
            'battery': get('H') == 'Allowed', 'liquid': get('I') == 'Allowed',
            'max_edge_cm': numeric(dec(dimensions[1], '最长边', True)),
            'min_g': numeric(min_g), 'max_g': numeric(max_g),
            'price_min_cny': numeric(low), 'price_max_cny': numeric(high), 'volume_divisor_g': divisor,
            'source_cell': f'{sheet["name"]}!E{n}:R{n}', 'source_row': n}
    if any(set(entries) != GROUPS for entries in methods.values()):
        raise ValueError('OGL 费率表必须完整包含两种发运方法的六档规则')
    for group in GROUPS:
        a, b = methods['Pick-up'][group], methods['Self-Drop'][group]
        if {k:v for k,v in a.items() if k not in IGNORED_DIFF_FIELDS} != {k:v for k,v in b.items() if k not in IGNORED_DIFF_FIELDS}:
            raise ValueError('OGL 自送与揽收费率不同，需要按发运方法更新计费规则')
    return sheet['name'], list(methods['Pick-up'].values()), {'scheme': 'OGL'}, []


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
    if platform == 'ozon' and not effective_date:
        raise ValueError('导入 OGL 费率必须提供已核实的 --effective-date 生效日期')
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


def import_template(path, platform, root, effective_date=None, preview=False, *, expected_version=None, customized_from=None):
    pack, source_bytes = parse_template(path, platform, effective_date)
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    # 文件锁只覆盖本地版本切换，不包含网络请求。
    with (root / '.import.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_current(root)
        old_id = current['platforms'].get(platform)
        if expected_version is not None and (old_id or '') != expected_version:
            raise ValueError('运费表已更新，请重新读取并预览后再导入')
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
            if customized_from is not None:
                current.setdefault('customized_from', {})[platform] = customized_from
            atomic_write(root / 'current.json', json.dumps(current, ensure_ascii=False, indent=2).encode())
        return {**diff, 'status': status, 'report': str(folder / 'diff.md')}


def load_active(root, platform, version=None):
    """按平台固定一份规则快照；不要求无关平台也已导入。"""
    if platform not in ('ozon', 'yandex'):
        raise ValueError('模板仅支持 ozon/yandex')
    explicit = version is not None
    version = version or read_current(root)['platforms'].get(platform)
    if platform == 'ozon' and not version:
        return default_snapshot()
    if not version:
        raise ValueError(f'{platform} 尚未导入费率，请先使用模块的 --import-template 命令')
    if platform == 'ozon':
        bundled = default_snapshot()
        if version == bundled['version']:
            return bundled
    pack = read_version(root, version)
    content = pack['content']
    if content['platform'] != platform:
        raise ValueError('当前索引指向了错误平台')
    if content['effective_date'] and date.fromisoformat(content['effective_date']) > date.today():
        raise ValueError('当前规则尚未到官方生效日')
    if platform == 'ozon' and content.get('policies', {}).get('scheme') != 'OGL':
        if explicit:
            raise ValueError('旧第三方物流报价已停用，请使用 OGL 重新核价')
        return default_snapshot()
    return copy.deepcopy(pack)


def default_snapshot():
    """随应用分发的已核对 OGL 运价；已有版本仍保持独立、可读取。"""
    pack = json.loads(Path(__file__).with_name('ogl_20260824.json').read_text(encoding='utf-8'))
    if pack['version'] != 'ozon-' + digest(pack['content'])[:20]:
        raise ValueError('应用内 OGL 运价校验失败，请重新安装完整费率文件')
    if date.fromisoformat(pack['content']['effective_date']) > date.today():
        raise ValueError('应用内 OGL 运价尚未到生效日期')
    return pack

"""从已安装的项目依赖生成英文声明；保留上游原文，不推测版权人或许可证。"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
from importlib import metadata
import json
from pathlib import Path
import re
import subprocess

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'front/src/legal/third-party-notices.json'
INPUTS = ['requirements.txt', 'requirements-dev.txt', 'package.json', 'package-lock.json',
          'front/package.json', 'front/pnpm-lock.yaml', 'scripts/generate_third_party_notices.py',
          'scripts/legal-notices/supplements.json', 'scripts/legal-notices/python-supplements.json']
NOTICE_NAME = re.compile(r'^(licen[sc]e|notice|copying|copyright|third[-_ ]party[-_ ]notices?)(\..*|-.*)?$', re.I)


def input_digest() -> str:
    digest = hashlib.sha256()
    for name in INPUTS:
        digest.update(name.encode())
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()


def read_notices(folder: Path) -> list[tuple[str, str]]:
    result = []
    for path in sorted(folder.rglob('*')):
        relative = path.relative_to(folder)
        if 'node_modules' in relative.parts or not path.is_file():
            continue
        if NOTICE_NAME.match(path.name) and path.suffix.lower() not in {'.js', '.map', '.ts', '.html'}:
            text = path.read_text(errors='replace').strip()
            if text:
                result.append((str(relative), text))
    if not result:
        for path in sorted(folder.glob('*')):
            if path.is_file() and path.name.lower().startswith('readme'):
                text = path.read_text(errors='replace')
                match = re.search(r'(?im)^(?:#{1,6}\s*)?(?:license|licence|copyright)(?:\s|\(|:|$)', text)
                if match:
                    result.append((path.name + ' (license section)', text[match.start():].strip()))
    return result


class ChromiumCredits(HTMLParser):
    """仅提取上游标题、主页和原始许可证文本，不执行或渲染 HTML。"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self.entry = None
        self.capture = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div' and attrs.get('class') == 'product':
            self.entry = {'name': '', 'homepage': '', 'text': ''}
            self.entries.append(self.entry)
        if self.entry is None:
            return
        if tag == 'span' and attrs.get('class') == 'title':
            self.capture = 'name'
        elif tag == 'pre':
            self.capture = 'text'
        elif tag == 'a' and not self.entry['homepage']:
            self.entry['homepage'] = attrs.get('href', '')

    def handle_endtag(self, tag):
        if tag in {'span', 'pre'}:
            self.capture = ''

    def handle_data(self, data):
        if self.entry is not None and self.capture:
            self.entry[self.capture] += data


def python_dependencies():
    pending = []
    for filename in ['requirements.txt', 'requirements-dev.txt']:
        for line in (ROOT / filename).read_text().splitlines():
            if line.strip() and not line.startswith(('#', '-')):
                pending.append(Requirement(line))
    seen = set()
    distributions = {}
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in seen or (requirement.marker and not requirement.marker.evaluate()):
            continue
        seen.add(key)
        distribution = metadata.distribution(name)
        if requirement.specifier and not requirement.specifier.contains(distribution.version):
            raise RuntimeError(f'依赖版本不满足要求：{requirement}，已安装 {distribution.version}')
        distributions[name] = distribution
        for value in distribution.requires or []:
            child = Requirement(value)
            if not child.marker or any(child.marker.evaluate({'extra': extra}) for extra in {'', *requirement.extras}):
                child.marker = None
                pending.append(child)
    return distributions.values()


def generate():
    entries = {}
    texts = {}
    missing = []
    supplement_path = ROOT / 'scripts/legal-notices/supplements.json'
    supplements = json.loads(supplement_path.read_text()) if supplement_path.exists() else {}
    python_supplements = ROOT / 'scripts/legal-notices/python-supplements.json'
    if python_supplements.exists():
        supplements.update(json.loads(python_supplements.read_text()))

    def add(ecosystem, name, version, license_name, homepage, notices):
        key = f'{ecosystem}:{name}@{version}'
        if key in entries:
            return
        extra = supplements.get(key)
        if extra:
            notices = [*notices, (extra['source'], extra['text'])]
        refs = []
        for source, content in notices:
            content = content.strip()
            if not content:
                continue
            digest = hashlib.sha256(content.encode()).hexdigest()[:20]
            texts[digest] = content
            refs.append({'source': source, 'text': digest})
        # Chromium 的公共领域声明可能很短；不能把字数少误判为原文缺失。
        declaration_only = refs and all(
            len(texts[ref['text']]) < 350 and
            ('(license section)' in ref['source'] or 'See the LICENSE file' in texts[ref['text']])
            for ref in refs
        )
        incomplete = not refs or (ecosystem == 'npm' and not extra and declaration_only)
        if incomplete:
            missing.append(key)
        entries[key] = {'name': name, 'version': version, 'ecosystem': ecosystem,
                        'license': license_name or 'See included notices', 'homepage': homepage or '',
                        'noticeStatus': 'declaration-only' if incomplete else 'included', 'notices': refs}

    report = json.loads(subprocess.check_output(['pnpm', 'licenses', 'list', '--json'], cwd=ROOT / 'front'))
    for group in report.values():
        for item in group:
            for folder in item['paths']:
                path = Path(folder)
                package = json.loads((path / 'package.json').read_text())
                homepage = item.get('homepage') or f'https://www.npmjs.com/package/{package["name"]}/v/{package["version"]}'
                add('npm', package['name'], package['version'], item.get('license'), homepage, read_notices(path))
    root_lock = json.loads((ROOT / 'package-lock.json').read_text())
    for folder, locked in root_lock['packages'].items():
        if not folder:
            continue
        path = ROOT / folder
        if not path.exists() and locked.get('optional'):
            continue
        package = json.loads((path / 'package.json').read_text())
        if package['version'] != locked['version']:
            raise RuntimeError(f'根目录 npm 依赖版本与锁文件不一致：{folder}')
        add('npm', package['name'], package['version'], package.get('license'), package.get('homepage'), read_notices(path))

    for dist in python_dependencies():
        meta = dist.metadata
        notices = []
        for filename in dist.files or []:
            if NOTICE_NAME.match(Path(filename).name):
                notices.append((str(filename), Path(dist.locate_file(filename)).read_text(errors='replace')))
        license_name = meta.get('License-Expression') or meta.get('License') or ''
        if '\n' in license_name:
            notices.append(('Package metadata: License', license_name))
            license_name = 'See included notices'
        homepage = meta.get('Home-page', '')
        if not homepage:
            urls = meta.get_all('Project-URL') or []
            homepage = urls[0].split(', ', 1)[-1] if urls else ''
        add('Python', meta['Name'], dist.version, license_name, homepage, notices)

    electron = ROOT / 'front/node_modules/electron'
    electron_version = json.loads((electron / 'package.json').read_text())['version']
    parser = ChromiumCredits()
    parser.feed((electron / 'dist/LICENSES.chromium.html').read_text())
    if not parser.entries or any(not entry['text'].strip() for entry in parser.entries):
        raise RuntimeError('Electron 的 Chromium 声明解析不完整')
    for index, entry in enumerate(parser.entries):
        # 上游 credits 未逐项提供版本，不伪造版本号；重复标题保留其不同声明。
        add('Electron / Chromium', entry['name'], f'Electron {electron_version} / notice {index + 1}',
            'See included notices', entry['homepage'], [('Electron LICENSES.chromium.html', entry['text'])])

    return {'inputFiles': INPUTS, 'inputDigest': input_digest(), 'incompleteNotices': sorted(missing),
            'packages': sorted(entries.values(), key=lambda x: (x['ecosystem'], x['name'].lower(), x['version'])),
            'texts': dict(sorted(texts.items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='检查声明是否与当前安装环境一致')
    parser.add_argument('--require-complete', action='store_true', help='发行审核：存在缺失的许可证原文时失败')
    args = parser.parse_args()
    data = json.dumps(generate(), ensure_ascii=False, indent=2) + '\n'
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != data:
            raise SystemExit('第三方声明已过期，请重新运行 scripts/generate_third_party_notices.py')
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(data)
    print(f'第三方声明已{"核对" if args.check else "生成"}：{len(json.loads(data)["packages"])} 项')
    missing = json.loads(data)['incompleteNotices']
    if missing:
        print('以下上游包未包含完整许可证原文，声明中已明确标注：\n' + '\n'.join(missing))
        if args.require_complete:
            raise SystemExit('许可证原文尚未补齐，不能通过完整性发行审核')


if __name__ == '__main__':
    main()

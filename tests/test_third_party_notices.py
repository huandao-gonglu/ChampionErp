"""交付依赖边界与许可证原文保留的回归检查。"""
from importlib import metadata
import io
import json
from types import SimpleNamespace
from urllib.parse import urlparse

from scripts import generate_third_party_notices as notices


def test_web_download_route_returns_notice_file_and_missing_file_is_404(tmp_path, monkeypatch):
    from erp_web.http_route_units import get_routes

    class Handler:
        def __init__(self):
            self.status = None
            self.headers = {}
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, name, value):
            self.headers[name] = value

        def end_headers(self):
            pass

    monkeypatch.setattr(get_routes, 'get_context', lambda: SimpleNamespace(paths=SimpleNamespace(front_dist_dir=tmp_path)))
    route = '/licenses/THIRD-PARTY-NOTICES.txt'
    assert route in get_routes.HANDLED_PATHS
    missing = Handler()
    assert get_routes.handle_get(missing, urlparse(route))
    assert missing.status == 404
    folder = tmp_path / 'licenses'
    folder.mkdir()
    content = '第三方声明\nCopyright Example\n'
    (folder / 'THIRD-PARTY-NOTICES.txt').write_text(content, encoding='utf-8')
    handler = Handler()
    assert get_routes.handle_get(handler, urlparse(route))
    assert handler.status == 200
    assert handler.headers['Content-Type'] == 'text/plain; charset=utf-8'
    assert handler.wfile.getvalue() == content.encode('utf-8')
    assert not get_routes.handle_get(Handler(), urlparse('/licenses/private.txt'))


def test_split_preserves_all_original_content():
    catalog = json.loads(notices.OUTPUT.read_text())
    entry = next(item for item in catalog['packages'] if item['name'] == 'vue')
    vue = '\n\n'.join(catalog['texts'][part['text']] for notice in entry['notices'] for part in notice['parts'])
    apache = (notices.ROOT / 'LICENSE').read_text()
    original = f'Copyright 独立作者\n\n{vue}\n特殊 NOTICE\n{apache}\n附加声明'
    parts = list(notices.split_notice(original))
    assert ''.join(text for _, text in parts) == original
    assert sum(kind == 'license' for kind, _ in parts) == 2
    attribution = ''.join(text for kind, text in parts if kind == 'notice')
    assert 'Yuxi (Evan) You' in attribution
    assert '特殊 NOTICE' in attribution
    assert '附加声明' in attribution


def test_python_dependencies_exclude_dev_but_keep_extras_and_transitives(tmp_path, monkeypatch):
    (tmp_path / 'requirements.txt').write_text('runtime[extra]==1\n')
    (tmp_path / 'requirements-dev.txt').write_text('pytest\n')
    packages = {
        'runtime': SimpleNamespace(version='1', requires=['child; extra == "extra"', 'ignored; sys_platform == "never"']),
        'child': SimpleNamespace(version='1', requires=[]),
    }
    monkeypatch.setattr(notices, 'ROOT', tmp_path)
    monkeypatch.setattr(metadata, 'distribution', packages.__getitem__)
    assert list(notices.python_dependencies()) == [packages['runtime'], packages['child']]


def test_catalog_only_contains_runtime_dependencies_and_valid_text_references():
    catalog = json.loads(notices.OUTPUT.read_text())
    names = {item['name'].lower() for item in catalog['packages']}
    assert not names & {'pytest', 'vitest', 'eslint', 'vite', 'electron-builder', '@playwright/test'}
    assert {'vue', 'requests'} <= names
    assert not catalog['incompleteNotices']
    assert catalog['inputDigest'] == notices.input_digest()
    referenced = {part['text'] for item in catalog['packages'] for notice in item['notices'] for part in notice['parts']}
    assert referenced == set(catalog['texts'])
    # 全文不同就不能仅凭许可证名字合并；共同正文不携带组件独有版权。
    vue = next(item for item in catalog['packages'] if item['name'] == 'vue')
    assert any('Yuxi (Evan) You' in catalog['texts'][part['text']]
               for notice in vue['notices'] for part in notice['parts'] if part['kind'] == 'notice')

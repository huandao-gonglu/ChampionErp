"""发布程序的桌面原子替换、并行更新保护及服务器失败回退。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from deploy import release_server
from scripts import release


def app(path, content):
    archive = path / 'Contents/Resources/app.asar'
    archive.parent.mkdir(parents=True)
    archive.write_bytes(content)
    return path


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    source = app(tmp_path / 'built.app', b'new')
    target = app(tmp_path / 'installed.app', b'old')
    monkeypatch.setattr(release, 'desktop_running', lambda target: False)
    monkeypatch.setattr(release, 'check_runtime', lambda executable: None)
    monkeypatch.setattr(release.subprocess, 'run', lambda args, **kwargs: shutil.copytree(args[1], args[2]))
    return source, target


def test_desktop_update_preserves_old_package_and_installs_exact_build(desktop):
    source, target = desktop
    old = release.desktop_hash(target)
    result = release.install_desktop(source, target, old, 'release-1', 0)
    assert release.desktop_hash(target) == release.desktop_hash(source)
    assert release.desktop_hash(Path(result['backup'])) == old


def test_desktop_smoke_failure_restores_previous_program(desktop, monkeypatch):
    source, target = desktop
    old = release.desktop_hash(target)
    def failed(executable):
        raise RuntimeError('执行器检查失败')
    monkeypatch.setattr(release, 'check_runtime', failed)
    with pytest.raises(RuntimeError, match='执行器检查失败'):
        release.install_desktop(source, target, old, 'release-1', 0)
    assert release.desktop_hash(target) == old


def test_concurrent_desktop_update_is_not_overwritten(desktop):
    source, target = desktop
    old = release.desktop_hash(target)
    (target / 'Contents/Resources/app.asar').write_bytes(b'other-update')
    with pytest.raises(RuntimeError, match='其他更新'):
        release.install_desktop(source, target, old, 'release-1', 0)
    assert (target / 'Contents/Resources/app.asar').read_bytes() == b'other-update'


def test_running_desktop_times_out_without_replacing_program(desktop, monkeypatch):
    source, target = desktop
    old = release.desktop_hash(target)
    monkeypatch.setattr(release, 'desktop_running', lambda target: True)
    with pytest.raises(RuntimeError, match='退出超时'):
        release.install_desktop(source, target, old, 'release-1', 0)
    assert release.desktop_hash(target) == old


def test_server_failed_smoke_restores_code_and_service_configuration(tmp_path, monkeypatch):
    base = tmp_path / 'server'
    previous = base / 'releases/previous'
    previous.mkdir(parents=True)
    (base / 'current').symlink_to(previous)
    override = tmp_path / 'service/release-python.conf'
    override.parent.mkdir()
    override.write_text('previous-python')
    staging = tmp_path / 'staging'
    staging.mkdir()
    manifest = staging / 'release-manifest.json'
    manifest.write_text(json.dumps({'revision': 'new-revision'}))
    archive = staging / 'server.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        package.add(manifest, arcname=manifest.name)
    monkeypatch.setattr(release_server, 'BASE', base)
    monkeypatch.setattr(release_server, 'BACKUPS', tmp_path / 'backups')
    monkeypatch.setattr(release_server, 'PYTHON_OVERRIDE', override)
    monkeypatch.setattr(release_server.os, 'geteuid', lambda: 0)
    calls = []
    monkeypatch.setattr(release_server, 'run', lambda *args: calls.append(args))
    monkeypatch.setattr(release_server, 'wait_ready', lambda: None)
    def failed(path):
        raise RuntimeError('HTTP 验证失败')
    monkeypatch.setattr(release_server, 'smoke_check', failed)
    args = argparse.Namespace(archive=archive, sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        release_id='release-1', revision='new-revision', expected_current=str(previous))
    with pytest.raises(RuntimeError, match='HTTP 验证失败'):
        release_server.deploy(args)
    assert (base / 'current').resolve() == previous
    assert override.read_text() == 'previous-python'
    assert json.loads((staging / 'server-result.json').read_text())['status'] == 'rolled_back'
    assert calls[-1] == ('systemctl', 'start', release_server.SERVICE)


def test_public_verification_identifies_release_client_and_checks_asset_digest(tmp_path):
    static = tmp_path / 'erp_web/static/dist'
    (static / 'assets').mkdir(parents=True)
    (static / 'index.html').write_text('<script src="/assets/index-test.js"></script>')
    (static / 'assets/index-test.js').write_bytes(b'compiled application')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get('User-Agent', '').startswith('Python-urllib'):
                self.send_error(403)
                return
            if self.path == '/api/account/session':
                content = b'{"ok":true,"initialized":true}'
            else:
                content = (static / ('index.html' if self.path == '/' else self.path.lstrip('/'))).read_bytes()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(content)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = release.verify_public(f'http://127.0.0.1:{server.server_port}', tmp_path)
        assert result['account_session']
        assert result['web_sha256'] == release.digest(static / 'assets/index-test.js')
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_resume_refuses_when_another_server_release_is_active(tmp_path, monkeypatch):
    archive = tmp_path / 'server.tar.gz'
    archive.write_bytes(b'original package')
    release.write_record(tmp_path / 'release.json', {'status': 'failed_after_server_published',
        'archive_sha256': release.digest(archive), 'server': {'release': '/expected-release'}})
    monkeypatch.setattr(release, 'remote_output', lambda *args: '/other-release')
    with pytest.raises(RuntimeError, match='其他发布'):
        release.resume_release(argparse.Namespace(resume=tmp_path))

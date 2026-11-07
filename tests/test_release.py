"""发布入口、桌面更新分发、断点继续及服务器失败回退。"""
import argparse
import hashlib
import json
from io import BytesIO
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from erp_web.release_guard import locked_file, release_guard
from deploy import release_server
from scripts import release


@pytest.mark.parametrize(('flags', 'policy'), [
    ([], 'optional'), (['--desktop-update', 'required'], 'required'), (['--server-only'], None),
])
def test_release_cli_selects_desktop_policy(flags, policy, tmp_path, monkeypatch):
    options = []
    monkeypatch.setattr(release.sys, 'argv', ['release.py', '--output-dir', str(tmp_path), *flags])
    monkeypatch.setattr(release, 'release', options.append)
    release.main()
    assert options[0].desktop_update == policy


@pytest.mark.parametrize('skip', [False, True])
@pytest.mark.parametrize('desktop', [False, True])
def test_release_cli_runs_selected_checks_before_web_build(tmp_path, monkeypatch, skip, desktop):
    identity = tmp_path / 'identity'
    identity.touch()
    flags = ['--output-dir', str(tmp_path), '--ssh-identity', str(identity)]
    if skip:
        flags.append('--skip-tests')
    if not desktop:
        flags.append('--server-only')
    monkeypatch.setattr(release.sys, 'argv', ['release.py', *flags])
    monkeypatch.setattr(release.sys, 'platform', 'darwin')
    monkeypatch.setattr(release, 'output', lambda *args: {
        ('git', 'status', '--porcelain', '--untracked-files=normal'): '',
        ('git', 'rev-parse', 'HEAD'): 'revision',
        ('node', '-p', 'process.arch'): 'arm64',
    }[args])
    calls = []
    monkeypatch.setattr(release, 'check_new_desktop_version', lambda update: calls.append(('版本预检', update['arch'])))
    monkeypatch.setattr(release, 'remote_output', lambda *args: '/opt/champion-erp/releases/previous')

    def git_archive(command, *, cwd, stdout, check):
        assert command == ['git', 'archive', 'revision']
        with tarfile.open(fileobj=stdout, mode='w'):
            pass

    monkeypatch.setattr(release.subprocess, 'run', git_archive)

    def capture(command, **kwargs):
        calls.append(tuple(command))
        if command == ['pnpm', 'build']:
            raise RuntimeError('测试到达网页构建')

    monkeypatch.setattr(release, 'run', capture)
    with pytest.raises(RuntimeError, match='测试到达网页构建'):
        release.main()
    expected = [('版本预检', 'arm64')] if desktop else []
    expected.append(('pnpm', 'install', '--frozen-lockfile', '--ignore-scripts'))
    if desktop:
        expected.append(('node', 'node_modules/electron/install.js'))
    expected.extend([(release.sys.executable, 'scripts/generate_third_party_notices.py', '--check'),
                     ('pnpm', 'types:check')])
    if not skip:
        expected.extend([(release.sys.executable, '-m', 'pytest', 'tests', '-q'),
                         ('pnpm', 'test:run'), ('pnpm', 'test:desktop')])
    assert calls == [*expected, ('pnpm', 'build')]
    assert json.loads(next(tmp_path.glob('*/release.json')).read_text())['skip_tests'] is skip


def test_new_desktop_version_checked_before_switch(monkeypatch):
    update = {'public_url': 'https://erp.example', 'platform': 'darwin', 'arch': 'arm64', 'version': '1.10.0'}
    url = release.desktop_manifest_url(update)
    def fetch(request, timeout):
        assert request.full_url == url
        assert request.get_header('User-agent') == 'gongluErp-release'
        if previous is None:
            raise release.HTTPError(url, 404, '首次发布', {}, None)
        response = BytesIO(json.dumps({**update, 'version': previous}).encode())
        response.geturl = lambda: url
        return response

    monkeypatch.setattr(release, 'urlopen', fetch)
    for previous in (None, '1.9.9'):
        release.check_new_desktop_version(update)
    for previous in ('1.10.0', '2.0.0'):
        with pytest.raises(RuntimeError, match='提高桌面版本号'):
            release.check_new_desktop_version(update)


def test_server_timeout_can_resume_and_failed_smoke_restores_previous_service(tmp_path, monkeypatch, release_guard_dir):
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
    active = {'server', 'active', '商品提交'}
    monkeypatch.setattr(release_server, 'in_use', lambda directory, name: name in active)
    monkeypatch.setattr(release_server, 'run', lambda *args: calls.append(args))
    monkeypatch.setattr(release_server, 'wait_ready', lambda: None)
    def failed(path):
        raise RuntimeError('HTTP 验证失败')
    monkeypatch.setattr(release_server, 'smoke_check', failed)
    args = argparse.Namespace(archive=archive, sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        release_id='release-1', revision='new-revision', expected_current=str(previous), wait_tasks_seconds=0)
    with pytest.raises(TimeoutError, match='商品提交'):
        release_server.deploy(args)
    assert json.loads((staging / 'server-result.json').read_text())['status'] == 'prepared'
    assert not any(call[0] == 'systemctl' for call in calls)
    active.remove('active')
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


def test_safe_release_waits_for_submitted_work_and_allows_existing_handoff(release_guard_dir):
    from concurrent.futures import ThreadPoolExecutor

    finish = threading.Event()
    with release_guard.serving(release_guard_dir), ThreadPoolExecutor() as pool:
        with release_guard.admit('业务请求') as accepted:
            assert accepted
            with locked_file(release_guard_dir, 'admission', exclusive=True):
                with release_guard.admit('订单处理') as admitted:
                    assert not admitted
                job = release_guard.submit('图片批次', pool.submit, finish.wait, 5)
        try:
            with pytest.raises(TimeoutError, match='图片批次'), release_server.safe_release(release_guard_dir, 0):
                pytest.fail('已有图片批次尚未结束')
            assert release_guard.accepting()
        finally:
            finish.set()
            job.result(timeout=2)
        with release_server.safe_release(release_guard_dir, 2):
            assert not release_guard.accepting()
        assert release_guard.accepting()


def test_guard_requires_maintenance_for_an_uninstrumented_running_service(release_guard_dir, monkeypatch):
    monkeypatch.setattr(release_server.subprocess, 'check_output', lambda *args, **kwargs: 'active\n')
    with pytest.raises(RuntimeError, match='首次接入'), release_server.safe_release(release_guard_dir, 0):
        pytest.fail('旧服务仍在运行')
    monkeypatch.setattr(release_server.subprocess, 'check_output', lambda *args, **kwargs: 'inactive\n')
    with release_server.safe_release(release_guard_dir, 0), release_guard.serving(release_guard_dir):
        assert not release_guard.accepting()


def test_release_waits_across_processes_and_reopens_admission(release_guard_dir, monkeypatch):
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    # 用独立进程模拟线上服务，避免同进程线程测试掩盖部署端的锁行为。
    code = """
import sys
from erp_web.release_guard import release_guard
with release_guard.serving(sys.argv[1]):
    with release_guard.admit('商品提交') as allowed:
        assert allowed
        print('ready', flush=True)
        input()
    input()
"""
    waiting = threading.Event()
    probe = release_server.in_use

    def observe(directory, name):
        busy = probe(directory, name)
        if name == 'active' and busy:
            waiting.set()
        return busy

    monkeypatch.setattr(release_server, 'in_use', observe)

    def switch():
        with release_server.safe_release(release_guard_dir, 5):
            assert probe(release_guard_dir, 'admission')
            assert not probe(release_guard_dir, 'active')

    with subprocess.Popen([sys.executable, '-c', code, str(release_guard_dir)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as backend:
        try:
            assert backend.stdout.readline().strip() == 'ready'
            with ThreadPoolExecutor() as pool:
                release = pool.submit(switch)
                assert waiting.wait(2)
                assert not release.done()
                backend.stdin.write('\n')
                backend.stdin.flush()
                release.result(timeout=4)
            assert not probe(release_guard_dir, 'admission')
            backend.stdin.write('\n')
            backend.stdin.flush()
            assert backend.wait(timeout=2) == 0
        finally:
            backend.kill()


@pytest.mark.parametrize('desktop', [None, {'policy': 'required'}, {'policy': 'optional'}])
def test_resume_after_drain_timeout_uses_staged_build(tmp_path, monkeypatch, desktop):
    archive = tmp_path / 'server.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        entry = tmp_path / 'REVISION'
        entry.write_text('revision')
        package.add(entry, arcname='REVISION')

    release.write_record(tmp_path / 'release.json', {
        'status': 'failed_after_server_staged', 'archive_sha256': release.digest(archive),
        'revision': 'revision', 'version': '1.2.8', 'desktop_update': desktop,
    })
    calls = []

    def switch(args, record, folder, source):
        assert (source / 'REVISION').read_text() == record['revision']
        record.update(status='server_published', server={'release': '/published'})
        calls.append('server')

    def remote(args, command):
        return '/published' if command[0] == 'readlink' else json.dumps({'revision': 'revision'})

    monkeypatch.setattr(release, 'switch_server', switch)
    monkeypatch.setattr(release, 'remote_output', remote)
    def publish(args, record, folder, source):
        calls.append(record['desktop_update']['policy'])

    def verify(url, source):
        calls.append('public')
        return {'account_session': True}

    monkeypatch.setattr(release, 'verify_public', verify)
    monkeypatch.setattr(release, 'publish_desktop_update', publish)
    release.resume_release(argparse.Namespace(resume=tmp_path, public_url='https://erp.example'))
    assert calls == ['server', 'public'] + ([desktop['policy']] if desktop else [])
    assert json.loads((tmp_path / 'release.json').read_text())['status'] == 'complete'

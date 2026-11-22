"""发布入口、桌面更新分发、断点继续及服务器失败回退。"""
import argparse
import hashlib
import json
import os
import plistlib
from io import BytesIO, StringIO
import subprocess
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from erp_web.release_guard import locked_file, release_guard
from deploy import release_server
from scripts import release


@pytest.mark.parametrize('official', [False, True])
def test_desktop_release_always_includes_native_zip(tmp_path, monkeypatch, official):
    desktop = tmp_path / 'desktop'
    app = desktop / 'mac-arm64/gongluErp.app'
    (app / 'Contents').mkdir(parents=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'gongluErp'}))
    (desktop / 'erp.dmg').write_bytes(b'installer')
    archive = desktop / 'erp.zip'
    archive.write_bytes(b'signed native update')
    monkeypatch.setattr(release, 'output', lambda *args: 'arm64')
    monkeypatch.setattr(release.subprocess, 'check_output', lambda *args, **kwargs: 'Authority=Developer ID Application: test')
    calls = []
    monkeypatch.setattr(release.subprocess, 'run', lambda *args, **kwargs: calls.append(args[0]))
    args = argparse.Namespace(signed_desktop=official, desktop_update='optional', update_notes='', public_url='https://erp.example')
    record = release.desktop_release_artifacts(tmp_path, app, '1.2.89', args)
    assert record['mac_zip'] == {'name': 'erp.zip', 'sha256': release.digest(archive)}
    assert calls == ([['xcrun', 'stapler', 'validate', app]] if official else [])
    archive.unlink()
    with pytest.raises(RuntimeError, match='唯一的 .zip'):
        release.desktop_release_artifacts(tmp_path, app, '1.2.89', args)


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
@pytest.mark.parametrize('signed', [False, True])
def test_release_cli_runs_selected_checks_before_web_build(tmp_path, monkeypatch, skip, desktop, signed):
    identity = tmp_path / 'identity'
    identity.touch()
    flags = ['--output-dir', str(tmp_path), '--ssh-identity', str(identity)]
    if skip:
        flags.append('--skip-tests')
    if not desktop:
        flags.append('--server-only')
    elif signed:
        flags.append('--signed-desktop')
    monkeypatch.setattr(release.sys, 'argv', ['release.py', *flags])
    monkeypatch.setattr(release.sys, 'platform', 'darwin')
    monkeypatch.setattr(release.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(release, 'output', lambda *args: {
        ('git', 'status', '--porcelain', '--untracked-files=normal'): '',
        ('git', 'rev-parse', 'HEAD'): 'revision',
        ('node', '-p', 'process.arch'): 'arm64',
    }[args])
    calls = []
    parallel_calls = []
    monkeypatch.setattr(release, 'check_new_desktop_version', lambda update: calls.append(('版本预检', update['arch'])))
    monkeypatch.setattr(release, 'remote_output', lambda *args: '/opt/champion-erp/releases/previous')

    def git_archive(command, *, cwd, stdout, check):
        assert command == ['git', 'archive', 'revision']
        with tarfile.open(fileobj=stdout, mode='w'):
            pass

    monkeypatch.setattr(release.subprocess, 'run', git_archive)

    def capture(command, **kwargs):
        assert kwargs['env']['ERP_DESKTOP_SIGNING_MODE'] == ('developer-id' if desktop and signed else 'local')
        assert kwargs['env']['CSC_IDENTITY_AUTO_DISCOVERY'] == 'true'
        calls.append(tuple(command))
        if command == ['pnpm', 'build']:
            raise RuntimeError('测试到达网页构建')

    monkeypatch.setattr(release, 'run', capture)
    def capture_parallel(tasks, *, log):
        parallel_calls.append([tuple(command) for command, _, _ in tasks])
        for command, cwd, env in tasks:
            capture(command, cwd=cwd, env=env, log=log)

    monkeypatch.setattr(release, 'run_parallel', capture_parallel)
    with pytest.raises(RuntimeError, match='测试到达网页构建'):
        release.main()
    expected = [('版本预检', 'arm64')] if desktop else []
    install = ('pnpm', 'install', '--frozen-lockfile', '--ignore-scripts')
    if (release.ROOT / '.pnpm-store').is_dir():
        install += ('--store-dir', str(release.ROOT / '.pnpm-store'))
    expected.append(install)
    if desktop:
        expected.append(('node', 'node_modules/electron/install.js'))
    expected.extend([(release.sys.executable, 'scripts/generate_third_party_notices.py', '--check'),
                     ('pnpm', 'types:check')])
    if not skip:
        expected.extend([(release.sys.executable, '-m', 'pytest', 'tests', '-q'),
                         ('pnpm', 'test:run'), ('pnpm', 'test:desktop')])
    assert calls == [*expected, ('pnpm', 'build')]
    expected_parallel = [[(release.sys.executable, 'scripts/generate_third_party_notices.py', '--check'),
                          ('pnpm', 'types:check')]]
    if desktop:
        expected_parallel.append([('pnpm', 'build'),
                                  (release.sys.executable, 'scripts/build_desktop_runtime.py')])
    assert parallel_calls == expected_parallel
    assert json.loads(next(tmp_path.glob('*/release.json')).read_text())['skip_tests'] is skip


def test_parallel_commands_overlap_and_preserve_output(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    tasks = []
    for own, other, label in ((first, second, '网页输出'), (second, first, '执行器输出')):
        code = (
            'from pathlib import Path; import time\n'
            f'Path({str(own)!r}).touch()\n'
            'deadline = time.monotonic() + 3\n'
            f'while not Path({str(other)!r}).exists():\n'
            '    assert time.monotonic() < deadline, "两个命令没有并发启动"\n'
            '    time.sleep(0.01)\n'
            f'print({label!r})\n'
        )
        tasks.append(([sys.executable, '-u', '-c', code], tmp_path, os.environ.copy()))
    log = StringIO()
    release.run_parallel(tasks, log=log)
    assert '网页输出\n' in log.getvalue()
    assert '执行器输出\n' in log.getvalue()


@pytest.mark.parametrize('failure_first', [False, True])
def test_parallel_failure_preserves_logs_and_stops_descendants(tmp_path, failure_first):
    ready, child_ready, stopped = (tmp_path / name for name in ('ready', 'child-ready', 'stopped'))
    child = (
        'from pathlib import Path; import signal, time\n'
        'def stop(*_):\n'
        f'    Path({str(stopped)!r}).touch()\n'
        '    raise SystemExit(0)\n'
        'signal.signal(signal.SIGTERM, stop)\n'
        f'Path({str(child_ready)!r}).touch()\n'
        'time.sleep(6)\n'
    )
    sibling = (
        'from pathlib import Path; import subprocess, sys, time, signal\n'
        f'child = subprocess.Popen([sys.executable, "-u", "-c", {child!r}])\n'
        'signal.signal(signal.SIGTERM, lambda *_: child.wait(timeout=1))\n'
        'deadline = time.monotonic() + 3\n'
        f'while not Path({str(child_ready)!r}).exists():\n'
        '    assert time.monotonic() < deadline\n'
        '    time.sleep(0.01)\n'
        'print("另一命令已开始")\n'
        f'Path({str(ready)!r}).touch()\n'
        'child.wait()\n'
    )
    failed = (
        'from pathlib import Path; import sys, time\n'
        'deadline = time.monotonic() + 3\n'
        f'while not Path({str(ready)!r}).exists():\n'
        '    assert time.monotonic() < deadline\n'
        '    time.sleep(0.01)\n'
        'print("失败前的标准输出")\n'
        'print("真实构建错误", file=sys.stderr)\n'
        'sys.exit(7)\n'
    )
    codes = [failed, sibling] if failure_first else [sibling, failed]
    tasks = [([sys.executable, '-u', '-c', code], tmp_path, os.environ.copy()) for code in codes]
    log = StringIO()
    started = time.monotonic()
    with pytest.raises(subprocess.CalledProcessError) as raised:
        release.run_parallel(tasks, log=log)
    assert raised.value.returncode == 7
    assert time.monotonic() - started < 4
    assert stopped.exists(), '取消任务时没有通知其子进程退出'
    assert '另一命令已开始\n' in log.getvalue()
    assert '失败前的标准输出\n' in log.getvalue()
    assert '真实构建错误\n' in log.getvalue()


def test_parallel_failure_kills_command_ignoring_termination(tmp_path):
    ready = tmp_path / 'ready'
    sibling = (
        'from pathlib import Path; import signal, time\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        f'Path({str(ready)!r}).touch()\n'
        'time.sleep(8)\n'
    )
    failed = (
        'from pathlib import Path; import time\n'
        'deadline = time.monotonic() + 3\n'
        f'while not Path({str(ready)!r}).exists():\n'
        '    assert time.monotonic() < deadline\n'
        '    time.sleep(0.01)\n'
        'raise SystemExit(9)\n'
    )
    tasks = [([sys.executable, '-u', '-c', code], tmp_path, os.environ.copy()) for code in (sibling, failed)]
    started = time.monotonic()
    with pytest.raises(subprocess.CalledProcessError) as raised:
        release.run_parallel(tasks, log=StringIO())
    assert raised.value.returncode == 9
    assert time.monotonic() - started < 4


@pytest.mark.parametrize('error_type', [FileNotFoundError, KeyboardInterrupt])
def test_parallel_start_failure_or_interrupt_cleans_started_commands(tmp_path, monkeypatch, error_type):
    ready = tmp_path / 'ready'
    code = (
        'from pathlib import Path; import time\n'
        'print("启动异常前的输出")\n'
        f'Path({str(ready)!r}).touch()\n'
        'time.sleep(8)\n'
    )
    original_popen = subprocess.Popen
    children = []

    def start(*args, **kwargs):
        if children:
            deadline = time.monotonic() + 3
            while not ready.exists():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            raise error_type('模拟启动失败或人工中断')
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(release.subprocess, 'Popen', start)
    tasks = [([sys.executable, '-u', '-c', code], tmp_path, os.environ.copy())] * 2
    log = StringIO()
    with pytest.raises(error_type):
        release.run_parallel(tasks, log=log)
    assert children[0].poll() is not None
    assert '启动异常前的输出\n' in log.getvalue()


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


@pytest.mark.parametrize('failure', ['backup', 'verify', 'smoke'])
def test_server_timeout_can_resume_and_failed_check_restores_previous_service(tmp_path, monkeypatch, release_guard_dir, failure):
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
    backups = tmp_path / 'backups'
    old_backup = backups / '20261113t034159z-c87d897ac01d'
    old_backup.mkdir(parents=True)
    (old_backup / 'accounts.tar.gz').write_bytes(b'previous')
    (old_backup / 'previous-release.json').write_text('{}')
    monkeypatch.setattr(release_server, 'BACKUPS', backups)
    monkeypatch.setattr(release_server, 'PYTHON_OVERRIDE', override)
    monkeypatch.setattr(release_server.os, 'geteuid', lambda: 0)
    calls = []
    active = {'server', 'active', '商品提交'}
    monkeypatch.setattr(release_server, 'in_use', lambda directory, name: name in active)
    def run(*command):
        calls.append(command)
        if '--archive' in command:
            assert old_backup.is_dir()
            if command[2] == failure:
                raise RuntimeError('发布检查失败')
            if command[2] == 'backup':
                (backups / args.release_id / 'accounts.tar.gz').write_bytes(b'current')

    monkeypatch.setattr(release_server, 'run', run)
    monkeypatch.setattr(release_server, 'wait_ready', lambda: None)
    def failed(path):
        assert not old_backup.exists()
        assert (backups / args.release_id / 'accounts.tar.gz').read_bytes() == b'current'
        raise RuntimeError('发布检查失败')
    monkeypatch.setattr(release_server, 'smoke_check', failed)
    args = argparse.Namespace(archive=archive, sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        release_id='20261114t074137z-b7ea0ac50729', revision='new-revision', expected_current=str(previous), wait_tasks_seconds=0)
    with pytest.raises(TimeoutError, match='商品提交'):
        release_server.deploy(args)
    assert json.loads((staging / 'server-result.json').read_text())['status'] == 'prepared'
    assert not any(call[0] == 'systemctl' for call in calls)
    active.remove('active')
    with pytest.raises(RuntimeError, match='发布检查失败'):
        release_server.deploy(args)
    if failure != 'smoke':
        assert (old_backup / 'accounts.tar.gz').read_bytes() == b'previous'
    assert (base / 'current').resolve() == previous
    assert override.read_text() == 'previous-python'
    assert json.loads((staging / 'server-result.json').read_text())['status'] == 'rolled_back'
    assert calls[-1] == ('systemctl', 'start', release_server.SERVICE)


def test_release_backup_cleanup_preserves_manual_files_and_symlinks(tmp_path):
    current = tmp_path / '20261114t075316z-b7ea0ac50729'
    current.mkdir()
    manual = tmp_path / '手动备份'
    manual.mkdir()
    mixed = tmp_path / '20261113t000000z-000000000000'
    mixed.mkdir()
    for directory in (current, manual, mixed):
        (directory / 'accounts.tar.gz').write_bytes(b'data')
        (directory / 'previous-release.json').write_text('{}')
    (mixed / '人工恢复说明').touch()
    link = tmp_path / '20261113t000001z-000000000000'
    link.symlink_to(manual, target_is_directory=True)
    release_server.prune_release_backups(current)
    assert link.is_symlink()
    assert (mixed / '人工恢复说明').is_file()
    for directory in (current, manual, mixed):
        assert (directory / 'accounts.tar.gz').read_bytes() == b'data'
        assert (directory / 'previous-release.json').is_file()


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


@pytest.mark.parametrize('provider', ['alibaba', 'alibaba_crossborder', 'onebound'])
@pytest.mark.parametrize('leak_secret', [False, True])
def test_server_smoke_checks_selected_application_and_revokes_session(tmp_path, monkeypatch, provider, leak_secret):
    from contextlib import redirect_stdout
    from io import StringIO
    from types import SimpleNamespace
    from urllib.error import HTTPError
    import urllib.request
    from erp_web.stores import account_store

    revoked = []
    store = SimpleNamespace(admin=lambda: {'id': 'admin', 'status': 'enabled', 'password_hash': 'hash'},
        create_session=lambda *args: ('temporary-token', SimpleNamespace(csrf_token='csrf', token_hash='token-hash')),
        revoke_session=revoked.append)
    monkeypatch.setattr(account_store, 'AccountStore', lambda path: store)
    applications = {key: {'app_key': value, 'secret_configured': True}
                    for key, value in [('alibaba', '1938727'), ('alibaba_crossborder', '4848173')]}
    applications['onebound'] = {'api_key': 'vendor-key', 'secret_configured': True}
    if leak_secret:
        applications[provider]['api_secret' if provider == 'onebound' else 'app_secret'] = 'must-not-be-public'
    replies = {
        '/api/account/session': {'user': {'role': 'admin'}},
        '/api/admin/settings': {'ok': True, 'revision': 'revision',
            'exchange_rates': {'exchange_rate_api_url': 'https://rates.example'},
            'alibaba_api_application': provider, **applications},
        '/api/alibaba/authorization': {'ok': True, 'application_type': provider, 'authorized': True, 'can_authorize': True},
        '/api/shipping/tariffs': {'items': [{'platform': name, 'version': '', 'rules_count': 0, 'source': 'shared'}
                                          for name in ('yandex', 'ozon')]},
        '/api/system-settings/forbidden-categories': {'categories': []},
        '/api/account/logout': {},
    }
    requests = []

    def fetch(request, timeout):
        path = request.full_url.removeprefix('http://127.0.0.1:5050')
        requests.append(path)
        if not request.has_header('Cookie'):
            raise HTTPError(request.full_url, 401, '请先登录', {}, None)
        response = BytesIO(json.dumps(replies[path]).encode())
        response.status = 200
        return response

    monkeypatch.setattr(urllib.request, 'urlopen', fetch)
    monkeypatch.setenv('ERP_APP_DIR', str(tmp_path))

    def execute(command, **kwargs):
        output = StringIO()
        with redirect_stdout(output):
            exec(compile(command[-1], '<发布验收>', 'exec'), {})
        return output.getvalue()

    monkeypatch.setattr(release_server.subprocess, 'check_output', execute)
    if leak_secret:
        with pytest.raises(AssertionError):
            release_server.smoke_check(tmp_path)
    else:
        result = release_server.smoke_check(tmp_path)
        assert result['alibaba'] == {'application': provider, 'authorized': True,
            'app_key': applications[provider].get('app_key', ''), 'can_authorize': True}
        assert result['anonymous_admin_denied']
    assert revoked == ['token-hash'] and requests[-1] == '/api/account/logout'


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
    monkeypatch.setattr(release, 'cleanup_server_artifacts', lambda *args: {})
    release.resume_release(argparse.Namespace(resume=tmp_path, public_url='https://erp.example'))
    assert calls == ['server', 'public'] + ([desktop['policy']] if desktop else [])
    assert json.loads((tmp_path / 'release.json').read_text())['status'] == 'complete'


def test_select_server_files_excludes_non_runtime_directories():
    tracked = [
        'erp_web/server.py',
        'erp_web/assets/fonts/NotoSansCJKsc-Regular.otf',
        'erp_shared/models.py',
        'deploy/release_server.py',
        'config/presets/platforms.json',
        'requirements.txt',
        'LICENSE',
        'scripts/install_release_dependencies.py',
        'scripts/migrate_credentials.py',
        'scripts/eval_agent_python.py',
        'front/src/App.vue',
        'tests/test_release.py',
        'docs/server-deployment.md',
        'desktop_runtime/main.py',
        'AGENTS.md',
    ]
    selected = release.select_server_files(tracked)
    assert selected == [
        'erp_web/server.py',
        'erp_web/assets/fonts/NotoSansCJKsc-Regular.otf',
        'erp_shared/models.py',
        'deploy/release_server.py',
        'config/presets/platforms.json',
        'requirements.txt',
        'LICENSE',
        'scripts/install_release_dependencies.py',
        'scripts/migrate_credentials.py',
    ]


def test_detect_reusable_server_assets_probe_runs_via_shell_and_filters_matches(tmp_path, monkeypatch):
    import shlex

    source = tmp_path / 'source'
    shared = tmp_path / 'shared-assets'
    current = tmp_path / 'current'
    shared.mkdir()
    current.mkdir()

    def write_asset(rel, payload):
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    size = release.LARGE_ASSET_THRESHOLD
    sha_shared = write_asset('erp_web/assets/fonts/shared.otf', b'S' * size)
    sha_current = write_asset('erp_web/assets/fonts/current.otf', b'C' * size)
    sha_mismatch = write_asset('erp_web/assets/fonts/mismatch.otf', b'M' * size)
    sha_symlink = write_asset('erp_web/assets/fonts/symlink.otf', b'L' * size)
    write_asset('erp_web/assets/small.png', b'tiny')

    (shared / sha_shared).write_bytes(b'S' * size)
    cur_file = current / 'erp_web/assets/fonts/current.otf'
    cur_file.parent.mkdir(parents=True, exist_ok=True)
    cur_file.write_bytes(b'C' * size)
    (shared / sha_mismatch).write_bytes(b'X' * size)
    real_target = tmp_path / 'outside.otf'
    real_target.write_bytes(b'L' * size)
    (shared / sha_symlink).symlink_to(real_target)

    def fake_remote(args, command):
        assert command[0] == 'python3' and command[1] == '-c'
        joined = shlex.join([sys.executable, *command[1:], str(shared), str(current)])
        return subprocess.check_output(['sh', '-c', joined], text=True).strip()

    monkeypatch.setattr(release, 'remote_output', fake_remote)
    selected = [
        'erp_web/assets/fonts/shared.otf',
        'erp_web/assets/fonts/current.otf',
        'erp_web/assets/fonts/mismatch.otf',
        'erp_web/assets/fonts/symlink.otf',
        'erp_web/assets/small.png',
    ]
    reusable = release.detect_reusable_server_assets(argparse.Namespace(), source, selected)
    assert reusable == {
        'erp_web/assets/fonts/shared.otf': sha_shared,
        'erp_web/assets/fonts/current.otf': sha_current,
    }


def test_ssh_session_multiplexes_ssh_but_keeps_scp_dedicated(tmp_path):
    identity = tmp_path / 'id_ed25519'
    identity.touch()
    args = argparse.Namespace(ssh_host='root@example', ssh_port=22, ssh_identity=identity)
    with release.ssh_session(args):
        ssh_cmd = release.ssh_args(args)
        direct_cmd = release.direct_ssh_args(args)
        scp_cmd = release.scp_args(args)
        assert 'ControlMaster=auto' in ssh_cmd
        assert any(opt.startswith('ControlPath=') for opt in ssh_cmd)
        assert 'ControlMaster=auto' not in direct_cmd
        assert not any(opt.startswith('ControlPath=') for opt in direct_cmd)
        assert 'Compression=no' in direct_cmd
        assert 'ControlMaster=auto' not in scp_cmd
        assert not any(opt.startswith('ControlPath=') for opt in scp_cmd)


@pytest.mark.parametrize(('flags', 'expected'), [
    ([], 'ssh'),
    (['--desktop-transfer', 'ssh'], 'ssh'),
    (['--desktop-transfer', 'tunnel'], 'tunnel'),
])
def test_release_cli_selects_desktop_transfer_mode(flags, expected, tmp_path, monkeypatch):
    options = []
    monkeypatch.setattr(release.sys, 'argv', ['release.py', '--output-dir', str(tmp_path), *flags])
    monkeypatch.setattr(release, 'release', options.append)
    release.main()
    assert options[0].desktop_transfer == expected


def test_release_requires_cloudflared_only_in_tunnel_mode(tmp_path, monkeypatch):
    identity = tmp_path / 'id'
    identity.touch()
    monkeypatch.setattr(release.sys, 'platform', 'darwin')
    monkeypatch.setattr(release.shutil, 'which', lambda name: None)
    monkeypatch.setattr(release, 'output', lambda *args: 'dirty')
    base = dict(ssh_identity=identity, desktop_update='optional', output_dir=tmp_path)
    with pytest.raises(RuntimeError, match='cloudflared'):
        release.release(argparse.Namespace(**base, desktop_transfer='tunnel'))
    with pytest.raises(RuntimeError, match='提交本次代码修改'):
        release.release(argparse.Namespace(**base, desktop_transfer='ssh'))


def test_sync_shared_assets_and_prepare_venv_reuse(tmp_path, monkeypatch):
    base = tmp_path / 'server'
    monkeypatch.setattr(release_server, 'BASE', base)
    current = base / 'releases/current-rel'
    font = current / 'erp_web/assets/fonts/NotoSansCJKsc-Regular.otf'
    font.parent.mkdir(parents=True)
    payload = b'font-data-' * 70000
    font.write_bytes(payload)
    font_sha = hashlib.sha256(payload).hexdigest()

    new_rel = base / 'releases/new-rel'
    new_rel.mkdir(parents=True)
    release_server.sync_shared_assets(new_rel, current, {'shared_assets': {'erp_web/assets/fonts/NotoSansCJKsc-Regular.otf': font_sha}})
    assert (new_rel / 'erp_web/assets/fonts/NotoSansCJKsc-Regular.otf').read_bytes() == payload
    assert (base / 'shared-assets' / font_sha).is_file()

    (new_rel / 'requirements.txt').write_text('example==1.0\n')
    runs = []

    def fake_run(*args):
        runs.append(args)
        if args[1:3] == ('-m', 'venv'):
            venv_dir = args[3]
            (venv_dir / 'bin').mkdir(parents=True, exist_ok=True)
            (venv_dir / 'bin/python').write_text('#!/bin/sh\n')

    monkeypatch.setattr(release_server, 'run', fake_run)
    release_server.prepare_venv(new_rel)
    assert len(runs) == 3
    assert (new_rel / '.venv').is_symlink()

    second_rel = base / 'releases/second-rel'
    second_rel.mkdir(parents=True)
    (second_rel / 'requirements.txt').write_text('example==1.0\n')
    runs.clear()
    release_server.prepare_venv(second_rel)
    assert runs == []
    assert (second_rel / '.venv').resolve() == (new_rel / '.venv').resolve()


def test_local_release_cleanup_prunes_old_artifacts_and_preserves_records(tmp_path):
    ids = [
        '20261118t000000z-' + 'a' * 12,
        '20261119t000000z-' + 'b' * 12,
        '20261120t000000z-' + 'c' * 12,
        '20261121t000000z-' + 'd' * 12,
    ]
    statuses = ['failed_after_preparing', 'complete', 'complete', 'complete']
    for rel_id, status in zip(ids, statuses):
        folder = tmp_path / rel_id
        (folder / 'desktop/mac-arm64/gongluErp.app').mkdir(parents=True)
        (folder / 'desktop/mac-arm64/gongluErp.app/bin').write_bytes(b'app')
        (folder / 'desktop/app.dmg').write_bytes(b'dmg')
        (folder / 'desktop/app.dmg.blockmap').write_bytes(b'bm')
        (folder / 'desktop/builder-debug.yml').write_text('debug')
        (folder / 'server.tar.gz').write_bytes(b'tar')
        (folder / 'build.log').write_text('log')
        release.write_record(folder / 'release.json', {'release_id': rel_id, 'status': status})
    manual = tmp_path / 'manual-notes'
    manual.mkdir()
    (manual / 'note.txt').write_text('keep')

    release.cleanup_local_releases(tmp_path, ids[-1], keep_completed=2)
    # 所有目录的 mac-arm64 中间目录与 blockmap 均被移除。
    for rel_id in ids:
        assert not (tmp_path / rel_id / 'desktop/mac-arm64').exists()
        assert not (tmp_path / rel_id / 'desktop/app.dmg.blockmap').exists()
        assert (tmp_path / rel_id / 'release.json').is_file()
        assert (tmp_path / rel_id / 'build.log').is_file()
    # 旧失败版和超出保留数的旧成功版清理了 desktop/ 与 server.tar.gz。
    for rel_id in ids[:2]:
        assert not (tmp_path / rel_id / 'desktop').exists()
        assert not (tmp_path / rel_id / 'server.tar.gz').exists()
    # 最近 2 个成功版保留安装包和 server.tar.gz，人工目录保留。
    for rel_id in ids[2:]:
        assert (tmp_path / rel_id / 'desktop/app.dmg').is_file()
        assert (tmp_path / rel_id / 'server.tar.gz').is_file()
    assert (manual / 'note.txt').read_text() == 'keep'


def test_backup_single_pass_and_streaming_verify(tmp_path):
    import sqlite3
    from deploy import backup_accounts

    app_dir = tmp_path / 'app'
    (app_dir / 'data/images').mkdir(parents=True)
    (app_dir / 'data/images/sample.jpg').write_bytes(b'\xff\xd8\xff' + b'0' * 4096)
    (app_dir / 'backups').mkdir()
    (app_dir / 'backups/skip.tar.gz').write_bytes(b'ignored')
    with sqlite3.connect(app_dir / 'erp.sqlite3') as db:
        db.execute('CREATE TABLE items (id TEXT PRIMARY KEY)')
        db.execute("INSERT INTO items VALUES ('ok-1')")
    env_file = tmp_path / 'newapi.env'
    env_file.write_text('TOKEN=secret\n')
    archive = tmp_path / 'out/accounts.tar.gz'

    backup_accounts.backup(app_dir, env_file, archive)
    backup_accounts.verify(archive)

    # 篡改包内图片后应被流式校验检出。
    tampered = tmp_path / 'out/tampered.tar.gz'
    with tarfile.open(archive, 'r:gz') as src, tarfile.open(tampered, 'w:gz') as dst:
        for member in src:
            data = src.extractfile(member).read() if member.isfile() else None
            if member.name == 'app/data/images/sample.jpg':
                data = b'corrupted'
                member.size = len(data)
            dst.addfile(member, BytesIO(data) if data is not None else None)
    with pytest.raises(RuntimeError, match='摘要不一致'):
        backup_accounts.verify(tampered)

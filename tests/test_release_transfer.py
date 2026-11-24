"""在回环网络验证桌面包拉取、续传和发布终端的实时反馈。"""
from contextlib import contextmanager
from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from scripts import release
from scripts.release_tunnel import artifact_origin, desktop_tunnel


def test_origin_restricts_files_and_honors_ranges(tmp_path):
    path = tmp_path / '包.zip'
    path.write_bytes(b'0123456789')
    with artifact_origin({'update.zip': path}) as (origin, prefix):
        url = origin + prefix + '/update.zip'
        with urlopen(Request(url, headers={'Range': 'bytes=3-5'})) as response:
            assert response.status == 206
            assert response.headers['Content-Range'] == 'bytes 3-5/10'
            assert response.read() == b'345'
        with urlopen(Request(url, method='HEAD')) as response:
            assert response.headers['Content-Length'] == '10'
            assert response.read() == b''
        for suffix in ('/', '/update.zip', prefix + '/', prefix + '/other'):
            with pytest.raises(HTTPError) as error:
                urlopen(origin + suffix)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url, headers={'Range': 'bytes=10-'}))
        assert error.value.code == 416


def test_parallel_resume_progress_checksum_and_reuse(tmp_path, monkeypatch):
    staging = tmp_path / '发布 暂存'
    staging.mkdir()
    shutil.copy(release.ROOT / 'deploy/pull_desktop.py', staging)
    monkeypatch.setattr(release, 'ssh_args', lambda args: ['sh', '-c'])
    files = {name: tmp_path / name for name in ('installer.dmg', 'update.zip')}
    payloads = {name: os.urandom(128 * 1024) for name in files}
    for name, content in payloads.items():
        files[name].write_bytes(content)
    artifacts = {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                 for name, data in payloads.items()}
    # 一个正确断点、一个损坏断点；均不能重放为未经校验的成品。
    (staging / 'installer.dmg').write_bytes(payloads['installer.dmg'][:32768])
    (staging / 'update.zip.https.part').write_bytes(b'x' * 32768)
    requests = []
    parallel = threading.Barrier(2)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            name = self.path.lstrip('/')
            body = payloads[name]
            requested = self.headers.get('Range')
            requests.append((name, requested))
            if requested:
                parallel.wait(timeout=8)
                self.send_response(206)
                self.send_header('Content-Range', f'bytes 32768-{len(body) - 1}/{len(body)}')
                body = body[32768:]
            else:
                self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body[:8192])
            if requested:
                threading.Event().wait(5.1)
            self.wfile.write(body[8192:])

    @contextmanager
    def tunnel(_files):
        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                yield f'http://127.0.0.1:{server.server_port}'
            finally:
                server.shutdown()
                worker.join()

    monkeypatch.setattr(release, 'desktop_tunnel', tunnel)
    run = partial(release.pull_desktop_files, None, str(staging), files, artifacts)
    log = io.StringIO()
    run(log)
    assert sorted(requests[:2]) == [('installer.dmg', 'bytes=32768-'), ('update.zip', 'bytes=32768-')]
    assert requests[2:] == [('update.zip', None)]
    assert '25%' in log.getvalue() and '100%' in log.getvalue() and 'KiB/s' in log.getvalue()
    assert '校验通过' in log.getvalue() and '停滞' in log.getvalue()
    assert any(float(rate) > 0 for rate in re.findall(r'([\d.]+) KiB/s', log.getvalue()))
    for name in files:
        assert (staging / name).read_bytes() == payloads[name]
    assert not list(staging.glob('*.part'))

    monkeypatch.setattr(release, 'desktop_tunnel', lambda *_: pytest.fail('已校验的包不应重传'))
    log = io.StringIO()
    run(log)
    assert log.getvalue().count('已复用') == 2


def test_pull_desktop_captures_curl_stderr_and_display_gate_avoids_cursor_overwrite(tmp_path, monkeypatch, capsys):
    staging = tmp_path / 'staging'
    staging.mkdir()
    shutil.copy(release.ROOT / 'deploy/pull_desktop.py', staging)
    monkeypatch.setattr(release, 'ssh_args', lambda args: ['sh', '-c'])
    payload = b'desktop-installer-data' * 1024
    artifact = tmp_path / 'installer.dmg'
    artifact.write_bytes(payload)
    artifacts = {'installer.dmg': {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}}
    attempts = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    @contextmanager
    def tunnel(_files):
        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                yield f'http://127.0.0.1:{server.server_port}'
            finally:
                server.shutdown()
                worker.join()

    monkeypatch.setattr(release, 'desktop_tunnel', tunnel)
    gate = threading.Event()
    gate.set()
    log = io.StringIO()
    release.pull_desktop_files(None, str(staging), {'installer.dmg': artifact}, artifacts, log, display_gate=gate)
    captured = capsys.readouterr()
    assert attempts == 2
    assert 'curl:' not in captured.err
    assert 'curl:' not in captured.out
    assert not captured.out.startswith('\x1b[')
    assert (staging / 'installer.dmg').read_bytes() == payload


def test_server_upload_shows_terminal_progress_with_redirected_output(tmp_path):
    log = tmp_path / 'build.log'
    program = """import os, sys
assert os.isatty(2)
print('server.tar.gz  50%  32 KiB/s', end='\\r', file=sys.stderr, flush=True)
print('server.tar.gz 100%  64 KiB/s', file=sys.stderr, flush=True)
sys.exit(5)
"""
    runner = """from pathlib import Path
from scripts.release import upload_server_files
import subprocess, sys
with Path(sys.argv[1]).open('w') as log:
    try:
        upload_server_files([sys.executable, '-c', sys.argv[2]], log)
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
"""
    result = subprocess.run([sys.executable, '-c', runner, str(log), program], stdin=subprocess.DEVNULL,
                            cwd=release.ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 5
    for output in (result.stdout, log.read_text().split('\n', 2)[-1]):
        assert '50%  32 KiB/s\nserver.tar.gz 100%  64 KiB/s' in output


def test_tunnel_closes_process_and_origin_after_interruption(tmp_path, monkeypatch):
    binary = tmp_path / 'cloudflared'
    state = tmp_path / 'state'
    binary.write_text(f'''#!{sys.executable}
import os, sys, time
from pathlib import Path
assert sys.argv[sys.argv.index('--protocol') + 1] == 'quic'
Path({str(state)!r}).write_text(str(os.getpid()) + ' ' + sys.argv[sys.argv.index('--url') + 1])
print('https://fixture.trycloudflare.com', flush=True)
print('Registered tunnel connection', flush=True)
time.sleep(60)
''')
    binary.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    path = tmp_path / 'desktop.dmg'
    path.write_bytes(b'installer')
    with pytest.raises(KeyboardInterrupt), desktop_tunnel({'installer.dmg': path}) as public:
        assert public.startswith('https://fixture.trycloudflare.com/')
        pid, origin = state.read_text().split()
        url = origin + '/' + public.rsplit('/', 1)[1] + '/installer.dmg'
        with urlopen(url, timeout=2) as response:
            assert response.read() == b'installer'
        raise KeyboardInterrupt
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid), 0)
    with pytest.raises(OSError):
        urlopen(url, timeout=2)


def test_direct_ssh_push_parallel_resume_corrupted_reset_and_reuse(tmp_path, monkeypatch):
    staging = tmp_path / '发布 暂存'
    staging.mkdir()
    shutil.copy(release.ROOT / 'deploy/pull_desktop.py', staging)
    monkeypatch.setattr(release, 'direct_ssh_args', lambda args: ['sh', '-c'])

    files = {name: tmp_path / name for name in ('installer.dmg', 'update.zip')}
    payloads = {name: os.urandom(256 * 1024) for name in files}
    for name, content in payloads.items():
        files[name].write_bytes(content)
    artifacts = {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                 for name, data in payloads.items()}

    # 一个来自 tunnel 的有效断点（跨模式续传），一个损坏的 ssh 断点（应重置从 0 重传）。
    (staging / 'installer.dmg.https.part').write_bytes(payloads['installer.dmg'][:65536])
    (staging / 'update.zip.ssh.part').write_bytes(b'x' * 65536)

    seen_offsets = {}
    real_popen = subprocess.Popen

    def spy_popen(cmd, *args, **kwargs):
        shell_cmd = cmd[-1]
        if '--receive' in shell_cmd:
            m = re.search(r'--receive\s+(\S+)\s+--offset\s+(\d+)', shell_cmd)
            if m:
                seen_offsets[m.group(1)] = int(m.group(2))
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, 'Popen', spy_popen)
    log = io.StringIO()
    release.push_desktop_files(None, str(staging), files, artifacts, log, chunk_size=32 * 1024)

    assert seen_offsets == {'installer.dmg': 65536, 'update.zip': 0}
    for name in files:
        assert (staging / name).read_bytes() == payloads[name]
    assert not list(staging.glob('*.part'))
    assert '100%' in log.getvalue() and '校验通过' in log.getvalue()

    # 第二次直接复用，不应再触发 --receive。
    seen_offsets.clear()
    log2 = io.StringIO()
    release.push_desktop_files(None, str(staging), files, artifacts, log2)
    assert seen_offsets == {}
    assert log2.getvalue().count('已复用') == 2


def test_direct_ssh_push_retries_partial_disconnect_and_honors_display_gate(tmp_path, monkeypatch, capsys):
    staging = tmp_path / 'staging'
    staging.mkdir()
    shutil.copy(release.ROOT / 'deploy/pull_desktop.py', staging)
    monkeypatch.setattr(release, 'direct_ssh_args', lambda args: ['sh', '-c'])

    payload = os.urandom(192 * 1024)
    artifact = tmp_path / 'installer.dmg'
    artifact.write_bytes(payload)
    artifacts = {'installer.dmg': {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}}

    receive_calls = []
    real_popen = subprocess.Popen

    def flaky_popen(cmd, *args, **kwargs):
        shell_cmd = cmd[-1]
        if '--receive' in shell_cmd:
            m = re.search(r'--offset\s+(\d+)', shell_cmd)
            offset = int(m.group(1))
            receive_calls.append(offset)
            if len(receive_calls) == 1:
                # 首次连接写入前 64 KiB 后模拟远端连接中断，保留部分文件供续传。
                (staging / 'installer.dmg.ssh.part').write_bytes(payload[:65536])
                return real_popen(['sh', '-c', 'echo "READY 0"; echo "PROGRESS 65536"; echo "INCOMPLETE 65536"; exit 1'],
                                  *args, **kwargs)
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, 'Popen', flaky_popen)
    monkeypatch.setattr(release.time, 'sleep', lambda *_: None)

    gate = threading.Event()  # 未打开时不应输出到终端
    log = io.StringIO()
    release.push_desktop_files(None, str(staging), {'installer.dmg': artifact}, artifacts, log, display_gate=gate)
    captured = capsys.readouterr()
    assert receive_calls == [0, 65536]
    assert captured.out == ''
    assert (staging / 'installer.dmg').read_bytes() == payload
    assert '校验通过' in log.getvalue()


def test_tunnel_download_prepare_adopts_ssh_partial_file(tmp_path, monkeypatch):
    from deploy import pull_desktop

    monkeypatch.chdir(tmp_path)
    payload = b'0123456789abcdef' * 1024
    (tmp_path / 'installer.dmg.ssh.part').write_bytes(payload[:4096])
    item = pull_desktop.Download(tmp_path / 'installer.dmg', len(payload), hashlib.sha256(payload).hexdigest())
    item.prepare()
    assert not (tmp_path / 'installer.dmg.ssh.part').exists()
    assert (tmp_path / 'installer.dmg.https.part').read_bytes() == payload[:4096]
    assert item.received() == 4096


def test_direct_ssh_receive_reports_small_chunk_progress_and_preempts_stale_receiver(tmp_path):
    staging = tmp_path / 'staging'
    staging.mkdir()
    script = staging / 'pull_desktop.py'
    shutil.copy(release.ROOT / 'deploy/pull_desktop.py', script)

    payload = os.urandom(128 * 1024)
    digest = hashlib.sha256(payload).hexdigest()

    # 启动一个模拟卡死未退出的旧接收进程，写入 8 KiB 后不再继续发送。
    stale = subprocess.Popen(
        [sys.executable, '-u', str(script), '--receive', 'installer.dmg',
         '--offset', '0', '--size', str(len(payload)), '--sha256', digest],
        cwd=staging, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert stale.stdout.readline().decode().strip() == 'READY 0'
        # 仅写入 8 KiB（远小于原 256 KiB 块大小），验证能立刻回报 PROGRESS 而不阻塞。
        stale.stdin.write(payload[:8192])
        stale.stdin.flush()
        import time as _time
        _time.sleep(0.45)
        stale.stdin.write(payload[8192:16384])
        stale.stdin.flush()
        progress_line = stale.stdout.readline().decode().strip()
        assert progress_line == 'PROGRESS 16384'

        # 旧进程仍持有文件锁时，带 --preempt 的探测应优雅接管而不是抛 BlockingIOError。
        probe = subprocess.run(
            [sys.executable, '-u', str(script), '--probe', '--preempt'],
            cwd=staging,
            input=json.dumps({'artifacts': {'installer.dmg': {'size': len(payload), 'sha256': digest}}}) + '\n',
            capture_output=True, text=True, timeout=10,
        )
        assert probe.returncode == 0, probe.stderr
        probed = json.loads(probe.stdout)
        assert probed['installer.dmg']['status'] == 'partial'
        assert probed['installer.dmg']['offset'] == 16384
        assert probed['installer.dmg']['prefix_sha256'] == hashlib.sha256(payload[:16384]).hexdigest()
        assert stale.wait(timeout=5) is not None
    finally:
        if stale.poll() is None:
            stale.kill()
            stale.wait()


def test_direct_ssh_push_resets_retry_budget_after_progress(tmp_path, monkeypatch):
    staging = tmp_path / 'staging'
    staging.mkdir()
    shutil.copy(release.ROOT / 'deploy/pull_desktop.py', staging)
    monkeypatch.setattr(release, 'direct_ssh_args', lambda args: ['sh', '-c'])

    payload = os.urandom(256 * 1024)
    artifact = tmp_path / 'installer.dmg'
    artifact.write_bytes(payload)
    artifacts = {'installer.dmg': {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}}

    receive_offsets = []
    real_popen = subprocess.Popen

    def incremental_flaky_popen(cmd, *args, **kwargs):
        shell_cmd = cmd[-1]
        if '--receive' in shell_cmd:
            m = re.search(r'--offset\s+(\d+)', shell_cmd)
            offset = int(m.group(1))
            receive_offsets.append(offset)
            # 模拟前 6 次每次推进 32 KiB 后断线（超过 5 次固定上限），第 7 次顺利传完。
            if len(receive_offsets) <= 6:
                next_offset = offset + 32 * 1024
                (staging / 'installer.dmg.ssh.part').write_bytes(payload[:next_offset])
                return real_popen(
                    ['sh', '-c', f'echo "READY {offset}"; echo "PROGRESS {next_offset}"; echo "INCOMPLETE {next_offset}"; exit 1'],
                    *args, **kwargs,
                )
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, 'Popen', incremental_flaky_popen)
    monkeypatch.setattr(release.time, 'sleep', lambda *_: None)

    log = io.StringIO()
    release.push_desktop_files(None, str(staging), {'installer.dmg': artifact}, artifacts, log)
    assert len(receive_offsets) == 7
    assert receive_offsets == [i * 32 * 1024 for i in range(7)]
    assert (staging / 'installer.dmg').read_bytes() == payload

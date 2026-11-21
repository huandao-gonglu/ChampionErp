"""在回环网络验证桌面包拉取、续传和发布终端的实时反馈。"""
from contextlib import contextmanager
from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
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

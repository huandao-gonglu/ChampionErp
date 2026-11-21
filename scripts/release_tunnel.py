"""通过临时 QUIC 隧道，仅提供本次发布的桌面安装包。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
from threading import Thread
import time


@contextmanager
def artifact_origin(files):
    prefix = '/' + secrets.token_urlsafe(32)
    routes = {prefix + '/' + name: path for name, path in files.items()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # 请求路径含临时访问凭据。
            pass

        def do_HEAD(self):
            self.serve_file(False)

        def do_GET(self):
            self.serve_file(True)

        def serve_file(self, send_body):
            path = routes.get(self.path)
            if path is None:
                self.send_error(404)
                return
            with path.open('rb') as source:
                size = os.fstat(source.fileno()).st_size
                start, end = 0, size - 1
                requested = self.headers.get('Range')
                if requested is not None:
                    match = re.fullmatch(r'bytes=(\d{1,20})-(\d{0,20})', requested)
                    if match:
                        start = int(match[1])
                        end = min(end, int(match[2])) if match[2] else end
                    if not match or start > end:
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{size}')
                        self.end_headers()
                        return
                self.send_response(206 if requested else 200)
                if requested:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Cache-Control', 'private, no-store')
                self.send_header('Accept-Ranges', 'bytes')
                remaining = end - start + 1
                self.send_header('Content-Length', str(remaining))
                self.end_headers()
                if send_body:
                    source.seek(start)
                    try:
                        while remaining:
                            data = source.read(min(256 * 1024, remaining))
                            if not data:
                                break
                            self.wfile.write(data)
                            remaining -= len(data)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', prefix
        finally:
            server.shutdown()
            worker.join()


@contextmanager
def desktop_tunnel(files):
    with artifact_origin(files) as (origin, prefix), tempfile.TemporaryDirectory() as work:
        log_path = Path(work) / 'cloudflared.log'
        with log_path.open('wb') as log:
            process = subprocess.Popen(['cloudflared', 'tunnel', '--no-autoupdate', '--protocol', 'quic',
                                        '--url', origin], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and process.poll() is None:
                logs = log_path.read_text(errors='replace')
                match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com\b', logs)
                if match and 'Registered tunnel connection' in logs:
                    yield match[0] + prefix
                    return
                time.sleep(0.2)
            raise RuntimeError('桌面传输隧道连接失败，请检查 cloudflared 和 QUIC 网络后用 --resume 续发')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

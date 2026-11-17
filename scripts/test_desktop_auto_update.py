"""实测自签 Electron 自动更新，所有应用、服务及用户目录均与正式 ERP 隔离。"""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / 'front'


def run(command, *, log=None, **kwargs):
    return subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT if log else None, **kwargs)


def events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def clear_test_state(identifier):
    """仅回收本次随机应用标识对应的更新服务与缓存。"""
    subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{identifier}.ShipIt'], capture_output=True)
    for domain in (identifier, identifier + '.ShipIt'):
        subprocess.run(['/usr/bin/defaults', 'delete', domain], capture_output=True)
        shutil.rmtree(Path.home() / 'Library/Caches' / domain, ignore_errors=True)


def verify(work, installed):
    identifier = 'com.champion.erp.updateAcceptance.' + uuid.uuid4().hex
    state = {'asset': None, 'requests': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state['requests'].append(self.path)
            if self.path.endswith('/latest.json'):
                body = json.dumps({
                    'platform': 'darwin', 'arch': 'arm64' if os.uname().machine == 'arm64' else 'x64',
                    'version': '1.0.1', 'minimum': '0.0.0', 'notes': '自动更新隔离验收',
                    'installer': {'file': 'installer.dmg', 'size': 1, 'sha256': '0' * 64},
                    'macZip': state['asset'],
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.endswith('/update.zip'):
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Length', str(state['archive'].stat().st_size))
                self.end_headers()
                with state['archive'].open('rb') as source:
                    shutil.copyfileobj(source, self.wfile)
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('ERP_', 'CHAMPION_ERP_', 'VITE_')) and k != 'ELECTRON_RUN_AS_NODE'}
    environment.update(ERP_DESKTOP_SIGNING_MODE='local', CSC_IDENTITY_AUTO_DISCOVERY='true')
    settings = {'root': str(work), 'identifier': identifier, 'targetVersion': '1.0.1',
                'publicOrigin': 'https://erp-update-test.invalid', 'feedOrigin': f'http://127.0.0.1:{server.server_port}'}
    runtime = work / 'runtime'
    runtime.mkdir()
    run(['/usr/bin/clang', '-x', 'c', '-', '-o', str(runtime / 'champion-erp-runtime')],
        input=b'int main(void) { return 0; }\n')
    try:
        bundles = []
        for version in ('1.0.0', '1.0.1'):
            source = work / f'source-{version}'
            source.mkdir()
            for name in ('updates.cjs', 'native-update-feed.cjs', 'update-signature.cjs'):
                shutil.copy2(FRONT / 'desktop' / name, source / name)
            shutil.copy2(ROOT / 'scripts/fixtures/desktop_auto_update/main.cjs', source / 'main.cjs')
            (source / 'settings.json').write_text(json.dumps(settings))
            (source / 'package.json').write_text(json.dumps({
                'name': 'erp-update-acceptance', 'productName': 'ERP Update Acceptance',
                'version': version, 'main': 'main.cjs', 'author': '自动更新验收', 'description': '隔离的自动更新验收应用',
            }))
            config = work / f'build-{version}.json'
            config.write_text(json.dumps({
                'extends': str(FRONT / 'electron-builder.config.mjs'), 'appId': identifier,
                'productName': 'ERP Update Acceptance',
                'directories': {'app': str(source), 'output': str(work / f'build-{version}')},
                'files': ['**/*'], 'extraResources': [{'from': str(runtime), 'to': 'runtime'}],
                'mac': {'extendInfo': {'LSUIElement': True, 'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}},
            }))
            with (work / f'build-{version}.log').open('w') as log:
                print(f'构建自签测试版本 {version}…', flush=True)
                run(['pnpm', 'exec', 'electron-builder', '--mac', '--dir', '--publish', 'never', '--config', str(config)],
                    cwd=FRONT, env=environment, log=log)
            bundles.append(next((work / f'build-{version}').glob('*/*.app')))

        good_zip = work / 'good.zip'
        run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(bundles[1]), str(good_zip)])
        wrong = work / 'wrong/ERP Update Acceptance.app'
        wrong.parent.mkdir()
        run(['/usr/bin/ditto', str(bundles[1]), str(wrong)])
        run(['/usr/bin/codesign', '--force', '--sign', '-', str(wrong)])
        wrong_zip = work / 'wrong.zip'
        run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(wrong), str(wrong_zip)])
        corrupted = work / 'corrupted.zip'
        corrupted.write_bytes('这不是有效的更新包'.encode())

        results = []
        for scenario, archive, checksum_bad in [
            ('checksum-mismatch', good_zip, True), ('invalid-zip', corrupted, False),
            ('wrong-signature', wrong_zip, False), ('success', good_zip, False),
        ]:
            clear_test_state(identifier)
            if installed.exists():
                shutil.rmtree(installed)
            run(['/usr/bin/ditto', str(bundles[0]), str(installed)])
            state['archive'] = archive
            state['requests'] = []
            state['asset'] = {'file': 'update.zip', 'size': archive.stat().st_size,
                              'sha256': '0' * 64 if checksum_bad else hashlib.sha256(archive.read_bytes()).hexdigest()}
            (work / 'control.json').write_text(json.dumps({'scenario': scenario}))
            report = work / f'{scenario}.jsonl'
            executable = plistlib.loads((installed / 'Contents/Info.plist').read_bytes())['CFBundleExecutable']
            print(f'实测场景：{scenario}', flush=True)
            with (work / f'{scenario}.log').open('w') as log:
                child = subprocess.Popen([str(installed / 'Contents/MacOS' / executable)], stdout=log, stderr=log, env=environment)
                try:
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        history = events(report)
                        if any(e['event'] == 'fatal' for e in history):
                            raise AssertionError(history)
                        if scenario == 'success' and any(e['event'] == 'restarted' for e in history):
                            break
                        if scenario != 'success' and any(e['event'] == 'result' for e in history):
                            break
                        if child.poll() not in (None, 0):
                            raise AssertionError(f'测试程序异常退出：{child.returncode}，{history}')
                        time.sleep(0.25)
                    else:
                        raise TimeoutError(f'{scenario} 超时，事件：{events(report)}')
                    child.wait(timeout=15)
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=10)
            history = events(report)
            actual_version = plistlib.loads((installed / 'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']
            assert actual_version == ('1.0.1' if scenario == 'success' else '1.0.0'), history
            assert any(e['event'] == 'dialog' and '安装并重启' in e.get('buttons', []) for e in history), history
            if scenario == 'success':
                restarted = next(e for e in history if e['event'] == 'restarted')
                assert restarted['version'] == '1.0.1'
                assert str(installed) in restarted['executable']
                assert any(e['event'] == 'downloaded' for e in history)
            else:
                assert not any(e['event'] in {'downloaded', 'restarted'} for e in history), history
                assert any(e['event'] == 'dialog' and e['message'] == '本次更新未完成。' for e in history), history
            results.append({'scenario': scenario, 'passed': True, 'installed_version': actual_version,
                            'events': history, 'requests': state['requests']})
            print(f'通过：{scenario}，安装版本 {actual_version}', flush=True)
        return results
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        clear_test_state(identifier)


def main():
    if sys.platform != 'darwin':
        raise SystemExit('此验收必须在 Mac 上运行。')
    applications = Path.home() / 'Applications'
    applications.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='erp-update-acceptance-') as temporary, \
            tempfile.TemporaryDirectory(prefix='ERP Update Acceptance-', dir=applications) as install_folder:
        work = Path(temporary)
        installed = Path(install_folder) / 'ERP Update Acceptance.app'
        try:
            results = verify(work, installed)
            electron_version = json.loads((FRONT / 'node_modules/electron/package.json').read_text())['version']
            print(json.dumps({'passed': True, 'electron_version': electron_version, 'results': results}, ensure_ascii=False, indent=2))
        except Exception:
            for log in work.glob('*.log'):
                print(f'验收日志 {log.name}：\n' + '\n'.join(log.read_text(errors='replace').splitlines()[-30:]), file=sys.stderr)
            raise


if __name__ == '__main__':
    main()

"""接收已构建的 ERP 发布包，备份数据后切换版本并验证真实 HTTP 入口。"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE = Path('/opt/champion-erp')
BACKUPS = Path('/srv/champion-erp/backups')
SERVICE = 'champion-erp.service'
PYTHON_OVERRIDE = Path('/etc/systemd/system/champion-erp.service.d/release-python.conf')
LOCAL_URL = 'http://127.0.0.1:5050'


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def request_json(path, token=''):
    headers = {'Cookie': 'champion_session=' + token} if token else {}
    with urlopen(Request(LOCAL_URL + path, headers=headers), timeout=10) as response:
        return json.load(response)


def wait_ready():
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            payload = request_json('/api/account/session')
            if payload.get('ok') and payload.get('initialized'):
                return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('应用未在规定时间内就绪')


def smoke_check(release):
    """仅创建短期验证会话，读取设置后通过真实退出入口撤销。"""
    code = '''
import json, os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from erp_web.stores.account_store import AccountStore
from erp_web.schemas.accounts import SESSION_COOKIE
store = AccountStore(Path(os.environ['ERP_APP_DIR']) / 'accounts.sqlite3')
admin = store.admin()
assert admin and admin['status'] == 'enabled', '管理员账号不可用于验证'
token, session = store.create_session(admin['id'], admin['password_hash'])
headers = {'Cookie': SESSION_COOKIE + '=' + token, 'X-CSRF-Token': session.csrf_token}
def read(path, authenticated=True):
    request = Request('http://127.0.0.1:5050' + path, headers=headers if authenticated else {})
    with urlopen(request, timeout=20) as response:
        return json.load(response)
try:
    assert read('/api/account/session')['user']['role'] == 'admin'
    settings = read('/api/admin/settings')
    assert settings['ok'] and settings['revision'] and settings['exchange_rates']['exchange_rate_api_url']
    assert 'app_secret' not in settings['alibaba']
    tariffs = read('/api/shipping/tariffs')
    assert {item['platform'] for item in tariffs['items']} == {'yandex', 'ozon'}
    for item in tariffs['items']:
        if item['version']:
            details = read('/api/shipping/tariffs/rules?platform=' + item['platform'])
            assert details['version'] == item['version'] and len(details['rules']) == item['rules_count']
            assert all(rule['currency'] in ('CNY', 'RUB') for rule in details['rules'])
    categories = read('/api/system-settings/forbidden-categories')
    try:
        read('/api/admin/settings', False)
    except HTTPError as error:
        assert error.code in (401, 403)
    else:
        raise AssertionError('匿名访问管理员设置未被拒绝')
    print(json.dumps({'admin_settings': True, 'tariffs': {item['platform']: {
        'version': item['version'], 'rules_count': item['rules_count'], 'source': item['source']
    } for item in tariffs['items']}, 'forbidden_categories': len(categories['categories']),
        'anonymous_admin_denied': True}, ensure_ascii=False))
finally:
    try:
        with urlopen(Request('http://127.0.0.1:5050/api/account/logout', data=b'{}',
            headers={**headers, 'Content-Type': 'application/json'}, method='POST'), timeout=20) as response:
            assert response.status == 200
    finally:
        store.revoke_session(session.token_hash)
'''
    output = subprocess.check_output([
        'runuser', '-u', 'champion-erp', '--', 'env', 'ERP_APP_DIR=/var/lib/champion-erp',
        str(release / '.venv/bin/python'), '-c', code,
    ], cwd=release, text=True)
    return json.loads(output)


def switch_link(target):
    temporary = BASE / 'current.release-next'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(BASE / 'current')


def deploy(args):
    if os.geteuid() != 0:
        raise RuntimeError('服务器发布须由具有服务管理权限的账号执行')
    archive = args.archive.resolve()
    with archive.open('rb') as source:
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
    if actual != args.sha256:
        raise RuntimeError('上传的发布包摘要不一致')
    # 发布 ID 由本地脚本生成，只允许单层目录名。
    if not args.release_id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-.' for c in args.release_id):
        raise ValueError('发布标识无效')
    release = BASE / 'releases' / args.release_id
    current = (BASE / 'current').resolve(strict=True)
    record_path = archive.parent / 'server-result.json'
    if current == release:
        manifest = json.loads((release / 'release-manifest.json').read_text())
        if manifest['revision'] != args.revision:
            raise RuntimeError('当前发布目录已属于其他代码版本')
        wait_ready()
        result = {'status': 'published', 'release': str(release), 'revision': args.revision,
                  'checks': smoke_check(release)}
        write_json(record_path, result)
        return
    if str(current) != args.expected_current:
        raise RuntimeError('服务器版本已被其他发布更新，请重新核对后发布')
    if release.exists():
        raise RuntimeError('发布目录已经存在，请使用新的发布标识')
    release.mkdir(parents=True, mode=0o755)
    with tarfile.open(archive, 'r:gz') as package:
        package.extractall(release, filter='data')
    manifest = json.loads((release / 'release-manifest.json').read_text())
    if manifest['revision'] != args.revision:
        raise RuntimeError('发布包代码版本不一致')
    print('准备服务器独立依赖环境', flush=True)
    python = release / '.venv/bin/python'
    run('/usr/bin/python3', '-m', 'venv', release / '.venv')
    run(python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', release / 'requirements.txt')
    run(python, '-m', 'pip', 'check')
    run(python, '-m', 'compileall', '-q', release / 'erp_web', release / 'erp_shared')
    run('runuser', '-u', 'champion-erp', '--', 'test', '-r', release / 'erp_web/static/dist/index.html')
    backup_dir = BACKUPS / args.release_id
    backup_dir.mkdir(parents=True, mode=0o700)
    previous_override = PYTHON_OVERRIDE.read_bytes() if PYTHON_OVERRIDE.exists() else None
    if previous_override is not None:
        (backup_dir / 'release-python.conf').write_bytes(previous_override)
    write_json(backup_dir / 'previous-release.json', {'release': str(current), 'python_override_existed': previous_override is not None})
    result = {'status': 'prepared', 'release': str(release), 'previous_release': str(current),
              'revision': args.revision, 'backup': str(backup_dir / 'accounts.tar.gz')}
    write_json(record_path, result)
    try:
        print('短暂停止 ERP，备份并验证业务数据', flush=True)
        run('systemctl', 'stop', SERVICE)
        backup_script = release / 'deploy/backup_accounts.py'
        run(python, backup_script, 'backup', '--archive', result['backup'])
        run(python, backup_script, 'verify', '--archive', result['backup'])
        switch_link(release)
        PYTHON_OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
        PYTHON_OVERRIDE.write_text('[Service]\nExecStart=\nExecStart=/opt/champion-erp/current/.venv/bin/python -m erp_web.server\n')
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', SERVICE)
        wait_ready()
        result.update(status='published', checks=smoke_check(release))
        write_json(record_path, result)
        print('服务器发布与登录设置接口验证通过', flush=True)
    except BaseException:
        print('发布未通过，恢复上一个程序版本', flush=True)
        run('systemctl', 'stop', SERVICE)
        switch_link(current)
        if previous_override is None:
            PYTHON_OVERRIDE.unlink(missing_ok=True)
        else:
            PYTHON_OVERRIDE.write_bytes(previous_override)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', SERVICE)
        wait_ready()
        result['status'] = 'rolled_back'
        write_json(record_path, result)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--expected-current', required=True)
    args = parser.parse_args()
    with (BASE / '.release.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deploy(args)


if __name__ == '__main__':
    main()

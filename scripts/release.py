"""从固定 Git 版本编译、发布服务器，并替换本机 macOS 桌面程序。"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def write_record(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def output(*args, cwd=ROOT):
    return subprocess.check_output([str(arg) for arg in args], cwd=cwd, text=True).strip()


def run(args, *, cwd, log, env):
    log.write('\n运行 ' + shlex.join([str(arg) for arg in args]) + '\n')
    log.flush()
    subprocess.run([str(arg) for arg in args], cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=env, check=True)


def ssh_args(args):
    return ['ssh', '-p', str(args.ssh_port), '-i', str(args.ssh_identity), '-o', 'BatchMode=yes',
            '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', args.ssh_host]


def remote_output(args, command):
    return output(*ssh_args(args), shlex.join([str(arg) for arg in command]))


def check_runtime(executable):
    """验证发行包中的真实执行器及 EOF 关闭，不接触本机业务目录。"""
    with tempfile.TemporaryDirectory(prefix='erp-runtime-smoke-') as temporary:
        folder = Path(temporary)
        with (folder / 'runtime.log').open('w') as errors:
            child = subprocess.Popen([str(executable), '--data-dir', str(folder / 'local')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True,
                env={key: value for key, value in os.environ.items() if not key.startswith(('ERP_', 'VITE_', 'CHAMPION_ERP_'))})
            selector = selectors.DefaultSelector()
            selector.register(child.stdout, selectors.EVENT_READ)
            def read_reply():
                if not selector.select(45):
                    raise RuntimeError('本机执行器验证超时')
                return json.loads(child.stdout.readline())
            try:
                ready = read_reply()
                if ready != {'ready': True, 'pid': child.pid}:
                    raise RuntimeError('本机执行器就绪回执无效')
                child.stdin.write(json.dumps({'id': 'release-check', 'operation': 'config.read', 'params': {}, 'timeout': 5}) + '\n')
                child.stdin.flush()
                if read_reply() != {'id': 'release-check', 'ok': True, 'result': {'models': {}}}:
                    raise RuntimeError('本机执行器配置读取验证失败')
                child.stdin.close()
                if child.wait(timeout=15) != 0 or list(folder.rglob('*.sqlite3')):
                    raise RuntimeError('本机执行器退出或业务隔离验证失败')
            finally:
                selector.close()
                if child.poll() is None:
                    child.kill()
                    child.wait()


def desktop_running(target):
    prefix = str(target / 'Contents') + '/'
    return any(row.startswith(prefix) for row in output('ps', '-axo', 'command=').splitlines())


def desktop_hash(target):
    return digest(target / 'Contents/Resources/app.asar') if target.exists() else None


def install_desktop(source, target, expected_hash, release_id, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    announced = False
    while desktop_running(target):
        if not announced:
            print('请正常退出桌面程序，脚本将自动继续替换。', flush=True)
            announced = True
        if time.monotonic() >= deadline:
            raise RuntimeError('等待桌面程序退出超时，已构建的新包保留在发布目录')
        time.sleep(3)
    if desktop_hash(target) != expected_hash:
        raise RuntimeError('本机程序已被其他更新更换，拒绝覆盖，请核对后重新发布')
    staging = target.parent / ('.' + target.stem + '-' + release_id + '.staging.app')
    backup = target.parent / ('.' + target.stem + '-' + release_id + '.backup.app')
    if staging.exists() or backup.exists():
        raise RuntimeError('桌面安装暂存或备份目录已存在，拒绝覆盖')
    subprocess.run(['ditto', str(source), str(staging)], check=True)
    if desktop_running(target) or desktop_hash(target) != expected_hash:
        raise RuntimeError('桌面程序状态在复制期间发生变化，保留暂存包并停止安装')
    source_hash = digest(source / 'Contents/Resources/app.asar')
    if digest(staging / 'Contents/Resources/app.asar') != source_hash:
        raise RuntimeError('桌面暂存包校验失败')
    had_target = target.exists()
    if had_target:
        target.rename(backup)
    try:
        staging.rename(target)
        if desktop_hash(target) != source_hash:
            raise RuntimeError('桌面替换后摘要不一致')
        check_runtime(target / 'Contents/Resources/runtime/champion-erp-runtime')
    except BaseException:
        if target.exists():
            target.rename(staging)
        if had_target:
            backup.rename(target)
        raise
    return {'target': str(target), 'backup': str(backup) if had_target else '', 'asar_sha256': source_hash}


def verify_public(public_url, source):
    index = (source / 'erp_web/static/dist/index.html').read_text()
    entry = re.search(r'src="(/assets/[^" ]+\.js)"', index)
    if not entry:
        raise RuntimeError('网页构建未找到入口资源')
    headers = {'User-Agent': 'gongluErp-release'}
    with urlopen(Request(public_url.rstrip('/') + '/api/account/session', headers=headers), timeout=30) as response:
        session = json.load(response)
    if not session.get('initialized') or not session.get('ok'):
        raise RuntimeError('公网账号入口尚未就绪')
    with urlopen(Request(public_url.rstrip('/') + '/', headers=headers), timeout=30) as response:
        if entry[1] not in response.read().decode():
            raise RuntimeError('公网页面尚未提供本次网页版本')
    with urlopen(Request(public_url.rstrip('/') + entry[1], headers=headers), timeout=30) as response:
        received = hashlib.sha256(response.read()).hexdigest()
    if received != digest(source / 'erp_web/static/dist' / entry[1].lstrip('/')):
        raise RuntimeError('公网静态资源与构建包不一致')
    return {'account_session': True, 'web_entry': entry[1], 'web_sha256': received}


def finish_install(args, record, folder, source):
    app = Path(record['desktop_source'])
    if desktop_hash(app) != record['desktop_asar_sha256']:
        raise RuntimeError('已构建的桌面包摘要发生变化，请重新构建')
    record['public'] = verify_public(args.public_url, source)
    write_record(folder / 'release.json', record)
    print('服务器已升级，正在替换本机桌面程序', flush=True)
    record['desktop'] = install_desktop(app, Path(record['desktop_target']), record['previous_desktop_sha256'],
                                      record['release_id'], args.wait_desktop_seconds)
    record['status'] = 'complete'
    write_record(folder / 'release.json', record)
    print(f"发布完成，版本 {record['version']}，代码 {record['revision'][:12]}。记录保存在 {folder / 'release.json'}", flush=True)


def resume_release(args):
    folder = args.resume.expanduser().resolve()
    record = json.loads((folder / 'release.json').read_text())
    if record['status'] not in {'server_published', 'failed_after_server_published'}:
        raise RuntimeError('该发布记录不处于可继续的服务器已发布阶段')
    archive = folder / 'server.tar.gz'
    if digest(archive) != record['archive_sha256']:
        raise RuntimeError('已有服务器发布包摘要发生变化')
    current = remote_output(args, ['readlink', '-f', '/opt/champion-erp/current'])
    if current != record['server']['release']:
        raise RuntimeError('服务器已切换到其他发布，拒绝安装旧版本桌面程序')
    manifest = json.loads(remote_output(args, ['cat', current + '/release-manifest.json']))
    if manifest['revision'] != record['revision']:
        raise RuntimeError('服务器代码版本与发布记录不一致')
    for key in ('previous_desktop_sha256', 'desktop_asar_sha256', 'desktop_target'):
        if key not in record:
            raise RuntimeError('旧发布记录缺少桌面校验信息，请先人工核对并补齐记录')
    try:
        with tempfile.TemporaryDirectory(prefix='erp-release-resume-') as temporary:
            source = Path(temporary)
            with tarfile.open(archive) as package:
                package.extractall(source, filter='data')
            finish_install(args, record, folder, source)
    except BaseException:
        record['status'] = 'failed_after_server_published'
        write_record(folder / 'release.json', record)
        raise


def release(args):
    if sys.platform != 'darwin':
        raise RuntimeError('统一发布包含本机 macOS 程序替换，请在目标 Mac 上执行')
    if output('git', 'status', '--porcelain', '--untracked-files=normal'):
        raise RuntimeError('请先提交本次代码修改，发布仅使用干净的 Git 版本')
    if not args.ssh_identity.is_file():
        raise RuntimeError('SSH 密钥文件不存在')
    args.desktop_target = args.desktop_target.expanduser().absolute()
    if args.desktop_target.suffix != '.app' or not os.access(args.desktop_target.parent, os.W_OK):
        raise RuntimeError('桌面安装位置须为可写目录下的 .app 路径')
    revision = output('git', 'rev-parse', 'HEAD')
    version = json.loads((ROOT / 'front/package.json').read_text())['version']
    release_id = datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz') + '-' + revision[:12]
    folder = args.output_dir / release_id
    folder.mkdir(mode=0o700)
    record = {'release_id': release_id, 'revision': revision, 'version': version, 'status': 'preparing'}
    record_path = folder / 'release.json'
    write_record(record_path, record)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('ERP_', 'VITE_', 'CHAMPION_ERP_'))}
    environment['CSC_IDENTITY_AUTO_DISCOVERY'] = 'false'
    previous_desktop = desktop_hash(args.desktop_target)
    record.update(previous_desktop_sha256=previous_desktop, desktop_target=str(args.desktop_target))
    write_record(record_path, record)
    try:
        current = remote_output(args, ['readlink', '-f', '/opt/champion-erp/current'])
        if not current.startswith('/opt/champion-erp/releases/'):
            raise RuntimeError('服务器当前版本路径不符合部署约定')
        record['previous_server_release'] = current
        with tempfile.TemporaryDirectory(prefix='erp-release-source-') as temporary, (folder / 'build.log').open('w') as log:
            source = Path(temporary) / 'source'
            source.mkdir()
            git_archive = folder / 'source.tar'
            with git_archive.open('wb') as archive:
                subprocess.run(['git', 'archive', revision], cwd=ROOT, stdout=archive, check=True)
            with tarfile.open(git_archive) as archive:
                archive.extractall(source, filter='data')
            git_archive.unlink()
            print('安装锁定的前端依赖并检查发布输入', flush=True)
            run(['pnpm', 'install', '--frozen-lockfile', '--ignore-scripts'], cwd=source / 'front', log=log, env=environment)
            run(['node', 'node_modules/electron/install.js'], cwd=source / 'front', log=log, env=environment)
            run([sys.executable, 'scripts/generate_third_party_notices.py', '--check'], cwd=source, log=log, env=environment)
            print('运行后端、前端及桌面回归检查', flush=True)
            for command, cwd in (
                ([sys.executable, '-m', 'pytest', 'tests', '-q'], source),
                (['pnpm', 'types:check'], source / 'front'),
                (['pnpm', 'test:run'], source / 'front'),
                (['pnpm', 'test:desktop'], source / 'front'),
            ):
                run(command, cwd=cwd, log=log, env=environment)
            print('编译网页、本机执行器及桌面安装包', flush=True)
            run(['pnpm', 'build'], cwd=source / 'front', log=log, env=environment)
            run([sys.executable, 'scripts/build_desktop_runtime.py'], cwd=source, log=log, env=environment)
            run(['pnpm', 'build:desktop'], cwd=source / 'front', log=log, env=environment)
            run(['pnpm', 'exec', 'electron-builder', '--mac', '--dir', '--config.directories.output=' + str(folder / 'desktop')],
                cwd=source / 'front', log=log, env=environment)
            apps = list((folder / 'desktop').glob('*/*.app'))
            if len(apps) != 1:
                raise RuntimeError('桌面构建没有产生唯一的应用包')
            app = apps[0]
            with (app / 'Contents/Info.plist').open('rb') as info:
                if plistlib.load(info)['CFBundleShortVersionString'] != version:
                    raise RuntimeError('桌面安装包版本不一致')
            check_runtime(app / 'Contents/Resources/runtime/champion-erp-runtime')
            # 只发布 Git 跟踪的公开源码及本次网页构建，排除工作区配置、数据库和依赖缓存。
            tracked = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', '-z', revision], cwd=ROOT).decode().split('\0')
            (source / 'REVISION').write_text(revision + '\n')
            write_record(source / 'release-manifest.json', {'revision': revision, 'version': version, 'release_id': release_id})
            archive_path = folder / 'server.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                for name in filter(None, tracked):
                    archive.add(source / name, arcname=name, recursive=False)
                archive.add(source / 'erp_web/static/dist', arcname='erp_web/static/dist')
                for name in ('REVISION', 'release-manifest.json'):
                    archive.add(source / name, arcname=name)
            record.update(status='built', archive_sha256=digest(archive_path), desktop_source=str(app),
                          desktop_asar_sha256=desktop_hash(app))
            write_record(record_path, record)
            if desktop_hash(args.desktop_target) != previous_desktop:
                raise RuntimeError('编译期间桌面程序已被其他更新更换，请核对后重新发布')
            remote_folder = '/srv/champion-erp/staging/' + release_id
            remote_output(args, ['mkdir', '-p', remote_folder])
            scp = ['scp', '-P', str(args.ssh_port), '-i', str(args.ssh_identity), '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes']
            print('上传服务器发布包并执行备份切换', flush=True)
            run([*scp, archive_path, source / 'deploy/release_server.py', args.ssh_host + ':' + remote_folder + '/'], cwd=source, log=log, env=environment)
            command = ['python3', remote_folder + '/release_server.py', '--archive', remote_folder + '/server.tar.gz',
                       '--sha256', record['archive_sha256'], '--release-id', release_id, '--revision', revision, '--expected-current', current]
            run([*ssh_args(args), shlex.join(command)], cwd=source, log=log, env=environment)
            record['server'] = json.loads(remote_output(args, ['cat', remote_folder + '/server-result.json']))
            record['status'] = 'server_published'
            write_record(record_path, record)
            finish_install(args, record, folder, source)
    except BaseException:
        record['status'] = 'failed_after_' + record['status']
        write_record(record_path, record)
        print(f'发布未完成，已保留状态和日志，请检查 {record_path}', file=sys.stderr, flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-host', default='root@107.174.48.241', help='服务器 SSH 登录目标')
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--ssh-identity', type=Path, default=Path.home() / '.ssh/id_ed25519')
    parser.add_argument('--public-url', default='https://hdgl.cc.cd', help='升级后验证的公网地址')
    current_app = Path('/Applications/Champion ERP.app')
    parser.add_argument('--desktop-target', type=Path, default=current_app if current_app.exists() else Path('/Applications/gongluErp.app'))
    parser.add_argument('--output-dir', type=Path, default=Path.home() / 'Library/Application Support/Champion ERP/releases')
    parser.add_argument('--wait-desktop-seconds', type=int, default=3600)
    parser.add_argument('--resume', type=Path, help='从已有发布目录继续公网验证和桌面替换')
    args = parser.parse_args()
    args.output_dir = args.output_dir.expanduser().absolute()
    if args.resume:
        args.output_dir = args.resume.expanduser().resolve().parent
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.output_dir / '.release.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.resume:
            resume_release(args)
        else:
            release(args)


if __name__ == '__main__':
    main()

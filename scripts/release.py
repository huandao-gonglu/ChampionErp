"""从固定 Git 版本发布服务器和网页，默认通过更新服务分发 macOS 桌面新版。"""
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
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
RELEASE_HEADERS = {'User-Agent': 'gongluErp-release', 'Cache-Control': 'no-cache'}


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


def verify_public(public_url, source):
    index = (source / 'erp_web/static/dist/index.html').read_text()
    entry = re.search(r'src="(/assets/[^" ]+\.js)"', index)
    if not entry:
        raise RuntimeError('网页构建未找到入口资源')
    headers = RELEASE_HEADERS
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


def desktop_release_artifacts(folder, app, version, args):
    architecture = output('lipo', '-archs', app / 'Contents/MacOS' / plistlib.loads(
        (app / 'Contents/Info.plist').read_bytes())['CFBundleExecutable'])
    arch = {'arm64': 'arm64', 'x86_64': 'x64'}.get(architecture)
    if not arch:
        raise RuntimeError('请为每个桌面架构单独构建和发布安装包')
    record = {'platform': 'darwin', 'arch': arch, 'version': version, 'policy': args.desktop_update,
              'notes': args.update_notes, 'public_url': args.public_url.rstrip('/')}
    for key, extension in [('installer', '.dmg'), *([('mac_zip', '.zip')] if args.signed_desktop else [])]:
        files = list((folder / 'desktop').glob('*' + extension))
        if len(files) != 1:
            raise RuntimeError(f'本次构建应产生唯一的 {extension} 桌面安装包')
        record[key] = {'name': files[0].name, 'sha256': digest(files[0])}
    if args.signed_desktop:
        signature = subprocess.check_output(['codesign', '-dv', '--verbose=2', app], stderr=subprocess.STDOUT, text=True)
        if 'Authority=Developer ID Application:' not in signature:
            raise RuntimeError('签名发布需要 Developer ID Application 身份')
        subprocess.run(['xcrun', 'stapler', 'validate', app], check=True)
    return record


def build_desktop_update(args, source, folder, version, log, environment):
    print('构建并验证桌面更新包', flush=True)
    run([sys.executable, 'scripts/build_desktop_runtime.py'], cwd=source, log=log, env=environment)
    run(['pnpm', 'build:desktop'], cwd=source / 'front', log=log, env=environment)
    targets = ['dmg', 'zip'] if args.signed_desktop else ['dmg']
    run(['pnpm', 'exec', 'electron-builder', '--mac', *targets, '--publish', 'never',
         '--config.directories.output=' + str(folder / 'desktop')],
        cwd=source / 'front', log=log, env=environment)
    apps = list((folder / 'desktop').glob('*/*.app'))
    if len(apps) != 1:
        raise RuntimeError('桌面构建没有产生唯一的应用包')
    app = apps[0]
    subprocess.run(['codesign', '--verify', '--deep', '--strict', app], check=True)
    with (app / 'Contents/Info.plist').open('rb') as info:
        if plistlib.load(info)['CFBundleShortVersionString'] != version:
            raise RuntimeError('桌面安装包版本不一致')
    check_runtime(app / 'Contents/Resources/runtime/champion-erp-runtime')
    return desktop_release_artifacts(folder, app, version, args)


def desktop_manifest_url(update):
    return f"{update['public_url']}/desktop-updates/{update['platform']}-{update['arch']}/latest.json"


def check_new_desktop_version(update):
    """在切换后端前识别版本冲突，服务器在正式发布时仍会持锁复核。"""
    url = desktop_manifest_url(update)
    try:
        with urlopen(Request(url, headers=RELEASE_HEADERS), timeout=30) as response:
            if response.geturl() != url:
                raise RuntimeError('桌面更新清单地址发生重定向，请检查更新站点配置')
            manifest = json.load(response)
    except HTTPError as error:
        if error.code == 404 and error.geturl() == url:
            return
        raise
    if (manifest['platform'], manifest['arch']) != (update['platform'], update['arch']):
        raise RuntimeError('桌面更新清单与本次发布平台不符')
    if tuple(map(int, update['version'].split('.'))) <= tuple(map(int, manifest['version'].split('.'))):
        raise RuntimeError(f"本地桌面版本 {update['version']}，已发布版本 {manifest['version']}。"
                           '请提高桌面版本号，或用 --server-only 仅发布服务器和网页')


def publish_desktop_update(args, record, folder, source):
    update = record.get('desktop_update')
    if not update or update.get('published'):
        return
    if args.public_url.rstrip('/') != update['public_url']:
        raise RuntimeError('续发目标与桌面包的更新服务地址不一致')
    staging = '/srv/champion-erp/staging/' + record['release_id']
    scp = ['scp', '-P', str(args.ssh_port), '-i', str(args.ssh_identity), '-o', 'BatchMode=yes',
           '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes']
    subprocess.run([*scp, source / 'deploy/publish_desktop.py', args.ssh_host + ':' + staging + '/'], check=True)
    command = ['python3', staging + '/publish_desktop.py', '--public-url', update['public_url'],
               '--platform', update['platform'], '--arch', update['arch'], '--version', update['version'],
               '--policy', update['policy'], '--notes', update['notes']]
    for key, remote_name, path_flag, digest_flag in (
        ('installer', 'installer.dmg', '--installer', '--sha256'),
        ('mac_zip', 'update.zip', '--mac-zip', '--zip-sha256'),
    ):
        if key in update:
            artifact = folder / 'desktop' / update[key]['name']
            if digest(artifact) != update[key]['sha256']:
                raise RuntimeError('桌面分发包摘要发生变化，请重新构建')
            destination = staging + '/' + remote_name
            subprocess.run([*scp, artifact, args.ssh_host + ':' + destination], check=True)
            command.extend([path_flag, destination, digest_flag, update[key]['sha256']])
    print(remote_output(args, command), flush=True)
    manifest_url = desktop_manifest_url(update)
    with urlopen(Request(manifest_url, headers=RELEASE_HEADERS), timeout=30) as response:
        manifest = json.load(response)
        if (response.geturl() != manifest_url or manifest['version'] != update['version']
                or manifest['installer']['sha256'] != update['installer']['sha256']
                or (update['policy'] == 'required' and manifest['minimum'] != update['version'])):
            raise RuntimeError('公网桌面更新清单与本次发布不一致')
    update['published'] = True
    write_record(folder / 'release.json', record)


def finish_release(args, record, folder, source):
    record['public'] = verify_public(args.public_url, source)
    write_record(folder / 'release.json', record)
    if record.get('desktop_update'):
        publish_desktop_update(args, record, folder, source)
    elif record.get('desktop_target'):
        print('旧发布记录没有桌面分发包，本次继续完成服务器和网页发布。', flush=True)
    record['status'] = 'complete'
    write_record(folder / 'release.json', record)
    print(f"发布完成，版本 {record['version']}，代码 {record['revision'][:12]}。记录保存在 {folder / 'release.json'}", flush=True)


def switch_server(args, record, folder, source):
    staging = '/srv/champion-erp/staging/' + record['release_id']
    command = ['python3', staging + '/deploy/release_server.py', '--archive', staging + '/server.tar.gz',
               '--sha256', record['archive_sha256'], '--release-id', record['release_id'],
               '--revision', record['revision'], '--expected-current', record['previous_server_release'],
               '--wait-tasks-seconds', str(args.wait_tasks_seconds)]
    # 等待原因直接输出到终端，同时保存在原发布日志中。
    with (folder / 'build.log').open('a') as log, subprocess.Popen(
        [*ssh_args(args), shlex.join(command)], cwd=source, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
            log.write(line)
        if process.wait():
            raise RuntimeError('服务器切换未完成，请检查本次发布日志')
    record['server'] = json.loads(remote_output(args, ['cat', staging + '/server-result.json']))
    record['status'] = 'server_published'
    write_record(folder / 'release.json', record)


def verify_server_release(args, record):
    current = remote_output(args, ['readlink', '-f', '/opt/champion-erp/current'])
    if current != record['server']['release']:
        raise RuntimeError('服务器已切换到其他发布，拒绝继续旧发布')
    manifest = json.loads(remote_output(args, ['cat', current + '/release-manifest.json']))
    if manifest['revision'] != record['revision']:
        raise RuntimeError('服务器代码版本与发布记录不一致')


def resume_release(args):
    folder = args.resume.expanduser().resolve()
    record = json.loads((folder / 'release.json').read_text())
    staged = record['status'] in {'server_staged', 'failed_after_server_staged'}
    if not staged and record['status'] not in {'server_published', 'failed_after_server_published'}:
        raise RuntimeError('只有已上传或已发布的版本可以继续')
    archive = folder / 'server.tar.gz'
    if digest(archive) != record['archive_sha256']:
        raise RuntimeError('已有服务器发布包摘要发生变化')
    if not staged:
        verify_server_release(args, record)
    try:
        with tempfile.TemporaryDirectory(prefix='erp-release-resume-') as temporary:
            source = Path(temporary)
            with tarfile.open(archive) as package:
                package.extractall(source, filter='data')
            if staged:
                switch_server(args, record, folder, source)
                verify_server_release(args, record)
            finish_release(args, record, folder, source)
    except BaseException:
        record['status'] = 'failed_after_' + record['status'].removeprefix('failed_after_')
        write_record(folder / 'release.json', record)
        raise


def release(args):
    if args.desktop_update and sys.platform != 'darwin':
        raise RuntimeError('构建 macOS 更新包需要 Mac，仅发布服务器和网页可使用 --server-only')
    if output('git', 'status', '--porcelain', '--untracked-files=normal'):
        raise RuntimeError('请先提交本次代码修改，发布仅使用干净的 Git 版本')
    if not args.ssh_identity.is_file():
        raise RuntimeError('SSH 密钥文件不存在')
    revision = output('git', 'rev-parse', 'HEAD')
    package = json.loads((ROOT / 'front/package.json').read_text())
    version = package['version']
    release_id = datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz') + '-' + revision[:12]
    folder = args.output_dir / release_id
    folder.mkdir(mode=0o700)
    record = {'release_id': release_id, 'revision': revision, 'version': version, 'status': 'preparing',
              'skip_tests': args.skip_tests}
    record_path = folder / 'release.json'
    write_record(record_path, record)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('ERP_', 'VITE_', 'CHAMPION_ERP_'))}
    environment['ERP_DESKTOP_SIGNING_MODE'] = 'developer-id' if args.signed_desktop else 'local'
    environment['CSC_IDENTITY_AUTO_DISCOVERY'] = 'true'
    try:
        if args.desktop_update:
            origin = package['desktopUpdateOrigin']
            if origin != args.public_url.rstrip('/'):
                raise RuntimeError('桌面包的发行站点必须与发布目标一致')
            check_new_desktop_version({'public_url': origin, 'platform': 'darwin',
                                       'arch': output('node', '-p', 'process.arch'), 'version': version})
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
            if args.desktop_update:
                run(['node', 'node_modules/electron/install.js'], cwd=source / 'front', log=log, env=environment)
            run([sys.executable, 'scripts/generate_third_party_notices.py', '--check'], cwd=source, log=log, env=environment)
            run(['pnpm', 'types:check'], cwd=source / 'front', log=log, env=environment)
            if args.skip_tests:
                print('已按 --skip-tests 跳过后端、前端和桌面全量测试', flush=True)
            else:
                print('运行后端、前端及桌面回归检查', flush=True)
                run([sys.executable, '-m', 'pytest', 'tests', '-q'], cwd=source, log=log, env=environment)
                run(['pnpm', 'test:run'], cwd=source / 'front', log=log, env=environment)
                run(['pnpm', 'test:desktop'], cwd=source / 'front', log=log, env=environment)
            print('编译网页', flush=True)
            run(['pnpm', 'build'], cwd=source / 'front', log=log, env=environment)
            if args.desktop_update:
                record['desktop_update'] = build_desktop_update(args, source, folder, version, log, environment)
                check_new_desktop_version(record['desktop_update'])
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
            record.update(status='built', archive_sha256=digest(archive_path))
            write_record(record_path, record)
            remote_folder = '/srv/champion-erp/staging/' + release_id
            remote_output(args, ['mkdir', '-p', remote_folder + '/erp_web', remote_folder + '/deploy'])
            scp = ['scp', '-P', str(args.ssh_port), '-i', str(args.ssh_identity), '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes']
            print('上传服务器发布包并执行备份切换', flush=True)
            run([*scp, archive_path, args.ssh_host + ':' + remote_folder + '/'], cwd=source, log=log, env=environment)
            run([*scp, source / 'deploy/release_server.py', args.ssh_host + ':' + remote_folder + '/deploy/'], cwd=source, log=log, env=environment)
            run([*scp, source / 'erp_web/__init__.py', source / 'erp_web/release_guard.py', args.ssh_host + ':' + remote_folder + '/erp_web/'], cwd=source, log=log, env=environment)
            record['status'] = 'server_staged'
            write_record(record_path, record)
            switch_server(args, record, folder, source)
            finish_release(args, record, folder, source)
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
    parser.add_argument('--output-dir', type=Path, default=Path.home() / 'Library/Application Support/Champion ERP/releases')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--desktop-update', choices=['optional', 'required'], help='发布桌面升级，默认 optional，可显式指定 required')
    mode.add_argument('--server-only', action='store_true', help='仅发布服务器和网页，跳过桌面打包和分发')
    parser.add_argument('--skip-tests', action='store_true', help='跳过后端、前端和桌面全量测试，继续执行构建检查与发布验收')
    parser.add_argument('--update-notes', default='', help='向用户展示的更新说明')
    parser.add_argument('--signed-desktop', action='store_true', help='使用 Developer ID 签名及公证构建原生更新 ZIP')
    parser.add_argument('--wait-tasks-seconds', type=int, default=3600, help='服务器等待已接任务安全停机的最长秒数')
    parser.add_argument('--resume', type=Path, help='从已上传的发布目录继续原有服务器切换、验证和更新包分发')
    args = parser.parse_args()
    if args.resume and (args.desktop_update or args.server_only or args.signed_desktop or args.update_notes):
        parser.error('--resume 沿用发布记录中的桌面策略、签名与更新说明')
    if args.server_only and (args.signed_desktop or args.update_notes):
        parser.error('--server-only 不能与桌面签名或更新说明一起使用')
    if not args.resume and not args.server_only and not args.desktop_update:
        args.desktop_update = 'optional'
    if len(args.update_notes) > 2000:
        parser.error('桌面更新说明最多 2000 字')
    if args.wait_tasks_seconds < 0:
        parser.error('等待任务时间必须为非负数')
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

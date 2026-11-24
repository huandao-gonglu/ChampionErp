"""从固定 Git 版本发布服务器和网页，默认通过更新服务分发 macOS 桌面新版。"""
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import plistlib
import pty
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from .release_tunnel import desktop_tunnel
else:
    from release_tunnel import desktop_tunnel
RELEASE_HEADERS = {'User-Agent': 'gongluErp-release', 'Cache-Control': 'no-cache'}
RELEASE_ID_RE = re.compile(r'[0-9]{8}t[0-9]{6}z-[0-9a-f]{12}')
SERVER_PACKAGE_PREFIXES = ('erp_web/', 'erp_shared/', 'deploy/', 'config/')
SERVER_PACKAGE_FILES = {'requirements.txt', 'LICENSE', 'scripts/install_release_dependencies.py'}
LARGE_ASSET_THRESHOLD = 512 * 1024
LOCAL_KEEP_COMPLETED = 2

_SSH_CONTROL_LOCK = threading.Lock()
_SSH_CONTROL_PATH = None


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


def _stop_process_groups(processes):
    """先通知整组退出，超过宽限期后回收仍在运行的命令及其子进程。"""
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 2
    for process in processes:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    # 命令本身退出后仍可能留下 pnpm / pip 等子进程，继续按原进程组回收。
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for process in processes:
        process.wait()


def run_parallel(tasks, *, log):
    """并发执行独立命令，失败或中断时停止其余进程组，并保留全部输出。

    tasks: [(args, cwd, env), ...]
    输出按提交顺序归并；异常退出也保留已产生的 stdout / stderr。
    """
    processes, outputs = [], []
    with ExitStack() as stack:
        try:
            for args, cwd, env in tasks:
                command = [str(arg) for arg in args]
                log.write('\n运行 ' + shlex.join(command) + '\n')
                log.flush()
                buffer = stack.enter_context(tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace'))
                outputs.append((command, buffer))
                processes.append(subprocess.Popen(command, cwd=cwd, stdout=buffer,
                    stderr=subprocess.STDOUT, env=env, start_new_session=True))
            while processes:
                results = [process.poll() for process in processes]
                for process, result in zip(processes, results):
                    if result is not None and result != 0:
                        raise subprocess.CalledProcessError(result, process.args)
                if all(result is not None for result in results):
                    break
                time.sleep(0.05)
        except BaseException as error:
            _stop_process_groups(processes)
            log.write(f'\n并行任务中止：{error}\n')
            raise
        finally:
            for command, buffer in outputs:
                log.write('\n输出 ' + shlex.join(command) + '\n')
                buffer.seek(0)
                shutil.copyfileobj(buffer, log)
            log.flush()


def _ssh_common_options(*, multiplex=True, known_hosts=None):
    options = [
        '-o', 'BatchMode=yes',
        '-o', 'ForwardAgent=no',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=15',
        '-o', 'ServerAliveInterval=20',
        '-o', 'ServerAliveCountMax=3',
    ]
    if known_hosts is not None:
        options.extend(['-o', f'UserKnownHostsFile={known_hosts}'])
    if multiplex:
        with _SSH_CONTROL_LOCK:
            control_path = _SSH_CONTROL_PATH
        if control_path is not None:
            options.extend([
                '-o', 'ControlMaster=auto',
                '-o', f'ControlPath={control_path}',
                '-o', 'ControlPersist=600',
            ])
    return options


def ssh_args(args):
    return ['ssh', '-p', str(args.ssh_port), '-i', str(args.ssh_identity),
            *_ssh_common_options(multiplex=True, known_hosts=getattr(args, 'known_hosts', None)), args.ssh_host]


def direct_ssh_args(args):
    # 桌面包直连传输使用独立非压缩 SSH 连接，避免复用通道限速与跨阶段闲置断连。
    return ['ssh', '-p', str(args.ssh_port), '-i', str(args.ssh_identity),
            *_ssh_common_options(multiplex=False, known_hosts=getattr(args, 'known_hosts', None)),
            '-o', 'Compression=no', args.ssh_host]


def scp_args(args):
    # 大文件 SCP 使用独立非压缩连接，避免跨构建阶段闲置或复用通道限速。
    return ['scp', '-P', str(args.ssh_port), '-i', str(args.ssh_identity),
            *_ssh_common_options(multiplex=False, known_hosts=getattr(args, 'known_hosts', None)),
            '-o', 'Compression=no']


@contextmanager
def ssh_session(args):
    """在单次发布期间复用同一条 SSH ControlMaster 连接，减少弱网下的重复握手与断连。"""
    global _SSH_CONTROL_PATH
    if not getattr(args, 'ssh_host', None) or not getattr(args, 'ssh_identity', None):
        yield
        return
    # macOS Unix Socket 路径上限 104 字节，放 /tmp 下保持路径简短。
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='erp-ssh-') as work:
        sock = Path(work) / 'mux.sock'
        with _SSH_CONTROL_LOCK:
            previous = _SSH_CONTROL_PATH
            _SSH_CONTROL_PATH = str(sock)
        try:
            yield
        finally:
            if sock.exists():
                subprocess.run(
                    ['ssh', '-S', str(sock), '-O', 'exit', args.ssh_host],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            with _SSH_CONTROL_LOCK:
                _SSH_CONTROL_PATH = previous


def _retry_ssh(action, *, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except subprocess.CalledProcessError as error:
            if error.returncode != 255 or attempt >= attempts:
                raise
            time.sleep(2 * attempt)


def remote_output(args, command):
    joined = shlex.join([str(arg) for arg in command])
    return _retry_ssh(lambda: output(*ssh_args(args), joined))


def select_server_files(tracked):
    """仅挑选服务器运行与依赖安装所需文件，排除前端源码、测试、文档与本机执行器源码。"""
    selected = []
    for name in filter(None, tracked):
        if (name.startswith(SERVER_PACKAGE_PREFIXES)
                or name in SERVER_PACKAGE_FILES
                or (name.startswith('scripts/migrate_') and name.endswith('.py'))):
            selected.append(name)
    return selected


_REUSABLE_ASSET_PROBE = '\n'.join([
    'import hashlib, json, sys',
    'from pathlib import Path',
    'items = json.loads(sys.argv[1])',
    'shared = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/opt/champion-erp/shared-assets")',
    'current = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/opt/champion-erp/current")',
    'def ok(p, h):',
    '    if not p.is_file() or p.is_symlink():',
    '        return False',
    '    with p.open("rb") as source:',
    '        return hashlib.file_digest(source, "sha256").hexdigest() == h',
    'print(json.dumps([k for k, h in items.items() if ok(shared / h, h) or ok(current / k, h)]))',
])


def detect_reusable_server_assets(args, source, selected):
    """检查服务器上已存在的同摘要大体积静态资产，避免每次发布经 SCP 重复上传。"""
    candidates = {}
    for rel in selected:
        if not rel.startswith('erp_web/assets/'):
            continue
        path = source / rel
        if path.is_file() and not path.is_symlink() and path.stat().st_size >= LARGE_ASSET_THRESHOLD:
            candidates[rel] = digest(path)
    if not candidates:
        return {}
    try:
        raw = remote_output(args, ['python3', '-c', _REUSABLE_ASSET_PROBE, json.dumps(candidates)])
        reusable = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(reusable, list):
        return {}
    return {rel: candidates[rel] for rel in reusable if rel in candidates}


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


def _fetch_bytes(url, *, headers=RELEASE_HEADERS, timeout=30, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                return response.geturl(), response.read()
        except HTTPError as error:
            if error.code < 500 or attempt >= attempts:
                raise
        except (http.client.HTTPException, URLError, TimeoutError, OSError):
            if attempt >= attempts:
                raise
        time.sleep(2 * attempt)


def verify_public(public_url, source):
    index = (source / 'erp_web/static/dist/index.html').read_text()
    entry = re.search(r'src="(/assets/[^" ]+\.js)"', index)
    if not entry:
        raise RuntimeError('网页构建未找到入口资源')
    base = public_url.rstrip('/')
    _, session_body = _fetch_bytes(base + '/api/account/session')
    session = json.loads(session_body)
    if not session.get('initialized') or not session.get('ok'):
        raise RuntimeError('公网账号入口尚未就绪')
    _, page_body = _fetch_bytes(base + '/')
    if entry[1] not in page_body.decode():
        raise RuntimeError('公网页面尚未提供本次网页版本')
    _, asset_body = _fetch_bytes(base + entry[1])
    received = hashlib.sha256(asset_body).hexdigest()
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
    for key, extension in [('installer', '.dmg'), ('mac_zip', '.zip')]:
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


def prune_desktop_build_intermediates(desktop_dir):
    """桌面安装包校验完成后，立即回收未打包的 .app 中间目录与无用 blockmap/debug 文件。"""
    if not desktop_dir.is_dir():
        return
    for entry in desktop_dir.iterdir():
        if entry.is_symlink():
            continue
        if entry.is_dir() and entry.name.startswith('mac'):
            shutil.rmtree(entry, ignore_errors=True)
        elif entry.is_file() and (entry.name.endswith('.blockmap') or entry.name == 'builder-debug.yml'):
            entry.unlink(missing_ok=True)


def cleanup_local_releases(output_dir, current_release_id, *, keep_completed=LOCAL_KEEP_COMPLETED):
    """发布完成后回收本机历史构建产物，仅保留最近若干已完成版本安装包与全部日志/记录。"""
    if not output_dir.is_dir() or not RELEASE_ID_RE.fullmatch(current_release_id):
        return {'removed_paths': []}
    completed = []
    older_failed = []
    removed = []
    for entry in sorted(output_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if entry.is_symlink() or not entry.is_dir() or not RELEASE_ID_RE.fullmatch(entry.name):
            continue
        if entry.name > current_release_id:
            continue
        prune_desktop_build_intermediates(entry / 'desktop')
        record_path = entry / 'release.json'
        status = ''
        if record_path.is_file() and not record_path.is_symlink():
            try:
                status = json.loads(record_path.read_text()).get('status', '')
            except (OSError, ValueError):
                status = ''
        if status == 'complete':
            completed.append(entry)
        elif entry.name < current_release_id:
            older_failed.append(entry)
    targets = completed[max(1, keep_completed):] + older_failed
    for directory in targets:
        for artifact in (directory / 'desktop', directory / 'server.tar.gz'):
            if artifact.is_symlink() or not artifact.exists():
                continue
            if artifact.is_dir():
                shutil.rmtree(artifact)
            else:
                artifact.unlink()
            removed.append(str(artifact))
    return {'removed_paths': removed}


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


def upload_server_files(command, log):
    """为 SCP 保留终端进度表，同时写入发布日志。"""
    argv = [str(arg) for arg in command]
    log.write('\n运行 ' + shlex.join(argv) + '\n')
    log.flush()

    def show_progress(fd):
        data = os.read(fd, 8192)
        lines = data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        log.write(lines.decode(errors='replace'))
        log.flush()
        return data if sys.stdout.isatty() else lines

    result = os.waitstatus_to_exitcode(pty.spawn(argv, master_read=show_progress))
    if result:
        raise subprocess.CalledProcessError(result, argv)


def prepare_desktop_transfer_inputs(update, folder, staging):
    files, artifacts = {}, {}
    command = ['python3', staging + '/publish_desktop.py', '--public-url', update['public_url'],
               '--platform', update['platform'], '--arch', update['arch'], '--version', update['version'],
               '--policy', update['policy'], '--notes', update['notes']]
    for key, remote_name, path_flag, digest_flag in (
        ('installer', 'installer.dmg', '--installer', '--sha256'),
        ('mac_zip', 'update.zip', '--mac-zip', '--zip-sha256'),
    ):
        if key in update:
            artifact = folder / 'desktop' / update[key]['name']
            if (artifact.parent != folder / 'desktop' or artifact.is_symlink() or not artifact.is_file()
                    or digest(artifact) != update[key]['sha256']):
                raise RuntimeError('桌面分发包摘要发生变化，请重新构建')
            destination = staging + '/' + remote_name
            files[remote_name] = artifact
            artifacts[remote_name] = {'size': artifact.stat().st_size, 'sha256': update[key]['sha256']}
            command.extend([path_flag, destination, digest_flag, update[key]['sha256']])
    return files, artifacts, command


def pull_desktop_files(args, staging, files, artifacts, log, *, display_gate=None, cancel_event=None):
    command = 'cd ' + shlex.quote(staging) + ' && exec python3 -u pull_desktop.py --preempt'
    first_name = next(iter(artifacts))
    for attempt in range(1, 4):
        if cancel_event and cancel_event.is_set():
            return
        stderr_lines = []
        with subprocess.Popen([*ssh_args(args), command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process, ExitStack() as stack:
            def _drain_stderr():
                for err_line in process.stderr:
                    stderr_lines.append(err_line)

            stderr_reader = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_reader.start()
            watcher_stop = threading.Event()
            if cancel_event is not None:
                def _watch_cancel():
                    while not watcher_stop.is_set():
                        if cancel_event.is_set() and process.poll() is None:
                            process.kill()
                            return
                        watcher_stop.wait(0.1)
                threading.Thread(target=_watch_cancel, daemon=True).start()
            try:
                # 先查询远端的实际文件，所有包均已校验时无需启动隧道。
                request = {'artifacts': artifacts, 'interactive': sys.stdout.isatty()
                           and shutil.get_terminal_size().columns >= 80}
                process.stdin.write(json.dumps(request) + '\n')
                process.stdin.flush()
                state = process.stdout.readline().strip()
                if state == 'download':
                    if display_gate is None or display_gate.is_set():
                        print('正在建立桌面包 HTTPS 传输隧道', flush=True)
                    url = stack.enter_context(desktop_tunnel(files))
                    if cancel_event and cancel_event.is_set():
                        return
                    process.stdin.write(url + '\n')
                    process.stdin.flush()
                elif state != 'cached':
                    process.stdin.close()
                    code = process.wait()
                    stderr_reader.join(timeout=2)
                    detail = ''.join(stderr_lines).strip()
                    if detail:
                        log.write(f'\nSSH 准备桌面包传输失败（exit={code}）：{detail}\n')
                        log.flush()
                    if code == 255 and attempt < 3 and not (cancel_event and cancel_event.is_set()):
                        time.sleep(2 * attempt)
                        continue
                    message = '服务器未能准备桌面包传输，请检查 SSH 输出'
                    if detail:
                        message += f'：{detail.splitlines()[-1]}'
                    raise RuntimeError(message)
                process.stdin.close()
                printed_to_terminal = display_gate is None
                for line in process.stdout:
                    cleaned = re.sub(r'\x1b\[\d+[AK]', '', line)
                    log.write(cleaned)
                    log.flush()
                    if display_gate is None:
                        print(line, end='', flush=True)
                    elif display_gate.is_set():
                        if not printed_to_terminal:
                            if not cleaned.startswith(first_name + ' '):
                                continue
                            # 首次接管终端输出时去掉上移光标，避免覆写服务器切换日志。
                            print(re.sub(r'^\x1b\[\d+A', '', line), end='', flush=True)
                            printed_to_terminal = True
                        else:
                            print(line, end='', flush=True)
                exit_code = process.wait()
                stderr_reader.join(timeout=2)
                if cancel_event and cancel_event.is_set():
                    return
                if exit_code:
                    detail = ''.join(stderr_lines).strip()
                    if detail:
                        log.write(f'\n桌面包传输异常退出（exit={exit_code}）：{detail}\n')
                        log.flush()
                    raise RuntimeError('桌面包传输未完成，可用 --resume 继续')
                return
            finally:
                watcher_stop.set()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                stderr_reader.join(timeout=2)


def _prefix_digest(path, length):
    hasher = hashlib.sha256()
    remaining = length
    with path.open('rb') as source:
        while remaining > 0:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError('桌面包文件大小异常，无法计算断点前缀摘要')
            hasher.update(chunk)
            remaining -= len(chunk)
    return hasher.hexdigest()


def _format_transfer_speed(bytes_per_second):
    if bytes_per_second >= 1048576:
        return f'{bytes_per_second / 1048576:.2f} MiB/s'
    return f'{bytes_per_second / 1024:.1f} KiB/s'


def _probe_remote_desktop(args, staging, artifacts, log, *, cancel_event=None, preempt=False):
    remote = ['python3', '-u', 'pull_desktop.py', '--probe']
    if preempt:
        remote.append('--preempt')
    command = 'cd ' + shlex.quote(staging) + ' && exec ' + shlex.join(remote)
    payload = json.dumps({'artifacts': artifacts}) + '\n'
    for attempt in range(1, 4):
        if cancel_event and cancel_event.is_set():
            return None
        result = subprocess.run(
            [*direct_ssh_args(args), command],
            input=payload, capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        detail = result.stderr.strip()
        if detail:
            log.write(f'\nSSH 探测桌面包断点状态失败（exit={result.returncode}）：{detail}\n')
            log.flush()
        if result.returncode == 255 and attempt < 3 and not (cancel_event and cancel_event.is_set()):
            if cancel_event:
                cancel_event.wait(2 * attempt)
            else:
                time.sleep(2 * attempt)
            continue
        message = '服务器未能准备直连桌面包传输，请检查 SSH 输出'
        if detail:
            message += f'：{detail.splitlines()[-1]}'
        raise RuntimeError(message)
    return None


def push_desktop_files(args, staging, files, artifacts, log, *, display_gate=None, cancel_event=None,
                       stall_timeout=45, chunk_size=256 * 1024):
    """通过多条独立直连 SSH 管道并行推送桌面包，支持前缀摘要校验续传、卡死重连与实时进度。"""
    probed = _probe_remote_desktop(args, staging, artifacts, log, cancel_event=cancel_event, preempt=True)
    if probed is None:
        return
    lock = threading.Lock()
    items = []
    now = time.monotonic()
    for name, meta in artifacts.items():
        state = probed.get(name, {'status': 'pending', 'offset': 0})
        size = meta['size']
        path = files[name]
        if state.get('status') == 'cached':
            offset, status, done = size, '已复用', True
        else:
            candidate = int(state.get('offset', 0))
            if (state.get('status') == 'partial' and 0 < candidate < size
                    and state.get('prefix_sha256') == _prefix_digest(path, candidate)):
                offset, status, done = candidate, '续传', False
            else:
                offset, status, done = 0, '等待传输', False
        items.append({
            'name': name, 'path': path, 'size': size, 'sha256': meta['sha256'],
            'offset': offset, 'received': offset, 'status': status, 'done': done,
            'last_progress_at': now, 'active_process': None, 'error': None,
        })

    interactive = sys.stdout.isatty() and shutil.get_terminal_size().columns >= 80
    previous = [item['received'] for item in items]
    last_change = [now] * len(items)
    last_sample = now
    rendered_on_terminal = False

    def _render_frame(sample_now):
        nonlocal last_sample, rendered_on_terminal
        dt = max(sample_now - last_sample, 0.001)
        lines = []
        with lock:
            snapshot = [(item['name'], item['size'], item['received'], item['status']) for item in items]
        for i, (name, size, count, status) in enumerate(snapshot):
            speed = max(0, count - previous[i]) / dt
            if count != previous[i]:
                last_change[i] = sample_now
            idle = int(sample_now - last_change[i])
            shown_status = f'停滞 {idle}s' if idle >= 3 and status in ('续传', '传输中') else status
            percent = min(100, count * 100 // size)
            bar = '#' * (percent // 10) + '-' * (10 - percent // 10)
            lines.append(f'{name} [{bar}] {percent:3d}% '
                         f'{count / 1048576:.1f}/{size / 1048576:.1f} MiB '
                         f'{_format_transfer_speed(speed)}  {shown_status}')
            previous[i] = count
        for line in lines:
            log.write(line + '\n')
        log.flush()
        if display_gate is None or display_gate.is_set():
            if interactive and rendered_on_terminal:
                print(f'\033[{len(lines)}A', end='')
            for line in lines:
                print(('\033[2K' if interactive else '') + line, flush=True)
            rendered_on_terminal = True
        last_sample = sample_now

    pending = [item for item in items if not item['done']]
    if not pending:
        _render_frame(time.monotonic())
        return

    def _upload_one(item):
        name = item['name']
        size = item['size']
        sha256 = item['sha256']
        path = item['path']
        last_error = ''
        consecutive_failures = 0
        total_attempts = 0
        max_consecutive_failures = 5
        max_total_attempts = 20
        while consecutive_failures < max_consecutive_failures and total_attempts < max_total_attempts:
            total_attempts += 1
            if cancel_event and cancel_event.is_set():
                return
            if total_attempts > 1:
                try:
                    reprobed = _probe_remote_desktop(
                        args, staging, {name: {'size': size, 'sha256': sha256}},
                        log, cancel_event=cancel_event, preempt=True)
                except Exception as exc:
                    reprobed = None
                    last_error = str(exc)
                if cancel_event and cancel_event.is_set():
                    return
                if reprobed is None:
                    consecutive_failures += 1
                    if consecutive_failures < max_consecutive_failures and total_attempts < max_total_attempts:
                        with lock:
                            item['status'] = f'等待第 {total_attempts + 1} 次尝试'
                        wait_s = min(8, 2 ** (consecutive_failures - 1))
                        if cancel_event:
                            cancel_event.wait(wait_s)
                        else:
                            time.sleep(wait_s)
                        continue
                    break
                state = reprobed.get(name, {'status': 'pending', 'offset': 0})
                if state.get('status') == 'cached':
                    with lock:
                        item['received'] = size
                        item['status'] = '校验通过'
                        item['done'] = True
                    return
                candidate = int(state.get('offset', 0))
                if (state.get('status') == 'partial' and 0 < candidate < size
                        and state.get('prefix_sha256') == _prefix_digest(path, candidate)):
                    offset = candidate
                else:
                    offset = 0
                with lock:
                    item['offset'] = offset
                    item['received'] = offset
            else:
                with lock:
                    offset = item['offset']

            attempt_start_received = offset
            with lock:
                item['status'] = '续传' if offset else '传输中'
                item['last_progress_at'] = time.monotonic()

            remote_cmd = (
                'cd ' + shlex.quote(staging) + ' && exec '
                + shlex.join(['python3', '-u', 'pull_desktop.py', '--receive', name,
                              '--offset', str(offset), '--size', str(size), '--sha256', sha256, '--preempt'])
            )
            ready_event = threading.Event()
            verified_event = threading.Event()
            protocol_errors = []
            stderr_chunks = []

            process = subprocess.Popen(
                [*direct_ssh_args(args), remote_cmd],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            with lock:
                item['active_process'] = process

            def _read_stdout():
                for raw in process.stdout:
                    line = raw.decode(errors='replace').strip()
                    if line.startswith('READY '):
                        ready_event.set()
                    elif line.startswith('PROGRESS '):
                        try:
                            received = int(line.split(' ', 1)[1])
                        except ValueError:
                            continue
                        with lock:
                            if received > item['received']:
                                item['received'] = min(size, received)
                            item['last_progress_at'] = time.monotonic()
                    elif line.startswith('VERIFIED '):
                        with lock:
                            item['received'] = size
                            item['last_progress_at'] = time.monotonic()
                        verified_event.set()
                        ready_event.set()
                    elif line == 'CHECKSUM_MISMATCH':
                        protocol_errors.append('校验和不一致')
                    elif line.startswith('INCOMPLETE '):
                        protocol_errors.append('传输提前中断')

            def _read_stderr():
                for raw in process.stderr:
                    stderr_chunks.append(raw.decode(errors='replace'))

            stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
            stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            try:
                ready_deadline = time.monotonic() + 30
                while not ready_event.is_set() and process.poll() is None:
                    if (cancel_event and cancel_event.is_set()) or time.monotonic() >= ready_deadline:
                        process.kill()
                        break
                    ready_event.wait(0.1)
                if ready_event.is_set() and not verified_event.is_set() and process.poll() is None:
                    with path.open('rb') as source:
                        source.seek(offset)
                        remaining = size - offset
                        while remaining > 0:
                            if cancel_event and cancel_event.is_set():
                                process.kill()
                                break
                            data = source.read(min(chunk_size, remaining))
                            if not data:
                                break
                            try:
                                process.stdin.write(data)
                            except (BrokenPipeError, OSError):
                                break
                            remaining -= len(data)
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                exit_code = process.wait()
                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=2)
            finally:
                with lock:
                    if item['active_process'] is process:
                        item['active_process'] = None
                if process.poll() is None:
                    process.kill()
                    process.wait()
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)

            if cancel_event and cancel_event.is_set():
                return
            if exit_code == 0 and verified_event.is_set():
                with lock:
                    item['received'] = size
                    item['status'] = '校验通过'
                    item['done'] = True
                return
            stderr_text = ''.join(stderr_chunks).strip()
            if stderr_text:
                last_error = stderr_text.splitlines()[-1]
                log.write(f'\n{name} 直连上传异常（exit={exit_code}）：{stderr_text}\n')
                log.flush()
            elif protocol_errors:
                last_error = protocol_errors[-1]
            else:
                last_error = f'连接中断（exit={exit_code}）'
            with lock:
                made_progress = item['received'] > attempt_start_received
            consecutive_failures = 0 if made_progress else consecutive_failures + 1
            if consecutive_failures < max_consecutive_failures and total_attempts < max_total_attempts:
                with lock:
                    item['status'] = f'等待第 {total_attempts + 1} 次尝试'
                wait_s = 1 if made_progress else min(8, 2 ** (consecutive_failures - 1))
                if cancel_event:
                    cancel_event.wait(wait_s)
                else:
                    time.sleep(wait_s)

        detail = f'（{last_error}）' if last_error else ''
        with lock:
            item['error'] = RuntimeError(
                f'{name} 直连上传或校验失败{detail}，可用 --resume 继续，或改用 --desktop-transfer tunnel'
            )
            item['status'] = '失败'
            item['done'] = True

    workers = [threading.Thread(target=_upload_one, args=(item,), daemon=True) for item in pending]
    for worker in workers:
        worker.start()

    try:
        while True:
            if cancel_event and cancel_event.is_set():
                with lock:
                    active = [item['active_process'] for item in items if item['active_process'] is not None]
                for process in active:
                    if process.poll() is None:
                        process.kill()
                for worker in workers:
                    worker.join(timeout=5)
                return
            tick = time.monotonic()
            with lock:
                stalled = [
                    item['active_process'] for item in items
                    if item['active_process'] is not None
                    and item['status'] in ('续传', '传输中')
                    and tick - item['last_progress_at'] >= stall_timeout
                ]
            for process in stalled:
                if process.poll() is None:
                    process.kill()
            _render_frame(tick)
            if not any(worker.is_alive() for worker in workers):
                break
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if (cancel_event and cancel_event.is_set()) or not any(worker.is_alive() for worker in workers):
                    break
                time.sleep(0.05)
    finally:
        for worker in workers:
            worker.join(timeout=5)

    if cancel_event and cancel_event.is_set():
        return
    for item in items:
        if item['error'] is not None:
            raise item['error']


def transfer_desktop_files(args, staging, files, artifacts, log, *, display_gate=None, cancel_event=None):
    method = getattr(args, 'desktop_transfer', 'ssh')
    if method == 'tunnel':
        pull_desktop_files(args, staging, files, artifacts, log,
                           display_gate=display_gate, cancel_event=cancel_event)
    else:
        push_desktop_files(args, staging, files, artifacts, log,
                           display_gate=display_gate, cancel_event=cancel_event)


def publish_desktop_update(args, record, folder, source, *, already_pulled=False, upload_helpers=True):
    update = record.get('desktop_update')
    if not update or update.get('published'):
        return
    if args.public_url.rstrip('/') != update['public_url']:
        raise RuntimeError('续发目标与桌面包的更新服务地址不一致')
    staging = '/srv/champion-erp/staging/' + record['release_id']
    files, artifacts, command = prepare_desktop_transfer_inputs(update, folder, staging)
    if not already_pulled:
        if upload_helpers:
            # 拉取端与当前编排脚本配套，续发旧版本时仍使用旧包自带的发布程序。
            _retry_ssh(lambda: subprocess.run(
                [*scp_args(args), source / 'deploy/publish_desktop.py', ROOT / 'deploy/pull_desktop.py',
                 args.ssh_host + ':' + staging + '/'], check=True))
        with (folder / 'build.log').open('a') as log:
            transfer_desktop_files(args, staging, files, artifacts, log)
    print(remote_output(args, command), flush=True)
    manifest_url = desktop_manifest_url(update)
    final_url, manifest_body = _fetch_bytes(manifest_url)
    manifest = json.loads(manifest_body)
    if (final_url != manifest_url or manifest['version'] != update['version']
            or manifest['installer']['sha256'] != update['installer']['sha256']
            or ('mac_zip' in update and manifest.get('macZip', {}).get('sha256') != update['mac_zip']['sha256'])
            or (update['policy'] == 'required' and manifest['minimum'] != update['version'])):
        raise RuntimeError('公网桌面更新清单与本次发布不一致')
    update['published'] = True
    write_record(folder / 'release.json', record)


def cleanup_server_artifacts(args, record, source, *, upload_helper=True):
    # 使用本次源码，旧发布记录也能重试清理；清理失败保留 server_published 以便续发。
    staging = '/srv/champion-erp/staging/' + record['release_id']
    if upload_helper:
        cleanup_script = source / 'deploy/cleanup_releases.py'
        if not cleanup_script.is_file():
            cleanup_script = ROOT / 'deploy/cleanup_releases.py'
        _retry_ssh(lambda: subprocess.run(
            [*scp_args(args), cleanup_script, args.ssh_host + ':' + staging + '/cleanup_releases.py'], check=True))
    return json.loads(remote_output(args, ['python3', staging + '/cleanup_releases.py',
        '--release-id', record['release_id'], '--revision', record['revision']]))


def finish_release(args, record, folder, source, *, desktop_already_pulled=False, upload_helpers=True, public_verified=False):
    if not public_verified:
        record['public'] = verify_public(args.public_url, source)
        write_record(folder / 'release.json', record)
    if record.get('desktop_update'):
        if desktop_already_pulled or not upload_helpers:
            publish_desktop_update(args, record, folder, source,
                                   already_pulled=desktop_already_pulled, upload_helpers=upload_helpers)
        else:
            publish_desktop_update(args, record, folder, source)
    elif record.get('desktop_target'):
        print('旧发布记录没有桌面分发包，本次继续完成服务器和网页发布。', flush=True)
    print('清理历史程序、桌面安装包及上传副本，仅保留当前版本', flush=True)
    if upload_helpers:
        record['cleanup'] = cleanup_server_artifacts(args, record, source)
    else:
        record['cleanup'] = cleanup_server_artifacts(args, record, source, upload_helper=False)
    record['status'] = 'complete'
    write_record(folder / 'release.json', record)
    output_dir = getattr(args, 'output_dir', None)
    if output_dir and record.get('release_id'):
        cleanup_local_releases(output_dir, record['release_id'])
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
    known_hosts = getattr(args, 'known_hosts', None)
    if known_hosts is not None and not known_hosts.is_file():
        raise RuntimeError('SSH known_hosts 文件不存在')
    update = record.get('desktop_update')
    if (update and not update.get('published')
            and getattr(args, 'desktop_transfer', 'ssh') == 'tunnel'
            and not shutil.which('cloudflared')):
        raise RuntimeError('桌面隧道发布需要 cloudflared，请先在构建机安装，或改用 --desktop-transfer ssh')
    archive = folder / 'server.tar.gz'
    if digest(archive) != record['archive_sha256']:
        raise RuntimeError('已有服务器发布包摘要发生变化')
    with ssh_session(args):
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
    if args.desktop_update and getattr(args, 'desktop_transfer', 'ssh') == 'tunnel' and not shutil.which('cloudflared'):
        raise RuntimeError('桌面隧道发布需要 cloudflared，请先在构建机安装，或改用 --desktop-transfer ssh')
    allow_dirty = getattr(args, 'allow_dirty', False)
    dirty_output = output('git', 'status', '--porcelain', '--untracked-files=normal')
    if dirty_output and not allow_dirty:
        raise RuntimeError('请先提交本次代码修改，发布仅使用干净的 Git 版本')
    if not args.ssh_identity.is_file():
        raise RuntimeError('SSH 密钥文件不存在')
    known_hosts = getattr(args, 'known_hosts', None)
    if known_hosts is not None and not known_hosts.is_file():
        raise RuntimeError('SSH known_hosts 文件不存在')
    revision = output('git', 'rev-parse', 'HEAD')
    if allow_dirty:
        if dirty_output:
            print(f'已按 --allow-dirty 忽略工作区未提交修改，仅按已提交版本 {revision[:12]} 构建发布', flush=True)
        package = json.loads(output('git', 'show', f'{revision}:front/package.json'))
    else:
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
        with tempfile.TemporaryDirectory(prefix='erp-release-source-') as temporary:
            source = Path(temporary) / 'source'
            source.mkdir()
            with (folder / 'build.log').open('w') as log:
                git_archive = folder / 'source.tar'
                with git_archive.open('wb') as archive:
                    subprocess.run(['git', 'archive', revision], cwd=ROOT, stdout=archive, check=True)
                with tarfile.open(git_archive) as archive:
                    archive.extractall(source, filter='data')
                git_archive.unlink()
                # pnpm 安装：复用项目全局 store 避免冷解析。
                print('安装锁定的前端依赖并检查发布输入', flush=True)
                pnpm_store = ROOT / '.pnpm-store'
                pnpm_install = ['pnpm', 'install', '--frozen-lockfile', '--ignore-scripts']
                if pnpm_store.is_dir():
                    pnpm_install.extend(['--store-dir', str(pnpm_store)])
                run(pnpm_install, cwd=source / 'front', log=log, env=environment)
                if args.desktop_update:
                    run(['node', 'node_modules/electron/install.js'], cwd=source / 'front', log=log, env=environment)
                # 声明检查与类型检查互不依赖，并行执行。
                print('检查声明与类型', flush=True)
                run_parallel([
                    ([sys.executable, 'scripts/generate_third_party_notices.py', '--check'], source, environment),
                    (['pnpm', 'types:check'], source / 'front', environment),
                ], log=log)
                if args.skip_tests:
                    print('已按 --skip-tests 跳过后端、前端和桌面全量测试', flush=True)
                else:
                    print('运行后端、前端及桌面回归检查', flush=True)
                    run([sys.executable, '-m', 'pytest', 'tests', '-q'], cwd=source, log=log, env=environment)
                    run(['pnpm', 'test:run'], cwd=source / 'front', log=log, env=environment)
                    run(['pnpm', 'test:desktop'], cwd=source / 'front', log=log, env=environment)
                if args.desktop_update:
                    # 网页构建与 Python 桌面执行器构建互不依赖，并行执行。
                    # 网页构建（vue-tsc -b && vite build）完成后，桌面前端只需
                    # vite build --mode desktop，跳过重复 vue-tsc 类型编译。
                    print('编译网页并构建桌面执行器', flush=True)
                    run_parallel([
                        (['pnpm', 'build'], source / 'front', environment),
                        ([sys.executable, 'scripts/build_desktop_runtime.py'], source, environment),
                    ], log=log)
                    # 网页 vue-tsc 已完成增量缓存，桌面前端只需 vite build。
                    print('构建并验证桌面更新包', flush=True)
                    run(['pnpm', 'exec', 'vite', 'build', '--mode', 'desktop'], cwd=source / 'front', log=log, env=environment)
                    run(['pnpm', 'exec', 'electron-builder', '--mac', 'dmg', 'zip', '--publish', 'never',
                         '--config.directories.output=' + str(folder / 'desktop')],
                        cwd=source / 'front', log=log, env=environment)
                    apps = list((folder / 'desktop').glob('*/*.app'))
                    if len(apps) != 1:
                        raise RuntimeError('桌面构建没有产生唯一的应用包')
                    app = apps[0]
                    # electron-builder 的 afterSign 钩子已完成严格的 --verify --deep --strict -R 签名复核。
                    with (app / 'Contents/Info.plist').open('rb') as info:
                        if plistlib.load(info)['CFBundleShortVersionString'] != version:
                            raise RuntimeError('桌面安装包版本不一致')
                    check_runtime(app / 'Contents/Resources/runtime/champion-erp-runtime')
                    record['desktop_update'] = desktop_release_artifacts(folder, app, version, args)
                    prune_desktop_build_intermediates(folder / 'desktop')
                    check_new_desktop_version(record['desktop_update'])
                else:
                    print('编译网页', flush=True)
                    run(['pnpm', 'build'], cwd=source / 'front', log=log, env=environment)
            # 本地构建完成后再建立 SSH ControlMaster，避免长耗时构建期间连接闲置失效。
            with ssh_session(args):
                with (folder / 'build.log').open('a') as log:
                    # 只打包服务器运行所需源码、本次网页构建与未在服务器缓存的静态资产，排除前端源码/测试/文档。
                    tracked = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', '-z', revision], cwd=ROOT).decode().split('\0')
                    selected = select_server_files(tracked)
                    shared_assets = detect_reusable_server_assets(args, source, selected)
                    (source / 'REVISION').write_text(revision + '\n')
                    manifest_payload = {'revision': revision, 'version': version, 'release_id': release_id}
                    if shared_assets:
                        manifest_payload['shared_assets'] = shared_assets
                    write_record(source / 'release-manifest.json', manifest_payload)
                    archive_path = folder / 'server.tar.gz'
                    with tarfile.open(archive_path, 'w:gz', compresslevel=1) as archive:
                        for name in selected:
                            if name in shared_assets:
                                continue
                            archive.add(source / name, arcname=name, recursive=False)
                        archive.add(source / 'erp_web/static/dist', arcname='erp_web/static/dist')
                        for name in ('REVISION', 'release-manifest.json'):
                            archive.add(source / name, arcname=name)
                    record.update(status='built', archive_sha256=digest(archive_path))
                    write_record(record_path, record)
                    remote_folder = '/srv/champion-erp/staging/' + release_id
                    remote_output(args, ['mkdir', '-p', remote_folder + '/erp_web', remote_folder + '/deploy'])
                    # 将全部发布辅助脚本一次性随服务器包上传，减少重复 SCP 往返。
                    staging = Path(temporary) / 'staging'
                    (staging / 'deploy').mkdir(parents=True)
                    (staging / 'erp_web').mkdir()
                    shutil.copy2(source / 'deploy/release_server.py', staging / 'deploy/')
                    shutil.copy2(source / 'erp_web/__init__.py', staging / 'erp_web/')
                    shutil.copy2(source / 'erp_web/release_guard.py', staging / 'erp_web/')
                    extra_helpers = [staging / 'cleanup_releases.py']
                    shutil.copy2(source / 'deploy/cleanup_releases.py', staging / 'cleanup_releases.py')
                    if record.get('desktop_update'):
                        shutil.copy2(source / 'deploy/publish_desktop.py', staging / 'publish_desktop.py')
                        shutil.copy2(ROOT / 'deploy/pull_desktop.py', staging / 'pull_desktop.py')
                        extra_helpers.extend([staging / 'publish_desktop.py', staging / 'pull_desktop.py'])
                    print('上传服务器发布包并执行备份切换', flush=True)
                    _retry_ssh(lambda: upload_server_files(
                        [*scp_args(args), '-r', archive_path, staging / 'deploy', staging / 'erp_web',
                         *extra_helpers, args.ssh_host + ':' + remote_folder + '/'], log))
                    record['status'] = 'server_staged'
                    write_record(record_path, record)
                # 服务器切换与桌面包传输并行执行，缩短整体等待时间。
                pull_thread = None
                pull_error = []
                pull_log = io.StringIO()
                display_gate = threading.Event()
                cancel_event = threading.Event()
                if record.get('desktop_update'):
                    desktop_files, desktop_artifacts, _ = prepare_desktop_transfer_inputs(
                        record['desktop_update'], folder, remote_folder)

                    def _pull_worker():
                        try:
                            transfer_desktop_files(
                                args, remote_folder, desktop_files, desktop_artifacts, pull_log,
                                display_gate=display_gate, cancel_event=cancel_event,
                            )
                        except BaseException as exc:
                            pull_error.append(exc)

                    pull_thread = threading.Thread(target=_pull_worker, daemon=True)
                    pull_thread.start()
                try:
                    switch_server(args, record, folder, source)
                    record['public'] = verify_public(args.public_url, source)
                    write_record(record_path, record)
                except BaseException:
                    if pull_thread is not None:
                        cancel_event.set()
                        pull_thread.join(timeout=15)
                    raise
                desktop_already_pulled = False
                if pull_thread is not None:
                    if pull_thread.is_alive():
                        print('服务器已切换，继续完成桌面包传输', flush=True)
                    else:
                        print('服务器已切换，桌面包已完成传输', flush=True)
                    display_gate.set()
                    pull_thread.join()
                    if pull_log.getvalue():
                        with (folder / 'build.log').open('a') as build_log:
                            build_log.write(pull_log.getvalue())
                    if pull_error:
                        raise pull_error[0]
                    desktop_already_pulled = True
                finish_release(args, record, folder, source,
                               desktop_already_pulled=desktop_already_pulled,
                               upload_helpers=False, public_verified=True)
    except BaseException:
        record['status'] = 'failed_after_' + record['status']
        write_record(record_path, record)
        print(f'发布未完成，已保留状态和日志，请检查 {record_path}', file=sys.stderr, flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-host', default='root@107.174.48.238', help='服务器 SSH 登录目标')
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--ssh-identity', type=Path, default=Path.home() / '.ssh/id_ed25519')
    parser.add_argument('--known-hosts', type=Path, help='自定义 SSH known_hosts 文件路径（CI 环境可用）')
    parser.add_argument('--public-url', default='https://hdgl.cc.cd', help='升级后验证的公网地址')
    parser.add_argument('--output-dir', type=Path, default=Path.home() / 'Library/Application Support/Champion ERP/releases')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--desktop-update', choices=['optional', 'required'], help='发布桌面升级，默认 optional，可显式指定 required')
    mode.add_argument('--server-only', action='store_true', help='仅发布服务器和网页，跳过桌面打包和分发')
    parser.add_argument('--skip-tests', action='store_true', help='跳过后端、前端和桌面全量测试，继续执行构建检查与发布验收')
    parser.add_argument('--allow-dirty', action='store_true', help='忽略工作区未提交修改，仅按当前已提交的 Git HEAD 构建发布')
    parser.add_argument('--desktop-transfer', choices=['ssh', 'tunnel'], default='ssh',
                        help='桌面包传输方式：默认 ssh（直连多路独立 SSH 可续传），直连拥塞时可改为 tunnel（Cloudflare QUIC 隧道）')
    parser.add_argument('--update-notes', default='', help='向用户展示的更新说明')
    parser.add_argument('--signed-desktop', action='store_true', help='使用 Developer ID 签名及公证构建原生更新 ZIP')
    parser.add_argument('--wait-tasks-seconds', type=int, default=3600, help='服务器等待已接任务安全停机的最长秒数')
    parser.add_argument('--resume', type=Path, help='从已上传的发布目录继续原有服务器切换、验证和更新包分发')
    args = parser.parse_args()
    if args.resume and (args.desktop_update or args.server_only or args.signed_desktop or args.update_notes or args.allow_dirty):
        parser.error('--resume 沿用发布记录中的桌面策略、签名与更新说明')
    if args.server_only and (args.signed_desktop or args.update_notes):
        parser.error('--server-only 不能与桌面签名或更新说明一起使用')
    if not args.resume and not args.server_only and not args.desktop_update:
        args.desktop_update = 'optional'
    if len(args.update_notes) > 2000:
        parser.error('桌面更新说明最多 2000 字')
    if args.wait_tasks_seconds < 0:
        parser.error('等待任务时间必须为非负数')
    if args.known_hosts is not None:
        args.known_hosts = args.known_hosts.expanduser().resolve()
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

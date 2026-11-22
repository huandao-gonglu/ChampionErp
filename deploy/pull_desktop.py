"""在发布暂存目录并行接收或拉取桌面包，向 SSH 终端报告实际落盘进度。"""
import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time

ALLOWED_ARTIFACTS = {'installer.dmg', 'update.zip'}
SHA256_RE = re.compile(r'[0-9a-f]{64}')
RECEIVE_CHUNK_SIZE = 256 * 1024


def file_sha256(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def format_speed(bytes_per_second):
    if bytes_per_second >= 1048576:
        return f'{bytes_per_second / 1048576:.2f} MiB/s'
    return f'{bytes_per_second / 1024:.1f} KiB/s'


@dataclass
class Download:
    target: Path
    size: int
    sha256: str
    status: str = '等待传输'

    @property
    def partial(self):
        return self.target.with_name(self.target.name + '.https.part')

    def received(self):
        if self.status in ('已复用', '校验通过'):
            return self.size
        try:
            return self.partial.stat().st_size
        except FileNotFoundError:
            return 0

    def verified(self, path):
        if not path.is_file() or path.is_symlink() or path.stat().st_size != self.size:
            return False
        return file_sha256(path) == self.sha256

    def prepare(self):
        if self.verified(self.target):
            self.status = '已复用'
            return
        # 复用旧版 SCP 或直连 SSH 中断遗留的部分文件。
        for legacy in (self.target.with_name(self.target.name + '.ssh.part'), self.target):
            if legacy.is_file() and not legacy.is_symlink() and not self.partial.exists() and legacy.stat().st_size < self.size:
                legacy.replace(self.partial)
                break
        if self.verified(self.partial):
            self.partial.replace(self.target)
            self.status = '已复用'
        elif self.partial.exists() and self.received() >= self.size:
            self.partial.unlink()

    async def fetch(self, base_url):
        last_error = ''
        for attempt in range(1, 6):
            self.status = '续传' if self.received() else '传输中'
            # 通过标准输入传入带临时凭据的 URL，不在进程命令行显示；
            # 捕获 stderr 避免重试期间的 curl 报错打乱终端进度条光标位置。
            process = await asyncio.create_subprocess_exec(
                'curl', '--disable', '--fail', '--silent', '--show-error', '--connect-timeout', '15',
                '--speed-limit', '1', '--speed-time', '30', '--continue-at', '-', '--output',
                str(self.partial), '--config', '-',
                stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                url = base_url + '/' + self.target.name
                _, stderr_bytes = await process.communicate(('url = ' + json.dumps(url) + '\n').encode())
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if process.returncode == 0 and self.verified(self.partial):
                self.partial.replace(self.target)
                self.status = '校验通过'
                return
            if stderr_bytes:
                last_error = stderr_bytes.decode(errors='replace').strip().splitlines()[-1]
            # 校验失败或对端拒绝 Range 时，从头获取一次完整内容。
            if process.returncode in (0, 33) or self.received() >= self.size:
                self.partial.unlink(missing_ok=True)
            if attempt < 5:
                self.status = f'等待第 {attempt + 1} 次尝试'
                # 首次重试多为新隧道 DNS/边缘路由未就绪，使用 1s 短等待即可。
                await asyncio.sleep(1 if attempt == 1 else min(16, 2 ** (attempt - 1)))
        detail = f'（{last_error}）' if last_error else ''
        raise RuntimeError(f'{self.target.name} 传输或校验失败{detail}，可用 --resume 继续')


async def transfer(downloads, base_url, interactive):
    pending = [item for item in downloads if item.status != '已复用']
    previous = [item.received() for item in downloads]
    last_sample = time.monotonic()
    last_change = [last_sample] * len(downloads)
    rendered = False
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(item.fetch(base_url)) for item in pending]
        while True:
            now = time.monotonic()
            lines = []
            for i, item in enumerate(downloads):
                count = item.received()
                speed = max(0, count - previous[i]) / max(now - last_sample, 0.001)
                if count != previous[i]:
                    last_change[i] = now
                idle = int(now - last_change[i])
                status = f'停滞 {idle}s' if idle >= 3 and item.status in ('续传', '传输中') else item.status
                percent = min(100, count * 100 // item.size)
                bar = '#' * (percent // 10) + '-' * (10 - percent // 10)
                lines.append(f'{item.target.name} [{bar}] {percent:3d}% '
                             f'{count / 1048576:.1f}/{item.size / 1048576:.1f} MiB '
                             f'{format_speed(speed)}  {status}')
                previous[i] = count
            if interactive and rendered:
                print(f'\033[{len(lines)}A', end='')
            for line in lines:
                print(('\033[2K' if interactive else '') + line, flush=True)
            rendered = True
            if all(task.done() for task in tasks):
                break
            last_sample = now
            await asyncio.wait(tasks, timeout=1)


def _validate_artifact(name, size, sha256):
    if name not in ALLOWED_ARTIFACTS:
        raise ValueError('桌面包名称无效')
    if not isinstance(size, int) or size <= 0:
        raise ValueError('桌面包大小无效')
    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
        raise ValueError('桌面包摘要格式无效')


def probe_ssh():
    """检查目标暂存目录中的成品和断点文件，供直连 SSH 续传前核对前缀摘要。"""
    request = json.loads(sys.stdin.readline())
    artifacts = request['artifacts']
    if not artifacts or not artifacts.keys() <= ALLOWED_ARTIFACTS:
        raise ValueError('桌面包名称无效')
    result = {}
    with ExitStack() as stack:
        dir_lock = stack.enter_context(open('.desktop-transfer.lock', 'a'))
        fcntl.flock(dir_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        for name, item in artifacts.items():
            size, sha256 = item['size'], item['sha256']
            _validate_artifact(name, size, sha256)
            file_lock = stack.enter_context(open(f'.desktop-transfer.{name}.lock', 'a'))
            fcntl.flock(file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            target = Path(name)
            partial = Path(name + '.ssh.part')
            if target.is_file() and not target.is_symlink() and target.stat().st_size == size and file_sha256(target) == sha256:
                partial.unlink(missing_ok=True)
                result[name] = {'status': 'cached', 'offset': size}
                continue
            if not partial.exists():
                for legacy in (Path(name + '.https.part'), target):
                    if legacy.is_file() and not legacy.is_symlink() and legacy.stat().st_size < size:
                        legacy.replace(partial)
                        break
            if partial.is_file() and not partial.is_symlink():
                current_size = partial.stat().st_size
                if current_size == size and file_sha256(partial) == sha256:
                    partial.replace(target)
                    result[name] = {'status': 'cached', 'offset': size}
                elif 0 < current_size < size:
                    result[name] = {
                        'status': 'partial',
                        'offset': current_size,
                        'prefix_sha256': file_sha256(partial),
                    }
                else:
                    partial.unlink(missing_ok=True)
                    result[name] = {'status': 'pending', 'offset': 0}
            else:
                result[name] = {'status': 'pending', 'offset': 0}
    print(json.dumps(result), flush=True)


def receive_ssh(name, offset, size, sha256):
    """通过标准输入接收单个桌面包的原始字节流，实时回报落盘进度并在完成后校验替换。"""
    _validate_artifact(name, size, sha256)
    if offset < 0 or offset >= size:
        raise ValueError('续传偏移量无效')
    with ExitStack() as stack:
        dir_lock = stack.enter_context(open('.desktop-transfer.lock', 'a'))
        fcntl.flock(dir_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        file_lock = stack.enter_context(open(f'.desktop-transfer.{name}.lock', 'a'))
        fcntl.flock(file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = Path(name)
        partial = Path(name + '.ssh.part')
        if target.is_file() and not target.is_symlink() and target.stat().st_size == size and file_sha256(target) == sha256:
            partial.unlink(missing_ok=True)
            print(f'VERIFIED {size}', flush=True)
            return 0
        if offset == 0:
            open_mode = 'wb'
        else:
            if not partial.is_file() or partial.is_symlink() or partial.stat().st_size < offset:
                raise RuntimeError('断点文件缺失或小于声明偏移量')
            open_mode = 'r+b'
        with partial.open(open_mode) as stream:
            if offset > 0:
                stream.truncate(offset)
                stream.seek(offset)
            print(f'READY {offset}', flush=True)
            written = offset
            last_report_time = time.monotonic()
            last_report_bytes = written
            reader = sys.stdin.buffer
            while True:
                chunk = reader.read(RECEIVE_CHUNK_SIZE)
                if not chunk:
                    break
                if written + len(chunk) > size:
                    raise ValueError('接收字节数超出声明大小')
                stream.write(chunk)
                written += len(chunk)
                now = time.monotonic()
                if now - last_report_time >= 0.4 or written - last_report_bytes >= 2 * 1024 * 1024:
                    stream.flush()
                    print(f'PROGRESS {written}', flush=True)
                    last_report_time = now
                    last_report_bytes = written
            stream.flush()
        print(f'PROGRESS {written}', flush=True)
        if written != size:
            print(f'INCOMPLETE {written}', flush=True)
            return 1
        if file_sha256(partial) != sha256:
            partial.unlink(missing_ok=True)
            print('CHECKSUM_MISMATCH', flush=True)
            return 2
        partial.replace(target)
        print(f'VERIFIED {size}', flush=True)
        return 0


async def pull_tunnel_main():
    request = json.loads(sys.stdin.readline())
    artifacts = request['artifacts']
    if not artifacts or not artifacts.keys() <= ALLOWED_ARTIFACTS:
        raise ValueError('桌面包名称无效')
    downloads = [Download(Path(name), item['size'], item['sha256']) for name, item in artifacts.items()]
    if any(item.size <= 0 for item in downloads):
        raise ValueError('桌面包为空')
    with open('.desktop-transfer.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for item in downloads:
            item.prepare()
        needed = any(item.status != '已复用' for item in downloads)
        if needed and not shutil.which('curl'):
            raise RuntimeError('服务器缺少拉取桌面包所需的 curl')
        print('download' if needed else 'cached', flush=True)
        url = input() if needed else ''
        await transfer(downloads, url, request['interactive'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--probe', action='store_true', help='探测暂存目录中桌面包的完成与断点状态')
    mode.add_argument('--receive', metavar='NAME', help='从标准输入接收单个桌面包并校验落盘')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--size', type=int)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if args.probe:
        probe_ssh()
    elif args.receive:
        if args.size is None or not args.sha256:
            parser.error('--receive 需要同时提供 --size 和 --sha256')
        sys.exit(receive_ssh(args.receive, args.offset, args.size, args.sha256))
    else:
        asyncio.run(pull_tunnel_main())


if __name__ == '__main__':
    main()

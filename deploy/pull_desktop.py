"""在发布暂存目录并行拉取桌面包，向 SSH 终端报告实际落盘进度。"""
import asyncio
from dataclasses import dataclass
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time


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
        if not path.is_file() or path.stat().st_size != self.size:
            return False
        with path.open('rb') as source:
            return hashlib.file_digest(source, 'sha256').hexdigest() == self.sha256

    def prepare(self):
        if self.verified(self.target):
            self.status = '已复用'
            return
        # 旧版 SCP 中断后，部分数据可能位于正式文件名下。
        if self.target.exists() and not self.partial.exists() and self.target.stat().st_size < self.size:
            self.target.replace(self.partial)
        if self.verified(self.partial):
            self.partial.replace(self.target)
            self.status = '已复用'
        elif self.partial.exists() and self.received() >= self.size:
            self.partial.unlink()

    async def fetch(self, base_url):
        for attempt in range(1, 5):
            self.status = '续传' if self.received() else '传输中'
            # 通过标准输入传入带临时凭据的 URL，不在进程命令行显示。
            process = await asyncio.create_subprocess_exec(
                'curl', '--disable', '--fail', '--silent', '--show-error', '--connect-timeout', '15',
                '--speed-limit', '1', '--speed-time', '30', '--continue-at', '-', '--output',
                str(self.partial), '--config', '-', stdin=asyncio.subprocess.PIPE)
            try:
                url = base_url + '/' + self.target.name
                await process.communicate(('url = ' + json.dumps(url) + '\n').encode())
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if process.returncode == 0 and self.verified(self.partial):
                self.partial.replace(self.target)
                self.status = '校验通过'
                return
            # 校验失败或对端拒绝 Range 时，从头获取一次完整内容。
            if process.returncode in (0, 33) or self.received() >= self.size:
                self.partial.unlink(missing_ok=True)
            if attempt < 4:
                self.status = f'等待第 {attempt + 1} 次尝试'
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f'{self.target.name} 传输或校验失败，可用 --resume 继续')


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
                             f'{speed / 1024:.1f} KiB/s  {status}')
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


async def main():
    request = json.loads(sys.stdin.readline())
    artifacts = request['artifacts']
    allowed = {'installer.dmg', 'update.zip'}
    if not artifacts or not artifacts.keys() <= allowed:
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


if __name__ == '__main__':
    asyncio.run(main())

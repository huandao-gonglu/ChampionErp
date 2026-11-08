"""优先从本机 wheel 缓存安装发布依赖，缺项时补齐当前依赖集合。"""
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile


def install(requirements: Path) -> None:
    # 不同发布使用独立虚拟环境，仅共享可重新安装的 wheel。
    cache = (Path.home() / '.cache' / 'champion-erp' / 'wheels' /
             f'{sys.implementation.cache_tag}-{sysconfig.get_platform()}' / requirements.stem)
    cache.mkdir(parents=True, exist_ok=True)
    pip = [sys.executable, '-m', 'pip', '--disable-pip-version-check']
    inputs = ['-r', str(requirements.resolve())]
    # 离线解析只使用指定的 wheel 目录。联网补齐时仍沿用用户配置的 pip 源。
    local_env = {**os.environ, 'PIP_CONFIG_FILE': os.devnull, 'PIP_FIND_LINKS': ''}
    local = ['--no-index', '--only-binary=:all:', '--find-links', str(cache)]
    if any(cache.glob('*.whl')):
        # 先离线解析。安装本身的权限、磁盘等错误应直接报出。
        resolved = subprocess.run([*pip, 'install', '--dry-run', *local, *inputs],
                                  env=local_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if resolved.returncode == 0:
            print(f'从本机缓存安装 Python 依赖：{cache}', flush=True)
            subprocess.run([*pip, 'install', *local, *inputs], check=True, env=local_env)
            return

    print(f'本机缓存缺少所需依赖，正在补齐：{cache}', flush=True)
    with tempfile.TemporaryDirectory(dir=cache) as temporary:
        # pip 可将仅提供源码的依赖构建为 wheel。先安装验证，再逐文件原子写入共享缓存。
        subprocess.run([*pip, 'wheel', '--find-links', str(cache), '--wheel-dir', temporary,
                        *inputs], check=True)
        subprocess.run([*pip, 'install', '--no-index', '--only-binary=:all:',
                        '--find-links', temporary, *inputs], check=True, env=local_env)
        for wheel in Path(temporary).glob('*.whl'):
            os.replace(wheel, cache / wheel.name)


if __name__ == '__main__':
    install(Path(sys.argv[1]))

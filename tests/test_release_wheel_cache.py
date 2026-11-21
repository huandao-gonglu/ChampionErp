"""使用本地包源验证首次填充、离线安装及缺项补齐。"""
import os
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pytest

from scripts.install_release_dependencies import install


def write_wheel(source, version):
    name = 'release_cache_example'
    info = f'{name}-{version}.dist-info'
    wheel = source / f'{name}-{version}-py3-none-any.whl'
    files = {
        f'{name}/__init__.py': f'VERSION = "{version}"\n',
        f'{info}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n',
        f'{info}/WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    record = f'{info}/RECORD'
    files[record] = ''.join(f'{path},,\n' for path in [*files, record])
    with ZipFile(wheel, 'w') as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return wheel


def test_build_installs_from_cache_and_recovers_after_missing_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    monkeypatch.setenv('PIP_CONFIG_FILE', os.devnull)
    monkeypatch.setenv('PIP_NO_INDEX', '1')
    monkeypatch.setenv('PIP_FIND_LINKS', str(source))
    requirements = tmp_path / 'requirements.txt'

    def install_version(version, target):
        requirements.write_text(f'release-cache-example=={version}\n')
        monkeypatch.setenv('PIP_TARGET', str(target))
        install(requirements)
        assert (target / 'release_cache_example/__init__.py').read_text() == f'VERSION = "{version}"\n'

    first = write_wheel(source, '1.0')
    install_version('1.0', tmp_path / 'first')
    first.unlink()
    install_version('1.0', tmp_path / 'offline')
    with pytest.raises(subprocess.CalledProcessError):
        install_version('2.0', tmp_path / 'missing')
    second = write_wheel(source, '2.0')
    install_version('2.0', tmp_path / 'second')
    second.unlink()
    install_version('2.0', tmp_path / 'offline-second')
    # 升级依赖并补齐缓存后，过时的 1.0 wheel 已被自动清理。
    cached_wheels = sorted(p.name for p in (tmp_path / '.cache/champion-erp/wheels').rglob('*.whl'))
    assert cached_wheels == ['release_cache_example-2.0-py3-none-any.whl']


def test_desktop_runtime_build_reuses_content_cache_and_prunes_stale(tmp_path, monkeypatch):
    from scripts import build_desktop_runtime as builder

    root = tmp_path / 'repo'
    (root / 'desktop_runtime').mkdir(parents=True)
    (root / 'desktop_runtime/main.py').write_text('v1\n')
    (root / 'erp_shared').mkdir()
    (root / 'erp_shared/common.py').write_text('shared\n')
    (root / 'scripts').mkdir()
    for name in ('desktop_runtime_entry.py', 'desktop_runtime.spec', 'build_desktop_runtime.py'):
        (root / 'scripts' / name).write_text(name)
    (root / 'requirements-desktop.txt').write_text('pydantic==2.13.4\n')
    (root / 'requirements-desktop-build.txt').write_text('-r requirements-desktop.txt\n')
    (root / 'LICENSE').write_text('MIT\n')
    monkeypatch.setattr(builder, 'ROOT', root)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    builds = []

    def fake_run(cmd, **kwargs):
        if 'PyInstaller' in cmd:
            builds.append(cmd)
            out = root / 'front/.desktop-runtime/champion-erp-runtime'
            out.mkdir(parents=True, exist_ok=True)
            (out / builder.EXECUTABLE_NAME).write_text((root / 'desktop_runtime/main.py').read_text())

    monkeypatch.setattr(builder.subprocess, 'run', fake_run)
    builder.main()
    assert len(builds) == 1
    # 输入未变时直接复用缓存，不再调用 PyInstaller。
    builder.main()
    assert len(builds) == 1
    # 变更输入后重新构建并只保留最近 CACHE_KEEP 个缓存。
    for version in ('v2\n', 'v3\n'):
        (root / 'desktop_runtime/main.py').write_text(version)
        builder.main()
    assert len(builds) == 3
    cache_entries = [p for p in (tmp_path / 'home/.cache/champion-erp/desktop-runtime').iterdir() if p.is_dir()]
    assert len(cache_entries) == builder.CACHE_KEEP
    assert (root / 'front/.desktop-runtime/champion-erp-runtime' / builder.EXECUTABLE_NAME).read_text() == 'v3\n'

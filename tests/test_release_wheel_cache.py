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

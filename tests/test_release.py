"""发布程序的桌面原子替换、并行更新保护及服务器失败回退。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

import pytest

from deploy import release_server
from scripts import release


def app(path, content):
    archive = path / 'Contents/Resources/app.asar'
    archive.parent.mkdir(parents=True)
    archive.write_bytes(content)
    return path


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    source = app(tmp_path / 'built.app', b'new')
    target = app(tmp_path / 'installed.app', b'old')
    monkeypatch.setattr(release, 'desktop_running', lambda target: False)
    monkeypatch.setattr(release, 'check_runtime', lambda executable: None)
    monkeypatch.setattr(release.subprocess, 'run', lambda args, **kwargs: shutil.copytree(args[1], args[2]))
    return source, target


def test_desktop_update_preserves_old_package_and_installs_exact_build(desktop):
    source, target = desktop
    old = release.desktop_hash(target)
    result = release.install_desktop(source, target, old, 'release-1', 0)
    assert release.desktop_hash(target) == release.desktop_hash(source)
    assert release.desktop_hash(Path(result['backup'])) == old


def test_desktop_smoke_failure_restores_previous_program(desktop, monkeypatch):
    source, target = desktop
    old = release.desktop_hash(target)
    def failed(executable):
        raise RuntimeError('执行器检查失败')
    monkeypatch.setattr(release, 'check_runtime', failed)
    with pytest.raises(RuntimeError, match='执行器检查失败'):
        release.install_desktop(source, target, old, 'release-1', 0)
    assert release.desktop_hash(target) == old


def test_concurrent_desktop_update_is_not_overwritten(desktop):
    source, target = desktop
    old = release.desktop_hash(target)
    (target / 'Contents/Resources/app.asar').write_bytes(b'other-update')
    with pytest.raises(RuntimeError, match='其他更新'):
        release.install_desktop(source, target, old, 'release-1', 0)
    assert (target / 'Contents/Resources/app.asar').read_bytes() == b'other-update'


def test_running_desktop_times_out_without_replacing_program(desktop, monkeypatch):
    source, target = desktop
    old = release.desktop_hash(target)
    monkeypatch.setattr(release, 'desktop_running', lambda target: True)
    with pytest.raises(RuntimeError, match='退出超时'):
        release.install_desktop(source, target, old, 'release-1', 0)
    assert release.desktop_hash(target) == old


def test_server_failed_smoke_restores_code_and_service_configuration(tmp_path, monkeypatch):
    base = tmp_path / 'server'
    previous = base / 'releases/previous'
    previous.mkdir(parents=True)
    (base / 'current').symlink_to(previous)
    override = tmp_path / 'service/release-python.conf'
    override.parent.mkdir()
    override.write_text('previous-python')
    staging = tmp_path / 'staging'
    staging.mkdir()
    manifest = staging / 'release-manifest.json'
    manifest.write_text(json.dumps({'revision': 'new-revision'}))
    archive = staging / 'server.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        package.add(manifest, arcname=manifest.name)
    monkeypatch.setattr(release_server, 'BASE', base)
    monkeypatch.setattr(release_server, 'BACKUPS', tmp_path / 'backups')
    monkeypatch.setattr(release_server, 'PYTHON_OVERRIDE', override)
    monkeypatch.setattr(release_server.os, 'geteuid', lambda: 0)
    calls = []
    monkeypatch.setattr(release_server, 'run', lambda *args: calls.append(args))
    monkeypatch.setattr(release_server, 'wait_ready', lambda: None)
    def failed(path):
        raise RuntimeError('HTTP 验证失败')
    monkeypatch.setattr(release_server, 'smoke_check', failed)
    args = argparse.Namespace(archive=archive, sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        release_id='release-1', revision='new-revision', expected_current=str(previous))
    with pytest.raises(RuntimeError, match='HTTP 验证失败'):
        release_server.deploy(args)
    assert (base / 'current').resolve() == previous
    assert override.read_text() == 'previous-python'
    assert json.loads((staging / 'server-result.json').read_text())['status'] == 'rolled_back'
    assert calls[-1] == ('systemctl', 'start', release_server.SERVICE)

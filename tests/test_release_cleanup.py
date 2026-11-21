"""发布产物清理只删除可识别旧包，并保护当前版、待续发版本与业务资料。"""
import argparse
import json

import pytest

from deploy import cleanup_releases as cleaner
from deploy.publish_desktop import publish
from scripts import release


def record(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def installation(tmp_path, monkeypatch):
    base, staging, desktop = (tmp_path / name for name in ('app', 'staging', 'web/desktop-updates'))
    monkeypatch.setattr(cleaner, 'BASE', base)
    monkeypatch.setattr(cleaner, 'STAGING', staging)
    monkeypatch.setattr(cleaner, 'DESKTOP', desktop)
    ids = ['20261116t000000z-' + 'a' * 12, '20261117t000000z-' + 'b' * 12,
           '20261118t000000z-' + 'c' * 12]
    for release_id, char in zip(ids, 'abc'):
        path = base / 'releases' / release_id
        record(path / 'release-manifest.json', {'release_id': release_id, 'revision': char * 40})
        (path / 'program.py').write_text('程序')
        record(staging / release_id / 'server-result.json', {
            'status': 'published', 'release': str(path), 'revision': char * 40,
        })
        for name in cleaner.UPLOADS:
            (staging / release_id / name).write_bytes(b'archive')
    (base / 'current').symlink_to(base / 'releases' / ids[1])
    installer = tmp_path / 'installer.dmg'
    installer.write_bytes(b'installer')
    for version in ('1.0.0', '1.1.0'):
        publish(desktop, platform='darwin', arch='arm64', version=version, installer=installer,
                sha256=release.digest(installer))
    return base, staging, desktop, ids


def test_cleanup_keeps_current_pending_manual_data_and_records(installation, tmp_path):
    base, staging, desktop, (old, current, future) = installation
    # 较早开始、基于当前版本准备的并发发布，仍然可以继续。
    pending = '20261116t120000z-' + 'd' * 12
    record(base / 'releases' / pending / 'release-manifest.json', {
        'release_id': pending, 'revision': 'd' * 40,
    })
    record(staging / pending / 'server-result.json', {
        'status': 'prepared', 'release': str(base / 'releases' / pending),
        'previous_release': str(base / 'releases' / current), 'revision': 'd' * 40,
    })
    (staging / pending / 'server.tar.gz').write_bytes(b'pending')
    manual = base / 'releases/manual'
    manual.mkdir()
    external = tmp_path / '业务数据'
    external.mkdir()
    (external / 'accounts.sqlite3').write_bytes('保留'.encode())
    linked = base / 'releases' / ('20261115t000000z-' + 'e' * 12)
    linked.symlink_to(external, target_is_directory=True)
    # 不符合发布约定的资料目录不因名称像版本号就被删除。
    mixed = desktop / 'darwin-arm64/0.9.0'
    record(mixed / 'package.json', {'version': '0.9.0', 'platform': 'darwin', 'arch': 'arm64'})
    (mixed / '人工说明').touch()
    cleaner.cleanup(current, 'b' * 40, dry_run=True)
    assert (base / 'releases' / old).is_dir()
    cleaner.cleanup(current, 'b' * 40)
    assert not (base / 'releases' / old).exists()
    for release_id in (current, future, pending):
        assert (base / 'releases' / release_id).is_dir()
    for release_id in (old, current):
        assert (staging / release_id / 'server-result.json').is_file()
        assert not any((staging / release_id / name).exists() for name in cleaner.UPLOADS)
    assert (staging / future / 'server.tar.gz').is_file()
    assert (staging / pending / 'server.tar.gz').is_file()
    assert manual.is_dir() and linked.is_symlink()
    assert (external / 'accounts.sqlite3').read_bytes() == '保留'.encode()
    assert mixed.is_dir()
    assert not (desktop / 'darwin-arm64/1.0.0').exists()
    assert (desktop / 'darwin-arm64/1.1.0/installer.dmg').is_file()
    # 网络回执丢失或局部清理失败后可以安全重试。
    assert cleaner.cleanup(current, 'b' * 40)['directories'] == []


def test_cleanup_removes_orphan_failed_staging_unused_venvs_and_shared_assets(installation):
    base, staging, desktop, (old, current, future) = installation
    # 中途失败、未写 server-result.json 的旧暂存目录应被整体回收。
    orphan = '20261116t060000z-' + 'e' * 12
    (staging / orphan / 'deploy').mkdir(parents=True)
    (staging / orphan / 'deploy/release_server.py').write_text('script')
    (staging / orphan / 'server.tar.gz').write_bytes(b'leaked-tar')
    # 含人工文件的无记录暂存目录必须保留。
    manual_staging = '20261116t070000z-' + 'f' * 12
    (staging / manual_staging).mkdir(parents=True)
    (staging / manual_staging / 'server.tar.gz').write_bytes(b'tar')
    (staging / manual_staging / '人工保留.txt').write_text('keep')
    # 辅助脚本副本应在清理时一并回收。
    (staging / current / 'deploy').mkdir(parents=True)
    (staging / current / 'publish_desktop.py').write_text('pub')
    # 共享 venv 与共享大静态资产：当前版在用的保留，过时的清理。
    active_venv = base / 'venvs/py3.12-1111111111111111'
    stale_venv = base / 'venvs/py3.12-2222222222222222'
    for venv in (active_venv, stale_venv):
        venv.mkdir(parents=True)
        (venv / '.ready').write_text('ok')
    (base / 'releases' / current / '.venv').symlink_to(active_venv)
    active_asset = '1' * 64
    stale_asset = '2' * 64
    (base / 'shared-assets').mkdir(parents=True)
    (base / 'shared-assets' / active_asset).write_bytes(b'active')
    (base / 'shared-assets' / stale_asset).write_bytes(b'stale')
    record(base / 'releases' / current / 'release-manifest.json', {
        'release_id': current, 'revision': 'b' * 40,
        'shared_assets': {'erp_web/assets/fonts/font.otf': active_asset},
    })

    cleaner.cleanup(current, 'b' * 40)
    assert not (staging / orphan).exists()
    assert (staging / manual_staging / '人工保留.txt').is_file()
    assert not (staging / current / 'deploy').exists()
    assert not (staging / current / 'publish_desktop.py').exists()
    assert (staging / current / 'server-result.json').is_file()
    assert active_venv.is_dir() and not stale_venv.exists()
    assert (base / 'shared-assets' / active_asset).is_file()
    assert not (base / 'shared-assets' / stale_asset).exists()


@pytest.mark.parametrize('damage', ['current', 'result', 'manifest', 'installer', 'digest'])
def test_invalid_current_state_prevents_any_deletion(installation, damage):
    base, staging, desktop, (old, current, future) = installation
    if damage == 'current':
        (base / 'current').unlink()
        (base / 'current').symlink_to(base / 'releases' / future)
    elif damage == 'result':
        record(staging / current / 'server-result.json', {'status': 'prepared'})
    elif damage == 'manifest':
        (desktop / 'darwin-arm64/latest.json').write_text('{}')
    else:
        (desktop / 'darwin-arm64/1.1.0/installer.dmg').write_bytes(b'corrupted' if damage == 'digest' else b'broken')
    with pytest.raises(RuntimeError, match='拒绝清理'):
        cleaner.cleanup(current, 'b' * 40)
    assert (base / 'releases' / old).is_dir()
    assert (staging / old / 'server.tar.gz').is_file()
    assert (desktop / 'darwin-arm64/1.0.0/installer.dmg').is_file()


@pytest.mark.parametrize('failure', [None, 'public', 'desktop', 'cleanup'])
def test_cleanup_runs_after_all_acceptance_and_failure_can_resume(tmp_path, monkeypatch, failure):
    calls = []
    def step(name, value=None):
        def run(*args):
            calls.append(name)
            if failure == name:
                raise RuntimeError('验收失败')
            return value
        return run
    monkeypatch.setattr(release, 'verify_public', step('public', {'ok': True}))
    monkeypatch.setattr(release, 'publish_desktop_update', step('desktop'))
    monkeypatch.setattr(release, 'cleanup_server_artifacts', step('cleanup', {'directories': []}))
    result = {'status': 'server_published', 'desktop_update': {'policy': 'optional'},
              'version': '1.2.101', 'revision': 'b' * 40}
    args = argparse.Namespace(public_url='https://erp.example')
    if failure:
        with pytest.raises(RuntimeError, match='验收失败'):
            release.finish_release(args, result, tmp_path, tmp_path)
        assert result['status'] == 'server_published'
        assert calls == ['public', 'desktop', 'cleanup'][:['public', 'desktop', 'cleanup'].index(failure) + 1]
    else:
        release.finish_release(args, result, tmp_path, tmp_path)
        assert calls == ['public', 'desktop', 'cleanup']
        assert result['status'] == 'complete'

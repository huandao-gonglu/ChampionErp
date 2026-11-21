"""发布全部验收通过后，清理可重建的历史程序和安装包，保留数据与发布记录。"""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil


BASE = Path('/opt/champion-erp')
STAGING = Path('/srv/champion-erp/staging')
DESKTOP = Path('/var/www/champion-erp/desktop-updates')
RELEASE_ID = re.compile(r'[0-9]{8}t[0-9]{6}z-[0-9a-f]{12}')
VERSION = re.compile(r'(?:0|[1-9][0-9]{0,7})(?:\.(?:0|[1-9][0-9]{0,7})){2}')
VENV_KEY = re.compile(r'py\d+\.\d+-[0-9a-f]{16}')
ASSET_HASH = re.compile(r'[0-9a-f]{64}')
UPLOADS = ('server.tar.gz', 'installer.dmg', 'update.zip', 'installer.dmg.https.part', 'update.zip.https.part')
STAGING_HELPER_FILES = ('publish_desktop.py', 'pull_desktop.py', 'cleanup_releases.py', '.desktop-transfer.lock')
STAGING_HELPER_DIRS = ('deploy', 'erp_web')
KNOWN_STAGING_ENTRIES = set(UPLOADS) | set(STAGING_HELPER_FILES) | set(STAGING_HELPER_DIRS)


def read_record(path):
    if path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def release_matches(record, release_id):
    revision = record.get('revision', '')
    return (isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision)
            and revision.startswith(release_id.rsplit('-', 1)[-1]))


def cleanup_plan(base, staging, desktop, release_id, revision):
    """先完整校验保留对象，再生成计划；调用方持有服务器和桌面发布锁。"""
    if not RELEASE_ID.fullmatch(release_id):
        raise ValueError('发布标识无效')
    releases = base / 'releases'
    current = releases / release_id
    if (releases.is_symlink() or staging.is_symlink() or desktop.is_symlink()
            or current.is_symlink() or (base / 'current').resolve(strict=True) != current):
        raise RuntimeError('服务器当前版本已变化或目录不符合部署约定，拒绝清理')
    manifest = read_record(current / 'release-manifest.json')
    result = read_record(staging / release_id / 'server-result.json')
    if (manifest.get('release_id') != release_id or manifest.get('revision') != revision
            or not release_matches(manifest, release_id) or result.get('status') != 'published'
            or result.get('release') != str(current) or result.get('revision') != revision):
        raise RuntimeError('当前版本尚未发布成功或发布记录不一致，拒绝清理')
    directories, files = [], []
    # 另一发布可能已完成准备，仍在等待本次运行版收尾；保留其断点继续能力。
    pending = set()
    for directory in staging.iterdir():
        if directory.is_symlink() or not directory.is_dir() or not RELEASE_ID.fullmatch(directory.name):
            continue
        record = read_record(directory / 'server-result.json')
        if record.get('previous_release') == str(current) and record.get('status') != 'published':
            pending.add(directory.name)
        if directory.name > release_id or directory.name in pending:
            continue
        has_valid_record = (record.get('release') == str(releases / directory.name)
                            and release_matches(record, directory.name))
        if has_valid_record:
            for name in UPLOADS:
                path = directory / name
                if path.is_file() and not path.is_symlink():
                    files.append(path)
            # 清理已不需要的临时辅助脚本和副本，仅保留 server-result.json 审计记录。
            for name in STAGING_HELPER_FILES:
                if directory.name == release_id and name == 'cleanup_releases.py':
                    continue
                path = directory / name
                if path.is_file() and not path.is_symlink():
                    files.append(path)
            for name in STAGING_HELPER_DIRS:
                path = directory / name
                if path.is_dir() and not path.is_symlink():
                    directories.append(path)
        elif directory.name < release_id and not (directory / 'server-result.json').exists():
            # 在写入 server-result.json 前即中断的旧暂存目录，只要不含未知/人工文件即可整体回收。
            entries = list(directory.iterdir())
            if all(not entry.is_symlink() and entry.name in KNOWN_STAGING_ENTRIES for entry in entries):
                directories.append(directory)
    kept_releases = []
    for directory in releases.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        if (not RELEASE_ID.fullmatch(directory.name)
                or directory.name >= release_id or directory.name in pending):
            kept_releases.append(directory)
            continue
        record = read_record(directory / 'release-manifest.json')
        if record.get('release_id') == directory.name and release_matches(record, directory.name):
            directories.append(directory)
        else:
            kept_releases.append(directory)
    venvs = base / 'venvs'
    if venvs.is_dir() and not venvs.is_symlink():
        active_venvs = set()
        for rel_dir in kept_releases:
            venv_path = rel_dir / '.venv'
            if venv_path.exists():
                try:
                    active_venvs.add(venv_path.resolve(strict=True))
                except OSError:
                    pass
        for entry in venvs.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            if (VENV_KEY.fullmatch(entry.name) or entry.name.startswith('.building-')) and entry.resolve() not in active_venvs:
                directories.append(entry)
    shared_assets = base / 'shared-assets'
    if shared_assets.is_dir() and not shared_assets.is_symlink():
        active_assets = set()
        for rel_dir in kept_releases:
            rel_manifest = read_record(rel_dir / 'release-manifest.json')
            for sha in (rel_manifest.get('shared_assets') or {}).values():
                if isinstance(sha, str) and ASSET_HASH.fullmatch(sha):
                    active_assets.add(sha)
            assets_dir = rel_dir / 'erp_web/assets'
            if assets_dir.is_dir():
                for asset_file in assets_dir.rglob('*'):
                    if asset_file.is_file() and not asset_file.is_symlink() and asset_file.stat().st_size >= 512 * 1024:
                        with asset_file.open('rb') as stream:
                            active_assets.add(hashlib.file_digest(stream, 'sha256').hexdigest())
        for entry in shared_assets.iterdir():
            if entry.is_symlink() or not entry.is_file():
                continue
            if ASSET_HASH.fullmatch(entry.name) and entry.name not in active_assets:
                files.append(entry)
    if desktop.exists():
        for target in desktop.iterdir():
            if (target.is_symlink() or not target.is_dir()
                    or not re.fullmatch(r'(?:darwin|win32|linux)-(?:arm64|x64)', target.name)):
                continue
            latest = read_record(target / 'latest.json')
            version = latest.get('version', '')
            if (not isinstance(version, str) or not VERSION.fullmatch(version)
                    or f"{latest.get('platform')}-{latest.get('arch')}" != target.name):
                raise RuntimeError(f'桌面更新清单无效，拒绝清理：{target.name}')
            bundle = target / version
            package = read_record(bundle / 'package.json')
            if bundle.is_symlink() or any(package.get(key) != latest.get(key) for key in
                                         ('version', 'platform', 'arch', 'installer', 'macZip')):
                raise RuntimeError(f'当前桌面安装包与清单不一致，拒绝清理：{target.name}')
            for key in ('installer', 'macZip'):
                asset = latest.get(key)
                if key == 'macZip' and asset is None:
                    continue
                if (not isinstance(asset, dict) or asset.get('file') not in
                        {'installer.dmg', 'installer.exe', 'installer.AppImage', 'update.zip'}):
                    raise RuntimeError('当前桌面安装包记录无效，拒绝清理')
                path = bundle / asset['file']
                if path.is_symlink() or not path.is_file() or path.stat().st_size != asset.get('size'):
                    raise RuntimeError('当前桌面安装包缺失或大小不一致，拒绝清理')
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != asset.get('sha256'):
                        raise RuntimeError('当前桌面安装包摘要不一致，拒绝清理')
            for directory in target.iterdir():
                if (directory.is_symlink() or not directory.is_dir() or not VERSION.fullmatch(directory.name)
                        or tuple(map(int, directory.name.split('.'))) >= tuple(map(int, version.split('.')))):
                    continue
                record = read_record(directory / 'package.json')
                if (record.get('version') == directory.name
                        and f"{record.get('platform')}-{record.get('arch')}" == target.name
                        and {p.name for p in directory.iterdir()} <=
                        {'package.json', 'installer.dmg', 'installer.exe', 'installer.AppImage', 'update.zip', 'native.json'}):
                    directories.append(directory)
    return directories, files


def cleanup(release_id, revision, *, dry_run=False):
    with ExitStack() as stack:
        for path in (BASE / '.release.lock', DESKTOP.parent / '.desktop-publish.lock'):
            lock = stack.enter_context(path.open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directories, files = cleanup_plan(BASE, STAGING, DESKTOP, release_id, revision)
        for path in files:
            if not dry_run:
                path.unlink()
        for path in directories:
            if not dry_run:
                shutil.rmtree(path)
        result = {'dry_run': dry_run, 'directories': [str(p) for p in directories],
                  'uploads': [str(p) for p in files]}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--dry-run', action='store_true', help='只输出清理计划')
    cleanup(**vars(parser.parse_args()))


if __name__ == '__main__':
    main()

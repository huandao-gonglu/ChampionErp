"""将已上传的桌面安装包发布到同源 HTTPS 更新目录，最后原子切换版本清单。"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


SUFFIXES = {'darwin': '.dmg', 'win32': '.exe', 'linux': '.AppImage'}


def version_parts(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,7})(?:\.(?:0|[1-9][0-9]{0,7})){2}', value):
        raise ValueError('版本号应为三段非负整数，例如 1.3.0')
    return tuple(map(int, value.split('.')))


def asset(source, path, expected_digest):
    if not source.is_file() or not 0 < source.stat().st_size < 4 * 1024 ** 3 or not re.fullmatch('[0-9a-f]{64}', expected_digest):
        raise ValueError('安装包或 SHA-256 摘要无效')
    with source.open('rb') as stream, path.open('xb') as output:
        shutil.copyfileobj(stream, output, length=1024 * 1024)
    with path.open('rb') as stored:
        digest = hashlib.file_digest(stored, 'sha256').hexdigest()
    if digest != expected_digest:
        raise ValueError('上传安装包的 SHA-256 校验失败')
    path.chmod(0o644)
    return {'file': path.name, 'sha256': digest, 'size': path.stat().st_size}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path.chmod(0o644)


def verify_public_asset(url, size):
    request = Request(url, headers={'Range': 'bytes=0-0', 'User-Agent': 'Champion-ERP-Release/1'})
    with urlopen(request, timeout=30) as response:
        if (response.status != 206 or response.geturl() != url
                or response.headers.get('Content-Range') != f'bytes 0-0/{size}'
                or len(response.read(2)) != 1):
            raise RuntimeError('公网安装包或分段下载未就绪，保留原版本策略')


def minimum_version(version, policy, minimum, previous):
    floor = minimum if minimum is not None else (previous['minimum'] if previous else '0.0.0')
    if policy == 'required':
        if minimum is not None and minimum != version:
            raise ValueError('强制发布的最低版本必须等于本次版本')
        floor = version
    if version_parts(floor) > version_parts(version):
        raise ValueError('最低可用版本不能高于可下载版本')
    return floor


def publish(root, *, platform, arch, version, installer, sha256, policy='optional',
            minimum=None, notes='', mac_zip=None, zip_sha256=None, public_url='https://hdgl.cc.cd', verify=None):
    if platform not in SUFFIXES or arch not in {'arm64', 'x64'} or policy not in {'optional', 'required'}:
        raise ValueError('发布目标或更新策略无效')
    parsed = urlsplit(public_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path.strip('/') or parsed.query or parsed.fragment:
        raise ValueError('更新服务需要 HTTPS 站点根地址')
    if installer is None or installer.suffix.lower() != SUFFIXES[platform].lower() or not isinstance(notes, str) or len(notes) > 2000:
        raise ValueError('安装包格式或更新说明无效')
    if bool(mac_zip) != bool(zip_sha256) or mac_zip and (platform != 'darwin' or mac_zip.suffix != '.zip'):
        raise ValueError('签名自动更新包须提供 macOS ZIP 及其摘要')
    latest = version_parts(version)
    directory = root / f'{platform}-{arch}'
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    directory.chmod(0o755)
    # 在公共目录之外串行变更清单，版本目录一经发布即固定。
    with (root.parent / '.desktop-publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = directory / 'latest.json'
        previous = json.loads(index.read_text()) if index.exists() else None
        if previous and latest < version_parts(previous['version']):
            raise ValueError('已有更新的桌面版本，拒绝旧发布覆盖')
        floor = minimum_version(version, policy, minimum, previous)
        with tempfile.TemporaryDirectory(dir=directory, prefix='.publishing-') as tmp:
            work = Path(tmp)
            bundle = work / version
            bundle.mkdir(mode=0o755)
            bundle.chmod(0o755)
            package = asset(installer, bundle / ('installer' + SUFFIXES[platform]), sha256)
            info = {'version': version, 'platform': platform, 'arch': arch, 'installer': package}
            if mac_zip:
                archive = asset(mac_zip, bundle / 'update.zip', zip_sha256)
                info['macZip'] = archive
                write_json(bundle / 'native.json', {
                    'url': f'{public_url.rstrip("/")}/desktop-updates/{platform}-{arch}/{version}/update.zip',
                    'name': version, 'notes': notes, 'sha256': archive['sha256'], 'size': archive['size'],
                })
            write_json(bundle / 'package.json', info)
            target = directory / version
            if target.exists():
                if json.loads((target / 'package.json').read_text()) != info:
                    raise ValueError('同一版本已有不同安装包，请提高桌面版本号')
            else:
                bundle.rename(target)
            if verify:
                base = f'{public_url.rstrip("/")}/desktop-updates/{platform}-{arch}/{version}'
                verify(f'{base}/{package["file"]}', package['size'])
                if mac_zip:
                    verify(f'{base}/update.zip', info['macZip']['size'])
                    verify(f'{base}/native.json', (target / 'native.json').stat().st_size)
            manifest = {**info, 'minimum': floor, 'notes': notes}
            write_json(work / 'latest.json', manifest)
            os.replace(work / 'latest.json', index)
            return manifest


def change_policy(root, platform, arch, version, policy, minimum=None):
    """只调整当前已发布包的最低版本，旧版本的管理命令不能覆盖新发布。"""
    if platform not in SUFFIXES or arch not in {'arm64', 'x64'} or policy not in {'optional', 'required'}:
        raise ValueError('发布目标或更新策略无效')
    directory = root / f'{platform}-{arch}'
    with (root.parent / '.desktop-publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = directory / 'latest.json'
        manifest = json.loads(index.read_text())
        if manifest['version'] != version:
            raise ValueError('当前发布版本已经变化，请重新核对')
        manifest['minimum'] = minimum_version(version, policy, minimum, manifest)
        with tempfile.TemporaryDirectory(dir=directory, prefix='.policy-') as tmp:
            replacement = Path(tmp) / 'latest.json'
            write_json(replacement, manifest)
            os.replace(replacement, index)
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/var/www/champion-erp/desktop-updates'))
    parser.add_argument('--platform', choices=SUFFIXES, required=True)
    parser.add_argument('--arch', choices=['arm64', 'x64'], required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--installer', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--policy-only', action='store_true', help='只调整已发布版本的最低版本策略')
    parser.add_argument('--policy', choices=['optional', 'required'], default='optional')
    parser.add_argument('--minimum', help='显式指定最低可用版本，可用于撤回强制要求')
    parser.add_argument('--notes', default='')
    parser.add_argument('--public-url', default='https://hdgl.cc.cd')
    parser.add_argument('--mac-zip', type=Path)
    parser.add_argument('--zip-sha256')
    args = vars(parser.parse_args())
    if args.pop('policy_only'):
        if any(args[key] for key in ('installer', 'sha256', 'mac_zip', 'zip_sha256', 'notes')):
            parser.error('单独调整策略时不能指定安装包或更新说明')
        result = change_policy(**{key: args[key] for key in ('root', 'platform', 'arch', 'version', 'policy', 'minimum')})
    else:
        if not args['installer'] or not args['sha256']:
            parser.error('发布需要提供 --installer 和 --sha256')
        result = publish(**args, verify=verify_public_asset)
    print(f"已发布 {result['platform']}-{result['arch']} {result['version']}，最低可用版本 {result['minimum']}")


if __name__ == '__main__':
    main()

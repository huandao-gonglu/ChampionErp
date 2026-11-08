"""按桌面发行目录内同一份最低版本策略校验新业务请求。"""
import json
from pathlib import Path
import re

from erp_web.schemas.api import DesktopInstallers

RELEASES = Path('/var/www/champion-erp/desktop-updates')
VERSION = r'(?:0|[1-9][0-9]{0,7})(?:\.(?:0|[1-9][0-9]{0,7})){2}'
CLIENT = re.compile(rf'((?:darwin|win32|linux)-(?:arm64|x64))/({VERSION})')


def minimum_update(client):
    if not client:
        return None
    match = CLIENT.fullmatch(client)
    if not match:
        raise ValueError('桌面版本标识无效')
    target, version = match.groups()
    try:
        policy = json.loads((RELEASES / target / 'latest.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except ValueError as error:
        raise RuntimeError('已发布的桌面版本策略无效') from error
    floor = policy['minimum']
    if not re.fullmatch(VERSION, floor) or f"{policy['platform']}-{policy['arch']}" != target:
        raise RuntimeError('已发布的桌面版本策略无效')
    if tuple(map(int, version.split('.'))) < tuple(map(int, floor.split('.'))):
        return floor
    return None


def available_installers() -> DesktopInstallers:
    items = []
    for platform, arch, label, suffix in (
        ('win32', 'x64', 'Windows 64 位', '.exe'),
        ('win32', 'arm64', 'Windows ARM', '.exe'),
        ('darwin', 'arm64', 'macOS Apple 芯片', '.dmg'),
        ('darwin', 'x64', 'macOS Intel 芯片', '.dmg'),
        ('linux', 'x64', 'Linux 64 位', '.AppImage'),
        ('linux', 'arm64', 'Linux ARM', '.AppImage'),
    ):
        target = f'{platform}-{arch}'
        try:
            manifest = json.loads((RELEASES / target / 'latest.json').read_text(encoding='utf-8'))
            version = manifest['version']
            filename = manifest['installer']['file']
            if (not re.fullmatch(VERSION, version) or filename != 'installer' + suffix
                    or manifest['platform'] != platform or manifest['arch'] != arch):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        items.append({'label': label, 'version': version, 'url': f'/desktop-updates/{target}/{version}/{filename}'})
    return {'ok': True, 'items': items}

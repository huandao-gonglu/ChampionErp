"""按桌面发行目录内同一份最低版本策略校验新业务请求。"""
import json
from pathlib import Path
import re

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

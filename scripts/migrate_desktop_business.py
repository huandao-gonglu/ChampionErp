"""离线迁移旧桌面业务数据：一致性备份、复制、路径转换和关联校验。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from erp_shared.desktop import LOCAL_MODEL_FIELDS, local_configuration_fingerprint


def _copy_tree(source, target):
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for entry in source.iterdir():
        if entry.is_dir():
            _copy_tree(entry, target / entry.name)
        elif entry.suffix in {'.sqlite3', '.sqlite', '.db'}:
            with sqlite3.connect(f'{entry.as_uri()}?mode=ro', uri=True) as original, sqlite3.connect(target / entry.name) as copied:
                original.backup(copied)
            (target / entry.name).chmod(0o600)
        elif not entry.name.endswith(('-wal', '-shm', '-journal')):
            shutil.copy2(entry, target / entry.name)
            (target / entry.name).chmod(0o600)


def _rewrite(value, source, target):
    if isinstance(value, list):
        return [_rewrite(item, source, target) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, source, target) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    if value.startswith('/file?'):
        path = parse_qs(urlsplit(value).query).get('path', [''])[0]
        updated = _rewrite(path, source, target)
        return '/file?path=' + quote(updated, safe='') if updated != path else value
    prefix = str(source) + os.sep
    if value == str(source) or value.startswith(prefix):
        return str(target) + value[len(str(source)):]
    if value.startswith('file://'):
        parsed = urlsplit(value)
        from urllib.parse import unquote
        path = unquote(parsed.path)
        if path.startswith(prefix):
            return Path(str(target) + path[len(str(source)):]).as_uri()
    return value


def _identities(connection):
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    result = {}
    for (table,) in tables:
        safe = '"' + table.replace('"', '""') + '"'
        columns = connection.execute(f'PRAGMA table_info({safe})').fetchall()
        keys = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
        if keys:
            select = ','.join('"' + key.replace('"', '""') + '"' for key in keys)
            values = sorted(map(repr, connection.execute(f'SELECT {select} FROM {safe}').fetchall()))
            result[table] = (len(values), hashlib.sha256('\n'.join(values).encode()).hexdigest())
        else:
            result[table] = (connection.execute(f'SELECT count(*) FROM {safe}').fetchone()[0], '')
    return result


def _rewrite_database(path, source, target):
    with sqlite3.connect(path) as connection:
        before = _identities(connection)
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            safe = '"' + table.replace('"', '""') + '"'
            columns = connection.execute(f'PRAGMA table_info({safe})').fetchall()
            keys = [row[1] for row in columns if row[5]]
            locators = ['"' + key.replace('"', '""') + '"' for key in keys] or ['rowid']
            for column in columns:
                if column[2].upper() != 'TEXT' or column[5]:
                    continue
                field = '"' + column[1].replace('"', '""') + '"'
                for row in connection.execute(f'SELECT {",".join(locators)},{field} FROM {safe} WHERE {field} IS NOT NULL').fetchall():
                    identity, raw = row[:-1], row[-1]
                    if not isinstance(raw, str):
                        continue
                    try:
                        value = json.loads(raw)
                    except (ValueError, TypeError):
                        updated = _rewrite(raw, source, target)
                    else:
                        value_updated = _rewrite(value, source, target)
                        updated = json.dumps(value_updated, ensure_ascii=False) if value_updated != value else raw
                    if updated != raw:
                        where = ' AND '.join(key + ' IS ?' for key in locators)
                        connection.execute(f'UPDATE {safe} SET {field}=? WHERE {where}', (updated, *identity))
        if before != _identities(connection):
            raise RuntimeError(f'{path.name} 的业务 ID 或记录数量发生变化，迁移已中止。')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError(f'{path.name} 完整性或关联关系校验未通过。')
        return {name: count for name, (count, _) in before.items()}


def migrate(source: Path, target: Path, backup: Path, local: Path, *, source_prefix: Path | None = None):
    source, target, backup, local = [path.expanduser().resolve() for path in (source, target, backup, local)]
    origin = source_prefix if source_prefix is not None else source
    if not origin.is_absolute():
        raise ValueError('原始数据目录前缀必须是绝对路径。')
    if not (source / 'erp.sqlite3').is_file():
        raise ValueError('来源目录缺少 erp.sqlite3。')
    if target.exists() or backup.exists():
        raise ValueError('目标目录与备份目录必须尚不存在，禁止覆盖已有业务数据。')
    if any(left.is_relative_to(right) or right.is_relative_to(left) for left, right in ((source, target), (source, backup), (target, backup), (local, target), (local, backup))):
        raise ValueError('来源、目标与备份目录不能相互包含。')
    backup.mkdir(parents=True, mode=0o700)
    for name in ('erp.sqlite3', 'config', 'data'):
        entry = source / name
        if not entry.exists():
            continue
        if entry.is_dir():
            _copy_tree(entry, backup / name)
        else:
            with sqlite3.connect(f'{entry.as_uri()}?mode=ro', uri=True) as original, sqlite3.connect(backup / name) as copied:
                original.backup(copied)
            (backup / name).chmod(0o600)
    # 备份完成后才生成业务目标；失败保留备份，目标明确标记为未完成。
    target.mkdir(parents=True, mode=0o700)
    marker = target / 'migration-incomplete'
    marker.write_text('迁移未完成，禁止启动业务后端。\n')
    _copy_tree(backup, target)
    # 本机配置在备份中保留，但不得随业务目录迁入服务器。
    (target / 'config' / 'desktop-connections.json').unlink(missing_ok=True)
    (target / 'config' / 'desktop-environment.json').unlink(missing_ok=True)
    from erp_web.services.ai_model_config import normalize_ai_models
    original_config = backup / 'config' / 'app_config.json'
    original_models = normalize_ai_models(json.loads(original_config.read_text()).get('ai_models', [])) if original_config.exists() else []
    original_local = {str(model.get('local_connection_id') or model['id']):
                      {key: model[key] for key in LOCAL_MODEL_FIELDS if key in model}
                      for model in original_models if model.get('connection_type') in {'browser', 'cli'}}
    for path in (target / 'config').rglob('*.json'):
        value = json.loads(path.read_text())
        path.write_text(json.dumps(_rewrite(value, origin, target), ensure_ascii=False, indent=2))
    summaries = {}
    for path in target.rglob('*'):
        if path.is_file() and path.suffix in {'.db', '.sqlite', '.sqlite3'}:
            summaries[str(path.relative_to(target))] = _rewrite_database(path, origin, target)
    # 通过原 owner 读写业务配置和密钥，分离本机字段。不会启动任何 worker。
    from erp_web.context import AppContext, AppPaths
    from erp_web.db import ErpDatabase
    from desktop_runtime.config import LocalConfig
    paths = AppPaths.from_app_dir(target)
    context = AppContext(paths, ErpDatabase(paths.db_path))
    try:
        config = context.config.load_app_config()
        local_models = {}
        for model in config.get('ai_models', []):
            if model.get('connection_type') not in {'browser', 'cli'}:
                continue
            reference = str(model.get('local_connection_id') or model['id'])
            local_fields = {key: model.pop(key) for key in LOCAL_MODEL_FIELDS if key in model}
            local_fields.update(original_local.get(reference, {}))
            if model.get('local_connection_id') and not local_fields:
                local_fields = LocalConfig(local).read().get(reference, {})
            local_models[reference] = local_fields
            model['local_connection_id'] = reference
            model['local_config_fingerprint'] = local_configuration_fingerprint(local_fields)
            # 本机配置的指纹表示法变更，旧探测记录不能冒充迁移后的测试结果。
            model.pop('capability_profiles', None)
        if local_models:
            local_owner = LocalConfig(local)
            if local_owner.path.exists():
                shutil.copy2(local_owner.path, backup / 'desktop-connections.before.json')
            local_owner.update(local_models)
        context.config.save_app_config(config)
    finally:
        context.close()
    report = {'source': str(source), 'source_prefix': str(origin), 'target': str(target), 'backup': str(backup),
              'local': str(local), 'databases': summaries,
              'completed_at': datetime.now(timezone.utc).isoformat()}
    (target / 'desktop-migration-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    marker.unlink()
    return report


def main():
    parser = argparse.ArgumentParser(description='请先停止来源桌面和目标后端；备份后迁移业务数据，不复制浏览器登录资料。')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--local', type=Path, required=True)
    parser.add_argument('--source-prefix', type=Path, help='跨机器迁移时，备份中原始绝对路径的目录前缀')
    args = parser.parse_args()
    os.umask(0o077)
    report = migrate(args.source, args.target, args.backup, args.local, source_prefix=args.source_prefix)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

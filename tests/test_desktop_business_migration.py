"""一次性迁移保留商品、草稿、历史与素材；失败不覆盖来源或已有目标。"""
import json
from pathlib import Path
import pytest
from erp_web.context import AppContext, AppPaths
from erp_web.db import ErpDatabase
from scripts.migrate_desktop_business import migrate
from desktop_runtime.config import LocalConfig


def test_migration_backs_up_and_preserves_business_ids_and_assets(tmp_path):
    source, target, backup = [tmp_path / name for name in ('source', 'server', 'backup')]
    paths = AppPaths.from_app_dir(source)
    app = AppContext(paths, ErpDatabase(paths.db_path))
    path = source / 'data' / 'images' / 'original.png'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'fixture-image')
    product = app.products.save_product({'name': '保留商品', 'source': {'title': '保留商品',
        'source_url': 'https://detail.1688.com/offer/123.html', 'image_pool': [
            {'id': 'original', 'path': str(path), 'origin': '1688', 'selected': True}]}})
    config = app.config.load_app_config()
    config['ai_models'] = [{'id': 'canvas', 'connection_type': 'browser', 'browser_provider': 'gemini',
                           'browser_profile': 'personal', 'browser_url': 'https://gemini.google.com/share/owned',
                           'browser_proxy': 'http://127.0.0.1:7890'}]
    app.config.save_app_config(config)
    original_config = paths.app_config_path.read_bytes()
    app.close()
    report = migrate(source, target, backup, source)
    assert (backup / 'erp.sqlite3').is_file()
    assert (backup / 'config' / 'app_config.json').read_bytes() == original_config
    assert paths.app_config_path.read_bytes() == original_config
    assert not (target / 'migration-incomplete').exists()
    assert (target / 'data' / 'images' / 'original.png').read_bytes() == b'fixture-image'
    migrated = AppContext(AppPaths.from_app_dir(target), ErpDatabase(target / 'erp.sqlite3'))
    try:
        saved = migrated.products.load_product_from_index(product['product_id'])
        assert saved['product_id'] == product['product_id']
        assert saved['source']['image_pool'][0]['id'] == 'original'
        assert str(source) not in saved['source']['image_pool'][0]['path']
        model = migrated.config.load_app_config()['ai_models'][0]
        assert model['local_connection_id'] == 'canvas'
        assert 'browser_proxy' not in model and 'browser_profile' not in model
        assert LocalConfig(source).read()['canvas']['browser_profile'] == 'personal'
    finally:
        migrated.close()
    assert 'erp.sqlite3' in report['databases']
    assert json.loads((target / 'desktop-migration-report.json').read_text())['backup'] == str(backup)
    with pytest.raises(ValueError, match='禁止覆盖'):
        migrate(source, target, tmp_path / 'another-backup', source)


def test_partial_migration_is_rejected_before_database_initialization(tmp_path, monkeypatch):
    from erp_web import context
    target = tmp_path / 'partial'
    target.mkdir()
    (target / 'migration-incomplete').write_text('迁移尚未完成')
    monkeypatch.setattr(context, '_DEFAULT_APP_DIR', target)
    with pytest.raises(RuntimeError, match='迁移尚未完成'):
        context.build_default_context()
    assert not (target / 'erp.sqlite3').exists()


def test_cross_machine_snapshot_rewrites_original_computer_paths(tmp_path):
    origin = Path('/original-computer/Champion ERP/backend')
    source, target, backup = [tmp_path / name for name in ('uploaded-snapshot', 'server-data', 'server-backup')]
    app = AppContext(AppPaths.from_app_dir(source), ErpDatabase(source / 'erp.sqlite3'))
    image = source / 'data/images/source.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'fixture-image')
    product = app.products.save_product({'name': '跨机器迁移', 'source': {'title': '跨机器迁移', 'image_pool': [
        {'id': 'image', 'path': str(origin / 'data/images/source.png'), 'origin': '1688'}]}})
    app.config.load_app_config()
    app.close()
    report = migrate(source, target, backup, tmp_path / 'local-settings', source_prefix=origin)
    migrated = AppContext(AppPaths.from_app_dir(target), ErpDatabase(target / 'erp.sqlite3'))
    try:
        saved = migrated.products.load_product_from_index(product['product_id'])
        assert saved['source']['image_pool'][0]['path'] == str(target / 'data/images/source.png')
        assert report['source_prefix'] == str(origin)
    finally:
        migrated.close()

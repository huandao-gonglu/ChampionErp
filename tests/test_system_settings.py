"""系统设置的持久化、单项更新和订单同步冷却。"""
import time
import pytest
from erp_web.context import get_context
from erp_web.stores.config_store import ConfigStore
from erp_web import app_config
from tests.test_order_notifications import store, service


def test_default_settings_and_persistence_preserve_other_configuration():
    context = get_context()
    config = context.config
    before = config.load_app_config()
    assert config.system_settings() == {'orders_auto_sync_interval_hours': 5}
    config.save_system_settings({'orders_auto_sync_interval_hours': 12})
    loaded = ConfigStore(context.paths, context.db).load_app_config()
    assert loaded == {**before, 'system_settings': {'orders_auto_sync_interval_hours': 12}}
    merged = config.merge_app_config_fields(loaded, {'system_settings': {'orders_auto_sync_interval_hours': 5}})
    config.save_app_config(merged)
    assert config.system_settings()['orders_auto_sync_interval_hours'] == 12


@pytest.mark.parametrize('hours', [0, 4, 5.5, True, '6'])
def test_invalid_interval_rejected_without_changing_saved_value(hours):
    config = get_context().config
    with pytest.raises(ValueError):
        config.save_system_settings({'orders_auto_sync_interval_hours': hours})
    assert config.system_settings()['orders_auto_sync_interval_hours'] == 5


def test_old_app_config_uses_default_interval():
    old = app_config.default_app_config()
    old.pop('system_settings')
    assert app_config.normalize_app_config(old)['system_settings']['orders_auto_sync_interval_hours'] == 5


def test_configured_interval_is_read_again_for_next_entry(store, monkeypatch):
    config = get_context().config
    config.save_system_settings({'orders_auto_sync_interval_hours': 12})
    svc = service(store, {})
    svc.auto_sync_interval_provider = lambda: config.system_settings()['orders_auto_sync_interval_hours'] * 3600
    now = time.time()
    monkeypatch.setattr('erp_web.services.order_notification_service.time.time', lambda: now)
    assert svc.sync('ozon', automatic=True)['operation_ids']
    job = store.claim({'ozon': '3'}, now=now)
    store.finish(job, now=now)
    now += 6 * 3600
    assert svc.sync('ozon', automatic=True)['operation_ids'] == []
    config.save_system_settings({'orders_auto_sync_interval_hours': 5})
    assert svc.sync('ozon', automatic=True)['operation_ids']

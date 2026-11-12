"""同步策略真实配置、执行接线与历史确认预算保持。"""
import json
import time

import pytest

from erp_web.context import get_context
from erp_web.schemas.sync_policies import POLICIES
from erp_web.schemas.task_queue import confirmation_policy
from erp_web.stores.config_store import ConfigStore
from tests.test_order_notifications import store, event
from tests.test_task_queue import queue


def test_catalog_covers_all_business_retry_families_and_uses_effective_values():
    config = get_context().config
    assert set(POLICIES) == {'confirmation_yandex_content', 'confirmation_yandex_price', 'confirmation_default',
                            'order_callback', 'yandex_submission', 'wechat_delivery', 'newapi_account'}
    catalog = config.sync_policy_catalog()
    assert len(catalog['items']) == 7
    assert all(item['values'] == config.sync_policy(item['key']) for item in catalog['items'])
    assert all(set(item['values']) == {field['key'] for field in item['fields']} for item in catalog['items'])
    assert confirmation_policy('publish', 'yandex', 'publish').max_requests == 120
    assert confirmation_policy('online', 'yandex', 'price', 20).max_requests == 40
    assert confirmation_policy('publish', 'ozon', 'publish', 4).max_requests == 160


def test_partial_updates_persist_and_do_not_reset_other_strategies_or_settings():
    app = get_context()
    config = app.config
    before = config.system_settings()
    config.save_system_settings({'sync_policies': {'confirmation_default': {'interval': 90}}})
    config.save_system_settings({'sync_policies': {'order_callback': {'max_attempts': 3}}})
    config.save_system_settings({'sync_policies': {'confirmation_default': {'max_checks': 4}}})
    config.save_system_settings({'orders_auto_sync_interval_hours': 12})
    reopened = ConfigStore(app.paths, app.db)
    assert reopened.sync_policy('confirmation_default')['interval'] == 90
    assert reopened.sync_policy('confirmation_default')['max_checks'] == 4
    assert reopened.sync_policy('order_callback')['max_attempts'] == 3
    assert reopened.system_settings()['watermark'] == before['watermark']
    assert reopened.system_settings()['orders_auto_sync_interval_hours'] == 12


@pytest.mark.parametrize('patch', [
    {'missing': {'interval': 60}}, {'confirmation_default': {'interval': 1}},
    {'confirmation_default': {'max_checks': True}}, {'confirmation_default': {'lifetime': 60}},
    {'confirmation_default': {'interval': '60'}}, {'confirmation_default': {'max_checks': 1.5}},
    {'order_callback': {'max_attempts': 0}}, {'order_callback': {'base_delay': 2000, 'max_delay': 100}},
    {'yandex_submission': {'interval': 3, 'max_delay': 2}}, {'wechat_delivery': {'retry_delay': float('nan')}},
])
def test_invalid_policy_is_rejected_atomically(patch):
    config = get_context().config
    before = config.system_settings()
    with pytest.raises(ValueError):
        config.save_system_settings({'sync_policies': patch, 'orders_auto_sync_interval_hours': 12})
    assert config.system_settings() == before


def test_shared_account_strategy_cannot_be_overridden_by_personal_space(tmp_path):
    from erp_web.context import AppContext, AppPaths
    root = get_context().config
    root.save_system_settings({'sync_policies': {'newapi_account': {'interval': 90}}})
    from erp_web.db import ErpDatabase
    paths = AppPaths.from_app_dir(tmp_path / 'user')
    paths.app_dir.mkdir(parents=True)
    user = AppContext(paths, ErpDatabase(paths.db_path))
    try:
        user.config.shared_config = root
        assert user.config.sync_policy('newapi_account')['interval'] == 90
        row = next(item for item in user.config.sync_policy_catalog()['items'] if item['key'] == 'newapi_account')
        assert not row['editable']
        with pytest.raises(ValueError, match='管理员'):
            user.config.save_system_settings({'sync_policies': {'newapi_account': {'interval': 5}}})
        assert root.sync_policy('newapi_account')['interval'] == 90
    finally:
        user.close()


def test_new_confirmation_uses_saved_strategy_but_existing_budget_is_frozen(queue):
    svc, _, _, task_id = queue
    config = get_context().config
    config.save_system_settings({'sync_policies': {'confirmation_default': {
        'first_delay': 15, 'interval': 90, 'max_checks': 4, 'lifetime': 600,
        'min_requests': 50, 'requests_per_target': 10, 'cooldown': 45}}})
    svc.detail(task_id)
    original = svc.store.get(task_id)
    policy = json.loads(original['policy'])
    assert policy['first_delay'] == 15 and policy['interval'] == 90 and policy['max_checks'] == 4
    assert policy['max_requests'] == 50 and policy['cooldown'] == 45
    config.save_system_settings({'sync_policies': {'confirmation_default': {'max_checks': 10, 'lifetime': 1200}}})
    svc.detail(task_id)
    assert svc.store.get(task_id) == original
    # 老持久化策略不含 cooldown 仍能读取。
    assert confirmation_policy('publish', 'ozon', 'publish').cooldown == 30
    with svc.store.connect() as conn:
        conn.execute('UPDATE confirmations SET last_checked_at=? WHERE id=?', (time.time()-31, task_id))
    assert svc.store.claim(task_id, manual=True)[1] == 'cooldown'


def test_order_callback_retry_uses_configured_attempts_and_delay(store):
    config = get_context().config
    config.save_system_settings({'sync_policies': {'order_callback': {'max_attempts': 2, 'base_delay': 7, 'max_delay': 100}}})
    store.enqueue(event())
    job = store.claim({'ozon': '3'}, now=time.time())
    now = time.time()
    store.finish(job, error='暂时失败', retryable=True, now=now, retry_policy=config.sync_policy('order_callback'))
    with store.connect() as conn:
        row = dict(conn.execute('SELECT * FROM inbox WHERE id=?', (job['id'],)).fetchone())
    assert row['status'] == 'retry' and row['next_attempt'] == now + 14
    job = store.claim({'ozon': '3'}, now=now+15)
    store.finish(job, error='仍然失败', retryable=True, now=now+16, retry_policy=config.sync_policy('order_callback'))
    with store.connect() as conn:
        assert conn.execute('SELECT status FROM inbox WHERE id=?', (job['id'],)).fetchone()[0] == 'failed'


def test_publishing_config_uses_saved_values_and_keeps_legacy_reads():
    app = get_context()
    app.config.update_store_config_fields('yandex', {'publish_step_interval_seconds': 4, 'publish_step_retry_limit': 5})
    assert app.config.sync_policy('yandex_submission')['interval'] == 4
    app.config.save_system_settings({'sync_policies': {'yandex_submission': {'interval': 6, 'max_retries': 3, 'max_delay': 60}}})
    config = app.config.publishing_config()
    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
    from erp_web.runtime_units.publish_yandex import _step_retry_limit
    assert YandexPublishingAdapter().submission_step_interval_seconds(config) == 6
    assert _step_retry_limit(config) == 3
    assert config['yandex']['publish_step_max_delay_seconds'] == 60


def test_settings_http_write_and_shared_query_use_the_same_values():
    from tests.test_watermark import post
    from erp_web.business_query_composition import BUSINESS_QUERIES
    config = get_context().config
    result, status = post('/api/system-settings', {'sync_policies': {'confirmation_yandex_content': {'max_checks': 12}}})
    assert status == 200 and result['sync_policies']['confirmation_yandex_content']['max_checks'] == 12
    query = next(row for row in BUSINESS_QUERIES.definitions if row.name == 'settings.sync_policies')
    assert query.path == '/api/business-queries/settings.sync_policies'
    rows = query.loader(get_context(), None)['items']
    assert next(row for row in rows if row['key'] == 'confirmation_yandex_content')['values']['max_checks'] == 12
    before = config.system_settings()
    assert post('/api/system-settings', {'sync_policies': {'unknown': {'interval': 5}}})[1] == 400
    assert config.system_settings() == before

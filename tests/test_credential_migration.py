"""已有授权无损迁移、重复执行、冲突和中断恢复。"""
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from erp_web.context import AppPaths
from erp_web.db import ErpDatabase
from erp_web.stores.config_store import ConfigStore
from erp_web.stores.credential_layout import assert_credentials_migrated
from erp_web.stores.order_notification_store import OrderNotificationStore
from erp_web.stores.fulfillment_store import FulfillmentStore
from erp_web.stores.wechat_channel_store import WechatChannelStore
from erp_web.services.crossborderbus_client import CrossborderBusClient
from scripts import migrate_credentials as migration


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / "instance"
    root.mkdir()
    db = ErpDatabase(root / "erp.sqlite3")
    config = ConfigStore(AppPaths.from_app_dir(root), db)
    db.replace_runtime_secrets("app_config", {'["alibaba_cookie"]': "原有应用密钥"})
    orders = OrderNotificationStore(root / "data/order-notifications.sqlite3")
    FulfillmentStore(orders)
    wechat = WechatChannelStore(root / "data/wechat-channel.sqlite3", config)
    bus = {"client_secret": "企业密钥", "app_uid": 42, "access_token": "现有令牌", "expires_token": "现有刷新令牌", "expires_at": 9999999999}
    state = {"generation": 7, "conversation_id": "保留原会话", "cursor": "原游标", "context_token": "原回复上下文",
             "history_position": 23, "accept_after_ms": 1234,
             "credentials": {"token": "微信令牌", "account_id": "原账号", "user_id": "原用户", "base_url": "https://example.test"}}
    with orders.connect() as conn:
        conn.execute("INSERT INTO bus_settings VALUES('credentials',?)", (json.dumps(bus),))
        conn.execute("INSERT INTO bus_settings VALUES('catalog',?)", (json.dumps({"identity": "原合作目录", "sections": []}),))
        conn.executemany("INSERT INTO settings VALUES(?,?)", [('token:yandex', '原回调令牌'), ('public_url', 'https://notify.example.test'), ('last_received:yandex', '原收件时间')])
        conn.execute("INSERT INTO orders VALUES('order','yandex','42','processing','{}',1,2)")
        conn.commit()
    with wechat._connect() as conn:
        conn.execute("UPDATE channel_state SET payload=?", (json.dumps(state),))
        conn.execute("INSERT INTO channel_inbox VALUES(7,'incoming','{}')")
        conn.execute("INSERT INTO channel_deliveries(generation,delivery_key,text,client_id) VALUES(7,'reply','原待发送消息','client')")
    return root, db, config, orders, state, bus


def test_migrate_preserves_authorizations_bindings_and_business_records(legacy):
    root, db, config, orders, original_state, bus = legacy
    with pytest.raises(RuntimeError, match="尚|旧业务库"):
        assert_credentials_migrated(root)
    result = migration.migrate(root)
    assert result['status'] == 'migrated'
    assert config.integration_credentials('crossborderbus') == bus
    assert config.order_notification_token('yandex') == '原回调令牌'
    assert config.order_notification_public_url() == 'https://notify.example.test'
    assert db.load_runtime_secrets('app_config') == {'["alibaba_cookie"]': '原有应用密钥'}
    restored = WechatChannelStore(root / 'data/wechat-channel.sqlite3', config)
    state = restored.state()
    assert {key: value for key, value in state.items() if key != 'credential_id'} == original_state
    assert len(restored.inbox(7)) == 1
    assert restored.pending(7)[0]['text'] == '原待发送消息'
    with restored._connect() as conn:
        assert 'credentials' not in json.loads(conn.execute('SELECT payload FROM channel_state').fetchone()[0])
    with orders.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1
        assert conn.execute("SELECT value FROM settings WHERE key='last_received:yandex'").fetchone()[0] == '原收件时间'
        assert not conn.execute("SELECT 1 FROM bus_settings WHERE key='credentials'").fetchone()
    assert_credentials_migrated(root)
    assert migration.migrate(root)['status'] == 'already_current'
    assert config.order_notification_token('yandex') == '原回调令牌'
    backup = Path(result['backup'])
    with sqlite3.connect(backup / 'data/order-notifications.sqlite3') as conn:
        assert json.loads(conn.execute("SELECT value FROM bus_settings WHERE key='credentials'").fetchone()[0]) == bus
    assert (backup / 'erp.sqlite3').stat().st_mode & 0o077 == 0


def test_conflict_preserves_both_credentials_without_overwrite(legacy):
    root, _, config, orders, _, bus = legacy
    other = {'client_secret': '另一个账号的密钥'}
    config.save_integration_credentials('crossborderbus', other)
    with pytest.raises(ValueError, match='冲突'):
        migration.migrate(root)
    assert config.integration_credentials('crossborderbus') == other
    with orders.connect() as conn:
        assert json.loads(conn.execute("SELECT value FROM bus_settings WHERE key='credentials'").fetchone()[0]) == bus


def test_interrupted_cleanup_can_resume_without_rotating_tokens(legacy, monkeypatch):
    root, _, config, _, _, bus = legacy
    cleanup = migration._remove_legacy
    monkeypatch.setattr(migration, '_remove_legacy', lambda *args: (_ for _ in ()).throw(RuntimeError('模拟中断')))
    with pytest.raises(RuntimeError, match='模拟中断'):
        migration.migrate(root)
    assert config.integration_credentials('crossborderbus') == bus
    with pytest.raises(RuntimeError):
        assert_credentials_migrated(root)
    monkeypatch.setattr(migration, '_remove_legacy', cleanup)
    migration.migrate(root)
    assert_credentials_migrated(root)
    assert config.order_notification_token('yandex') == '原回调令牌'


def test_crossborder_refresh_writes_only_to_main_database(legacy):
    root, _, config, orders, _, bus = legacy
    migration.migrate(root)
    config.save_integration_credentials('crossborderbus', {**bus, 'expires_at': 0})
    client = CrossborderBusClient(FulfillmentStore(orders), config)
    client._request = lambda *args: {'access_token': '新令牌', 'expires_token': '新刷新令牌', 'expires_in': 7200}
    assert client.token() == '新令牌'
    assert config.integration_credentials('crossborderbus')['expires_token'] == '新刷新令牌'
    with orders.connect() as conn:
        assert not conn.execute("SELECT 1 FROM bus_settings WHERE key='credentials'").fetchone()


def test_wechat_binding_commit_failure_preserves_old_reference(legacy, monkeypatch):
    root, _, config, _, state, _ = legacy
    migration.migrate(root)
    store = WechatChannelStore(root / 'data/wechat-channel.sqlite3', config)
    old_ref = store.state()['credential_id']
    monkeypatch.setattr(store, '_write', lambda *args: (_ for _ in ()).throw(RuntimeError('模拟渠道事务失败')))
    with pytest.raises(RuntimeError):
        store.configure(conversation_id='新会话', history_position=0, credentials={**state['credentials'], 'token': '新令牌'})
    assert store.state()['credential_id'] == old_ref
    assert store.state()['credentials'] == state['credentials']
    assert len(store.pending(7)) == 1


def test_callback_token_creation_is_atomic_across_config_instances(tmp_path):
    root = tmp_path / 'instances'
    root.mkdir()
    first = ConfigStore(AppPaths.from_app_dir(root), ErpDatabase(root / 'erp.sqlite3'))
    second = ConfigStore(AppPaths.from_app_dir(root), ErpDatabase(root / 'erp.sqlite3'))
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda i: (first if i % 2 else second).order_notification_token('yandex'), range(32)))
    assert len(set(tokens)) == 1 and tokens[0]


def test_wechat_unbind_removes_only_its_credential_record(legacy):
    root, _, config, _, _, bus = legacy
    migration.migrate(root)
    store = WechatChannelStore(root / 'data/wechat-channel.sqlite3', config)
    old_ref = store.state()['credential_id']
    store.configure(conversation_id='', history_position=0, unbind=True)
    assert store.state()['credentials'] is None
    assert store.state()['credential_id'] == ''
    assert not config.integration_credentials('wechat', old_ref)
    assert config.integration_credentials('crossborderbus') == bus
    with store._connect() as conn:
        assert 'credentials' not in json.loads(conn.execute('SELECT payload FROM channel_state').fetchone()[0])

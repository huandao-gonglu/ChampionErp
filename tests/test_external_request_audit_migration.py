"""真实遗留形状回归：请求审计迁出主库，完整保留业务与阻断记录。"""
from contextlib import closing
import sqlite3

import pytest

from erp_web.context import AppContext, AppPaths
from erp_web.db import ErpDatabase, _current_schema_signature, _schema_signature
from erp_web.schemas.external_requests import RequestContext
from erp_web.stores.external_request_store import ExternalRequestStore
from scripts.migrate_external_request_audit import SOURCE_AUDIT_SCHEMA_SQL, migrate


@pytest.fixture
def legacy_database(tmp_path):
    path = tmp_path / "erp.sqlite3"
    ErpDatabase(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SOURCE_AUDIT_SCHEMA_SQL)
        conn.execute("INSERT INTO store_auth(platform,credentials_json) VALUES(?,?)", ("ozon", '{"client_id":"保留账号","api_key":"隔离测试凭据"}'))
        conn.execute("""INSERT INTO external_attempts
            (id,operation_id,parent_id,attempt,platform,account_id,credential_id,interface,source,trigger,method,semantics,created,decision)
            VALUES('request-1','job-1','',1,'ozon','shop','fingerprint','/tree','category','manual','POST','read',1,'rejected')""")
        conn.execute("INSERT INTO external_blocks VALUES('ozon','shop','account','','{}',1,7)")
        conn.execute("INSERT INTO external_policies VALUES('ozon','*',2,60)")
        conn.execute("INSERT INTO external_recoveries VALUES('recovery-1','ozon','shop','interface','/tree',1,'权限已开通')")
        conn.commit()
    return path


def test_migration_preserves_business_audit_blocks_and_private_backup(legacy_database):
    with pytest.raises(RuntimeError, match="多余对象.*external_attempts"):
        ErpDatabase(legacy_database)
    backup = migrate(legacy_database)
    assert backup.stat().st_mode & 0o777 == 0o600
    with closing(sqlite3.connect(backup)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_attempts").fetchone()[0] == 1
    ErpDatabase(legacy_database)
    with closing(sqlite3.connect(legacy_database)) as conn:
        assert _schema_signature(conn) == _current_schema_signature()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
        assert conn.execute("SELECT credentials_json FROM store_auth WHERE platform='ozon'").fetchone()[0] == '{"client_id":"保留账号","api_key":"隔离测试凭据"}'
    target = legacy_database.parent / "data/external-requests.sqlite3"
    assert target.stat().st_mode & 0o777 == 0o600
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute("SELECT quota_key,operation_id FROM external_attempts").fetchone() == ("", "job-1")
        assert conn.execute("SELECT blocked_count FROM external_blocks").fetchone()[0] == 7
        assert conn.execute("SELECT concurrency,requests_per_minute,consecutive_failure_limit FROM external_policies").fetchone() == (2, 60, None)
        assert conn.execute("SELECT reason FROM external_recoveries").fetchone()[0] == "权限已开通"


def test_unknown_schema_is_rejected_before_backups_or_destination(legacy_database):
    with closing(sqlite3.connect(legacy_database)) as conn:
        conn.execute("CREATE TABLE unknown_table(id TEXT)")
        conn.commit()
    with pytest.raises(ValueError, match="其他结构差异"):
        migrate(legacy_database)
    assert not (legacy_database.parent / "data").exists()
    with closing(sqlite3.connect(legacy_database)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_attempts").fetchone()[0] == 1


@pytest.mark.parametrize("conflict", [False, True])
def test_existing_destination_is_resumable_but_conflicting_rows_are_not_overwritten(legacy_database, conflict):
    target = legacy_database.parent / "data/external-requests.sqlite3"
    ExternalRequestStore(target)
    count = 99 if conflict else 7
    with closing(sqlite3.connect(target)) as conn:
        conn.execute("INSERT INTO external_blocks VALUES('ozon','shop','account','','{}',1,?)", (count,))
        conn.commit()
    if conflict:
        with pytest.raises(ValueError, match="冲突记录"):
            migrate(legacy_database)
        with closing(sqlite3.connect(legacy_database)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM external_attempts").fetchone()[0] == 1
        with closing(sqlite3.connect(target)) as conn:
            assert conn.execute("SELECT blocked_count FROM external_blocks").fetchone()[0] == 99
            assert conn.execute("SELECT COUNT(*) FROM external_attempts").fetchone()[0] == 0
    else:
        migrate(legacy_database)
        ErpDatabase(legacy_database)
        with closing(sqlite3.connect(target)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM external_blocks").fetchone()[0] == 1


def test_request_manager_never_changes_main_database_schema(tmp_path):
    paths = AppPaths.from_app_dir(tmp_path)
    context = AppContext(paths=paths, db=ErpDatabase(paths.db_path))
    try:
        context.external_requests.start(RequestContext(platform="ozon", account_id="shop", interface="/tree", source="test"), "POST")
    finally:
        context.close()
    ErpDatabase(paths.db_path)
    with closing(sqlite3.connect(paths.db_path)) as conn:
        assert _schema_signature(conn) == _current_schema_signature()
    assert (paths.data_dir / "external-requests.sqlite3").is_file()

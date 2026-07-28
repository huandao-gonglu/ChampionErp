from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from erp_web.context import AppPaths, get_context
from erp_web.db import ErpDatabase, REQUIRED_TABLES, new_draft_id
from erp_web.product_model import normalize_product_model
from erp_web.facades import product_facade
from erp_web.runtime_units import (
    mercadolibre_orders,
    pricing_runtime,
    publish_bus,
    publish_helpers,
)
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.stores.config_store import ConfigStore
from tests.runtime_test_utils import temp_app_context

from .helpers import ROOT, load_json, read, sensitive_paths


def _database_tables(database: ErpDatabase) -> set[str]:
    with closing(sqlite3.connect(database.db_path)) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def _minimal_product() -> dict:
    return {
        "name": "Acceptance product",
        "source": {
            "title": "Acceptance product",
            "source_url": "https://example.invalid/acceptance-product",
            "source_platform": "1688",
            "price": "10",
            "currency": "CNY",
            "image_pool": [],
        },
        "drafts": {
            "mercadolibre": {
                "enabled": True,
                "title": "Acceptance product",
                "description": "Acceptance product description",
                "category_id": "MLM-TEST",
                "attributes": {"BRAND": "Generic"},
                "price": "20",
                "status": "copy_ready",
            }
        },
    }


# 验收：所有报告要求持久化的状态都必须拥有正式 SQLite 表。
def test_required_persistence_tables_exist(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    expected = {
        "store_auth",
        "publish_logs",
        "upc_pool",
        "order_notifications",
        "publish_jobs",
        "research_runs",
        "research_candidates",
        "ai_sessions",
        "exchange_rates",
    }
    assert expected.issubset(_database_tables(database))
    assert expected.issubset(set(REQUIRED_TABLES))


# 验收：UPC 领取必须在事务中原子占号，并发请求不能得到重复 UPC。
def test_upc_assignment_is_atomic_under_concurrency(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    values = [f"000000000{i:03d}" for i in range(20)]
    database.import_upcs(values)
    with ThreadPoolExecutor(max_workers=10) as executor:
        assigned = list(executor.map(database.assign_upc, [f"product-{i}" for i in range(20)]))
    assert len(assigned) == 20
    assert len(set(assigned)) == 20
    assert set(assigned) == set(values)


def test_upc_assignment_public_flow_is_database_backed_and_survives_restart(tmp_path) -> None:
    with temp_app_context(tmp_path) as context:
        saved = context.products.save_product(_minimal_product())
        imported, status = product_facade.import_upcs_payload(
            {"values": ["012345678905"]}
        )
        assert status == 200
        assert imported["upcPool"] == {"total": 1, "free": 1, "used": 0}

        assigned = publish_helpers.assign_upc()
        assert assigned["ok"] is True
        assert assigned["upc"] == "012345678905"
        assert assigned["product"]["product_id"] == saved["product_id"]
        assert assigned["product"]["upc"] == "012345678905"
        assert assigned["product"]["drafts"]["mercadolibre"]["upc"] == "012345678905"
        assert assigned["upcPool"] == {"total": 1, "free": 0, "used": 1}
        assert not (tmp_path / "upc_pool.json").exists()

    with temp_app_context(tmp_path) as restarted:
        loaded = restarted.products.load_product()
        assert loaded["upc"] == "012345678905"
        assert restarted.db.upc_pool_stats() == {"total": 1, "free": 0, "used": 1}


# 验收：Mercado Libre webhook 通知必须追加写入 order_notifications 表而不是截断 JSON。
def test_order_notifications_are_append_only_database_rows(tmp_path) -> None:
    with temp_app_context(tmp_path) as context:
        payloads = [
            {
                "topic": "acceptance_test",
                "resource": f"/orders/{index}",
                "user_id": "acceptance-user",
            }
            for index in range(205)
        ]
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(
                executor.map(
                    mercadolibre_orders.record_mercadolibre_order_notification,
                    payloads,
                )
            )
        assert all(result["ok"] is True for result in results)
        rows = context.db.list_order_notifications(limit=300)
        public_rows = mercadolibre_orders.load_mercadolibre_order_notifications(limit=300)
        assert len(rows) == 205
        assert len(public_rows) == 200
        assert {row["resource"] for row in rows} == {
            f"/orders/{index}" for index in range(205)
        }
        assert not list(tmp_path.rglob("mercadolibre_order_notifications.json"))

    with temp_app_context(tmp_path) as restarted:
        assert len(restarted.db.list_order_notifications(limit=300)) == 205


# 验收：旧 store_config 凭据必须自动迁入 store_auth 并从 JSON 文件物理清除。
def test_legacy_store_credentials_are_migrated_and_scrubbed(tmp_path) -> None:
    paths = AppPaths.from_app_dir(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.store_config_path.write_text(
        json.dumps(
            {
                "mercadolibre": {
                    "site_id": "MLM",
                    "access_token": "plain-access-token",
                    "refresh_token": "plain-refresh-token",
                    "app_secret": "plain-app-secret",
                    "code_verifier": "plain-code-verifier",
                }
            }
        ),
        encoding="utf-8",
    )
    database = ErpDatabase(paths.db_path)
    store = ConfigStore(paths, database)
    loaded = store.load_store_config()
    persisted_file = load_json(paths.store_config_path)
    persisted_auth = database.get_store_auth("mercadolibre")
    assert loaded["mercadolibre"]["access_token"] == "plain-access-token"
    assert loaded["mercadolibre"]["site_id"] == "MLM"
    assert persisted_auth["credentials"]["access_token"] == "plain-access-token"
    assert persisted_auth["credentials"]["refresh_token"] == "plain-refresh-token"
    assert persisted_auth["credentials"]["app_secret"] == "plain-app-secret"
    assert persisted_auth["credentials"]["code_verifier"] == "plain-code-verifier"
    assert not sensitive_paths(persisted_file)
    reopened = ConfigStore(paths, ErpDatabase(paths.db_path)).load_store_config()
    assert reopened["mercadolibre"]["access_token"] == "plain-access-token"
    assert reopened["mercadolibre"]["refresh_token"] == "plain-refresh-token"
    assert reopened["mercadolibre"]["app_secret"] == "plain-app-secret"
    assert reopened["mercadolibre"]["code_verifier"] == "plain-code-verifier"


# 验收：发布日志必须可按数据库倒序读取且不再创建全局 publish_logs.json。
def test_publish_logs_use_sqlite_as_single_index(tmp_path) -> None:
    with temp_app_context(tmp_path) as context:
        for index in range(205):
            publish_bus.append_publish_log(
                {
                    "platform": "mercadolibre",
                    "product_id": f"p{index}",
                    "status": "success" if index % 2 == 0 else "failed",
                    "time": f"{index:03d}",
                }
            )
        rows = publish_bus.load_publish_logs(limit=300)
        assert len(rows) == 205
        assert rows[0]["product_id"] == "p204"
        assert rows[-1]["product_id"] == "p0"
        with context.db._connect() as connection:
            successful = connection.execute(
                """
                SELECT COUNT(*) FROM publish_logs
                WHERE platform = ? AND status = ?
                """,
                ("mercadolibre", "success"),
            ).fetchone()[0]
        assert successful == 103
        assert not list(tmp_path.rglob("publish_logs.json"))

    with temp_app_context(tmp_path):
        assert len(publish_bus.load_publish_logs(limit=300)) == 205


# 验收：发布任务必须写入 publish_jobs 表，持久化 payload 中不得包含店铺配置或凭据。
def test_publish_jobs_never_persist_credentials(tmp_path) -> None:
    access_token = "plain-access-token"
    app_secret = "plain-app-secret"

    class SuccessfulAdapter:
        @staticmethod
        def resolve_category(product: dict, config: dict) -> dict:
            return product

        @staticmethod
        def required_attributes_missing(product: dict, config: dict) -> list[str]:
            return []

        @staticmethod
        def publish(product: dict, platform: str, config: dict) -> dict:
            assert config["mercadolibre"]["access_token"] == access_token
            return {"ok": True, "status": "published"}

    database = ErpDatabase(tmp_path / "erp.sqlite3")
    bus = PublishingBus(
        database,
        {"mercadolibre": SuccessfulAdapter()},
        config_provider=lambda: {
            "mercadolibre": {
                "access_token": access_token,
                "app_secret": app_secret,
            }
        },
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            {"product_id": "p1", "name": "Credential-safe job"},
            ["mercadolibre"],
        )
        bus.wait(queued["job_id"], timeout=2)
        persisted = database.load_publish_job(queued["job_id"])
        with database._connect() as connection:
            raw_payload = connection.execute(
                "SELECT payload_json FROM publish_jobs WHERE job_id = ?",
                (queued["job_id"],),
            ).fetchone()[0]
    finally:
        bus.executor.shutdown(wait=True)
    assert persisted["status"] == "completed"
    assert "config" not in persisted
    assert not sensitive_paths(persisted)
    assert access_token not in raw_payload
    assert app_secret not in raw_payload


# 验收：选品运行和候选商品必须写穿 research_runs/research_candidates 并可在重启后恢复。
def test_product_research_results_survive_registry_restart(tmp_path) -> None:
    with temp_app_context(tmp_path) as context:
        context.research.store(
            {
                "run_id": "run-1",
                "status": "completed",
                "search_mode": "ai_web_search",
                "created_at": "2026-07-28T00:00:00Z",
                "completed_at": "2026-07-28T00:01:00Z",
                "description": "completed",
                "items": [{"id": "candidate-1", "title": "Storage Box"}],
            }
        )
        with context.db._connect() as connection:
            candidate_count = connection.execute(
                "SELECT COUNT(*) FROM research_candidates WHERE run_id = ?",
                ("run-1",),
            ).fetchone()[0]
        assert candidate_count == 1

    with temp_app_context(tmp_path) as restarted:
        restored = restarted.research.get("run-1")
        assert restored is not None
        assert restored["status"] == "completed"
        assert restored["items"] == [{"id": "candidate-1", "title": "Storage Box"}]


# 验收：AI 对话列表和定位必须查询 ai_sessions 表，事件正文才允许保留 JSONL。
def test_ai_work_metadata_is_database_indexed(tmp_path) -> None:
    with temp_app_context(tmp_path) as context:
        conversation = context.ai_journal.start_conversation(
            use_case_id="acceptance.ai-work",
            capability="chat",
            provider_id="fake",
            model={"id": "fake-model"},
        )
        conversation.emit_text_delta("persisted text")
        conversation.finish({"ok": True})
        conversation_id = conversation.conversation_id
        assert conversation_id not in context.ai_journal._conditions
        session = context.db.get_ai_session(conversation_id)
        assert session is not None
        assert session["status"] == "completed"

    with temp_app_context(tmp_path) as restarted:
        listed = restarted.ai_journal.list_conversations(limit=10)
        events = restarted.ai_journal.read_events(conversation_id)
        assert listed[0]["conversation_id"] == conversation_id
        assert listed[0]["status"] == "completed"
        assert any(event.get("delta") == "persisted text" for event in events)


# 验收：AI Key、1688 cookie/token 等运行态秘密不得再明文写入 app_config.json。
def test_app_config_never_persists_runtime_secrets(tmp_path) -> None:
    secrets = {
        "cookie": "plain-cookie",
        "access": "plain-1688-token",
        "app_secret": "plain-1688-secret",
        "ai_key": "plain-ai-key",
    }
    with temp_app_context(tmp_path):
        store = get_context().config
        config = store.default_app_config()
        config["alibaba_cookie"] = secrets["cookie"]
        config["1688_api"] = {
            **config.get("1688_api", {}),
            "access_token": secrets["access"],
            "app_secret": secrets["app_secret"],
        }
        config["ai_models"] = [
            {"id": "model-1", "provider": "OpenAI-Compatible", "api_key": secrets["ai_key"]}
        ]
        store.save_app_config(config)

    database_names = {"erp.sqlite3", "erp.sqlite3-wal", "erp.sqlite3-shm"}
    for path in tmp_path.rglob("*"):
        if (
            not path.is_file()
            or path.name in database_names
            or path.suffix in {".sqlite", ".db"}
        ):
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for secret in secrets.values():
            assert secret not in content, f"{path} 仍明文保存运行态秘密"

    with temp_app_context(tmp_path):
        restored = get_context().config.load_app_config()
        assert restored["alibaba_cookie"] == secrets["cookie"]
        assert restored["1688_api"]["access_token"] == secrets["access"]
        assert restored["1688_api"]["app_secret"] == secrets["app_secret"]
        assert restored["ai_models"][0]["api_key"] == secrets["ai_key"]


# 验收：实时汇率必须写入 exchange_rates 表并能在服务上下文重建后读取。
def test_exchange_rates_survive_service_restart(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"rates":{"CNY":7.2,"MXN":18.5,"RUB":91.2}}'

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return Response()

    monkeypatch.setattr(pricing_runtime.urllib.request, "urlopen", fake_urlopen)
    config = {
        "api_url": "https://rates.example.invalid/latest",
        "timeout_seconds": 2,
        "cache_ttl_seconds": 3600,
    }
    with temp_app_context(tmp_path):
        first = get_context().exchange_rates.get_rates(config)
        assert first["ok"] is True
        assert first["cached"] is False

    with temp_app_context(tmp_path):
        second = get_context().exchange_rates.get_rates(config)
        assert second["ok"] is True
        assert second["cached"] is True
        assert second["source"] == "exchange_rates_table"
        assert second["rates"]["mxn_usd_rate"] == 18.5
    assert calls == ["https://rates.example.invalid/latest"]


# 验收：发布日志只能存在于表和大报文 artifact，商品/草稿 JSON 不得再内嵌 publish_logs。
def test_publish_logs_are_not_embedded_in_product_or_draft_json(tmp_path) -> None:
    normalized = normalize_product_model(
        {
            "name": "Legacy logs",
            "publish_logs": [{"status": "success"}],
            "source": {"title": "Legacy logs", "image_pool": []},
            "drafts": {
                "mercadolibre": {
                    "title": "Legacy logs",
                    "publish_logs": [{"status": "failed"}],
                }
            },
        }
    )

    def all_keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from all_keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from all_keys(item)

    assert "publish_logs" not in set(all_keys(normalized))
    with temp_app_context(tmp_path) as context:
        saved = context.products.save_product(normalized)
        loaded = context.products.load_product_from_index(
            saved["product_id"],
            "",
        )
    assert "publish_logs" not in set(all_keys(loaded))


# 验收：草稿必须只有一个事实来源，platform_drafts 不得同时保存完整 draft_json 和重复拆解列。
def test_platform_drafts_have_one_source_of_truth(tmp_path) -> None:
    with temp_app_context(tmp_path) as context:
        saved = context.products.save_product(_minimal_product())
        database = context.db
    with closing(sqlite3.connect(database.db_path)) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(platform_drafts)")
        }
        product_json = json.loads(
            connection.execute("SELECT product_json FROM products LIMIT 1").fetchone()[0]
        )
        draft_count = connection.execute(
            "SELECT COUNT(*) FROM platform_drafts"
        ).fetchone()[0]
    duplicated_columns = {"title", "description", "category_id", "attributes_json", "price_json"}
    assert draft_count > 0
    assert "drafts" not in product_json, "products.product_json 不得继续复制 platform_drafts"
    assert not ("draft_json" in columns and duplicated_columns & columns)
    with temp_app_context(tmp_path) as restarted:
        restored = restarted.products.load_product_from_index(
            saved["product_id"],
            "",
        )
    restored_draft = restored["drafts"]["mercadolibre"]
    assert restored_draft["title"] == "Acceptance product"
    assert restored_draft["category_id"] == "MLM-TEST"
    assert restored_draft["attributes"] == {"BRAND": "Generic"}


# 验收：无语义 draft_id 上线后必须彻底删除 draft_id_aliases 表及所有别名解析代码。
def test_draft_id_aliases_are_removed(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    assert "draft_id_aliases" not in _database_tables(database)
    backend = "\n".join(read(path) for path in ("erp_web/db.py", "erp_web/stores/product_store.py"))
    assert "resolve_draft_id_alias" not in backend
    generated = {new_draft_id() for _ in range(100)}
    assert len(generated) == 100
    assert all(
        len(draft_id) == 13
        and draft_id.startswith("d")
        and all(char in "0123456789abcdef" for char in draft_id[1:])
        for draft_id in generated
    )
    assert all(
        platform not in draft_id
        for platform in ("mercadolibre", "ozon", "yandex")
        for draft_id in generated
    )


# 验收：死 category_cache 表和 data/category_cache 文件轨必须全部删除。
def test_dead_category_cache_storage_is_removed(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    assert "category_cache" not in _database_tables(database)
    assert not (ROOT / "data/category_cache").exists()
    assert "CATEGORY_CACHE_PATH" not in read("erp_web/runtime_units/category_store.py")


# 验收：仓库只能存在根目录 erp.sqlite3，不得保留 data/erp.sqlite 双库歧义。
def test_only_one_database_location_exists() -> None:
    assert not (ROOT / "data/erp.sqlite").exists()
    assert "DEFAULT_DB_NAME = \"erp.sqlite3\"" in read("erp_web/db.py")

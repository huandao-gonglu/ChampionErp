from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields

import pytest

from erp_web import runtime as runtime_compat
from erp_web.context import AppContext, AppPaths
from erp_web.db import ErpDatabase, REQUIRED_TABLES, SCHEMA_VERSION
from erp_web.product_model import normalize_product_model
from erp_web.runtime_units import ozon_category_api, publish_bus
from erp_web.runtime_units.ozon_category_api import TtlCache
from erp_web.services.ai_gateway_providers import AiProviderClient
from erp_web.services.product_research_service import ProductResearchRunRegistry
from erp_web.stores.config_store import ConfigStore
from erp_web.stores.product_store import ProductStore
from tests.runtime_test_utils import temp_app_context

from .helpers import (
    ROOT,
    forbidden_calls,
    format_findings,
    function_definitions,
    parse_python,
    python_files,
)


# 验收：runtime.py 必须彻底移除全量命名空间快照注入并只保留无状态惰性转发。
def test_runtime_snapshot_injection_is_removed() -> None:
    forbidden_definitions = {"_sync_runtime_units", "_install_runtime_units"}
    definitions = {
        definition.qualname
        for path in python_files("erp_web")
        for definition in function_definitions(path)
    }
    mutation_calls = forbidden_calls(
        python_files("erp_web"),
        {"module.__dict__.update", "__dict__.update"},
    )
    assert not forbidden_definitions & definitions
    assert not mutation_calls, format_findings(mutation_calls)
    assert runtime_compat.normalize_product_model is normalize_product_model


# 验收：发布总线的生命周期必须归 AppContext 所有，不得恢复模块级 BUS 单例容器。
def test_publishing_bus_is_owned_by_app_context(tmp_path) -> None:
    first = AppContext(
        AppPaths.from_app_dir(tmp_path / "first"),
        ErpDatabase(tmp_path / "first/erp.sqlite3"),
    )
    second = AppContext(
        AppPaths.from_app_dir(tmp_path / "second"),
        ErpDatabase(tmp_path / "second/erp.sqlite3"),
    )
    first_bus = first.publishing_bus
    second_bus = second.publishing_bus
    try:
        assert first.publishing_bus is first_bus
        assert second.publishing_bus is second_bus
        assert first_bus is not second_bus
        assert first_bus.store is first.db
        assert second_bus.store is second.db
    finally:
        first_bus.executor.shutdown(wait=True)
        second_bus.executor.shutdown(wait=True)


# 验收：ErpDatabase 构造时必须集中初始化 schema、WAL、busy timeout 和外键约束。
def test_erp_database_owns_schema_and_connection_policy(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    with database._connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert set(REQUIRED_TABLES).issubset(tables)
    assert user_version == SCHEMA_VERSION
    assert str(journal_mode).lower() == "wal"
    assert foreign_keys == 1
    assert busy_timeout >= 5_000


# 验收：选品运行状态必须由 ProductResearchRunRegistry 持有，不得恢复模块级 RUNS 全局表。
def test_product_research_runs_are_registry_owned(tmp_path) -> None:
    first = ProductResearchRunRegistry(ErpDatabase(tmp_path / "first.sqlite3"))
    second = ProductResearchRunRegistry(ErpDatabase(tmp_path / "second.sqlite3"))
    first.store(
        {
            "run_id": "same-id",
            "status": "completed",
            "description": "first-context",
            "items": [],
        }
    )
    second.store(
        {
            "run_id": "same-id",
            "status": "completed",
            "description": "second-context",
            "items": [],
        }
    )
    first_run = first.get("same-id")
    second_run = second.get("same-id")
    assert first_run is not None
    assert second_run is not None
    assert first_run["description"] == "first-context"
    assert second_run["description"] == "second-context"


# 验收：路径和端口必须由不可变 AppPaths 统一派生，不能继续散落成可重绑定全局配置。
def test_app_paths_is_a_frozen_environment_value(tmp_path) -> None:
    paths = AppPaths.from_app_dir(tmp_path)
    assert paths.app_dir == tmp_path
    assert paths.db_path == tmp_path / "erp.sqlite3"
    assert paths.front_dist_index_path == tmp_path / "erp_web/static/dist/index.html"
    with pytest.raises(FrozenInstanceError):
        paths.app_dir = tmp_path / "mutated"  # type: ignore[misc]


# 验收：category_store 不得再维护按 app_dir 分桶的 SQLite 初始化状态。
def test_category_store_has_no_sqlite_init_workaround() -> None:
    tree = parse_python("erp_web/runtime_units/category_store.py")
    identifiers = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    calls = {
        getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "_SQLITE_INIT_STATE" not in identifiers
    assert "initialize_database" not in calls


# 验收：汇率缓存必须封装在 ExchangeRateService 中并由 AppContext 持有。
def test_exchange_rate_cache_is_service_owned(tmp_path) -> None:
    first = AppContext(
        AppPaths.from_app_dir(tmp_path / "first"),
        ErpDatabase(tmp_path / "first/erp.sqlite3"),
    )
    second = AppContext(
        AppPaths.from_app_dir(tmp_path / "second"),
        ErpDatabase(tmp_path / "second/erp.sqlite3"),
    )
    assert first.exchange_rates is first.exchange_rates
    assert first.exchange_rates is not second.exchange_rates
    assert first.exchange_rates._db is first.db
    assert second.exchange_rates._db is second.db


# 验收：AI 对话锁和元数据必须由 AiWorkJournal 管理，并在终态释放会话 Condition。
def test_ai_work_conditions_are_journal_owned_and_released(tmp_path) -> None:
    with temp_app_context(tmp_path) as context:
        journal = context.ai_journal
        conversation = journal.start_conversation(
            use_case_id="acceptance.condition-release",
            capability="chat",
            provider_id="fake",
            model={"id": "fake-model"},
        )
        conversation_id = conversation.conversation_id
        assert conversation_id in journal._conditions
        conversation.finish({"ok": True})
        assert conversation_id not in journal._conditions

        failed_conversation = journal.start_conversation(
            use_case_id="acceptance.condition-failure-release",
            capability="chat",
            provider_id="fake",
            model={"id": "fake-model"},
        )
        failed_conversation_id = failed_conversation.conversation_id
        assert failed_conversation_id in journal._conditions
        failed_conversation.fail(RuntimeError("acceptance failure"))
        assert failed_conversation_id not in journal._conditions


# 验收：AI 用例参数必须先收敛成 AiProviderClient，稳定门面不得重新膨胀。
def test_ai_provider_client_replaces_parameter_tunneling() -> None:
    assert {
        "app_dir",
        "use_case_id",
        "model",
        "required_capabilities",
        "timeout_seconds",
    }.issubset({field.name for field in fields(AiProviderClient)})
    assert callable(AiProviderClient.for_use_case)
    facade = ROOT / "erp_web/services/ai_gateway.py"
    assert len(facade.read_text(encoding="utf-8").splitlines()) < 800


# 验收：产品与配置读写必须由 ProductStore/ConfigStore 拥有，runtime product_store 只能薄委托。
def test_product_and_config_stores_own_io(tmp_path) -> None:
    context = AppContext(
        AppPaths.from_app_dir(tmp_path),
        ErpDatabase(tmp_path / "erp.sqlite3"),
    )
    assert isinstance(context.products, ProductStore)
    assert isinstance(context.config, ConfigStore)
    assert context.products._db is context.db
    assert context.config._db is context.db

    wrappers = [
        definition
        for definition in function_definitions(
            "erp_web/runtime_units/product_store.py"
        )
        if not definition.qualname.startswith("_")
    ]
    assert wrappers
    for definition in wrappers:
        body = list(definition.node.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        assert len(body) == 1
        assert isinstance(body[0], (ast.Return, ast.Expr))


# 验收：发布日志必须写入 SQLite，不得恢复 publish_logs.json 的读改写截断实现。
def test_publish_logs_are_database_backed(tmp_path) -> None:
    with temp_app_context(tmp_path) as context:
        publish_bus.append_publish_log(
            {
                "platform": "mercadolibre",
                "product_id": "stateful-p1",
                "status": "success",
            }
        )
        rows = publish_bus.load_publish_logs(limit=10)
        assert rows[0]["product_id"] == "stateful-p1"
        assert context.db.list_publish_logs(limit=10)[0]["product_id"] == "stateful-p1"
        assert not list(tmp_path.rglob("publish_logs.json"))


# 验收：Ozon 类目树缓存必须使用带锁的通用 TtlCache，而不是裸模块字典。
def test_ozon_category_cache_uses_thread_safe_ttl_cache(monkeypatch) -> None:
    assert isinstance(ozon_category_api._tree_cache, TtlCache)
    now = [100.0]
    monkeypatch.setattr(ozon_category_api.time, "monotonic", lambda: now[0])
    cache = TtlCache(ttl_seconds=5)
    source = {"items": ["original"]}
    cache.set("tree", source)
    source["items"].append("caller-mutation")
    cached = cache.get("tree")
    assert cached == {"items": ["original"]}
    cached["items"].append("read-mutation")
    assert cache.get("tree") == {"items": ["original"]}
    with ThreadPoolExecutor(max_workers=8) as executor:
        concurrent_values = list(
            executor.map(
                lambda index: (
                    cache.set("shared", {"value": index}),
                    cache.get("shared"),
                )[1],
                range(200),
            )
        )
    assert all(
        isinstance(value, dict) and isinstance(value.get("value"), int)
        for value in concurrent_values
    )
    now[0] += 5
    assert cache.get("tree") is None

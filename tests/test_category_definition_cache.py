# -*- coding: utf-8 -*-
"""统一 SQLite 类目属性定义与词典缓存契约测试（CategoryCatalogStore）。

覆盖读取规则的四条路径与 60s 失败冷却：
1. fresh cache：直接返回（source=cache），不调用 live；
2. 无 fresh cache：请求 live，成功后写回并携带指纹；
3. live 瞬时错误（timeout/连接失败/429/5xx）：返回 stale 窗口内的定义
   （source=stale, cache.stale=True），并在冷却期内不再重复请求 live；
4. 超过 stale_until：抛出可重试 CATEGORY_ATTRIBUTES_UNAVAILABLE。

并证明 401/403/凭据缺失/结构错误不得被 stale 掩盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from erp_web.runtime_units.category_definition_support import (
    CategoryAttributesUnavailableError,
    is_transient_category_api_error,
)
from erp_web.schemas.category_definition import (
    CategoryAttributeDefinition,
    CategoryDefinition,
    definition_fingerprint,
)
from erp_web.stores.category_catalog_store import CategoryCatalogStore

T0 = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
FRESH_TTL = timedelta(hours=24)


def _store(tmp_path: Path) -> CategoryCatalogStore:
    return CategoryCatalogStore(tmp_path / "category-catalog.sqlite3")


def _definition(category_id: str = "1001") -> CategoryDefinition:
    return CategoryDefinition(
        platform="ozon",
        site="global",
        category_id=category_id,
        category_path="测试类目",
        required=(CategoryAttributeDefinition(id="85", name="品牌", required=True),),
    )


def _load(
    store: CategoryCatalogStore,
    live_loader,
    *,
    now: datetime | None = None,
    fresh_ttl: timedelta = FRESH_TTL,
    force_refresh: bool = False,
):
    return store.load_definition_through_cache(
        platform="ozon",
        credential_scope_hash="sha256:test-scope",
        site="global",
        category_id="1001",
        live_loader=live_loader,
        is_transient_error=is_transient_category_api_error,
        unavailable_error_factory=CategoryAttributesUnavailableError,
        fresh_ttl=fresh_ttl,
        force_refresh=force_refresh,
        now=now,
    )


def test_fresh_cache_served_without_live_call(tmp_path: Path) -> None:
    store = _store(tmp_path)
    calls = {"live": 0}

    def live_loader() -> CategoryDefinition:
        calls["live"] += 1
        return _definition()

    first = _load(store, live_loader, now=T0)
    assert first.cache.source == "live"
    assert first.fingerprint == definition_fingerprint(_definition())
    assert calls["live"] == 1

    second = _load(store, live_loader, now=T0 + timedelta(hours=1))
    assert second.cache.source == "cache"
    assert second.cache.stale is False
    assert second.fingerprint == first.fingerprint
    assert calls["live"] == 1, "fresh 缓存命中不得再次调用 live"


def test_live_success_writes_sqlite_cache_with_fingerprint(tmp_path: Path) -> None:
    store = _store(tmp_path)
    definition = _load(store, _definition, now=T0)
    entry = store.read_definition(
        platform="ozon",
        scope_key="sha256:test-scope",
        site="global",
        category_id="1001",
        now=T0,
    )
    assert entry is not None
    assert entry.fingerprint == definition.fingerprint
    assert entry.is_fresh(T0 + timedelta(hours=23))
    assert not entry.is_fresh(T0 + timedelta(hours=25))
    assert entry.can_serve_stale(T0 + timedelta(days=6))


def test_transient_live_failure_serves_stale_and_respects_cooldown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _load(store, _definition, now=T0)
    calls = {"fail": 0}

    def failing_loader() -> CategoryDefinition:
        calls["fail"] += 1
        raise TimeoutError("平台接口超时")

    result = _load(store, failing_loader, now=T0 + timedelta(hours=25))
    assert result.cache.source == "stale"
    assert result.cache.stale is True
    assert result.required[0].id == "85"
    assert calls["fail"] == 1

    # 60 秒冷却期内直接返回 stale，不重复打远端。
    repeated = _load(store, failing_loader, now=T0 + timedelta(hours=25, seconds=10))
    assert repeated.cache.source == "stale"
    assert calls["fail"] == 1


def test_expired_cache_raises_unavailable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _load(store, _definition, now=T0)

    def failing_loader() -> CategoryDefinition:
        raise TimeoutError("平台接口超时")

    far_future = T0 + timedelta(days=30)
    with pytest.raises(CategoryAttributesUnavailableError):
        _load(store, failing_loader, now=far_future)


def test_transient_failure_without_cache_raises_unavailable(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def failing_loader() -> CategoryDefinition:
        raise ConnectionError("connection reset")

    with pytest.raises(CategoryAttributesUnavailableError):
        _load(store, failing_loader, now=T0)


@pytest.mark.parametrize(
    "error_message",
    [
        "GET https://api.ozon.ru failed: 401 unauthorized",
        "GET https://api.ozon.ru failed: 403 forbidden",
        "请先填写 Ozon Client ID 和 API Key。",
    ],
)
def test_deterministic_auth_errors_not_masked_by_stale(
    tmp_path: Path,
    error_message: str,
) -> None:
    store = _store(tmp_path)
    _load(store, _definition, now=T0)

    def failing_loader() -> CategoryDefinition:
        raise RuntimeError(error_message)

    with pytest.raises(RuntimeError) as raised:
        _load(store, failing_loader, now=T0 + timedelta(hours=25))
    assert not isinstance(raised.value, CategoryAttributesUnavailableError)
    assert str(raised.value) == error_message


def test_structural_error_not_masked_by_stale(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _load(store, _definition, now=T0)

    def failing_loader() -> CategoryDefinition:
        raise ValueError("Ozon 类目属性响应缺少 result 列表。")

    with pytest.raises(ValueError):
        _load(store, failing_loader, now=T0 + timedelta(hours=25))


def test_definition_cache_scoped_by_credentials(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _load(store, _definition, now=T0)
    other_scope = store.read_definition(
        platform="ozon",
        scope_key="sha256:other-account",
        site="global",
        category_id="1001",
        now=T0,
    )
    assert other_scope is None


def test_glossary_persists_across_reopen_and_falls_back_to_general(tmp_path: Path) -> None:
    db_path = tmp_path / "category-catalog.sqlite3"
    store = CategoryCatalogStore(db_path)
    store.put_glossary("general", "zh-CN", {"Color": "颜色"})
    store.put_glossary("ozon", "zh-CN", {"Цвет товара": "商品颜色"})

    reopened = CategoryCatalogStore(db_path)
    hits = reopened.get_glossary("ozon", "zh-CN", ["Цвет товара", "Color", "Unknown"])
    assert hits == {"Цвет товара": "商品颜色", "Color": "颜色"}

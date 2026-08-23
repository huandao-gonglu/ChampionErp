# -*- coding: utf-8 -*-
"""类目属性定义缓存契约测试（类目 Schema 分离计划 Phase 1）。

覆盖读取规则的四条路径：
1. fresh cache：直接返回（source=cache），不调用 live；
2. 无 fresh cache：请求 live，成功后写回并携带指纹；
3. live 瞬时错误（timeout/连接失败/429/5xx）：返回 stale 窗口内的定义
   （source=stale, cache.stale=True）；
4. 超过 stale_until：抛出可重试 CATEGORY_ATTRIBUTES_UNAVAILABLE。

并证明 401/403/凭据缺失等确定性错误不得被 stale 掩盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from erp_web.runtime_units.category_definition_cache import (
    load_definition_through_cache,
    read_definition_cache,
)
from erp_web.runtime_units.category_definition_support import (
    CategoryAttributesUnavailableError,
)
from erp_web.schemas.category_definition import (
    CategoryAttributeDefinition,
    CategoryDefinition,
    definition_fingerprint,
)

T0 = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
FRESH_TTL = timedelta(hours=24)


def _definition(category_id: str = "1001") -> CategoryDefinition:
    return CategoryDefinition(
        platform="ozon",
        site="global",
        category_id=category_id,
        category_path="测试类目",
        required=(CategoryAttributeDefinition(id="85", name="品牌", required=True),),
    )


def _load(
    cache_root: Path,
    live_loader,
    *,
    now: datetime | None = None,
    fresh_ttl: timedelta = FRESH_TTL,
):
    return load_definition_through_cache(
        cache_root=cache_root,
        platform="ozon",
        credential_scope_hash="sha256:test-scope",
        site="global",
        category_id="1001",
        live_loader=live_loader,
        fresh_ttl=fresh_ttl,
        now=now,
    )


def test_fresh_cache_served_without_live_call(tmp_path: Path) -> None:
    calls = {"live": 0}

    def live_loader() -> CategoryDefinition:
        calls["live"] += 1
        return _definition()

    first = _load(tmp_path, live_loader, now=T0)
    assert first.cache.source == "live"
    assert first.fingerprint == definition_fingerprint(_definition())
    assert calls["live"] == 1

    second = _load(tmp_path, live_loader, now=T0 + timedelta(hours=1))
    assert second.cache.source == "cache"
    assert second.cache.stale is False
    assert second.fingerprint == first.fingerprint
    assert calls["live"] == 1, "fresh 缓存命中不得再次调用 live"


def test_live_success_writes_cache_with_fingerprint(tmp_path: Path) -> None:
    definition = _load(tmp_path, _definition, now=T0)
    entry = read_definition_cache(
        tmp_path,
        platform="ozon",
        credential_scope_hash="sha256:test-scope",
        site="global",
        category_id="1001",
        now=T0,
    )
    assert entry is not None
    assert entry.fingerprint == definition.fingerprint
    assert entry.is_fresh(T0 + timedelta(hours=23))
    assert not entry.is_fresh(T0 + timedelta(hours=25))
    assert entry.can_serve_stale(T0 + timedelta(days=6))


def test_transient_live_failure_serves_stale(tmp_path: Path) -> None:
    _load(tmp_path, _definition, now=T0)

    def failing_loader() -> CategoryDefinition:
        raise TimeoutError("平台接口超时")

    result = _load(tmp_path, failing_loader, now=T0 + timedelta(hours=25))
    assert result.cache.source == "stale"
    assert result.cache.stale is True
    assert result.required[0].id == "85"


def test_expired_cache_raises_unavailable(tmp_path: Path) -> None:
    _load(tmp_path, _definition, now=T0)

    def failing_loader() -> CategoryDefinition:
        raise TimeoutError("平台接口超时")

    far_future = T0 + timedelta(days=30)
    with pytest.raises(CategoryAttributesUnavailableError):
        _load(tmp_path, failing_loader, now=far_future)


def test_transient_failure_without_cache_raises_unavailable(
    tmp_path: Path,
) -> None:
    def failing_loader() -> CategoryDefinition:
        raise ConnectionError("connection reset")

    with pytest.raises(CategoryAttributesUnavailableError):
        _load(tmp_path, failing_loader, now=T0)


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
    # 即使仍在 stale 窗口内，确定性鉴权错误也不得回退旧定义。
    _load(tmp_path, _definition, now=T0)

    def failing_loader() -> CategoryDefinition:
        raise RuntimeError(error_message)

    with pytest.raises(RuntimeError) as raised:
        _load(tmp_path, failing_loader, now=T0 + timedelta(hours=25))
    assert not isinstance(raised.value, CategoryAttributesUnavailableError)
    assert str(raised.value) == error_message


def test_structural_error_not_masked_by_stale(tmp_path: Path) -> None:
    _load(tmp_path, _definition, now=T0)

    def failing_loader() -> CategoryDefinition:
        raise ValueError("Ozon 类目属性响应缺少 result 列表。")

    with pytest.raises(ValueError):
        _load(tmp_path, failing_loader, now=T0 + timedelta(hours=25))


def test_definition_cache_scoped_by_credentials(tmp_path: Path) -> None:
    _load(tmp_path, _definition, now=T0)
    other_scope = read_definition_cache(
        tmp_path,
        platform="ozon",
        credential_scope_hash="sha256:other-account",
        site="global",
        category_id="1001",
        now=T0,
    )
    assert other_scope is None

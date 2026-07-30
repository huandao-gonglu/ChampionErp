from __future__ import annotations

from pathlib import Path

import pytest

from erp_web.context import AppContext, AppPaths, get_context
from erp_web.db import ErpDatabase
from tests.runtime_test_utils import temp_app_context


def test_app_context_close_is_idempotent_and_does_not_create_lazy_bus(
    tmp_path: Path,
) -> None:
    context = AppContext(
        AppPaths.from_app_dir(tmp_path / "unused"),
        ErpDatabase(tmp_path / "unused/erp.sqlite3"),
    )

    assert context._publishing_bus is None
    context.close()
    context.close()

    assert context.closed is True
    assert context._publishing_bus is None
    with pytest.raises(RuntimeError, match="AppContext 已关闭"):
        _ = context.publishing_bus


def test_nested_temp_contexts_close_only_the_context_they_own(tmp_path: Path) -> None:
    previous = get_context()
    with temp_app_context(tmp_path / "outer") as outer:
        outer_bus = outer.publishing_bus
        assert outer.closed is False

        with temp_app_context(tmp_path / "inner") as inner:
            inner_bus = inner.publishing_bus
            assert get_context() is inner
            assert outer.closed is False

        assert inner.closed is True
        with pytest.raises(RuntimeError, match="cannot schedule"):
            inner_bus.executor.submit(lambda: None)
        assert get_context() is outer
        assert outer.closed is False
        assert outer.publishing_bus is outer_bus
        assert outer_bus.executor.submit(lambda: "active").result(timeout=1) == "active"

    assert outer.closed is True
    with pytest.raises(RuntimeError, match="cannot schedule"):
        outer_bus.executor.submit(lambda: None)
    assert get_context() is previous

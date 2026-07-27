# -*- coding: utf-8 -*-
from __future__ import annotations

"""Test helpers for rebinding runtime-unit module globals.

``erp_web.runtime`` used to re-inject its own namespace into every runtime
unit module before each call, so tests could override a single aggregator
attribute (paths, collaborator functions) and have the override visible
inside all units. The aggregator is now a stateless lazy-forwarding facade:
patching it does nothing to unit internals.

These helpers make the old test intent explicit: rebind the named global in
every ``erp_web.runtime_units`` (and ``erp_web.http_route_units``) module
whose namespace defines it, then restore the original bindings on exit.
"""

import importlib
import pkgutil
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

import erp_web.facades as _facades_package
import erp_web.http_route_units as _route_units_package
import erp_web.runtime_units as _runtime_units_package
from erp_web.context import AppContext, AppPaths, get_context, set_context
from erp_web.db import ErpDatabase


def _package_modules(package: ModuleType) -> list[ModuleType]:
    modules: list[ModuleType] = []
    for info in pkgutil.iter_modules(package.__path__):
        modules.append(importlib.import_module(f"{package.__name__}.{info.name}"))
    return modules


def _target_modules() -> list[ModuleType]:
    return (
        _package_modules(_runtime_units_package)
        + _package_modules(_route_units_package)
        + _package_modules(_facades_package)
    )


@contextmanager
def temp_app_context(app_dir: Path) -> Iterator[AppContext]:
    """Install a process context (paths + ErpDatabase) rooted at ``app_dir``.

    ``ErpDatabase`` runs its schema initialization in the constructor, so the
    temporary directory gets its own isolated SQLite store. The previous
    context is restored on exit.
    """
    previous = get_context()
    paths = AppPaths.from_app_dir(Path(app_dir))
    context = AppContext(paths=paths, db=ErpDatabase(paths.db_path))
    set_context(context)
    try:
        yield context
    finally:
        set_context(previous)


@contextmanager
def patch_unit_globals(**overrides: Any) -> Iterator[None]:
    """Rebind named globals in every unit module that defines them.

    Usage::

        with patch_unit_globals(APP_DIR=tmp_path, http_json=fake_http_json):
            ...

    Raises AttributeError if a name is not defined in any unit module, so a
    typo cannot silently patch nothing.

    When ``APP_DIR`` is overridden, the process ``AppContext`` (and therefore
    ``get_context().db``) is also swapped to an isolated SQLite store under
    that directory, mirroring the old per-APP_DIR ensure_sqlite_store()
    behavior.
    """
    modules = _target_modules()
    missing = [name for name in overrides if not any(name in module.__dict__ for module in modules)]
    if missing:
        raise AttributeError(f"unknown runtime unit globals: {missing}")
    saved: list[tuple[ModuleType, str, Any]] = []
    try:
        for module in modules:
            for name, value in overrides.items():
                if name in module.__dict__:
                    saved.append((module, name, module.__dict__[name]))
                    setattr(module, name, value)
        if "APP_DIR" in overrides:
            with temp_app_context(Path(overrides["APP_DIR"])):
                yield
        else:
            yield
    finally:
        for module, name, original in reversed(saved):
            setattr(module, name, original)

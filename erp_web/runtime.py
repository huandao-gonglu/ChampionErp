# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect as _inspect
from functools import wraps as _wraps

from .runtime_units import (
    auth_runtime as _auth_runtime,
    browser_debug as _browser_debug,
    category_refresh as _category_refresh,
    category_store as _category_store,
    collect_helpers as _collect_helpers,
    copy_generation as _copy_generation,
    image_pool as _image_pool,
    pricing_runtime as _pricing_runtime,
    publish_adapter as _publish_adapter,
    product_store as _product_store,
    publish_bus as _publish_bus,
    publish_helpers as _publish_helpers,
    publish_logs_runtime as _publish_logs_runtime,
    publish_mercadolibre as _publish_mercadolibre,
    publish_runtime as _publish_runtime,
    publish_validation as _publish_validation,
    runtime_api as _runtime_api,
    runtime_common as _runtime_common,
    source_collect as _source_collect,
    source_collect_browser as _source_collect_browser,
    source_collect_parsers as _source_collect_parsers,
    source_collect_workflows as _source_collect_workflows,
)

_RUNTIME_INTERNAL_NAMES = {
    "_RUNTIME_INTERNAL_NAMES",
    "_RUNTIME_UNITS",
    "_UNIT_MODULE_NAMES",
    "_make_runtime_function",
    "_sync_runtime_units",
    "_install_runtime_units",
    "_inspect",
    "_wraps",
}
_RUNTIME_UNITS = (
    _runtime_common,
    _category_store,
    _product_store,
    _image_pool,
    _collect_helpers,
    _publish_bus,
    _browser_debug,
    _category_refresh,
    _source_collect_browser,
    _source_collect_parsers,
    _source_collect_workflows,
    _source_collect,
    _copy_generation,
    _auth_runtime,
    _pricing_runtime,
    _publish_helpers,
    _publish_validation,
    _publish_logs_runtime,
    _publish_mercadolibre,
    _publish_adapter,
    _publish_runtime,
    _runtime_api,
)
_UNIT_MODULE_NAMES = {module.__name__ for module in _RUNTIME_UNITS}


def _sync_runtime_units() -> None:
    shared = {
        name: value
        for name, value in globals().items()
        if not name.startswith("__") and name not in _RUNTIME_INTERNAL_NAMES
    }
    for module in _RUNTIME_UNITS:
        module.__dict__.update(shared)


def _make_runtime_function(func):
    @_wraps(func)
    def runtime_function(*args, **kwargs):
        _sync_runtime_units()
        return func(*args, **kwargs)

    runtime_function.__module__ = __name__
    return runtime_function


def _install_runtime_units() -> None:
    namespace = globals()
    common_values = _runtime_common.__dict__
    for module in _RUNTIME_UNITS:
        for name, value in module.__dict__.items():
            if name.startswith("__"):
                continue
            if module is not _runtime_common and name in common_values and common_values[name] is value:
                continue
            if _inspect.isfunction(value) and value.__module__ in _UNIT_MODULE_NAMES:
                namespace[name] = _make_runtime_function(value)
            else:
                namespace[name] = value
    _sync_runtime_units()


_install_runtime_units()

__all__ = [name for name in globals() if not name.startswith("_")]

from __future__ import annotations

import inspect as _inspect
from functools import wraps as _wraps

from product_model_units import (
    category_model as _category_model,
    common as _common,
    defaults as _defaults,
    image_pool_model as _image_pool_model,
    merge_model as _merge_model,
)

_INTERNAL_NAMES = {
    "_INTERNAL_NAMES",
    "_UNITS",
    "_UNIT_MODULE_NAMES",
    "_make_unit_function",
    "_sync_units",
    "_install_units",
    "_inspect",
    "_wraps",
}
_UNITS = (
    _common,
    _category_model,
    _image_pool_model,
    _defaults,
    _merge_model,
)
_UNIT_MODULE_NAMES = {module.__name__ for module in _UNITS}


def _sync_units() -> None:
    shared = {
        name: value
        for name, value in globals().items()
        if not name.startswith("__") and name not in _INTERNAL_NAMES
    }
    for module in _UNITS:
        module.__dict__.update(shared)


def _make_unit_function(func):
    @_wraps(func)
    def unit_function(*args, **kwargs):
        _sync_units()
        return func(*args, **kwargs)

    unit_function.__module__ = __name__
    return unit_function


def _install_units() -> None:
    namespace = globals()
    common_values = _common.__dict__
    for module in _UNITS:
        for name, value in module.__dict__.items():
            if name.startswith("__"):
                continue
            if module is not _common and name in common_values and common_values[name] is value:
                continue
            if _inspect.isfunction(value) and value.__module__ in _UNIT_MODULE_NAMES:
                namespace[name] = _make_unit_function(value)
            else:
                namespace[name] = value
    _sync_units()


_install_units()

__all__ = [name for name in globals() if not name.startswith("_")]

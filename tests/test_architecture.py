# -*- coding: utf-8 -*-
from __future__ import annotations

"""Architecture guard tests.

The ``erp_web.runtime`` aggregator used to inject its namespace into every
runtime unit module, letting code rely on names it never imported. These tests
keep the codebase honest now that the injection is gone:

* every module in ``erp_web/`` must resolve all the names it loads (module
  scope, imports, locals or builtins) — no implicit cross-module names;
* the namespace-injection machinery must never come back.
"""

import importlib.util
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_implicit_names", APP_DIR / "scripts" / "check_implicit_names.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_erp_web_modules_have_no_unresolved_names() -> None:
    checker = _load_checker()
    findings = checker.run([APP_DIR / "erp_web"])
    formatted = "\n".join(f"{path}:{line}: unresolved name '{name}'" for path, line, name in findings)
    assert not findings, (
        "Modules load names they never define or import (previously masked by "
        "the erp_web.runtime namespace injection). Add explicit imports at the "
        "top of the module, or whitelist confirmed false positives in "
        "scripts/check_implicit_names.py:\n" + formatted
    )


def test_runtime_namespace_injection_stays_removed() -> None:
    assert not (APP_DIR / "erp_web" / "runtime.py").exists()
    for path in sorted((APP_DIR / "erp_web").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "_sync_runtime_units" not in text, f"namespace injection resurfaced in {path}"

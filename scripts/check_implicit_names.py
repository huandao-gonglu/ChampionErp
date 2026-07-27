#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""Report names that a module loads but never defines or imports.

The old ``erp_web.runtime`` aggregator injected its whole namespace into every
runtime unit module before each call, so code could silently rely on names it
never imported. The aggregator is now a stateless forwarding facade, which
turns every such implicit dependency into a latent NameError. This script
finds them statically.

How it works
------------
* ``symtable`` resolves scoping exactly like CPython (functions, classes,
  lambdas, comprehensions, ``global``/``nonlocal``), so a name counts as
  "unresolved" only when it really falls through to module scope without a
  module-level binding and is not a builtin.
* A second ``ast`` pass recovers the ``file:line`` of each offending load.
  Names that appear *only* inside annotations are skipped: with
  ``from __future__ import annotations`` they are never evaluated at runtime.

Usage::

    python3 scripts/check_implicit_names.py [package_dir ...]

Defaults to checking ``erp_web/``. Exits non-zero when unresolved names are
found (after applying ``WHITELIST``). ``tests/test_architecture.py`` runs the
same check in CI fashion so regressions cannot creep back in.
"""

import ast
import builtins
import symtable
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

_BUILTIN_NAMES = set(dir(builtins))
_MODULE_DUNDERS = {
    "__name__",
    "__file__",
    "__doc__",
    "__package__",
    "__spec__",
    "__loader__",
    "__path__",
    "__debug__",
    "__builtins__",
    "__annotations__",
    "__dict__",
    "__class__",
}

# Confirmed false positives, keyed by path relative to the repo root.
# Keep entries commented with the reason they are safe.
WHITELIST: dict[str, set[str]] = {}


def _module_level_names(table: symtable.SymbolTable) -> set[str]:
    names: set[str] = set()
    for symbol in table.get_symbols():
        if symbol.is_imported() or symbol.is_assigned() or symbol.is_declared_global():
            names.add(symbol.get_name())
    # A function that declares ``global X`` and assigns it also creates a
    # module-level binding at call time; treat it as defined.
    def _add_declared_globals(child: symtable.SymbolTable) -> None:
        for symbol in child.get_symbols():
            if symbol.is_declared_global() and symbol.is_assigned():
                names.add(symbol.get_name())
        for grandchild in child.get_children():
            _add_declared_globals(grandchild)

    for child in table.get_children():
        _add_declared_globals(child)
    return names


def _collect_global_reads(table: symtable.SymbolTable, reads: set[str]) -> None:
    is_module_scope = table.get_type() == "module"
    for symbol in table.get_symbols():
        name = symbol.get_name()
        if not symbol.is_referenced():
            continue
        if is_module_scope:
            # At module scope every non-assigned referenced symbol is a read
            # of a global (or builtin) name.
            if not (symbol.is_imported() or symbol.is_assigned()):
                reads.add(name)
        elif symbol.is_global():
            reads.add(name)
    for child in table.get_children():
        _collect_global_reads(child, reads)


class _AnnotationAwareVisitor(ast.NodeVisitor):
    """Collect Name loads with line numbers, skipping annotation subtrees."""

    def __init__(self, wanted: set[str]) -> None:
        self.wanted = wanted
        self.occurrences: dict[str, list[int]] = {}

    def _visit_optional(self, node: ast.AST | None) -> None:
        if node is not None:
            self.visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.wanted:
            self.occurrences.setdefault(node.id, []).append(node.lineno)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # Skip node.annotation (lazily evaluated under __future__ annotations).
        self._visit_optional(node.value)
        self.visit(node.target)

    def _visit_arguments(self, args: ast.arguments) -> None:
        for default in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
            self.visit(default)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        # Skip argument annotations and the return annotation.
        for statement in node.body:
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def check_file(path: Path) -> list[tuple[str, int, str]]:
    """Return (relative_path, line, name) findings for one Python file."""
    source = path.read_text(encoding="utf-8")
    relative = str(path.relative_to(REPO_ROOT))
    table = symtable.symtable(source, str(path), "exec")
    defined = _module_level_names(table)
    reads: set[str] = set()
    _collect_global_reads(table, reads)
    unresolved = {
        name
        for name in reads
        if name not in defined and name not in _BUILTIN_NAMES and name not in _MODULE_DUNDERS
    }
    unresolved -= WHITELIST.get(relative, set())
    if not unresolved:
        return []
    visitor = _AnnotationAwareVisitor(unresolved)
    visitor.visit(ast.parse(source, filename=str(path)))
    findings = []
    for name, lines in visitor.occurrences.items():
        for line in sorted(lines):
            findings.append((relative, line, name))
    return sorted(findings)


def iter_python_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def run(roots: Iterable[Path]) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in iter_python_files(roots):
        findings.extend(check_file(path))
    return findings


def main(argv: list[str]) -> int:
    roots = [Path(arg).resolve() for arg in argv] or [REPO_ROOT / "erp_web"]
    findings = run(roots)
    for relative, line, name in findings:
        print(f"{relative}:{line}: unresolved name '{name}'")
    if findings:
        print(f"\n{len(findings)} unresolved name load(s) found.")
        return 1
    print("No unresolved names found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Iterable, Iterator


ROOT = Path(__file__).resolve().parents[2]
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def python_files(relative_path: str) -> list[Path]:
    root = ROOT / relative_path
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def parse_python(path: Path | str) -> ast.Module:
    resolved = path if isinstance(path, Path) else ROOT / path
    return ast.parse(
        resolved.read_text(encoding="utf-8"),
        filename=str(resolved),
    )


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def scoped_walk(root: FunctionNode) -> Iterator[ast.AST]:
    stack: list[ast.AST] = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(list(ast.iter_child_nodes(node))):
            if child is not root and isinstance(
                child,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                ),
            ):
                continue
            stack.append(child)


def imported_targets(
    paths: Iterable[Path],
) -> list[tuple[Path, str]]:
    imports: list[tuple[Path, str]] = []
    for path in paths:
        tree = parse_python(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(
                    (path, alias.name) for alias in node.names
                )
                continue
            if isinstance(node, ast.ImportFrom):
                base = (
                    f"{'.' * node.level}{node.module or ''}"
                ).rstrip(".")
                for alias in node.names:
                    target = (
                        f"{base}.{alias.name}"
                        if base
                        else f"{'.' * node.level}{alias.name}"
                    )
                    imports.append((path, target))
                continue
            if not isinstance(node, ast.Call):
                continue
            if dotted_name(node.func) not in {
                "import_module",
                "importlib.import_module",
            }:
                continue
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                imports.append((path, node.args[0].value))
    return imports


def forbidden_calls(
    paths: Iterable[Path],
    forbidden_names: Iterable[str],
) -> list[str]:
    forbidden = set(forbidden_names)
    findings: list[str] = []
    for path in paths:
        tree = parse_python(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted_name(node.func)
            if name in forbidden or any(
                name.endswith(f".{item}") for item in forbidden
            ):
                findings.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} -> {name}"
                )
    return findings


def called_leaf_names(relative_path: str) -> list[str]:
    return [
        dotted_name(node.func).split(".")[-1]
        for node in ast.walk(parse_python(relative_path))
        if isinstance(node, ast.Call)
    ]


def sensitive_paths(value: Any, prefix: str = "") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            if (
                not str(key).casefold().startswith("masked_")
                and re.search(
                    (
                        r"(api[_-]?key|token|secret|password|cookie|"
                        r"code_verifier|private_key)"
                    ),
                    str(key),
                    re.I,
                )
                and item not in ("", None, {}, [])
            ):
                matches.append(current)
            matches.extend(sensitive_paths(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(
                sensitive_paths(item, f"{prefix}[{index}]")
            )
    return matches

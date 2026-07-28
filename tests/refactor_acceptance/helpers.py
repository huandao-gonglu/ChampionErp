from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


ROOT = Path(__file__).resolve().parents[2]
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class FunctionDefinition:
    path: Path
    qualname: str
    lineno: int
    node: FunctionNode


@dataclass(frozen=True)
class SourceFinding:
    path: Path
    qualname: str
    lineno: int
    detail: str


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def python_files(relative_path: str) -> list[Path]:
    root = ROOT / relative_path
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def parse_python(path: Path | str) -> ast.Module:
    resolved = path if isinstance(path, Path) else ROOT / path
    return ast.parse(resolved.read_text(encoding="utf-8"), filename=str(resolved))


def _definitions_in_body(
    path: Path,
    body: list[ast.stmt],
    prefix: str = "",
) -> Iterator[FunctionDefinition]:
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}.{statement.name}" if prefix else statement.name
            yield FunctionDefinition(path, qualname, statement.lineno, statement)
        elif isinstance(statement, ast.ClassDef):
            class_name = f"{prefix}.{statement.name}" if prefix else statement.name
            yield from _definitions_in_body(path, statement.body, class_name)


def function_definitions(path: Path | str) -> list[FunctionDefinition]:
    resolved = path if isinstance(path, Path) else ROOT / path
    return list(_definitions_in_body(resolved, parse_python(resolved).body))


def scoped_walk(root: FunctionNode) -> Iterator[ast.AST]:
    """遍历一个函数自身作用域，不把嵌套函数/类的调用算到外层。"""

    stack: list[ast.AST] = [root]
    while stack:
        node = stack.pop()
        yield node
        children = list(ast.iter_child_nodes(node))
        for child in reversed(children):
            if child is not root and isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
            ):
                continue
            stack.append(child)


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def scoped_calls(definition: FunctionDefinition) -> list[ast.Call]:
    return [
        node
        for node in scoped_walk(definition.node)
        if isinstance(node, ast.Call)
    ]


def imported_targets(paths: Iterable[Path]) -> list[tuple[Path, str]]:
    """返回导入的完整目标，覆盖 ``from erp_web import runtime`` 形式。"""

    imports: list[tuple[Path, str]] = []
    for path in paths:
        tree = parse_python(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend((path, alias.name) for alias in node.names)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            base = f"{'.' * node.level}{node.module or ''}".rstrip(".")
            for alias in node.names:
                target = f"{base}.{alias.name}" if base else f"{'.' * node.level}{alias.name}"
                imports.append((path, target))
        for definition in function_definitions(path):
            for call in scoped_calls(definition):
                name = dotted_name(call.func)
                if name not in {"import_module", "importlib.import_module"}:
                    continue
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    imports.append((path, call.args[0].value))
    return imports


def function_calls_name(relative_path: str, function_name: str) -> list[str]:
    callers: list[str] = []
    for definition in function_definitions(relative_path):
        if any(dotted_name(call.func).split(".")[-1] == function_name for call in scoped_calls(definition)):
            callers.append(definition.qualname)
    return callers


def unvalidated_body_reads(paths: Iterable[Path]) -> list[SourceFinding]:
    """要求每次 ``read_body`` 都直接嵌套在运行时 schema 校验调用中。

    这个约束故意比“同一函数稍后调用过校验器”严格：入口必须先得到校验后的
    payload，不能先读取 raw body、执行副作用，再补一个形式化校验。
    """

    findings: list[SourceFinding] = []
    for path in paths:
        for definition in function_definitions(path):
            nodes = list(scoped_walk(definition.node))
            node_set = set(nodes)
            parents = {
                child: parent
                for parent in nodes
                for child in ast.iter_child_nodes(parent)
                if child in node_set
            }
            for body_call in (
                node for node in nodes if isinstance(node, ast.Call)
            ):
                if dotted_name(body_call.func).split(".")[-1] != "read_body":
                    continue
                ancestor: ast.AST | None = body_call
                directly_validated = False
                while ancestor in parents:
                    ancestor = parents[ancestor]
                    if isinstance(ancestor, ast.Call):
                        leaf = dotted_name(ancestor.func).split(".")[-1]
                        directly_validated = (
                            leaf.startswith(("validate_", "parse_"))
                            or leaf
                            in {
                                "model_validate",
                                "validate_python",
                                "parse_obj",
                            }
                        )
                        if directly_validated:
                            break
                    if isinstance(
                        ancestor,
                        (
                            ast.Assign,
                            ast.AnnAssign,
                            ast.NamedExpr,
                            ast.Return,
                            ast.Expr,
                        ),
                    ):
                        break
                if directly_validated:
                    continue
                findings.append(
                    SourceFinding(
                        path=path,
                        qualname=definition.qualname,
                        lineno=body_call.lineno,
                        detail=(
                            "read_body 必须直接作为运行时 schema 校验器的参数，"
                            "禁止先消费 raw body"
                        ),
                    )
                )
    return findings


def forbidden_calls(
    paths: Iterable[Path],
    forbidden_names: Iterable[str],
) -> list[SourceFinding]:
    forbidden = set(forbidden_names)
    findings: list[SourceFinding] = []
    for path in paths:
        for definition in function_definitions(path):
            for call in scoped_calls(definition):
                name = dotted_name(call.func)
                if name in forbidden or any(name.endswith(f".{item}") for item in forbidden):
                    findings.append(
                        SourceFinding(path, definition.qualname, call.lineno, name)
                    )
    return findings


def platform_literal_branches(
    paths: Iterable[Path],
    platform_literals: Iterable[str],
) -> list[SourceFinding]:
    """找通用层里针对具体平台字符串的条件分支，忽略注释和普通数据声明。"""

    findings: list[SourceFinding] = []
    seen: set[tuple[Path, int, tuple[str, ...]]] = set()
    platform_keys = {
        str(value).strip().casefold()
        for value in platform_literals
        if str(value).strip()
    }
    markers = ("platform", "marketplace", "source_site", "source_platform", "target_platform")
    for path in paths:
        tree = parse_python(path)
        for node in ast.walk(tree):
            subject: ast.AST | None = None
            literal_scopes: list[ast.AST] = []
            if isinstance(node, ast.If):
                subject = node.test
                literal_scopes = [node.test]
            elif isinstance(node, ast.IfExp):
                subject = node.test
                literal_scopes = [node.test]
            elif isinstance(node, ast.Match):
                subject = node.subject
                literal_scopes = [
                    scope
                    for case in node.cases
                    for scope in (case.pattern, case.guard)
                    if scope is not None
                ]
            if subject is None:
                continue
            identifiers = {
                dotted_name(item)
                for item in ast.walk(subject)
                if isinstance(item, (ast.Name, ast.Attribute))
            }
            if not any(
                marker in identifier.casefold()
                for marker in markers
                for identifier in identifiers
            ):
                continue
            strings = {
                item.value
                for scope in literal_scopes
                for item in ast.walk(scope)
                if (
                    isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                )
            }
            platform_strings = sorted(
                value
                for value in strings
                if value.casefold() in platform_keys
            )
            if platform_strings:
                key = (path, node.lineno, tuple(platform_strings))
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    SourceFinding(
                        path=path,
                        qualname="<module>",
                        lineno=node.lineno,
                        detail=", ".join(platform_strings),
                    )
                )
    return findings


def string_literal_occurrences(
    paths: Iterable[Path],
    needle: str,
) -> list[SourceFinding]:
    """只检查 Python 字符串常量，不让注释中的历史名称触发误报或伪通过。"""

    findings: list[SourceFinding] = []
    seen: set[tuple[Path, int, str]] = set()
    needle = str(needle or "").casefold()
    for path in paths:
        tree = parse_python(path)
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and needle in node.value.casefold()
            ):
                continue
            parent = parents.get(node)
            grandparent = parents.get(parent) if parent is not None else None
            if (
                isinstance(parent, ast.Expr)
                and isinstance(
                    grandparent,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                )
                and grandparent.body
                and grandparent.body[0] is parent
            ):
                continue
            key = (path, node.lineno, node.value)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                SourceFinding(path, "<module>", node.lineno, repr(node.value))
            )
    return findings


def format_findings(findings: Iterable[SourceFinding]) -> str:
    return "\n".join(
        f"{finding.path.relative_to(ROOT)}:{finding.lineno} "
        f"{finding.qualname} — {finding.detail}"
        for finding in findings
    )


def sensitive_paths(value: Any, prefix: str = "") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            key_text = str(key)
            is_masked_summary = key_text.casefold().startswith("masked_")
            if not is_masked_summary and re.search(
                r"(api[_-]?key|token|secret|password|cookie|code_verifier|private_key)",
                key_text,
                re.I,
            ):
                if item not in ("", None, {}, []):
                    matches.append(path)
            matches.extend(sensitive_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(sensitive_paths(item, f"{prefix}[{index}]"))
    return matches


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

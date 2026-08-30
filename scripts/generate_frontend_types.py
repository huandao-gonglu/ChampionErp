#!/usr/bin/env python3
"""由后端 TypedDict schema 生成前端 wire-contract 类型。"""

from __future__ import annotations

import argparse
import difflib
import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any, ForwardRef, Literal, Union, get_args, get_origin, get_type_hints, is_typeddict


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "front/src/types/workflow.generated.ts"
WORKFLOW_TYPES = ROOT / "front/src/types/workflow.ts"
SCHEMA_MODULES = (
    "erp_web.schemas.api",
    "erp_web.schemas.config",
    "erp_web.schemas.image",
    "erp_web.schemas.mercadolibre",
    "erp_web.schemas.product",
    "erp_web.schemas.publish",
)
SCHEMA_CONSTANTS = ("API_SCHEMA_VERSION", "PRODUCT_SCHEMA_VERSION")
BLOCK_START = "// <schema-generated-types>"
BLOCK_END = "// </schema-generated-types>"


def _load_schema_types() -> tuple[list[type], dict[str, int]]:
    schema_types: list[type] = []
    constants: dict[str, int] = {}
    for module_name in SCHEMA_MODULES:
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if is_typeddict(value) and value.__module__ == module_name:
                schema_types.append(value)
            elif name in SCHEMA_CONSTANTS and isinstance(value, int):
                constants[name] = value
    return schema_types, constants


def _schema_name(value: type) -> str:
    return f"Backend{value.__name__}"


def _typescript_type(annotation: object) -> str:
    if annotation is Any:
        return "unknown"
    if isinstance(annotation, ForwardRef):
        return f"Backend{annotation.__forward_arg__}"
    if annotation is None or annotation is type(None):
        return "null"
    if annotation is str:
        return "string"
    if annotation in (int, float):
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation is object:
        return "unknown"
    if is_typeddict(annotation):
        return _schema_name(annotation)

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (list, set, frozenset):
        item = _typescript_type(args[0] if args else Any)
        return f"Array<{item}>"
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return f"Array<{_typescript_type(args[0])}>"
        return f"[{', '.join(_typescript_type(item) for item in args)}]"
    if origin is dict:
        key = _typescript_type(args[0] if args else str)
        value = _typescript_type(args[1] if len(args) > 1 else Any)
        return f"Record<{key}, {value}>"
    if origin in (Union, types.UnionType):
        rendered = dict.fromkeys(_typescript_type(item) for item in args)
        return " | ".join(rendered)
    if origin is Literal:
        return " | ".join(
            json.dumps(item, ensure_ascii=False)
            for item in args
        )
    if origin is not None:
        origin_name = getattr(origin, "__name__", "")
        if origin_name in {"Required", "NotRequired"} and args:
            return _typescript_type(args[0])

    raise TypeError(f"不支持的 schema 类型：{annotation!r}")


def _render_generated_file(schema_types: list[type], constants: dict[str, int]) -> str:
    lines = [
        "/**",
        " * 此文件由 scripts/generate_frontend_types.py 自动生成。",
        " * 请修改 erp_web/schemas 后重新运行生成器，不要手工编辑。",
        " */",
        "",
    ]
    for name in SCHEMA_CONSTANTS:
        if name in constants:
            lines.append(f"export const {name} = {constants[name]} as const")
    lines.append("")

    for schema_type in schema_types:
        hints = get_type_hints(schema_type, include_extras=True)
        required = set(getattr(schema_type, "__required_keys__", ()))
        lines.append(f"export interface {_schema_name(schema_type)} {{")
        for field, annotation in hints.items():
            optional = "" if field in required else "?"
            lines.append(f"  {field}{optional}: {_typescript_type(annotation)}")
        lines.extend(("}", ""))
    return "\n".join(lines).rstrip() + "\n"


def _render_workflow_export_block(schema_types: list[type], constants: dict[str, int]) -> str:
    value_exports = [name for name in SCHEMA_CONSTANTS if name in constants]
    type_exports = [_schema_name(schema_type) for schema_type in schema_types]
    lines = [BLOCK_START, "export {"]
    lines.extend(f"  {name}," for name in value_exports)
    lines.extend(f"  type {name}," for name in type_exports)
    lines.extend(("} from './workflow.generated'", BLOCK_END))
    return "\n".join(lines)


def _replace_generated_block(source: str, block: str) -> str:
    if BLOCK_START not in source or BLOCK_END not in source:
        return f"{block}\n\n{source}"
    prefix, remainder = source.split(BLOCK_START, 1)
    _, suffix = remainder.split(BLOCK_END, 1)
    return f"{prefix}{block}{suffix}"


def _diff(path: Path, expected: str) -> str:
    actual = path.read_text(encoding="utf-8") if path.exists() else ""
    return "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(path.relative_to(ROOT)),
            tofile=f"{path.relative_to(ROOT)}（应生成）",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查生成文件是否最新")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    schema_types, constants = _load_schema_types()
    generated = _render_generated_file(schema_types, constants)
    workflow_source = WORKFLOW_TYPES.read_text(encoding="utf-8")
    workflow_expected = _replace_generated_block(
        workflow_source,
        _render_workflow_export_block(schema_types, constants),
    )

    diffs = _diff(OUTPUT, generated) + _diff(WORKFLOW_TYPES, workflow_expected)
    if args.check:
        if diffs:
            print(diffs)
            return 1
        print("前端 workflow wire-contract 类型与后端 schema 一致。")
        return 0

    OUTPUT.write_text(generated, encoding="utf-8")
    WORKFLOW_TYPES.write_text(workflow_expected, encoding="utf-8")
    print(f"已生成 {OUTPUT.relative_to(ROOT)} 并更新 workflow 类型出口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

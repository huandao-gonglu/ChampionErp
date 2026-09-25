"""补充原生 Python 签名无法表达的 JSON Schema 参数约束。"""

import json
from typing import Any


_CONSTRAINTS = (
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems",
    "minProperties", "maxProperties",
)


def parameter_constraints(schema: dict[str, Any]) -> str:
    """只投影约束说明；类型生成和实际参数校验仍由各自的既有入口负责。"""
    rows: list[str] = []

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        constraints = {key: node[key] for key in _CONSTRAINTS if key in node}
        if constraints:
            values = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in constraints.items())
            rows.append(f"{path or '参数对象'}: {values}")
        for name, child in node.get("properties", {}).items():
            visit(child, f"{path}.{name}" if path else name)
        for keyword, suffix in (("items", "[]"), ("additionalProperties", ".*"), ("propertyNames", ".<键>")):
            visit(node.get(keyword), path + suffix)
        for keyword in ("anyOf", "oneOf", "allOf"):
            for index, child in enumerate(node.get(keyword, [])):
                visit(child, f"{path} ({keyword}[{index}])")

    visit(schema, "")
    return "参数约束（JSON Schema）：\n" + "\n".join(rows) if rows else ""

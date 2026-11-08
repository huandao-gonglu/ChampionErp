"""原生 Tool Search 的目录匹配策略；不执行工具、不管理发现状态。"""

import re
from collections.abc import Sequence
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition


def match_tool_queries(
    ctx: RunContext[Any], queries: Sequence[str], tools: Sequence[ToolDefinition],
) -> list[str]:
    """完整工具名支持下划线和空格分词；关键词同时支持英文名称和中文说明。

    原生 keywords 仅提取 ASCII 单词，而且完整工具名会召回提及该名称的
    其他工具。只适配这两个匹配缺口，权限、结果上限与恢复均交给原生 ToolSearch。
    """
    del ctx
    names = {tool.name for tool in tools}
    exact: list[str] = []
    scores: dict[str, int] = {}
    for query in queries:
        query = query.strip()
        name = query if query in names else "_".join(query.lower().split())
        if name in names:
            if name not in exact:
                exact.append(name)
            continue
        terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", query.lower()))
        for tool in tools:
            text = f"{tool.name} {tool.description or ''}".lower()
            words = set(re.findall(r"[a-z0-9]+", text))
            score = sum(term in text if not term.isascii() else term in words for term in terms)
            if score:
                scores[tool.name] = scores.get(tool.name, 0) + score
    return exact + [name for name in sorted(scores, key=lambda name: (-scores[name], name)) if name not in exact]

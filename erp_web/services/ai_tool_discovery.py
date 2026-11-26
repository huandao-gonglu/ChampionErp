"""原生 Tool Search 的目录匹配策略；不执行工具、不管理发现状态。"""

import re
from collections.abc import Sequence
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition


def _ascii_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _cjk_bigrams(term: str) -> set[str]:
    if len(term) <= 2:
        return set()
    return {term[index : index + 2] for index in range(len(term) - 1)}


def match_tool_queries(
    ctx: RunContext[Any], queries: Sequence[str], tools: Sequence[ToolDefinition],
) -> list[str]:
    """完整工具名支持下划线和空格分词；关键词支持英文名优先与中文说明，未发现工具优先。

    原生 keywords 仅提取 ASCII 单词，而且完整工具名会召回提及该名称的
    其他工具；自定义 strategy 需自行将未发现工具排在已发现工具之前。
    只适配这些匹配缺口，权限、结果上限与恢复均交给原生 ToolSearch。
    """
    discovered = set(getattr(ctx, "discovered_tool_names", ()) or ())
    names = {tool.name for tool in tools}
    exact: list[str] = []
    best_scores: dict[str, int] = {}
    total_scores: dict[str, int] = {}
    indexed = [
        (tool.name, _ascii_tokens(tool.name), _ascii_tokens(tool.description or ""), (tool.description or "").lower())
        for tool in tools
    ]
    for query in queries:
        query = query.strip()
        if not query:
            continue
        name = query if query in names else "_".join(query.lower().split())
        if name in names:
            if name not in exact:
                exact.append(name)
            continue
        terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", query.lower()))
        ascii_terms = {term for term in terms if term.isascii()}
        for tool_name, name_words, desc_words, desc_text in indexed:
            score = 0
            for term in terms:
                if not term.isascii():
                    if term in desc_text:
                        score += 4
                    else:
                        bigram_hits = sum(1 for bigram in _cjk_bigrams(term) if bigram in desc_text)
                        if bigram_hits:
                            score += min(bigram_hits * 2, 6)
                elif term in name_words:
                    score += 4
                elif term in desc_words:
                    score += 1
            if name_words and name_words <= ascii_terms:
                score += 8
            if score:
                if score > best_scores.get(tool_name, 0):
                    best_scores[tool_name] = score
                total_scores[tool_name] = total_scores.get(tool_name, 0) + score
    exact_set = set(exact)
    scored = [name for name in best_scores if name not in exact_set]
    scored.sort(key=lambda name: (-best_scores[name], -total_scores[name], name))
    ordered = exact + scored
    # 与原生 keywords 一致：未发现工具始终排在已发现工具之前，避免重复召回挤占名额。
    return [name for name in ordered if name not in discovered] + [name for name in ordered if name in discovered]

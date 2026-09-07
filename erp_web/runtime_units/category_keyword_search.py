"""类目关键词的批量只读查询；Agent 调用次数与生命周期仍由 Pydantic AI 管理。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Lock
from typing import Any

from erp_web.marketplaces.category_provider import CategorySearcher
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import (
    CATEGORY_SEARCH_CANDIDATES_PER_KEYWORD,
    CATEGORY_SEARCH_MAX_CANDIDATES,
    CategoryCandidate,
    CategoryCandidateLedger,
    CategoryKeywordSearchError,
    CategoryKeywordSearchResult,
    CategorySearchResult,
)


class CategoryKeywordBatchSearch:
    """在一次匹配内复用成功查询，以固定并发检索并公平合并每个词的候选。"""

    def __init__(
        self,
        *,
        searcher: CategorySearcher,
        ledger: CategoryCandidateLedger,
        limit: int = CATEGORY_SEARCH_MAX_CANDIDATES,
    ) -> None:
        self.searcher = searcher
        self.ledger = ledger
        self.limit = max(1, min(CATEGORY_SEARCH_MAX_CANDIDATES, limit))
        self._cache: dict[str, CategorySearchResult] = {}
        self._lock = Lock()

    def execute(self, arguments: dict[str, Any], context: AiExecutionContext) -> dict[str, Any]:
        # 同轮多个工具调用共享账本和缓存；排队时间也计入原有任务截止时间。
        if not self._lock.acquire(timeout=context.bounded_timeout_seconds()):
            raise TimeoutError("类目批量查询等待超时")
        try:
            context.bounded_timeout_seconds()
            return dict(self._search(arguments["keywords"], context))
        finally:
            self._lock.release()

    def _search(self, raw_keywords: list[str], context: AiExecutionContext) -> CategoryKeywordSearchResult:
        keywords = list(dict.fromkeys(" ".join(word.split()).casefold() for word in raw_keywords))
        keywords = [word for word in keywords if word]
        if not keywords:
            raise AiToolExecutionError("CATEGORY_SEARCH_KEYWORDS_EMPTY", "请提供非空的商品关键词列表")
        pending = [word for word in keywords if word not in self._cache]

        def search(keyword: str) -> CategorySearchResult | Exception:
            try:
                context.bounded_timeout_seconds()
                return self.searcher.search_categories(keyword)
            except Exception as exc:
                return exc

        results: dict[str, CategorySearchResult | Exception] = dict(self._cache)
        if pending:
            # 这里只并发领域查询，不创建模型循环；provider 继续使用入口绑定的超时与 deadline。
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="category-search") as pool:
                futures = [pool.submit(copy_context().run, search, keyword) for keyword in pending]
                outcomes = [future.result() for future in futures]
            for keyword, outcome in zip(pending, outcomes, strict=True):
                if keyword not in self.ledger.attempts:
                    self.ledger.record_attempt(keyword)
                results[keyword] = outcome
                if isinstance(outcome, Exception):
                    self.ledger.record_error(outcome)
                else:
                    self._cache[keyword] = outcome

        errors: list[CategoryKeywordSearchError] = []
        successes: list[CategorySearchResult] = []
        for keyword in keywords:
            outcome = results[keyword]
            if isinstance(outcome, Exception):
                safe_error = isinstance(outcome, AiToolExecutionError)
                errors.append({
                    "keyword": keyword,
                    "code": str(outcome.code)[:160] if safe_error else "CATEGORY_SEARCH_FAILED",
                    "message": str(outcome)[:500] if safe_error else "该关键词查询失败，请调整关键词后重试",
                    "retryable": bool(outcome.retryable) if safe_error else True,
                })
            else:
                successes.append({
                    "keyword": keyword,
                    "source": outcome["source"],
                    "candidates": outcome["candidates"][:CATEGORY_SEARCH_CANDIDATES_PER_KEYWORD],
                })

        merged: dict[str, CategoryCandidate] = {}
        # 轮流取各词的同一排名，避免第一个词占满全部返回名额。
        for rank in range(CATEGORY_SEARCH_CANDIDATES_PER_KEYWORD):
            for result in successes:
                rows = result["candidates"]
                if rank >= len(rows):
                    continue
                row = rows[rank]
                category_id = str(row.get("category_id") or "").strip()
                if not category_id:
                    continue
                candidate = merged.setdefault(category_id, {**row, "matched_keywords": []})
                if result["keyword"] not in candidate["matched_keywords"]:
                    candidate["matched_keywords"].append(result["keyword"])

        new_candidates: list[CategoryCandidate] = []
        repeated_ids: list[str] = []
        for category_id, candidate in merged.items():
            if self.ledger.get(category_id) is None:
                new_candidates.append(candidate)
            else:
                repeated_ids.append(category_id)
        visible = new_candidates[:self.limit]
        visible_ids = {row["category_id"] for row in visible}
        known_ids = visible_ids | set(repeated_ids)
        for result in successes:
            # 只有模型实际看到的 ID 才允许被最终选择；缓存保留全量，方便缩小词组后查询。
            self.ledger.add_result({
                **result,
                "candidates": [row for row in result["candidates"] if row["category_id"] in known_ids],
            })
        return {
            "keywords": keywords,
            "candidates": [
                {
                    "category_id": str(row["category_id"])[:160],
                    "name": str(row.get("name") or "")[:500],
                    "path_segments": [str(segment)[:500] for segment in (row.get("path_segments") or [])[:20]],
                    "matched_keywords": row["matched_keywords"],
                }
                for row in visible
            ],
            "errors": errors,
            "repeated_candidate_ids": repeated_ids,
            "truncated": len(new_candidates) > len(visible),
        }


__all__ = ["CategoryKeywordBatchSearch"]

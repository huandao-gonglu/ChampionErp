"""类目关键词的批量只读查询；Agent 调用次数与生命周期仍由 Pydantic AI 管理。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
from threading import Lock
from typing import Any

from erp_web.marketplaces.category_provider import CategorySearcher
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.category_search_language import validate_category_keywords
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import (
    CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
    CATEGORY_SEARCH_MAX_OUTPUT_BYTES,
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
        search_language: str,
        limit: int | None = None,
        max_output_bytes: int = CATEGORY_SEARCH_MAX_OUTPUT_BYTES,
        include_binding_ids: bool = False,
    ) -> None:
        self.search_language = search_language
        self.searcher = searcher
        self.ledger = ledger
        self.limit = None if limit is None else max(1, limit)
        self.max_output_bytes = max_output_bytes
        self.include_binding_ids = include_binding_ids
        self._cache: dict[str, CategorySearchResult] = {}
        self._page_results: dict[tuple[str, ...], dict[str, CategorySearchResult | Exception]] = {}
        self._lock = Lock()

    def validate_arguments(self, arguments: dict[str, Any]) -> None:
        keywords = [word for word in arguments["keywords"] if word.strip()]
        if not keywords:
            raise AiToolExecutionError("CATEGORY_SEARCH_KEYWORDS_EMPTY", "请提供非空的商品关键词列表")
        if len({" ".join(word.split()).casefold() for word in keywords}) > CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL:
            raise AiToolExecutionError("CATEGORY_SEARCH_TOO_MANY_KEYWORDS", "品名、别称与补充词合并后最多 64 个，请只保留有依据的不同搜索方向。")
        validate_category_keywords(keywords, self.search_language)

    def execute(self, arguments: dict[str, Any], context: AiExecutionContext) -> dict[str, Any]:
        self.validate_arguments(arguments)
        # 同轮多个工具调用共享账本和缓存；排队时间也计入原有任务截止时间。
        if not self._lock.acquire(timeout=context.bounded_timeout_seconds()):
            raise TimeoutError("类目批量查询等待超时")
        try:
            context.bounded_timeout_seconds()
            return dict(self._search(arguments["keywords"], context, offset=arguments.get("offset", 0)))
        finally:
            self._lock.release()

    def _search(self, raw_keywords: list[str], context: AiExecutionContext, *, offset: int = 0) -> CategoryKeywordSearchResult:
        keywords = list(dict.fromkeys(" ".join(word.split()).casefold() for word in raw_keywords))
        keywords = [word for word in keywords if word]
        if not keywords:
            raise AiToolExecutionError("CATEGORY_SEARCH_KEYWORDS_EMPTY", "请提供非空的商品关键词列表")
        # 续页固定首轮的成功和失败集合，避免某个失败词恢复后改变排序、跳过候选。
        snapshot = self._page_results.get(tuple(keywords)) if offset else None
        results: dict[str, CategorySearchResult | Exception] = dict(snapshot if snapshot is not None else self._cache)
        pending = [word for word in keywords if word not in results]

        def search(keyword: str) -> CategorySearchResult | Exception:
            try:
                context.bounded_timeout_seconds()
                return self.searcher.search_categories(keyword)
            except Exception as exc:
                return exc

        if pending:
            # 共享语料的平台先执行一次准备，后续关键词复用本次成功或失败。
            blocked = None
            batches = ([pending[:1]] + [pending[i:i+3] for i in range(1,len(pending),3)]) if getattr(self.searcher,"shares_corpus",False) else [pending[i:i+3] for i in range(0,len(pending),3)]
            with ThreadPoolExecutor(max_workers=3,thread_name_prefix="category-search") as pool:
                for batch in batches:
                    outcomes = [blocked]*len(batch) if blocked else [f.result() for f in [pool.submit(copy_context().run,search,word) for word in batch]]
                    for keyword,outcome in zip(batch,outcomes,strict=True):
                        if keyword not in self.ledger.attempts:
                            self.ledger.record_attempt(keyword)
                        results[keyword] = outcome
                        if isinstance(outcome,Exception):
                            self.ledger.record_error(outcome)
                            details = getattr(outcome,"details",{}) or {}
                            code = str(getattr(outcome,"code",""))
                            if getattr(self.searcher,"shares_corpus",False) or details.get("http_status") in (401,403,420,429) or any(part in code for part in ("AUTH","RATE_LIMIT","CREDENTIAL","DISABLED","REVOKED")):
                                blocked = outcome
                        else:
                            self._cache[keyword] = outcome

        self._page_results[tuple(keywords)] = {word: results[word] for word in keywords}

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
                    "candidates": outcome["candidates"],
                })

        merged: dict[str, CategoryCandidate] = {}
        rrf_scores: dict[str, float] = {}
        first_seen_order: dict[str, int] = {}
        # RRF（Reciprocal Rank Fusion）+ 多词命中加权；单词时退化为严格原排序。
        for rank in range(max((len(result["candidates"]) for result in successes), default=0)):
            for result in successes:
                rows = result["candidates"]
                if rank >= len(rows):
                    continue
                row = rows[rank]
                category_id = str(row.get("category_id") or "").strip()
                if not category_id:
                    continue
                if category_id not in merged:
                    merged[category_id] = {**row, "matched_keywords": []}
                    first_seen_order[category_id] = len(first_seen_order)
                    rrf_scores[category_id] = 0.0
                candidate = merged[category_id]
                if result["keyword"] not in candidate["matched_keywords"]:
                    candidate["matched_keywords"].append(result["keyword"])
                    rrf_scores[category_id] += 1.0 / (60.0 + rank)
        for category_id, candidate in merged.items():
            hits = len(candidate.get("matched_keywords") or ())
            if hits > 1:
                rrf_scores[category_id] += 0.005 * (hits - 1)
        ordered_items = sorted(
            merged.items(),
            key=lambda item: (-rrf_scores[item[0]], first_seen_order[item[0]]),
        )

        if offset < 0 or offset > len(merged):
            raise AiToolExecutionError("CATEGORY_SEARCH_OFFSET_INVALID", "分页位置超出本组关键词的候选范围，请使用相同关键词与返回的 next_offset。")
        output: CategoryKeywordSearchResult = {
            "keywords": keywords,
            "candidates": [],
            "errors": errors,
            "repeated_candidate_ids": [],
            "truncated": False,
            "has_more": False,
            "next_offset": None,
            "total_candidate_count": len(merged),
            "returned_candidate_count": 0,
            "remaining_candidate_count": len(merged) - offset,
            "query_candidate_counts": {
                result["keyword"]: len({row["category_id"] for row in result["candidates"] if row.get("category_id")})
                for result in successes
            },
            "search_language": self.search_language,
        }
        # 为分页计数预留足够空间；大小计算与 Tool Runtime 的 UTF-8 JSON 编码一致。
        used_bytes = self._json_size(output) + 128
        page_count = 0
        for category_id, row in ordered_items[offset:]:
            context.bounded_timeout_seconds()
            repeated = self.ledger.get(category_id) is not None
            candidate = {
                "category_id": category_id,
                "name": str(row.get("name") or ""),
                "path_segments": list(row.get("path_segments") or []),
                "matched_keywords": row["matched_keywords"],
            }
            if row.get("name_zh"):
                candidate["name_zh"] = str(row["name_zh"])
            if row.get("path_zh"):
                candidate["path_zh"] = str(row["path_zh"])
            if row.get("forbidden"):
                candidate["forbidden"] = True
                reason = str(row.get("forbidden_reason") or "").strip()
                if reason:
                    candidate["forbidden_reason"] = reason
            if self.include_binding_ids:
                candidate.update({key: row[key] for key in ("description_category_id", "type_id") if key in row})
            item = category_id if repeated else candidate
            item_bytes = self._json_size(item) + 1
            if used_bytes + item_bytes > self.max_output_bytes or (self.limit is not None and page_count >= self.limit):
                break
            used_bytes += item_bytes
            if repeated:
                output["repeated_candidate_ids"].append(category_id)
            else:
                output["candidates"].append(candidate)
            page_count += 1
        if used_bytes > self.max_output_bytes or (page_count == 0 and offset < len(merged)):
            raise AiToolExecutionError("CATEGORY_SEARCH_PAGE_TOO_LARGE", "单个类目或关键词统计超过工具输出预算，请缩小关键词组。")
        end = offset + page_count
        has_more = end < len(merged)
        output.update(
            returned_candidate_count=page_count,
            remaining_candidate_count=len(merged) - end,
            has_more=has_more,
            truncated=has_more,
            next_offset=end if has_more else None,
        )
        known_ids = {row["category_id"] for row in output["candidates"]} | set(output["repeated_candidate_ids"])
        for result in successes:
            # 只有实际交付的候选可选择；缓存保留完整结果，续页不重发平台查询。
            self.ledger.add_result({
                **result,
                "candidates": [row for row in result["candidates"] if row["category_id"] in known_ids],
            })
        return output

    @staticmethod
    def _json_size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


__all__ = ["CategoryKeywordBatchSearch"]

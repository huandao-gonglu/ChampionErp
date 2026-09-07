from __future__ import annotations

from typing import Any
from contextvars import ContextVar
from threading import Barrier, Lock

import pytest

from erp_web.runtime_units.category_tools import (
    CATEGORY_NAVIGATION_TOOL_DEFINITIONS,
    CATEGORY_SEARCH_TOOL_DEFINITIONS,
    CategoryCandidateLedger,
    build_category_match_toolset,
)
from erp_web.schemas.ai_tools import AiToolCommand, AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_tool_runtime import AiToolRuntime


class BoundSearcher:
    def __init__(self) -> None:
        self.keywords: list[str] = []

    def search_categories(self, keyword: str) -> dict[str, Any]:
        self.keywords.append(keyword)
        return {
            "keyword": keyword,
            "source": "mercadolibre_api",
            "candidates": [
                {
                    "category_id": "MLM-FAN",
                    "name": "Ventiladores",
                    "path_segments": ["Hogar", "Ventiladores"],
                    "search_rank": 0,
                    "publishable": True,
                    "platform": "mercadolibre",
                    "site": "MLM",
                }
            ],
        }


class BoundNavigator(BoundSearcher):
    def __init__(self) -> None:
        super().__init__()
        self.parents: list[list[str]] = []

    @staticmethod
    def _node(
        node_id: str,
        name: str,
        *,
        level: str,
        depth: int,
        parent_id: str,
        path: list[str],
        child_count: int,
    ) -> dict[str, Any]:
        node = {
            "node_id": node_id,
            "name": name,
            "level": level,
            "depth": depth,
            "parent_id": parent_id,
            "path_segments": path,
            "child_count": child_count,
            "publishable": level == "product_type",
            "platform": "ozon",
            "site": "global",
        }
        if level == "product_type":
            node.update(
                category_id=node_id,
                description_category_id=parent_id,
                type_id=node_id,
            )
        return node

    def root_categories(self) -> dict[str, Any]:
        return {
            "parent_ids": [],
            "source": "test",
            "nodes": [
                self._node(
                    "root-auto",
                    "Автотовары",
                    level="branch",
                    depth=1,
                    parent_id="",
                    path=["Автотовары"],
                    child_count=1,
                )
            ],
        }

    def browse_categories(self, parent_ids: list[str]) -> dict[str, Any]:
        self.parents.append(parent_ids)
        if parent_ids == ["root-auto"]:
            nodes = [
                self._node(
                    "group-radio",
                    "Автомагнитолы",
                    level="branch",
                    depth=2,
                    parent_id="root-auto",
                    path=["Автотовары", "Автомагнитолы"],
                    child_count=1,
                )
            ]
        else:
            nodes = [
                self._node(
                    "971326576",
                    "Аксессуар для автомагнитолы",
                    level="product_type",
                    depth=3,
                    parent_id="group-radio",
                    path=[
                        "Автотовары",
                        "Автомагнитолы",
                        "Аксессуар для автомагнитолы",
                    ],
                    child_count=0,
                )
            ]
        return {"parent_ids": parent_ids, "source": "test", "nodes": nodes}


def context() -> AiExecutionContext:
    return AiExecutionContext.create(
        timeout_seconds=10,
        budget_profile="category.match.default",
        permissions={"category.read"},
    )


def batch_runtime(searcher, ledger):
    return AiToolRuntime(
        toolset=build_category_match_toolset(searcher=searcher, ledger=ledger).toolset,
        execution_context=context(),
    )


def batch_command(keywords, call_id="batch"):
    return AiToolCommand(
        call_id=call_id,
        tool_name="search_categories",
        tool_version="3",
        arguments={"keywords": keywords},
        round=1,
    )


def test_batch_accepts_twelve_keywords_and_reuses_normalized_queries() -> None:
    searcher = BoundSearcher()
    ledger = CategoryCandidateLedger()
    runtime = batch_runtime(searcher, ledger)
    keywords = [f"fan {index}" for index in range(12)]
    first = runtime.execute(batch_command([*keywords, " FAN   0 ", " "]))
    second = runtime.execute(batch_command([" Fan  0 ", "fan 11"], "repeat"))
    assert first.ok and second.ok
    assert sorted(searcher.keywords) == sorted(keywords)
    assert ledger.search_count == 12
    assert list(first.output["candidates"][0]["matched_keywords"]) == keywords
    assert len(first.output["candidates"]) == 1
    assert not second.output["candidates"]
    assert list(second.output["repeated_candidate_ids"]) == ["MLM-FAN"]
    assert ledger.get("MLM-FAN") is not None


def test_batch_keeps_successes_and_reports_each_failed_keyword() -> None:
    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            if keyword == "failed":
                raise AiToolExecutionError("CATEGORY_SEARCH_TIMEOUT", "该词查询超时", retryable=True)
            if keyword == "secret":
                raise RuntimeError("不应暴露的内部响应或凭据")
            return super().search_categories(keyword)

    ledger = CategoryCandidateLedger()
    result = batch_runtime(Searcher(), ledger).execute(batch_command(["fan", "failed", "secret"]))
    assert result.ok
    assert result.output["candidates"][0]["category_id"] == "MLM-FAN"
    assert result.output["errors"][0] == {
        "keyword": "failed", "code": "CATEGORY_SEARCH_TIMEOUT",
        "message": "该词查询超时", "retryable": True,
    }
    assert result.output["errors"][1]["keyword"] == "secret"
    assert "不应暴露" not in str(result.output)
    assert ledger.successful_search_count == 1


def test_batch_bounds_concurrency_and_preserves_request_context() -> None:
    marker = ContextVar("category_test_marker", default="missing")
    token = marker.set("当前请求")
    barrier = Barrier(3, timeout=3)
    lock = Lock()
    active = maximum = 0

    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            nonlocal active, maximum
            assert marker.get() == "当前请求"
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                barrier.wait()
                return super().search_categories(keyword)
            finally:
                with lock:
                    active -= 1

    try:
        result = batch_runtime(Searcher(), CategoryCandidateLedger()).execute(
            batch_command([f"fan {index}" for index in range(12)])
        )
    finally:
        marker.reset(token)
    assert result.ok and not result.output["errors"]
    assert maximum == 3


def test_truncated_candidates_are_not_selectable_until_returned_by_narrower_query() -> None:
    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            result = super().search_categories(keyword)
            result["candidates"] = [
                {**result["candidates"][0], "category_id": f"{keyword}-{rank}"}
                for rank in range(8)
            ]
            return result

    searcher = Searcher()
    ledger = CategoryCandidateLedger()
    runtime = batch_runtime(searcher, ledger)
    result = runtime.execute(batch_command(["a", "b", "c", "d"]))
    assert result.ok and result.output["truncated"]
    ids = [row["category_id"] for row in result.output["candidates"]]
    assert len(ids) == 24
    assert ids[:4] == ["a-0", "b-0", "c-0", "d-0"]
    assert ledger.get("d-7") is None
    narrowed = runtime.execute(batch_command(["d"], "narrow"))
    assert narrowed.ok and not narrowed.output["truncated"]
    assert "d-7" in [row["category_id"] for row in narrowed.output["candidates"]]
    assert ledger.get("d-7") is not None
    assert len(searcher.keywords) == ledger.search_count == 4


def test_followup_omits_repeated_details_and_reserves_slots_for_new_candidates() -> None:
    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            result = super().search_categories(keyword)
            result["candidates"] = [
                {**result["candidates"][0], "category_id": f"{keyword}-{rank}"}
                for rank in range(8)
            ]
            return result

    ledger = CategoryCandidateLedger()
    runtime = batch_runtime(Searcher(), ledger)
    first = runtime.execute(batch_command(["a", "b", "c"]))
    followup = runtime.execute(batch_command(["a", "b", "c", "d"], "followup"))
    assert first.ok and followup.ok
    assert {row["category_id"] for row in followup.output["candidates"]} == {
        f"d-{rank}" for rank in range(8)
    }
    assert set(followup.output["repeated_candidate_ids"]) == {
        row["category_id"] for row in first.output["candidates"]
    }
    assert not followup.output["truncated"]
    assert ledger.get("a-0") is not None


@pytest.mark.parametrize("arguments", [
    {"keyword": "fan"}, {"keywords": "fan"}, {"keywords": []},
    {"keywords": [""]}, {"keywords": ["fan"] * 65},
])
def test_batch_rejects_invalid_input_before_search(arguments) -> None:
    searcher = BoundSearcher()
    result = batch_runtime(searcher, CategoryCandidateLedger()).execute(AiToolCommand(
        call_id="invalid", tool_name="search_categories", tool_version="3",
        arguments=arguments, round=1,
    ))
    assert not result.ok
    assert searcher.keywords == []


def test_category_toolset_only_exposes_keyword_search() -> None:
    searcher = BoundSearcher()
    ledger = CategoryCandidateLedger()
    bundle = build_category_match_toolset(searcher=searcher, ledger=ledger)
    toolset = bundle.toolset
    runtime = AiToolRuntime(
        toolset=toolset,
        execution_context=context(),
        max_tool_calls=3,
        max_output_bytes=32 * 1024,
    )
    command = AiToolCommand(
        call_id="call-search",
        tool_name="search_categories",
        tool_version="3",
        arguments={"keywords": ["ventilador"]},
        round=1,
    )

    first = runtime.execute(command)
    duplicate = runtime.execute(command)

    assert first.ok is True
    assert duplicate.ok is True
    assert duplicate.deduplicated is True
    assert searcher.keywords == ["ventilador"]
    assert ledger.search_count == 1
    assert ledger.get("MLM-FAN") is not None
    assert bundle.retrieval_mode == "keyword_search"
    assert bundle.initial_options == []
    assert toolset.toolset_id == "category.search"
    assert set(toolset.bindings) == {"search_categories"}
    assert [item.name for item in CATEGORY_SEARCH_TOOL_DEFINITIONS] == [
        "search_categories"
    ]
    definition = CATEGORY_SEARCH_TOOL_DEFINITIONS[0].to_dict()
    assert set(definition["input_schema"]["properties"]) == {"keywords"}
    assert "platform" not in str(definition)
    assert "site" not in str(definition)


def test_tool_output_hides_bound_scope_and_provider_metadata() -> None:
    result = AiToolRuntime(
        toolset=build_category_match_toolset(
            searcher=BoundSearcher(),
            ledger=CategoryCandidateLedger(),
        ).toolset,
        execution_context=context(),
    ).execute(
        AiToolCommand(
            call_id="call-search",
            tool_name="search_categories",
            tool_version="3",
            arguments={"keywords": ["ventilador"]},
            round=1,
        )
    )

    assert result.ok is True
    output = result.to_dict()["output"]
    assert output == {
        "keywords": ["ventilador"],
        "candidates": [
            {
                "category_id": "MLM-FAN",
                "name": "Ventiladores",
                "path_segments": ["Hogar", "Ventiladores"],
                "matched_keywords": ["ventilador"],
            }
        ],
        "errors": [],
        "repeated_candidate_ids": [],
        "truncated": False,
    }


def test_tool_rejects_platform_or_site_arguments() -> None:
    result = AiToolRuntime(
        toolset=build_category_match_toolset(
            searcher=BoundSearcher(),
            ledger=CategoryCandidateLedger(),
        ).toolset,
        execution_context=context(),
    ).execute(
        AiToolCommand(
            call_id="call-search",
            tool_name="search_categories",
            tool_version="3",
            arguments={
                "keywords": ["ventilador"],
                "platform": "ozon",
                "site": "global",
            },
            round=1,
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error["code"] == "TOOL_INPUT_SCHEMA_INVALID"


def test_ozon_navigation_exposes_roots_then_records_only_leaf_candidates() -> None:
    navigator = BoundNavigator()
    ledger = CategoryCandidateLedger()
    bundle = build_category_match_toolset(searcher=navigator, ledger=ledger)
    runtime = AiToolRuntime(
        toolset=bundle.toolset,
        execution_context=context(),
        max_tool_calls=4,
        max_output_bytes=128 * 1024,
    )

    assert bundle.retrieval_mode == "tree_navigation"
    assert bundle.initial_options[0]["node_id"] == "root-auto"
    assert set(bundle.toolset.bindings) == {"browse_categories"}
    assert [item.name for item in CATEGORY_NAVIGATION_TOOL_DEFINITIONS] == [
        "browse_categories"
    ]

    branch = runtime.execute(
        AiToolCommand(
            call_id="browse-root",
            tool_name="browse_categories",
            tool_version="1",
            arguments={"parent_ids": ["root-auto"]},
            round=1,
        )
    )
    assert branch.ok is True
    assert branch.to_dict()["output"]["nodes"][0]["level"] == "branch"
    assert ledger.candidates() == []

    leaf = runtime.execute(
        AiToolCommand(
            call_id="browse-group",
            tool_name="browse_categories",
            tool_version="1",
            arguments={"parent_ids": ["group-radio"]},
            round=2,
        )
    )
    assert leaf.ok is True
    output = leaf.to_dict()["output"]
    assert output["nodes"][0] == {
        "node_id": "971326576",
        "name": "Аксессуар для автомагнитолы",
        "level": "product_type",
        "depth": 3,
        "parent_id": "group-radio",
        "path_segments": [
            "Автотовары",
            "Автомагнитолы",
            "Аксессуар для автомагнитолы",
        ],
        "child_count": 0,
        "category_id": "971326576",
    }
    assert output["navigation_calls_used"] == 2
    assert output["navigation_calls_remaining"] == 2
    assert ledger.get("971326576") is not None
    assert navigator.parents == [["root-auto"], ["group-radio"]]


def test_navigation_rejects_nodes_not_returned_by_the_current_run() -> None:
    bundle = build_category_match_toolset(
        searcher=BoundNavigator(),
        ledger=CategoryCandidateLedger(),
    )
    binding = bundle.toolset.get("browse_categories")
    assert binding is not None

    try:
        binding.executor({"parent_ids": ["invented-node"]}, context())
    except RuntimeError as exc:
        assert "真实返回的 branch node_id" in str(exc)
    else:
        raise AssertionError("未拒绝当前运行从未返回过的树节点")

    binding.executor({"parent_ids": ["root-auto"]}, context())
    try:
        binding.executor({"parent_ids": ["root-auto"]}, context())
    except RuntimeError as exc:
        assert "分支已经展开" in str(exc)
    else:
        raise AssertionError("未拒绝重复展开的树分支")

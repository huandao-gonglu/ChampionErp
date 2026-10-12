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
        toolset=build_category_match_toolset(searcher=searcher, ledger=ledger, platform="mercadolibre", site="MLM").toolset,
        execution_context=context(),
    )


def batch_command(keywords, call_id="batch", *, ledger=None):
    return AiToolCommand(
        call_id=call_id,
        tool_name="search_categories",
        tool_version="5",
        arguments={"alternative_names": [], "product_identity": "桌面风扇，有电机和扇叶", "product_type": keywords[0], "keywords": keywords},
        round=1,
    )


def test_batch_accepts_twelve_keywords_and_reuses_normalized_queries() -> None:
    searcher = BoundSearcher()
    ledger = CategoryCandidateLedger()
    runtime = batch_runtime(searcher, ledger)
    keywords = [f"fan {index}" for index in range(12)]
    first = runtime.execute(batch_command([*keywords, " FAN   0 ", " "], ledger=ledger))
    second = runtime.execute(batch_command([" Fan  0 ", "fan 11"], "repeat", ledger=ledger))
    assert first.ok and second.ok
    assert sorted(searcher.keywords) == sorted(keywords)
    assert ledger.search_count == 12
    assert list(first.output["candidates"][0]["matched_keywords"]) == keywords
    assert len(first.output["candidates"]) == 1
    assert not second.output["candidates"]
    assert list(second.output["repeated_candidate_ids"]) == ["MLM-FAN"]
    assert ledger.get("MLM-FAN") is not None


def test_product_name_and_complementary_names_execute_as_one_batch() -> None:
    searcher = BoundSearcher()
    ledger = CategoryCandidateLedger()
    runtime = batch_runtime(searcher, ledger)
    result = runtime.execute(AiToolCommand(
        call_id="four-names", tool_name="search_categories", tool_version="5", round=1,
        arguments={
            "product_identity": "挂耳式纺织面罩，用于遮阳和防尘",
            "product_type": "mascarilla",
            "alternative_names": ["cubrebocas"],
            "keywords": ["mascarilla de tela", "protector facial solar"],
        },
    ))
    assert result.ok
    assert runtime.unique_call_count == 1
    assert set(searcher.keywords) == {
        "mascarilla", "cubrebocas", "mascarilla de tela", "protector facial solar",
    }
    assert result.output["search_language"] == "es"


def test_wrong_language_in_any_merged_name_rejects_entire_batch() -> None:
    searcher = BoundSearcher()
    runtime = batch_runtime(searcher, CategoryCandidateLedger())
    result = runtime.execute(AiToolCommand(
        call_id="wrong-alias", tool_name="search_categories", tool_version="5", round=1,
        arguments={"product_identity": "风扇", "product_type": "ventilador",
                   "alternative_names": ["风扇"], "keywords": ["ventilador USB"]},
    ))
    assert not result.ok
    assert result.error["code"] == "CATEGORY_SEARCH_LANGUAGE_MISMATCH"
    assert searcher.keywords == []


def test_batch_keeps_successes_and_reports_each_failed_keyword() -> None:
    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            if keyword == "failed":
                raise AiToolExecutionError("CATEGORY_SEARCH_TIMEOUT", "该词查询超时", retryable=True)
            if keyword == "secret":
                raise RuntimeError("不应暴露的内部响应或凭据")
            return super().search_categories(keyword)

    ledger = CategoryCandidateLedger()
    result = batch_runtime(Searcher(), ledger).execute(batch_command(["fan", "failed", "secret"], ledger=ledger))
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


def test_batch_returns_hundreds_of_candidates_without_count_truncation() -> None:
    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            result = super().search_categories(keyword)
            result["candidates"] = [
                {**result["candidates"][0], "category_id": f"{keyword}-{rank}"}
                for rank in range(100)
            ]
            return result

    searcher = Searcher()
    ledger = CategoryCandidateLedger()
    result = batch_runtime(searcher, ledger).execute(batch_command(["a", "b", "c"], ledger=ledger))
    assert result.ok, result.error
    assert len(result.output["candidates"]) == 300
    assert result.output["total_candidate_count"] == result.output["returned_candidate_count"] == 300
    assert result.output["query_candidate_counts"] == {"a": 100, "b": 100, "c": 100}
    assert result.output["next_offset"] is None
    assert not result.output["has_more"] and not result.output["truncated"]
    assert ledger.get("c-99") is not None
    assert len(ledger.candidates()) == 300


def test_byte_pages_preserve_all_candidates_and_only_register_delivered_ids() -> None:
    import json
    from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch

    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            result = super().search_categories(keyword)
            result["candidates"] = [
                {**result["candidates"][0], "category_id": str(rank),
                 "name": "完整俄文候选名称" * 10, "path_segments": ["厨房", "完整路径" * 30]}
                for rank in range(40)
            ]
            return result

    searcher = Searcher()
    ledger = CategoryCandidateLedger()
    batch = CategoryKeywordBatchSearch(searcher=searcher, ledger=ledger, search_language="es", max_output_bytes=4096)
    delivered = []
    offset = 0
    while True:
        result = batch.execute({"keywords": ["fan", "ventilador"], "offset": offset}, context())
        assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 4096
        page_ids = [row["category_id"] for row in result["candidates"]]
        delivered.extend(page_ids)
        assert result["total_candidate_count"] == 40
        assert result["returned_candidate_count"] == len(page_ids) > 0
        assert result["remaining_candidate_count"] == 40 - len(delivered)
        if not result["has_more"]:
            assert result["next_offset"] is None
            break
        assert result["next_offset"] > offset
        assert ledger.get("39") is None
        offset = result["next_offset"]
    assert delivered == [str(index) for index in range(40)]
    assert searcher.keywords == ["fan", "ventilador"]
    assert ledger.get("39") is not None
    repeated = batch.execute({"keywords": ["fan", "ventilador"]}, context())
    assert repeated["candidates"] == []
    assert repeated["repeated_candidate_ids"] == delivered
    assert not repeated["has_more"]



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
    first = runtime.execute(batch_command(["a", "b", "c"], ledger=ledger))
    followup = runtime.execute(batch_command(["a", "b", "c", "d"], "followup", ledger=ledger))
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
        call_id="invalid", tool_name="search_categories", tool_version="5",
        arguments=arguments, round=1,
    ))
    assert not result.ok
    assert searcher.keywords == []


def test_category_toolset_only_exposes_keyword_search() -> None:
    searcher = BoundSearcher()
    ledger = CategoryCandidateLedger()
    bundle = build_category_match_toolset(searcher=searcher, ledger=ledger, platform="mercadolibre", site="MLM")
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
        tool_version="5",
        arguments={"alternative_names": [], "product_identity": "桌面风扇，有电机和扇叶", "product_type": "ventilador", "keywords": ["ventilador"]},
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
    assert set(definition["input_schema"]["properties"]) == {"product_type", "alternative_names", "keywords", "product_identity", "offset"}
    assert "platform" not in str(definition)
    assert "site" not in str(definition)


def test_tool_output_hides_bound_scope_and_provider_metadata() -> None:
    result = AiToolRuntime(
        toolset=build_category_match_toolset(
            platform="mercadolibre", site="MLM",
            searcher=BoundSearcher(),
            ledger=CategoryCandidateLedger(),
        ).toolset,
        execution_context=context(),
    ).execute(
        AiToolCommand(
            call_id="call-search",
            tool_name="search_categories",
            tool_version="5",
            arguments={"alternative_names": [], "product_identity": "桌面风扇，有电机和扇叶", "product_type": "ventilador", "keywords": ["ventilador"]},
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
        "search_language": "es",
        "remaining_candidate_count": 0,
        "query_candidate_counts": {"ventilador": 1},
        "total_candidate_count": 1,
        "returned_candidate_count": 1,
        "has_more": False,
        "next_offset": None,
    }


def test_tool_rejects_platform_or_site_arguments() -> None:
    result = AiToolRuntime(
        toolset=build_category_match_toolset(
            platform="mercadolibre", site="MLM",
            searcher=BoundSearcher(),
            ledger=CategoryCandidateLedger(),
        ).toolset,
        execution_context=context(),
    ).execute(
        AiToolCommand(
            call_id="call-search",
            tool_name="search_categories",
            tool_version="5",
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
    bundle = build_category_match_toolset(searcher=navigator, ledger=ledger, platform="ozon", site="global")
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
        platform="mercadolibre", site="MLM",
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

def test_focused_pagination_fits_runtime_budget_and_freezes_failed_queries() -> None:
    from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch

    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            self.keywords.append(keyword)
            if keyword == "failed":
                if self.keywords.count(keyword) > 1:
                    raise AssertionError("续页不得重新查询失败词而改变候选排序")
                raise AiToolExecutionError("CATEGORY_SEARCH_TIMEOUT", "该词查询超时", retryable=True)
            return {"keyword": keyword, "source": "test", "candidates": [
                {"category_id": str(index), "name": "完整类目", "path_segments": ["厨房", "收纳"]}
                for index in range(30)
            ]}

    searcher = Searcher()
    ledger = CategoryCandidateLedger()
    batch = CategoryKeywordBatchSearch(searcher=searcher, ledger=ledger, search_language="es", max_output_bytes=2000)
    first = batch.execute({"keywords": ["fan", "failed"]}, context())
    assert first["has_more"]
    second = batch.execute({"keywords": ["fan", "failed"], "offset": first["next_offset"]}, context())
    assert second["total_candidate_count"] == first["total_candidate_count"] == 30
    assert second["errors"] == first["errors"]
    assert searcher.keywords.count("failed") == 1
    assert not ({row["category_id"] for row in first["candidates"]} & {row["category_id"] for row in second["candidates"]})


def test_large_search_output_pages_before_runtime_rejects_it() -> None:
    import json
    from erp_web.schemas.category import CATEGORY_SEARCH_MAX_OUTPUT_BYTES

    class Searcher(BoundSearcher):
        def search_categories(self, keyword):
            result = super().search_categories(keyword)
            result["candidates"] = [
                {**result["candidates"][0], "category_id": str(index), "name": "类目" * 200,
                 "path_segments": ["完整路径" * 100]}
                for index in range(300)
            ]
            return result

    ledger = CategoryCandidateLedger()
    runtime = AiToolRuntime(
        toolset=build_category_match_toolset(searcher=Searcher(), ledger=ledger, platform="mercadolibre", site="MLM").toolset,
        execution_context=context(), max_tool_calls=8, max_output_bytes=CATEGORY_SEARCH_MAX_OUTPUT_BYTES,
    )
    ids = []
    offset = 0
    while True:
        command = batch_command(["fan"], call_id=f"page-{offset}")
        result = runtime.execute(AiToolCommand(
            call_id=command.call_id, tool_name=command.tool_name, tool_version=command.tool_version,
            arguments={**command.arguments, "offset": offset}, round=1,
        ))
        assert result.ok, result.error
        output = result.to_dict()["output"]
        assert len(json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode()) <= CATEGORY_SEARCH_MAX_OUTPUT_BYTES
        ids.extend(row["category_id"] for row in output["candidates"])
        if not output["has_more"]:
            break
        offset = output["next_offset"]
        assert ledger.get("299") is None
    assert ids == [str(index) for index in range(300)]

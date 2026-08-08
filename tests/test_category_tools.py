from __future__ import annotations

from typing import Any

from erp_web.runtime_units.category_tools import (
    CATEGORY_NAVIGATION_TOOL_DEFINITIONS,
    CATEGORY_SEARCH_TOOL_DEFINITIONS,
    CategoryCandidateLedger,
    build_category_match_toolset,
)
from erp_web.schemas.ai_tools import AiToolCommand
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_tool_runtime import AiToolRuntime


class Recorder:
    conversation_id = "aic-category-tools"

    def record(self, event_type: str, **payload: Any) -> None:
        pass

    def emit(self, event_type: str, **payload: Any) -> None:
        pass

    def emit_custom(self, name: str, value: Any) -> None:
        pass

    def emit_text_delta(self, delta: str) -> None:
        pass

    def finish_assistant_message(self, raw_text: str = "") -> None:
        pass

    def finish(self, result: Any) -> None:
        pass

    def fail(self, error: Exception) -> None:
        pass


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


def test_category_toolset_only_exposes_keyword_search() -> None:
    searcher = BoundSearcher()
    ledger = CategoryCandidateLedger()
    bundle = build_category_match_toolset(searcher=searcher, ledger=ledger)
    toolset = bundle.toolset
    runtime = AiToolRuntime(
        toolset=toolset,
        execution_context=context(),
        recorder=Recorder(),
        max_tool_calls=3,
        max_output_bytes=32 * 1024,
    )
    command = AiToolCommand(
        call_id="call-search",
        tool_name="search_categories",
        tool_version="1",
        arguments={"keyword": "ventilador"},
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
    assert set(definition["input_schema"]["properties"]) == {"keyword"}
    assert "platform" not in str(definition)
    assert "site" not in str(definition)


def test_tool_output_hides_bound_scope_and_provider_metadata() -> None:
    result = AiToolRuntime(
        toolset=build_category_match_toolset(
            searcher=BoundSearcher(),
            ledger=CategoryCandidateLedger(),
        ).toolset,
        execution_context=context(),
        recorder=Recorder(),
    ).execute(
        AiToolCommand(
            call_id="call-search",
            tool_name="search_categories",
            tool_version="1",
            arguments={"keyword": "ventilador"},
            round=1,
        )
    )

    assert result.ok is True
    output = result.to_dict()["output"]
    assert output == {
        "keyword": "ventilador",
        "candidates": [
            {
                "category_id": "MLM-FAN",
                "name": "Ventiladores",
                "path_segments": ["Hogar", "Ventiladores"],
            }
        ],
        "searches_used": 1,
        "searches_remaining": 2,
        "must_finalize": False,
    }


def test_tool_rejects_platform_or_site_arguments() -> None:
    result = AiToolRuntime(
        toolset=build_category_match_toolset(
            searcher=BoundSearcher(),
            ledger=CategoryCandidateLedger(),
        ).toolset,
        execution_context=context(),
        recorder=Recorder(),
    ).execute(
        AiToolCommand(
            call_id="call-search",
            tool_name="search_categories",
            tool_version="1",
            arguments={
                "keyword": "ventilador",
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
        recorder=Recorder(),
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

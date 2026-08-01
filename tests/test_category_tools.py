from __future__ import annotations

from typing import Any

from erp_web.runtime_units.category_tools import (
    CATEGORY_SEARCH_TOOL_DEFINITIONS,
    CategoryCandidateLedger,
    build_category_search_toolset,
)
from erp_web.schemas.ai_tools import AiToolCall
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


def context() -> AiExecutionContext:
    return AiExecutionContext.create(
        timeout_seconds=10,
        budget_profile="category.match.default",
        permissions={"category.read"},
    )


def test_category_toolset_only_exposes_keyword_search() -> None:
    searcher = BoundSearcher()
    ledger = CategoryCandidateLedger()
    toolset = build_category_search_toolset(searcher=searcher, ledger=ledger)
    runtime = AiToolRuntime(
        toolset=toolset,
        execution_context=context(),
        recorder=Recorder(),
        max_tool_calls=3,
        max_output_bytes=32 * 1024,
    )
    call = AiToolCall(
        call_id="call-search",
        tool_name="search_categories",
        tool_version="1",
        arguments={"keyword": "ventilador"},
        round=1,
    )

    first = runtime.execute(call)
    duplicate = runtime.execute(call)

    assert first.ok is True
    assert duplicate.ok is True
    assert duplicate.deduplicated is True
    assert searcher.keywords == ["ventilador"]
    assert ledger.search_count == 1
    assert ledger.get("MLM-FAN") is not None
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
        toolset=build_category_search_toolset(
            searcher=BoundSearcher(),
            ledger=CategoryCandidateLedger(),
        ),
        execution_context=context(),
        recorder=Recorder(),
    ).execute(
        AiToolCall(
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
    }


def test_tool_rejects_platform_or_site_arguments() -> None:
    result = AiToolRuntime(
        toolset=build_category_search_toolset(
            searcher=BoundSearcher(),
            ledger=CategoryCandidateLedger(),
        ),
        execution_context=context(),
        recorder=Recorder(),
    ).execute(
        AiToolCall(
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

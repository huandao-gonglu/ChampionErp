from __future__ import annotations

from collections import deque
import time
from typing import Any

from erp_web.facades.category_match_facade import match_category
from erp_web.runtime_units.category_searchers import CategorySearchError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_agent_factory import AiAgentExecutionError
from erp_web.services.category_match_agent_service import CategoryMatchAgentRun


def candidate(
    category_id: str,
    *,
    platform: str = "mercadolibre",
    site: str = "MLM",
    publishable: bool = True,
    description_category_id: str = "",
    type_id: str = "",
) -> dict[str, Any]:
    row = {
        "category_id": category_id,
        "name": f"Category {category_id}",
        "path_segments": ["Home", f"Category {category_id}"],
        "search_rank": 0,
        "publishable": publishable,
        "platform": platform,
        "site": site,
    }
    if description_category_id:
        row["description_category_id"] = description_category_id
    if type_id:
        row["type_id"] = type_id
    return row


class FakeSearcher:
    def __init__(
        self,
        results: list[list[dict[str, Any]]] | None = None,
        *,
        source: str = "mercadolibre_api",
        error: Exception | None = None,
    ) -> None:
        self.results = deque(results or [])
        self.source = source
        self.error = error
        self.keywords: list[str] = []

    def search_categories(self, keyword: str) -> dict[str, Any]:
        self.keywords.append(keyword)
        if self.error is not None:
            raise self.error
        if not self.results:
            raise AssertionError("FakeSearcher 没有剩余结果")
        return {
            "keyword": keyword,
            "candidates": self.results.popleft(),
            "source": self.source,
        }


PRODUCT = {
    "name": "Portable fan",
    "brand": "Champion",
    "source": {
        "language": "zh-CN",
        "title": "便携式 USB 风扇",
        "description": "<p>桌面静音风扇，USB 供电。</p>",
        "attributes": {
            "Power": "USB",
            "价格": "99 元",
            "12345": "抓取噪音",
        },
    },
}
DRAFT = {
    "language": "es-MX",
    "title": "Ventilador portátil USB",
    "description": "Ventilador silencioso para escritorio.",
}
TARGET = {"platform": "mercadolibre", "site": "MLM", "language": "es-MX"}
TRACE = {"conversation_id": "aic-test", "task_run_id": "task-test"}


def execution_context() -> AiExecutionContext:
    return AiExecutionContext.create(
        timeout_seconds=10,
        budget_profile="category.match.default",
        permissions={"category.read"},
    )


def search(toolset, keyword: str) -> dict[str, Any]:
    binding = toolset.get("search_categories")
    assert binding is not None
    return binding.executor({"keyword": keyword}, execution_context())


def detail_loader(
    platform: str,
    category_id: str,
    *,
    site: str,
    include_attributes: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    assert include_attributes is True
    assert timeout_seconds > 0
    return {
        "category_id": category_id,
        "name_original": category_id,
        "path_original": ["Home", category_id],
        "platform": platform,
        "site": site,
        "attributes": {"required": [], "optional": []},
    }


def selected(category_id: str, confidence: float = 0.95) -> dict[str, Any]:
    return {
        "selected_category_id": category_id,
        "abstained": False,
        "model_confidence": confidence,
        "evidence": ["商品主体与类目一致"],
    }


def fake_agent_service(run):
    def service(payload, toolset, ledger, *, timeout_seconds):
        del ledger
        assert timeout_seconds > 0
        output, trace = run(payload, toolset)
        return CategoryMatchAgentRun.for_test(output, trace)

    return service


def test_first_model_request_contains_only_clean_product_facts() -> None:
    seen: dict[str, Any] = {}

    def run(payload, toolset):
        seen.update(payload=payload, toolset=toolset)
        search(toolset, "ventilador")
        return selected("MLM-FAN"), TRACE

    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[candidate("MLM-FAN")]]),
        agent_service=fake_agent_service(run),
        detail_loader=detail_loader,
    )

    assert result["status"] == "completed"
    assert seen["toolset"].toolset_id == "category.search"
    assert set(seen["payload"]) == {"target", "product"}
    serialized = str(seen["payload"])
    assert "candidates" not in serialized
    assert "retrieval" not in serialized
    assert "corpus" not in serialized
    facts = seen["payload"]["product"]
    assert facts["source"]["title"] == "便携式 USB 风扇"
    assert facts["target"]["title"] == "Ventilador portátil USB"
    assert facts["source"]["description"] == "桌面静音风扇，USB 供电。"
    assert facts["facts"]["attributes"] == {"Power": "USB"}


def test_model_can_change_keyword_until_a_candidate_matches() -> None:
    searcher = FakeSearcher([[], [candidate("MLM-FAN")]])

    def run(payload, toolset):
        assert search(toolset, "ventilador usb")["candidates"] == []
        second = search(toolset, "ventilador de mesa")
        assert second["candidates"][0]["category_id"] == "MLM-FAN"
        return selected("MLM-FAN"), TRACE

    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=searcher,
        agent_service=fake_agent_service(run),
        detail_loader=detail_loader,
    )

    assert result["status"] == "completed"
    assert result["decision"]["search_count"] == 2
    assert searcher.keywords == ["ventilador usb", "ventilador de mesa"]
    assert result["query"] == "ventilador de mesa"
    assert set(result["candidates"][0]) == {
        "category_id",
        "name",
        "path_segments",
    }


def test_model_cannot_finish_before_searching() -> None:
    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[candidate("MLM-FAN")]]),
        agent_service=fake_agent_service(
            lambda payload, toolset: (selected("MLM-FAN"), TRACE)
        ),
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "CATEGORY_SEARCH_REQUIRED"


def test_model_selected_unknown_category_is_a_protocol_failure() -> None:
    def run(payload, toolset):
        search(toolset, "ventilador")
        return selected("MLM-MADE-UP"), TRACE

    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[candidate("MLM-FAN")]]),
        agent_service=fake_agent_service(run),
    )

    assert result["ok"] is False
    assert result["failure"]["code"] == "MODEL_SELECTED_UNKNOWN_CATEGORY"


def test_model_can_abstain_after_real_searches() -> None:
    def run(payload, toolset):
        search(toolset, "ventilador")
        search(toolset, "ventilador de mesa")
        search(toolset, "aparato de ventilación")
        return {
            "selected_category_id": "",
            "abstained": True,
            "model_confidence": 0.2,
            "evidence": ["没有合适类目"],
        }, TRACE

    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[], [], []]),
        agent_service=fake_agent_service(run),
    )

    assert result["ok"] is True
    assert result["status"] == "unresolved"
    assert result["failure"]["code"] == "ABSTAIN_NO_MATCH"


def test_model_cannot_abstain_early_when_more_keywords_can_be_tried() -> None:
    def run(payload, toolset):
        search(toolset, "ventilador")
        return {
            "selected_category_id": "",
            "abstained": True,
            "model_confidence": 0.1,
            "evidence": ["首个关键词没有结果"],
        }, TRACE

    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[]]),
        agent_service=fake_agent_service(run),
    )

    assert result["ok"] is False
    assert result["failure"]["code"] == "CATEGORY_SEARCH_INCOMPLETE"


def test_cross_site_candidate_is_rejected_by_server_validation() -> None:
    def run(payload, toolset):
        search(toolset, "ventilador")
        return selected("MLB-FAN"), TRACE

    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[candidate("MLB-FAN", site="MLB")]]),
        agent_service=fake_agent_service(run),
    )

    assert result["ok"] is True
    assert result["status"] == "unresolved"
    assert result["failure"]["code"] == "SITE_RULE_VIOLATION"


def test_final_validation_reads_detail_and_attributes_once() -> None:
    calls: list[str] = []

    def run(payload, toolset):
        search(toolset, "ventilador")
        return selected("MLM-FAN"), TRACE

    def load_detail(*args, **kwargs):
        calls.append("detail")
        return {
            "category_id": "MLM-FAN",
            "platform": "mercadolibre",
            "site": "MLM",
            "attributes": {"required": [], "optional": []},
        }

    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[candidate("MLM-FAN")]]),
        agent_service=fake_agent_service(run),
        detail_loader=load_detail,
    )

    assert result["status"] == "completed"
    assert calls == ["detail"]


def test_provider_error_keeps_search_error_taxonomy() -> None:
    def run(payload, toolset):
        search(toolset, "ventilador")
        raise AssertionError("工具错误应先向模型返回")

    error = CategorySearchError(
        "CATEGORY_CREDENTIALS_MISSING",
        "请先填写 Ozon Client ID 和 API Key。",
    )
    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher(error=error),
        agent_service=fake_agent_service(run),
    )

    assert result["ok"] is False
    assert result["failure"]["code"] == "CATEGORY_CREDENTIALS_MISSING"


def test_total_deadline_covers_agent_service(monkeypatch) -> None:
    monkeypatch.setattr(
        "erp_web.facades.category_match_facade.CATEGORY_MATCH_DEADLINE_SECONDS",
        0.001,
    )

    def slow_model(payload, toolset):
        search(toolset, "ventilador")
        time.sleep(0.01)
        return selected("MLM-FAN"), TRACE

    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[candidate("MLM-FAN")]]),
        agent_service=fake_agent_service(slow_model),
    )

    assert result["ok"] is False
    assert result["failure"]["code"] == "TASK_DEADLINE_EXCEEDED"


def test_ozon_selection_requires_type_and_description_category_pair() -> None:
    def run(payload, toolset):
        search(toolset, "вентилятор")
        return selected("9001"), TRACE

    result = match_category(
        PRODUCT,
        {"title": "настольный usb вентилятор", "language": "ru-RU"},
        {"platform": "ozon", "site": "global", "language": "ru-RU"},
        searcher=FakeSearcher(
            [[candidate("9001", platform="ozon", site="global")]],
            source="ozon_cache",
        ),
        agent_service=fake_agent_service(run),
        detail_loader=lambda *args, **kwargs: {
            "category_id": "9001",
            "name_original": "Вентиляторы",
        },
    )

    assert result["ok"] is True
    assert result["status"] == "unresolved"
    assert result["failure"]["code"] == "CATEGORY_NOT_PUBLISHABLE"


def test_model_deadline_error_keeps_code() -> None:
    result = match_category(
        PRODUCT,
        DRAFT,
        TARGET,
        searcher=FakeSearcher([[candidate("MLM-FAN")]]),
        agent_service=fake_agent_service(
            lambda payload, toolset: (_ for _ in ()).throw(
                AiAgentExecutionError(
                    "TASK_DEADLINE_EXCEEDED",
                    "AI Agent 总 deadline 已耗尽",
                    retryable=True,
                )
            )
        ),
    )

    assert result["failure"]["code"] == "TASK_DEADLINE_EXCEEDED"

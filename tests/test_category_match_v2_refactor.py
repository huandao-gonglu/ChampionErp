"""1+N 多模态标准化、预召回单轮 0 工具精排、动态禁售账本写回与 RRF 融合回归测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from erp_web.context import get_context
from erp_web.facades import category_match_facade
from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch
from erp_web.runtime_units.category_tools import (
    CategoryCandidateLedger,
    build_category_match_toolset,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_agent_factory import AiAgentFactory
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.category_match_agent_service import run_category_match_agent
from erp_web.services.category_product_canonicalizer import (
    ProductCanonicalIdentity,
    build_product_facts,
)
from tests.ai_function_model_streaming import streaming_function_model as _streaming_model


def _factory_for(model: FunctionModel) -> AiAgentFactory:
    context = get_context()
    streaming_model = _streaming_model(model)

    def binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del args, kwargs
        return PydanticModelBinding(
            model=streaming_model,
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    return AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config={},
        model_binding_factory=binding,
    )


def test_build_product_facts_prioritizes_classification_attributes_and_sku_options() -> None:
    product = {
        "name": "便携桌面风扇",
        "attributes": {
            "促销活动": "买一送一",
            **{f"其他属性{i}": f"值{i}" for i in range(18)},
            "材质": "ABS塑料",
            "供电方式": "USB充电",
        },
        "sku_items": [
            {"id": "s1", "active": True, "options": {"颜色": "白色", "尺寸": "迷你款"}},
            {"id": "s2", "active": True, "options": {"颜色": "黑色", "尺寸": "大号款"}},
        ],
    }
    draft = {"title": "Portable Mini Fan", "sku_items": [{"sku_id": "s1", "selected": True}]}
    facts = build_product_facts(product, draft, {"platform": "mercadolibre", "site": "MLM"})
    attrs = facts["facts"]["attributes"]
    assert "促销活动" not in attrs
    assert attrs["材质"] == "ABS塑料"
    assert attrs["供电方式"] == "USB充电"
    assert len(attrs) <= 16
    assert facts["facts"]["sku_options"] == {"颜色": ["白色"], "尺寸": ["迷你款"]}


def test_rrf_ranking_boosts_multi_keyword_consensus_candidate() -> None:
    class FakeSearcher:
        def search_categories(self, keyword: str) -> dict[str, Any]:
            if keyword == "fan":
                rows = [
                    {"category_id": "A", "name": "Fan A", "path_segments": ["Home", "Fan A"]},
                    {"category_id": "SHARED", "name": "Desk Fan", "path_segments": ["Home", "Desk Fan"]},
                ]
            else:
                rows = [
                    {"category_id": "B", "name": "Cooler B", "path_segments": ["Home", "Cooler B"]},
                    {"category_id": "SHARED", "name": "Desk Fan", "path_segments": ["Home", "Desk Fan"]},
                ]
            return {"keyword": keyword, "source": "test", "candidates": rows}

    ledger = CategoryCandidateLedger()
    batch = CategoryKeywordBatchSearch(
        searcher=FakeSearcher(),
        ledger=ledger,
        search_language="es",
    )
    ctx = AiExecutionContext.create(
        timeout_seconds=10,
        budget_profile="category.match.default",
        permissions={"category.read"},
    )
    output = batch.execute({"keywords": ["fan", "ventilador"]}, ctx)
    assert output["candidates"][0]["category_id"] == "SHARED"
    assert set(output["candidates"][0]["matched_keywords"]) == {"fan", "ventilador"}


def test_prefetched_single_turn_rerank_finishes_with_zero_tool_calls() -> None:
    class FakeSearcher:
        def search_categories(self, keyword: str) -> dict[str, Any]:
            return {
                "keyword": keyword,
                "source": "test",
                "candidates": [
                    {
                        "category_id": "MLM-FAN",
                        "name": "Ventiladores de Escritorio",
                        "name_zh": "桌面风扇",
                        "path_segments": ["Hogar", "Ventiladores de Escritorio"],
                        "path_zh": "家居 / 桌面风扇",
                        "platform": "mercadolibre",
                        "site": "MLM",
                        "publishable": True,
                    }
                ],
            }

    ledger = CategoryCandidateLedger()
    searcher = FakeSearcher()
    bundle = build_category_match_toolset(
        searcher=searcher,
        ledger=ledger,
        platform="mercadolibre",
        site="MLM",
    )
    identity = ProductCanonicalIdentity(
        physical_form="USB 桌面小型电风扇",
        material_or_structure="ABS塑料外壳，带电机与扇叶",
        primary_use="桌面纳凉",
        search_terms_by_language={"es": ["ventilador de escritorio"]},
    )
    payload, toolset = category_match_facade.setup_category_match_search(
        {"platform": "mercadolibre", "site": "MLM", "language": "es-MX"},
        {"source": {"title": "桌面小风扇"}, "target": {"title": "Mini Fan"}},
        ledger,
        deadline_at=9_999_999_999.0,
        searcher=searcher,
        canonical_identity=identity,
    )
    assert "prefetched_candidates" in payload
    assert ledger.prefetched_count == 1
    assert ledger.search_count == 1

    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        turns += 1
        assert "prefetched_candidates" in str(messages)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "selected_category_id": "MLM-FAN",
                        "selected_category_path": ["Hogar", "Ventiladores de Escritorio"],
                        "type_relationship": "same_type",
                        "physical_comparison": {
                            "product_form": "USB 桌面小风扇",
                            "category_form": "桌面电风扇",
                            "compatible": True,
                        },
                        "abstained": False,
                        "model_confidence": 0.94,
                        "evidence": ["预召回候选中桌面风扇与实物结构完全一致"],
                    },
                    tool_call_id="single-turn-final",
                )
            ]
        )

    detail_calls = 0

    def detail_loader(category_id: str, *, timeout_seconds: float) -> dict[str, Any]:
        nonlocal detail_calls
        del timeout_seconds
        detail_calls += 1
        return {
            "platform": "mercadolibre",
            "site": "MLM",
            "category_id": category_id,
            "category_path": "Hogar / Ventiladores de Escritorio",
        }

    run = run_category_match_agent(
        payload,
        toolset,
        ledger,
        timeout_seconds=10,
        factory=_factory_for(FunctionModel(model)),
        candidate_detail_loader=detail_loader,
    )
    assert turns == 1
    assert run.outcome is not None
    assert run.outcome.usage["tool_calls"] == 0
    assert detail_calls == 1
    assert "MLM-FAN" in run.verified_details

    # finalize_category_match 复用 validator 已验证详情，不发起第二次 detail_loader 调用！
    final = category_match_facade.finalize_category_match(
        normalized_target={"platform": "mercadolibre", "site": "MLM"},
        ledger=ledger,
        deadline_at=9_999_999_999.0,
        detail_loader=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不得重复拉取详情")),
        agent_run=run,
    )
    assert final["ok"] is True
    assert final["status"] == "completed"
    assert final["selected_category_id"] == "MLM-FAN"


def test_dynamic_forbidden_checker_marks_ledger_for_forbidden_only_abstain() -> None:
    class FakeSearcher:
        def search_categories(self, keyword: str) -> dict[str, Any]:
            return {
                "keyword": keyword,
                "source": "test",
                "candidates": [
                    {
                        "category_id": "90001",
                        "name": "Лекарства",
                        "path_segments": ["Аптека", "Лекарства"],
                        "platform": "yandex",
                        "site": "global",
                        "publishable": True,
                    }
                ],
            }

    ledger = CategoryCandidateLedger()
    ledger.record_prefetched(
        [
            {
                "category_id": "90001",
                "name": "Лекарства",
                "path_segments": ["Аптека", "Лекарства"],
                "platform": "yandex",
                "site": "global",
                "publishable": True,
            }
        ],
        keywords=["лекарство"],
    )
    toolset = build_category_match_toolset(
        searcher=FakeSearcher(),
        ledger=ledger,
        platform="yandex",
        site="global",
    ).toolset

    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        agent_info.output_tools[0].name,
                        {
                            "selected_category_id": "90001",
                            "selected_category_path": ["Аптека", "Лекарства"],
                            "type_relationship": "same_type",
                            "physical_comparison": {
                                "product_form": "药品",
                                "category_form": "药品",
                                "compatible": True,
                            },
                            "abstained": False,
                            "model_confidence": 0.9,
                            "evidence": ["匹配"],
                        },
                        tool_call_id="try-1",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "selected_category_id": "",
                        "selected_category_path": [],
                        "type_relationship": "incompatible",
                        "physical_comparison": {
                            "product_form": "药品",
                            "category_form": "",
                            "compatible": False,
                        },
                        "abstained": True,
                        "abstain_reason": "forbidden_only",
                        "model_confidence": 0.2,
                        "evidence": ["唯一相符类目为禁售类目"],
                    },
                    tool_call_id="abstain-2",
                )
            ]
        )

    run = run_category_match_agent(
        {
            "target": {"platform": "yandex", "site": "global", "language": "ru-RU"},
            "product": {"source": {"title": "药品"}, "target": {"title": "Лекарство"}},
        },
        toolset,
        ledger,
        timeout_seconds=10,
        factory=_factory_for(FunctionModel(model)),
        candidate_detail_loader=lambda cid, *, timeout_seconds: {
            "category_id": cid,
            "category_path": "Аптека / Лекарства",
        },
        forbidden_checker=lambda cid, detail: {"message": f"类目 {cid} 在禁售名单中"},
    )
    assert turns == 2
    assert ledger.get("90001")["forbidden"] is True

    final = category_match_facade.finalize_category_match(
        normalized_target={"platform": "yandex", "site": "global"},
        ledger=ledger,
        deadline_at=9_999_999_999.0,
        agent_run=run,
    )
    assert final["ok"] is True
    assert final["status"] == "unresolved"
    assert final["failure"] is not None
    assert final["failure"]["code"] == "CATEGORY_FORBIDDEN_NO_ALTERNATIVE"


def test_match_categories_batch_canonicalizes_once_for_three_platforms() -> None:
    canon_calls = 0

    def fake_canonicalize(product, draft, facts, languages, *, timeout_seconds):
        nonlocal canon_calls
        del product, draft, facts, timeout_seconds
        canon_calls += 1
        assert set(languages) == {"ru-RU", "es"}
        return (
            ProductCanonicalIdentity(
                physical_form="桌面小风扇",
                material_or_structure="塑料",
                primary_use="桌面送风",
                search_terms_by_language={
                    "ru-RU": ["вентилятор"],
                    "es": ["ventilador"],
                },
            ),
            None,
        )

    def fake_searcher_factory(platform, *, site="", **kwargs):
        del kwargs

        class _S:
            def search_categories(self, keyword: str) -> dict[str, Any]:
                return {
                    "keyword": keyword,
                    "source": "test",
                    "candidates": [
                        {
                            "category_id": f"{platform}-1",
                            "name": "Fan",
                            "path_segments": ["Home", "Fan"],
                            "platform": platform,
                            "site": site,
                            "publishable": True,
                            **(
                                {"description_category_id": "100", "type_id": f"{platform}-1"}
                                if platform == "ozon"
                                else {}
                            ),
                        }
                    ],
                }

        return _S()

    product = {"name": "桌面小风扇"}
    draft = {"title": "Mini Desk Fan"}
    subjects = [
        (product, draft, {"platform": "ozon", "site": "global"}),
        (product, draft, {"platform": "yandex", "site": "global"}),
        (product, draft, {"platform": "mercadolibre", "site": "MLM"}),
    ]

    with (
        patch.object(category_match_facade, "_canonicalize_for_languages", side_effect=fake_canonicalize),
        patch.object(
            category_match_facade,
            "run_category_match_agent",
            side_effect=lambda payload, toolset, ledger, **kw: category_match_facade.CategoryMatchAgentRun(
                output={
                    "selected_category_id": f"{payload['target']['platform']}-1",
                    "selected_category_path": ["Home", "Fan"],
                    "type_relationship": "same_type",
                    "physical_comparison": {"product_form": "风扇", "category_form": "风扇", "compatible": True},
                    "abstained": False,
                    "abstain_reason": "",
                    "model_confidence": 0.9,
                    "evidence": ["一致"],
                },
                trace={"task_run_id": "t1"},
                verified_details={
                    f"{payload['target']['platform']}-1": {
                        "platform": payload["target"]["platform"],
                        "site": payload["target"]["site"],
                        "category_id": f"{payload['target']['platform']}-1",
                        "category_path": "Home / Fan",
                        **(
                            {"description_category_id": "100", "type_id": f"{payload['target']['platform']}-1"}
                            if payload["target"]["platform"] == "ozon"
                            else {}
                        ),
                    }
                },
            ),
        ),
    ):
        results = category_match_facade.match_categories_batch(
            subjects,
            searcher_factory=fake_searcher_factory,
            agent_service=category_match_facade.run_category_match_agent,
        )

    assert canon_calls == 1
    assert len(results) == 3
    assert [r["selected_category_id"] for r in results] == ["ozon-1", "yandex-1", "mercadolibre-1"]
    assert all(r["status"] == "completed" for r in results)


def test_canonicalizer_does_not_cap_max_tokens_for_reasoning_models(tmp_path: Path) -> None:
    from erp_web.services import category_product_canonicalizer as canonicalizer

    captured: dict[str, object] = {}

    def fake_chat_structured(**kwargs):
        captured.update(kwargs)
        return ProductCanonicalIdentity(
            physical_form="可调扳手",
            search_terms_by_language={"ru": ["разводной ключ"]},
        )

    with (
        patch.object(
            canonicalizer.ai_model_config,
            "resolve_ai_model",
            return_value={"id": "m1", "capabilities": ["chat", "json"]},
        ),
        patch.object(
            canonicalizer.ai_direct_request_service,
            "chat_structured",
            side_effect=fake_chat_structured,
        ),
    ):
        identity = canonicalizer.canonicalize_product_for_category_match(
            {"source": {"title": "活动扳手"}},
            ["ru"],
            app_dir=tmp_path,
            app_config={},
        )

    assert identity is not None
    assert identity.physical_form == "可调扳手"
    assert captured["max_tokens"] is None


def test_curate_public_candidates_prioritizes_selected_and_siblings_and_caps_at_20() -> None:
    ledger = CategoryCandidateLedger()
    rows = []
    # 先放 22 个无关类目与禁售类目，再放同父级兄弟和最终选中类目
    for idx in range(22):
        rows.append(
            {
                "category_id": f"noise-{idx}",
                "name": f"Шум {idx}",
                "path_segments": ["Все товары", "Разное", f"Шум {idx}"],
                "platform": "yandex",
                "site": "global",
                "publishable": True,
            }
        )
    rows.append(
        {
            "category_id": "forb-1",
            "name": "Запрещённый",
            "path_segments": ["Все товары", "Запрещённый"],
            "platform": "yandex",
            "site": "global",
            "publishable": True,
            "forbidden": True,
            "forbidden_reason": "禁售",
        }
    )
    rows.append(
        {
            "category_id": "sib-1",
            "name": "Рожковые ключи",
            "path_segments": ["Все товары", "Инструменты", "Ручной инструмент", "Рожковые ключи"],
            "platform": "yandex",
            "site": "global",
            "publishable": True,
        }
    )
    rows.append(
        {
            "category_id": "sel-1",
            "name": "Разводные ключи",
            "path_segments": ["Все товары", "Инструменты", "Ручной инструмент", "Разводные ключи"],
            "platform": "yandex",
            "site": "global",
            "publishable": True,
        }
    )
    ledger.record_prefetched(rows, keywords=["ключ"], source="test")

    curated = category_match_facade._curate_public_candidates(ledger, "sel-1")
    assert len(curated) == 20
    assert curated[0]["category_id"] == "sel-1"
    assert curated[1]["category_id"] == "sib-1"
    assert all(row["category_id"] != "forb-1" for row in curated)


def test_attribute_page_l2_enrichment_assembles_path_zh_from_segment_glossary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from erp_web.context import get_context
    from erp_web.runtime_units import category_localization
    from erp_web.schemas.category_definition import CategoryAttributePage
    from erp_web.stores.category_catalog_store import CategoryCatalogStore

    store = CategoryCatalogStore(tmp_path / "seg-l2.sqlite3")
    store.put_glossary(
        "yandex",
        "zh-CN",
        {
            "Все товары": "全部商品",
            "Инструменты": "工具",
            "Разводные ключи": "活动扳手",
        },
    )
    monkeypatch.setattr(get_context(), "_category_catalog_store", store, raising=False)
    page = CategoryAttributePage(
        platform="yandex",
        site="global",
        category_id="15695322",
        category_path="Все товары / Инструменты / Разводные ключи",
        attributes=(),
    )
    enriched = category_localization.enrich_attribute_page_l2(page)
    assert enriched.category_path_zh == "全部商品 / 工具 / 活动扳手"

    # category_match 返回的公开候选仅带 path_segments（无 path 字符串），也必须 0ms 拼出 path_zh / name_zh
    match_candidates = category_localization.enrich_category_candidates_l2(
        "yandex",
        [
            {
                "category_id": "15695322",
                "name": "Разводные ключи",
                "path_segments": ["Все товары", "Инструменты", "Разводные ключи"],
            }
        ],
    )
    assert match_candidates[0]["name_zh"] == "活动扳手"
    assert match_candidates[0]["path_zh"] == "全部商品 / 工具 / 活动扳手"


def test_manual_chinese_category_search_supports_ozon_passthrough_glossary_and_translate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest
    from erp_web.context import get_context
    from erp_web.facades import category_facade
    from erp_web.runtime_units import category_localization, text_translation
    from erp_web.stores.category_catalog_store import CategoryCatalogStore

    store = CategoryCatalogStore(tmp_path / "manual-search.sqlite3")
    store.put_glossary(
        "yandex",
        "zh-CN",
        {
            "Сантехнические, разводные ключи": "水暖扳手、活动扳手",
            "Все товары / Инструменты / Разводные ключи": "全部商品 / 工具 / 活动扳手",
        },
    )
    monkeypatch.setattr(get_context(), "_category_catalog_store", store, raising=False)
    monkeypatch.setattr(text_translation, "_catalog_store", lambda: store)

    # 1) Ozon 原生支持中文 FTS5，直接透传不翻译
    assert category_localization.resolve_manual_search_query("ozon", "global", "活动扳手") == (
        "活动扳手",
        "",
        "",
    )

    # 2) Yandex 精确命中 L2 反向同义词典：0ms 直接返回俄文词，不调模型，并回种正向缓存
    eff, lang, src = category_localization.resolve_manual_search_query("yandex", "global", "活动扳手")
    assert eff == "Сантехнические, разводные ключи"
    assert lang.startswith("ru")
    assert src == "glossary"
    # 二次搜索直接命中 L2 正向缓存
    eff2, _, src2 = category_localization.resolve_manual_search_query("yandex", "global", "活动扳手")
    assert eff2 == "Сантехнические, разводные ключи"
    assert src2 == "cache"

    # 3) Mercado Libre CBT 生词走唯一 text.translate 翻成英文并永久缓存
    ai_calls: list[tuple[str, dict[str, str]]] = []

    def fake_model_translate(language: str, batch: dict[str, str], **_kw: object) -> dict[str, str]:
        ai_calls.append((language, dict(batch)))
        return {"q": "desk fan"}

    monkeypatch.setattr(category_localization, "require_model_platform", lambda *a, **k: None)
    monkeypatch.setattr(text_translation, "_run_model_translation", fake_model_translate)

    seen_live_queries: list[str] = []

    def fake_live_search(platform: str, query: str, site: str = "", limit: int | None = 5) -> list[dict[str, object]]:
        seen_live_queries.append(f"{platform}:{site}:{query}")
        return [{"id": "CBT123", "name": "Fans", "path": "Home / Fans"}]

    monkeypatch.setattr(category_facade, "search_categories_live", fake_live_search)
    body1, status1 = category_facade.category_search_payload(
        {"platform": "mercadolibre", "site": "CBT", "query": "桌面风扇"}
    )
    body2, status2 = category_facade.category_search_payload(
        {"platform": "mercadolibre", "site": "CBT", "query": "桌面风扇"}
    )
    assert status1 == 200 and status2 == 200
    assert body1["query"] == "桌面风扇"
    assert body1["translated_query"] == "desk fan"
    assert body1["search_language"].startswith("en")
    assert body2["translated_query"] == "desk fan"
    assert seen_live_queries == ["mercadolibre:CBT:desk fan", "mercadolibre:CBT:desk fan"]
    assert len(ai_calls) == 1

    # 4) 未配模型时：Yandex 反向子串可兜底命中（搜“水暖”命中“水暖扳手、活动扳手”），完全无命中则给可操作报错
    monkeypatch.setattr(
        category_localization,
        "require_model_platform",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("未配置模型")),
    )
    fb, _, fb_src = category_localization.resolve_manual_search_query("yandex", "global", "水暖")
    assert fb == "Сантехнические, разводные ключи"
    assert fb_src == "glossary_fallback"
    with pytest.raises(RuntimeError, match="文本翻译"):
        category_localization.resolve_manual_search_query("mercadolibre", "MLM", "未知全新商品词")


def test_yandex_and_ozon_share_single_global_taxonomy_across_accounts_with_auth_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest
    from erp_web.runtime_units import ozon_category_api, yandex_category_api
    from erp_web.stores.category_catalog_store import CategoryCatalogStore

    store = CategoryCatalogStore(tmp_path / "shared-taxonomy.sqlite3")
    monkeypatch.setattr(yandex_category_api, "_catalog_store", lambda: store)
    monkeypatch.setattr(ozon_category_api, "_catalog_store", lambda: store)

    # --- Yandex：不同账号 token 共用 1 份 global 类目树；缺凭据仍拒绝 ---
    yandex_calls: list[str] = []
    current_yandex = {"token": "token-shop-A", "business_id": "biz-1"}
    monkeypatch.setattr(
        yandex_category_api,
        "_load_store_config",
        lambda: {"yandex": {"api_token": current_yandex["token"], "business_id": current_yandex["business_id"]}},
    )

    def fake_yandex_tree(token: str, *, language: str = "RU", timeout_seconds: float = 30.0):
        del language, timeout_seconds
        yandex_calls.append(token)
        return [{"id": 1, "name": "Инструменты", "children": [{"id": 1001, "name": "Разводные ключи"}]}]

    monkeypatch.setattr(yandex_category_api, "fetch_yandex_category_tree", fake_yandex_tree)

    recs_a, info_a = yandex_category_api.load_yandex_category_corpus()
    assert len(recs_a) == 1 and info_a["cache_source"] == "remote_cache"
    assert info_a["credential_scope_hash"] == "global"

    current_yandex.update({"token": "token-shop-B", "business_id": "biz-2"})
    recs_b, info_b = yandex_category_api.load_yandex_category_corpus()
    assert len(recs_b) == 1 and info_b["cache_source"] == "persistent_cache"
    assert yandex_calls == ["token-shop-A"], "第二个 Yandex 账号必须复用全局树，不得重复拉取"

    # 祖先反查不再依赖当前上下文的 token 哈希
    assert yandex_category_api.yandex_category_ancestor_ids("1001") == ("1",)

    current_yandex.update({"token": "", "business_id": ""})
    with pytest.raises(RuntimeError, match="Yandex API-Key Token"):
        yandex_category_api.load_yandex_category_corpus()

    # --- Ozon：不同 client_id 共用 1 份 global 类目树；缺凭据仍拒绝 ---
    ozon_calls: list[tuple[str, str]] = []
    current_ozon = {"client_id": "client-A", "api_key": "key-A"}
    monkeypatch.setattr(
        ozon_category_api,
        "_load_store_config",
        lambda: {"ozon": dict(current_ozon)},
    )

    def fake_ozon_json(method, url, client_id, api_key, payload=None, **kwargs):
        del method, url, api_key, kwargs
        lang = str((payload or {}).get("language") or "DEFAULT")
        ozon_calls.append((client_id, lang))
        if lang == "ZH_HANS":
            return {
                "result": [
                    {
                        "description_category_id": 17027949,
                        "category_name": "工具",
                        "children": [{"type_id": 94765, "type_name": "扳手", "children": []}],
                    }
                ]
            }
        return {
            "result": [
                {
                    "description_category_id": 17027949,
                    "category_name": "Инструменты",
                    "children": [{"type_id": 94765, "type_name": "Ключи", "children": []}],
                }
            ]
        }

    monkeypatch.setattr(ozon_category_api, "request_ozon_json", fake_ozon_json)

    oz_a, oz_info_a = ozon_category_api.load_ozon_category_corpus()
    assert len(oz_a) == 1 and oz_info_a["cache_source"] == "remote_cache"
    assert oz_info_a["credential_scope_hash"] == "global"

    current_ozon.update({"client_id": "client-B", "api_key": "key-B"})
    oz_b, oz_info_b = ozon_category_api.load_ozon_category_corpus()
    assert len(oz_b) == 1 and oz_info_b["cache_source"] == "persistent_cache"
    assert [cid for cid, _ in ozon_calls] == ["client-A", "client-A"]

    current_ozon.update({"client_id": "", "api_key": ""})
    with pytest.raises(RuntimeError, match="Ozon Client ID"):
        ozon_category_api.load_ozon_category_corpus()

    with store._connect() as conn:
        rows = conn.execute(
            "SELECT platform, scope_key FROM taxonomy_snapshots ORDER BY platform"
        ).fetchall()
    assert [(r["platform"], r["scope_key"]) for r in rows] == [
        ("ozon", "global"),
        ("yandex", "global"),
    ]


def test_yandex_definitions_isolated_by_business_id_and_survive_token_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone
    from erp_web.runtime_units import category_providers, yandex_category_api as api
    from erp_web.stores.category_catalog_store import CategoryCatalogStore

    store = CategoryCatalogStore(tmp_path / "yandex-biz-scope.sqlite3")
    monkeypatch.setattr(api, "_catalog_store", lambda: store)
    monkeypatch.setattr(category_providers, "_catalog_store", lambda: store)

    nodes = api._build_taxonomy_nodes(
        [{"id": 1, "name": "Обувь", "children": [{"id": 999001, "name": "Кроссовки"}]}]
    )
    store.save_taxonomy(
        "yandex",
        api.YANDEX_TAXONOMY_SCOPE,
        "RU",
        nodes,
        corpus_hash=api._stable_corpus_hash(nodes),
        retrieved_at=datetime.now(timezone.utc),
        fresh_ttl=api.YANDEX_CATEGORY_CACHE_FRESH_TTL,
        max_age=api.YANDEX_CATEGORY_CACHE_MAX_AGE,
    )

    creds = {"token": "tok-v1", "business_id": "1001"}
    monkeypatch.setattr(
        api,
        "_yandex_credentials",
        lambda: (creds["token"], creds["business_id"]),
    )

    calls: list[tuple[str, str]] = []

    def fake_params(token: str, category_id: str, business_id: str, **kwargs):
        del category_id, kwargs
        calls.append((token, business_id))
        return [
            {
                "id": 5678,
                "name": "Размер",
                "type": "TEXT",
                "distinctive": business_id == "1001",
            }
        ]

    monkeypatch.setattr(api, "fetch_yandex_category_parameters", fake_params)
    provider = category_providers.YandexCategoryProvider()

    # 1) business 1001 首次拉取 -> distinctive=True
    d1 = provider.attribute_definitions("999001")
    assert d1.cache.source == "live"
    assert d1.attribute_by_id("5678").variation_role == "variant"
    assert calls == [("tok-v1", "1001")]

    # 2) business 1001 轮换 api_token -> 命中原缓存，不重拉远端
    creds["token"] = "tok-v2-rotated"
    d2 = provider.attribute_definitions("999001")
    assert d2.cache.source == "cache"
    assert d2.attribute_by_id("5678").variation_role == "variant"
    assert calls == [("tok-v1", "1001")]

    # 3) 切换不同 business 2002 -> 严格隔离，独立拉取 distinctive=False
    creds.update({"token": "tok-biz-2", "business_id": "2002"})
    d3 = provider.attribute_definitions("999001")
    assert d3.cache.source == "live"
    assert d3.attribute_by_id("5678").variation_role == ""
    assert calls == [("tok-v1", "1001"), ("tok-biz-2", "2002")]


def test_ozon_definitions_and_values_shared_across_shops_with_auth_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone
    import pytest
    from erp_web.runtime_units import category_providers, ozon_category_api as api
    from erp_web.stores.category_catalog_store import CategoryCatalogStore

    store = CategoryCatalogStore(tmp_path / "ozon-shared-def.sqlite3")
    monkeypatch.setattr(api, "_catalog_store", lambda: store)
    monkeypatch.setattr(category_providers, "_catalog_store", lambda: store)

    nodes, _ = api._build_taxonomy_nodes(
        [
            {
                "description_category_id": 17027949,
                "category_name": "Шины",
                "children": [{"type_id": 94765, "type_name": "Летние шины", "children": []}],
            }
        ],
        None,
    )
    store.save_taxonomy(
        "ozon",
        api.OZON_CATALOG_SCOPE,
        api._TAXONOMY_LOCALE,
        nodes,
        corpus_hash=api._stable_corpus_hash(nodes),
        retrieved_at=datetime.now(timezone.utc),
        fresh_ttl=api.OZON_CATEGORY_CACHE_FRESH_TTL,
        max_age=api.OZON_CATEGORY_CACHE_MAX_AGE,
    )

    current = {"client_id": "shop-A", "api_key": "key-A"}
    monkeypatch.setattr(api, "_load_store_config", lambda: {"ozon": dict(current)})

    remote_calls: list[tuple[str, str]] = []

    def fake_ozon(method, url, client_id, api_key, payload=None, **kwargs):
        del method, api_key, kwargs
        lang = str((payload or {}).get("language") or "DEFAULT")
        remote_calls.append((client_id, f"{url}:{lang}"))
        if url == api.OZON_CATEGORY_ATTRIBUTES_URL:
            return {
                "result": [
                    {
                        "id": 9048,
                        "name": "Название модели" if lang == "DEFAULT" else "型号名称",
                        "type": "String",
                        "is_required": True,
                        "dictionary_id": 12345,
                    }
                ]
            }
        return {"result": [{"id": 1, "value": "Модель X"}], "has_next": False}

    monkeypatch.setattr(api, "request_ozon_json", fake_ozon)
    provider = category_providers.OzonCategoryProvider()

    def_a = provider.attribute_definitions("94765")
    vals_a = api.fetch_ozon_category_attribute_values("94765", "9048")
    assert def_a.cache.source == "live" and len(vals_a["values"]) == 1
    first_call_count = len(remote_calls)

    # 切换至 Shop B：属性定义与枚举页直接 0ms 复用全局缓存
    current.update({"client_id": "shop-B", "api_key": "key-B"})
    def_b = provider.attribute_definitions("94765")
    vals_b = api.fetch_ozon_category_attribute_values("94765", "9048")
    assert def_b.cache.source == "cache"
    assert vals_b["values"] == vals_a["values"]
    assert len(remote_calls) == first_call_count

    # 未配置凭据的 Shop C 仍被拦截
    current.update({"client_id": "", "api_key": ""})
    with pytest.raises(RuntimeError, match="Ozon Client ID"):
        provider.attribute_definitions("94765")


def test_shared_category_catalog_store_singleton_across_app_contexts_and_refcount(
    tmp_path: Path,
) -> None:
    from erp_web.context import AppContext, AppPaths
    from erp_web.db import ErpDatabase
    from erp_web.stores.category_catalog_store import CategoryCatalogStore

    root_paths = AppPaths.from_app_dir(tmp_path / "root")
    user_paths = AppPaths.from_app_dir(tmp_path / "user-b")
    other_paths = AppPaths.from_app_dir(tmp_path / "isolated")

    root_ctx = AppContext(root_paths, ErpDatabase(root_paths.db_path))
    user_ctx = AppContext(user_paths, ErpDatabase(user_paths.db_path))
    user_ctx.shared_paths = root_paths
    other_ctx = AppContext(other_paths, ErpDatabase(other_paths.db_path))

    try:
        store_root = root_ctx.category_catalog_store
        store_user = user_ctx.category_catalog_store
        store_other = other_ctx.category_catalog_store

        assert store_root is store_user, "共享同一 cache_dir 的多账号上下文必须复用同一 Store 单例"
        assert store_root is not store_other, "不同 shared_paths 必须保持独立 Store 实例"

        store_root.put_glossary("yandex", "zh-CN", {"Ключи": "扳手"})
        assert store_user._glossary_lru, "共享单例内存热缓存应直接对另一账号可见"

        # 关闭子账号上下文不得提前清空仍在使用的共享单例
        user_ctx.close()
        assert store_root._glossary_lru, "仍有活跃上下文持有共享单例时，close 不得清空内存状态"
        resolved = store_root.path.resolve()
        assert CategoryCatalogStore._shared_instances[resolved] == (store_root, 1)

        # 最后一个持有者关闭后应正常释放并移出注册表
        root_ctx.close()
        assert resolved not in CategoryCatalogStore._shared_instances
        assert not store_root._glossary_lru
    finally:
        user_ctx.close()
        root_ctx.close()
        other_ctx.close()


def test_v1_to_v2_migration_and_global_save_prune_legacy_scopes_preserving_glossary(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timedelta, timezone
    import sqlite3
    from erp_web.stores.category_catalog_store import (
        STORE_SCHEMA_VERSION,
        CategoryCatalogStore,
    )

    db_path = tmp_path / "legacy-v1.sqlite3"
    # 1) 构造 v1 形态的旧库：包含旧 sha256 凭据副本 + 已积累的翻译词典
    seed_store = CategoryCatalogStore(db_path)
    now = datetime.now(timezone.utc)
    legacy_node = {
        "node_key": "1001",
        "category_id": "1001",
        "parent_key": "",
        "level": "product_type",
        "depth": 1,
        "name": "Разводные ключи",
        "path_segments": ["Разводные ключи"],
        "path_text": "Разводные ключи",
        "publishable": True,
    }
    seed_store.save_taxonomy(
        "yandex",
        "sha256:legacy-token-a",
        "RU",
        [legacy_node],
        corpus_hash="sha256:legacy",
        retrieved_at=now,
        fresh_ttl=timedelta(hours=6),
        max_age=timedelta(days=7),
    )
    seed_store.set_cached_value(
        "ozon:attr_vals:sha256:old-client:1:2:3:0",
        "ozon",
        "sha256:old-client",
        {"values": []},
        ttl_seconds=900,
    )
    seed_store.set_cached_value(
        "yandex:params:sha256:old-token:biz1:1001",
        "yandex",
        "sha256:old-token",
        [],
        ttl_seconds=900,
    )
    seed_store.set_cached_value(
        "ml:GET:public:https://api.mercadolibre.com/categories/MLM1",
        "mercadolibre",
        "public",
        {"id": "MLM1"},
        ttl_seconds=900,
    )
    seed_store.put_glossary("yandex", "zh-CN", {"Разводные ключи": "活动扳手"})
    seed_store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    # 2) 重新打开触发 v1 -> v2 原地迁移
    migrated = CategoryCatalogStore(db_path)
    with migrated._connect() as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == STORE_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM taxonomy_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM taxonomy_nodes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM taxonomy_fts").fetchone()[0] == 0
        remaining_vals = [
            r["platform"]
            for r in conn.execute("SELECT platform FROM category_value_cache").fetchall()
        ]
    assert remaining_vals == ["mercadolibre"]
    assert migrated.get_glossary("yandex", "zh-CN", ["Разводные ключи"]) == {
        "Разводные ключи": "活动扳手"
    }

    # 3) 即便在 v2 库中存在残余非 global 行，写入 global 类目树也会原子清理
    migrated.save_taxonomy(
        "yandex",
        "sha256:stray-legacy",
        "RU",
        [legacy_node],
        corpus_hash="sha256:stray",
        retrieved_at=now,
        fresh_ttl=timedelta(hours=6),
        max_age=timedelta(days=7),
    )
    migrated.save_taxonomy(
        "yandex",
        "global",
        "RU",
        [legacy_node],
        corpus_hash="sha256:global",
        retrieved_at=now,
        fresh_ttl=timedelta(hours=6),
        max_age=timedelta(days=7),
    )
    with migrated._connect() as conn:
        scopes = [
            r["scope_key"]
            for r in conn.execute(
                "SELECT scope_key FROM taxonomy_snapshots WHERE platform = 'yandex'"
            ).fetchall()
        ]
    assert scopes == ["global"]
    assert migrated.get_glossary("yandex", "zh-CN", ["Разводные ключи"]) == {
        "Разводные ключи": "活动扳手"
    }

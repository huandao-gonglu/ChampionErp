"""真实失败搜索词的离线回归，不访问平台或模型。"""

import pytest

from erp_web.runtime_units import yandex_category_api


@pytest.fixture
def corpus(monkeypatch):
    tree = [
        {"id": 1, "name": "Одежда, обувь и аксессуары", "children": [
            {"id": 69337906, "name": "Баффы"},
        ]},
        {"id": 2, "name": "Косметика / Уход за лицом", "children": [
            {"id": 64690100, "name": "Маски для лица"},
            {"id": 64126577, "name": "Маски многоразовые для лица"},
            {"id": 3, "name": "Кремы"},
        ]},
        {"id": 4, "name": "Аптека", "children": [
            {"id": 80001041, "name": "Маски защитные многоразовые"},
        ]},
        {"id": 5, "name": "Авто", "children": [
            {"id": 71584873, "name": "Бафферы пружин автомобильной подвески"},
        ]},
    ]
    records = yandex_category_api._flatten_leaf_categories(tree)
    monkeypatch.setattr(
        yandex_category_api, "load_yandex_category_corpus",
        lambda **kwargs: (records, {"cache_source": "persistent_cache"}),
    )
    return records


@pytest.mark.parametrize("query", [
    "маска солнцезащитная от UV",
    "защитная маска для лица",
    "бафф маска для лица",
    "солнцезащитная маска лицо",
    "UV маска лицо защита",
    "маска для лица",
])
def test_real_failed_queries_recall_leaf_candidates(corpus, query):
    results = yandex_category_api.search_yandex_categories(query, limit=8)
    assert results
    assert all(row["is_leaf"] for row in results)
    assert all(row["source"] == "yandex_category_tree" for row in results)
    assert all(row["cache_source"] == "persistent_cache" for row in results)


def test_buff_query_does_not_match_automotive_word_prefix(corpus):
    results = yandex_category_api.search_yandex_categories("бафф маска для лица", limit=8)
    ids = [row["category_id"] for row in results]
    assert "69337906" in ids
    assert "71584873" not in ids
    buff = next(row for row in results if row["category_id"] == "69337906")
    assert buff["matched_terms"] == ["бафф"]
    assert [row["category_id"] for row in yandex_category_api.search_yandex_categories("бафф")] == ["69337906"]


def test_leaf_name_coverage_precedes_broad_path_matches(corpus):
    results = yandex_category_api.search_yandex_categories("МАСКА, для ЛИЦА!")
    assert results[0]["category_id"] == "64690100"
    assert results[0]["score"] > results[-1]["score"]
    assert "3" not in [row["category_id"] for row in results]
    assert "64690100" in [row["category_id"] for row in yandex_category_api.search_yandex_categories("уход за лицом")]
    assert yandex_category_api.search_yandex_categories("несуществующий товар") == []


@pytest.mark.parametrize("query", ["", "  ", "---", "для и на"])
def test_queries_without_search_terms_do_not_load_corpus(monkeypatch, query):
    def unexpected_load(**kwargs):
        raise AssertionError("无有效搜索词时不应加载类目树")
    monkeypatch.setattr(yandex_category_api, "load_yandex_category_corpus", unexpected_load)
    assert yandex_category_api.search_yandex_categories(query) == []


def test_match_tool_sees_kitchen_organizer_beyond_old_eight_and_fifty_limits(monkeypatch):
    """保鲜膜盒事故回归：低排名的合理候选必须穿过整条匹配链路。"""
    from erp_web.runtime_units.category_searchers import create_category_searcher
    from erp_web.runtime_units.category_tools import build_category_match_toolset
    from erp_web.schemas.category import CategoryCandidateLedger, CATEGORY_SEARCH_MAX_OUTPUT_BYTES
    from erp_web.schemas.ai_tools import AiToolCommand
    from erp_web.schemas.ai_trace import AiExecutionContext
    from erp_web.services.ai_tool_runtime import AiToolRuntime

    records = yandex_category_api._flatten_leaf_categories([
        {"id": 1, "name": "Другие товары", "children": [
            {"id": 1000 + index, "name": "Органайзеры настенные"}
            for index in range(60)
        ]},
        {"id": 2, "name": "Кухонные аксессуары", "children": [
            {"id": 62619392, "name": "Органайзеры кухонные"},
        ]},
    ])
    monkeypatch.setattr(yandex_category_api, "load_yandex_category_corpus",
                        lambda **kwargs: (records, {"cache_source": "persistent_cache"}))
    query = "настенный органайзер для кухни"
    # 人工查询仍尊重显式 limit；自动匹配取得全部命中。
    assert len(yandex_category_api.search_yandex_categories(query, limit=8)) == 8
    assert "62619392" not in {row["category_id"] for row in yandex_category_api.search_yandex_categories(query, limit=50)}
    searcher = create_category_searcher("yandex", site="global")
    ledger = CategoryCandidateLedger()
    runtime = AiToolRuntime(
        toolset=build_category_match_toolset(searcher=searcher, ledger=ledger, platform="yandex", site="global").toolset,
        execution_context=AiExecutionContext.create(timeout_seconds=10, budget_profile="category.match.default", permissions={"category.read"}),
        max_output_bytes=CATEGORY_SEARCH_MAX_OUTPUT_BYTES,
    )
    result = runtime.execute(AiToolCommand(
        call_id="kitchen-organizer", tool_name="search_categories", tool_version="5", round=1,
        arguments={"product_identity": "竹制卷材收纳切割盒", "product_type": query, "alternative_names": [], "keywords": [query]},
    ))
    assert result.ok, result.error
    assert result.output["query_candidate_counts"] == {query: 61}
    assert result.output["total_candidate_count"] == result.output["returned_candidate_count"] == 61
    assert not result.output["has_more"]
    assert ledger.get("62619392") is not None
    assert result.output["candidates"][-1]["category_id"] == "62619392"

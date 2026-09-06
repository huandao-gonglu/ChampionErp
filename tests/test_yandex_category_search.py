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

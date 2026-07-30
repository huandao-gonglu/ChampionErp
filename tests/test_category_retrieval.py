from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from erp_web.runtime_units import category_providers
from erp_web.runtime_units.category_providers import MercadoLibreCategoryProvider
from erp_web.runtime_units.category_retrieval import (
    CategoryCandidateRetriever,
    CategoryRetrievalError,
    build_category_query_variants,
    normalize_category_text,
)


def _corpus_info() -> dict[str, Any]:
    return {
        "corpus_hash": "sha256:test-corpus",
        "taxonomy_version": "test-v1",
        "locale": "ru-RU",
        "retrieved_at": "2026-07-30T00:00:00+00:00",
        "expires_at": "2026-07-30T00:15:00+00:00",
        "credential_scope_hash": "sha256:test-scope",
    }


class LocalTreeProvider:
    platform = "ozon"

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.preflight_calls = 0
        self.corpus_calls = 0

    def resolve_site(self, site: str = "") -> str:
        del site
        return "global"

    def preflight(self, site: str = "") -> dict[str, Any]:
        self.preflight_calls += 1
        return {
            "ok": True,
            "platform": self.platform,
            "site": self.resolve_site(site),
            "retrieval_mode": "full_tree_local",
            "corpus_info": _corpus_info(),
        }

    def category_corpus(
        self,
        site: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del site
        self.corpus_calls += 1
        return list(self.records), _corpus_info()

    def search(
        self,
        query: str,
        site: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        raise AssertionError("完整树召回不应调用旧 search")

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
    ) -> dict[str, Any]:
        raise AssertionError("完整树召回不应逐候选调用 detail")


class RemoteProvider:
    platform = "mercadolibre"

    def __init__(
        self,
        discoveries: dict[str, list[dict[str, Any]]] | None = None,
        *,
        preflight_error: Exception | None = None,
        discovery_error: Exception | None = None,
    ) -> None:
        self.discoveries = discoveries or {}
        self.preflight_error = preflight_error
        self.discovery_error = discovery_error
        self.discovery_queries: list[str] = []
        self.detail_calls: Counter[str] = Counter()

    def resolve_site(self, site: str = "") -> str:
        return str(site or "MLM").upper()

    def preflight(self, site: str = "") -> dict[str, Any]:
        if self.preflight_error:
            raise self.preflight_error
        now = "2026-07-30T00:00:00+00:00"
        return {
            "ok": True,
            "platform": self.platform,
            "site": self.resolve_site(site),
            "retrieval_mode": "remote_discovery",
            "corpus_info": {
                "corpus_hash": "",
                "taxonomy_version": None,
                "locale": "es-MX",
                "retrieved_at": now,
                "expires_at": now,
                "credential_scope_hash": "sha256:public-mlm",
            },
        }

    def discover(
        self,
        query: str,
        site: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        del site, limit
        self.discovery_queries.append(query)
        if self.discovery_error:
            raise self.discovery_error
        return list(self.discoveries.get(query, []))

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
    ) -> dict[str, Any]:
        del include_attributes
        self.detail_calls[category_id] += 1
        return {
            "category_id": category_id,
            "name_original": {
                "MLM-FAN": "Ventiladores",
                "MLM-COOL": "Climatizadores",
                "MLM-USB": "Accesorios USB",
            }.get(category_id, category_id),
            "path_original": [
                "Hogar",
                {
                    "MLM-FAN": "Ventiladores",
                    "MLM-COOL": "Climatizadores",
                    "MLM-USB": "Accesorios USB",
                }.get(category_id, category_id),
            ],
            "site": site,
        }

    def search(
        self,
        query: str,
        site: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        raise AssertionError("多查询召回不应调用旧 search")


def test_normalization_and_query_variants_are_broad_to_narrow() -> None:
    assert normalize_category_text("  ＵＳＢ—ВЕНТИЛЯТОР  ") == "usb вентилятор"

    variants = build_category_query_variants(
        {
            "query": "ACME X1 настольный USB вентилятор",
            "product_type": "вентилятор",
            "brand": "ACME",
            "model": "X1",
            "synonyms": ["вентиляторы бытовые"],
            "key_attributes": {"power_source": "USB"},
        }
    )

    assert variants[0] == {
        "query": "вентилятор",
        "source": "head_noun",
        "weight": 1.0,
    }
    assert [item["source"] for item in variants] == [
        "head_noun",
        "synonym",
        "without_brand_model",
        "specific_query",
        "key_attributes",
    ]
    assert len({item["query"] for item in variants}) == len(variants)


def test_ozon_specific_product_phrase_recalls_center_category() -> None:
    provider = LocalTreeProvider(
        [
            {
                "category_id": "fan-1",
                "type_id": "fan-1",
                "description_category_id": "climate",
                "name_original": "Вентиляторы бытовые",
                "path_original": [
                    "Бытовая техника",
                    "Климатическая техника",
                    "Вентиляторы бытовые",
                ],
            },
            {
                "category_id": "usb-1",
                "type_id": "usb-1",
                "description_category_id": "electronics",
                "name_original": "USB аксессуары",
                "path_original": ["Электроника", "USB аксессуары"],
            },
        ]
    )

    result = CategoryCandidateRetriever(lambda platform: provider).retrieve(
        {
            "platform": "ozon",
            "query": "настольный usb вентилятор",
            "product_type": "вентилятор",
            "limit": 20,
        }
    )

    assert result["retrieval_mode"] == "full_tree_local"
    assert result["candidates"][0]["category_id"] == "fan-1"
    assert "head_noun" in result["candidates"][0]["retrieval_sources"]
    assert result["candidates"][0]["path_segments"][-1] == (
        "Вентиляторы бытовые"
    )
    assert provider.preflight_calls == 1
    assert provider.corpus_calls == 1
    for forbidden in ("id", "path", "category_path", "raw"):
        assert forbidden not in result["candidates"][0]


def test_ozon_multiword_product_type_prefers_head_noun_over_modifier() -> None:
    provider = LocalTreeProvider(
        [
            {
                "category_id": "94765",
                "type_id": "94765",
                "description_category_id": "17027949",
                "name_original": "Шины для легковых автомобилей",
                "path_original": [
                    "Автотовары",
                    "Шины",
                    "Шины для легковых автомобилей",
                ],
            },
            {
                "category_id": "95885",
                "type_id": "95885",
                "description_category_id": "auto-tools",
                "name_original": "Домкрат автомобильный",
                "path_original": ["Автотовары", "Домкрат автомобильный"],
            },
        ]
    )

    result = CategoryCandidateRetriever(lambda platform: provider).retrieve(
        {
            "platform": "ozon",
            "query": "автомобильные шины",
            "product_type": "автомобильные шины",
        }
    )

    assert result["candidates"][0]["category_id"] == "94765"
    assert result["query_variants"][0]["query"] == "шины"
    assert result["query_variants"][0]["source"] == "head_noun"


def test_ozon_platform_synonym_bridges_daily_name_to_taxonomy_term() -> None:
    provider = LocalTreeProvider(
        [
            {
                "category_id": "971168976",
                "type_id": "971168976",
                "description_category_id": "17027899",
                "name_original": "Колье",
                "path_original": [
                    "Галантерея и аксессуары",
                    "Бижутерные украшения",
                    "Колье",
                ],
            }
        ]
    )

    result = CategoryCandidateRetriever(lambda platform: provider).retrieve(
        {
            "platform": "ozon",
            "query": "женское ожерелье",
            "product_type": "ожерелье",
        }
    )

    assert result["candidates"][0]["category_id"] == "971168976"
    assert "synonym" in result["candidates"][0]["retrieval_sources"]


def test_local_candidate_merge_deduplicates_ids_and_sorts_stably() -> None:
    provider = LocalTreeProvider(
        [
            {
                "category_id": "b",
                "name_original": "Вентиляторы",
                "path_original": ["Дом", "Вентиляторы"],
            },
            {
                "category_id": "a",
                "name_original": "Вентиляторы",
                "path_original": ["Бытовая техника", "Вентиляторы"],
            },
            {
                "category_id": "a",
                "name_original": "Вентиляторы",
                "path_original": ["Дубликат", "Вентиляторы"],
            },
        ]
    )

    result = CategoryCandidateRetriever(lambda platform: provider).retrieve(
        {
            "platform": "ozon",
            "query": "вентилятор",
            "product_type": "вентилятор",
        }
    )

    assert [item["category_id"] for item in result["candidates"]] == ["a", "b"]


def test_mercadolibre_multi_query_merges_before_detail_and_deduplicates() -> None:
    provider = RemoteProvider(
        {
            "ventilador": [
                {
                    "category_id": "MLM-FAN",
                    "name": "Ventiladores",
                    "provider_rank": 0,
                },
                {
                    "category_id": "MLM-COOL",
                    "name": "Climatizadores",
                    "provider_rank": 1,
                },
            ],
            "ventiladores": [
                {
                    "category_id": "MLM-FAN",
                    "name": "Ventiladores",
                    "provider_rank": 0,
                }
            ],
            "ventilador usb escritorio": [
                {
                    "category_id": "MLM-USB",
                    "name": "Accesorios USB",
                    "provider_rank": 0,
                },
                {
                    "category_id": "MLM-FAN",
                    "name": "Ventiladores",
                    "provider_rank": 1,
                },
            ],
        }
    )

    result = CategoryCandidateRetriever(lambda platform: provider).retrieve(
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "query": "ventilador USB escritorio",
            "product_type": "ventilador",
            "synonyms": ["ventiladores"],
            "limit": 3,
        }
    )

    ids = [item["category_id"] for item in result["candidates"]]
    assert ids[0] == "MLM-FAN"
    assert set(ids) == {"MLM-FAN", "MLM-COOL", "MLM-USB"}
    assert provider.detail_calls == Counter(
        {"MLM-FAN": 1, "MLM-COOL": 1, "MLM-USB": 1}
    )
    assert result["corpus_info"]["corpus_hash"].startswith("sha256:")
    assert result["coverage"]["matched_query_variant_count"] == 3
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("provider", "expected_code", "expected_stage"),
    [
        (
            RemoteProvider(
                preflight_error=RuntimeError(
                    "请先填写 Ozon Client ID 和 API Key。"
                )
            ),
            "CATEGORY_CREDENTIALS_MISSING",
            "preflight",
        ),
        (
            RemoteProvider(
                discovery_error=RuntimeError(
                    "GET domain_discovery failed: 400 invalid site"
                )
            ),
            "CATEGORY_PROVIDER_BAD_REQUEST",
            "discovery",
        ),
    ],
)
def test_provider_failures_are_not_reported_as_zero_retrieval(
    provider: RemoteProvider,
    expected_code: str,
    expected_stage: str,
) -> None:
    with pytest.raises(CategoryRetrievalError) as raised:
        CategoryCandidateRetriever(lambda platform: provider).retrieve(
            {
                "platform": "mercadolibre",
                "query": "ventilador",
                "product_type": "ventilador",
            }
        )

    assert raised.value.code == expected_code
    assert raised.value.stage == expected_stage
    assert raised.value.to_dict()["code"] != "RETRIEVAL_ZERO"


def test_malformed_discovery_payload_is_not_reported_as_zero_retrieval(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        category_providers,
        "http_json",
        lambda url: {"unexpected": "payload"},
    )

    with pytest.raises(CategoryRetrievalError) as raised:
        CategoryCandidateRetriever(
            lambda platform: MercadoLibreCategoryProvider()
        ).retrieve(
            {
                "platform": "mercadolibre",
                "site": "MLM",
                "query": "ventilador",
                "product_type": "ventilador",
            }
        )

    assert raised.value.code == "CATEGORY_PROVIDER_ERROR"
    assert raised.value.stage == "discovery"

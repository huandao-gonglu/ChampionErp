from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from erp_web.runtime_units.category_retrieval import (
    CategoryCandidateRetriever,
    aggregate_retrieval_baseline,
    category_tokens,
    retrieval_recall_at,
)


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = ROOT / "tests/fixtures/category_retrieval_golden.json"
DIFFICULTIES = ("L0", "L1", "L2", "L3", "L4", "L1", "L2", "L3", "L4", "L5")


def _load_fixture() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _golden_samples(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for group in fixture["groups"]:
        for index, title in enumerate(group["titles"]):
            sample_id = (
                f"{group['target_platform']}-"
                f"{group['expected_category_id'].lower()}-{index + 1:02d}"
            )
            samples.append(
                {
                    "sample_id": sample_id,
                    "product_id": f"product-{sample_id}",
                    "source_language": group["source_language"],
                    "source_title": title,
                    "source_description": title,
                    "key_attributes": dict(group["key_attributes"]),
                    "target_platform": group["target_platform"],
                    "target_site": group["target_site"],
                    "expected_category_id": group["expected_category_id"],
                    "acceptable_ancestor_ids": list(
                        group["acceptable_ancestor_ids"]
                    ),
                    "hard_negative_category_ids": list(
                        group["hard_negative_category_ids"]
                    ),
                    "difficulty": DIFFICULTIES[index],
                    "human_reason": group["human_reason"],
                    "adjudicator": fixture["adjudicator"],
                    "taxonomy_version": fixture["taxonomy_version"],
                    "product_type": group["product_type"],
                    "synonyms": list(group["synonyms"]),
                }
            )
    return samples


def _corpus_info(locale: str, platform: str) -> dict[str, Any]:
    return {
        "corpus_hash": f"sha256:offline-{platform}",
        "taxonomy_version": "offline-taxonomy-2026-07",
        "locale": locale,
        "retrieved_at": "2026-07-30T00:00:00+00:00",
        "expires_at": "2026-07-30T00:15:00+00:00",
        "credential_scope_hash": f"sha256:offline-{platform}-scope",
    }


class OfflineOzonProvider:
    platform = "ozon"

    def __init__(self, groups: list[dict[str, Any]]) -> None:
        self.records = [
            {
                "category_id": group["expected_category_id"],
                "type_id": group["expected_category_id"],
                "description_category_id": group["acceptable_ancestor_ids"][0],
                "name_original": group["category_name"],
                "path_original": list(group["category_path"]),
            }
            for group in groups
        ]

    def resolve_site(self, site: str = "") -> str:
        del site
        return "global"

    def preflight(self, site: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "platform": self.platform,
            "site": self.resolve_site(site),
            "retrieval_mode": "full_tree_local",
            "corpus_info": _corpus_info("ru-RU", self.platform),
        }

    def category_corpus(
        self,
        site: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del site
        return list(self.records), _corpus_info("ru-RU", self.platform)

    def search(
        self,
        query: str,
        site: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        raise AssertionError("离线完整树基线不调用旧 search")

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
    ) -> dict[str, Any]:
        raise AssertionError("离线完整树基线不逐项读取详情")


class OfflineMercadoLibreProvider:
    platform = "mercadolibre"

    def __init__(self, groups: list[dict[str, Any]]) -> None:
        self.groups = groups
        self.by_id = {
            group["expected_category_id"]: group for group in groups
        }

    def resolve_site(self, site: str = "") -> str:
        return str(site or "MLM").upper()

    def preflight(self, site: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "platform": self.platform,
            "site": self.resolve_site(site),
            "retrieval_mode": "remote_discovery",
            "corpus_info": _corpus_info("es-MX", self.platform),
        }

    def discover(
        self,
        query: str,
        site: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        del site
        query_terms = set(category_tokens(query))
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for group in self.groups:
            search_terms = set(
                category_tokens(
                    " ".join(
                        [
                            group["category_name"],
                            group["product_type"],
                            *group["synonyms"],
                        ]
                    )
                )
            )
            overlap = len(query_terms & search_terms)
            if overlap:
                scored.append(
                    (
                        overlap,
                        group["expected_category_id"],
                        group,
                    )
                )
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            {
                "category_id": group["expected_category_id"],
                "name": group["category_name"],
                "provider_rank": rank,
            }
            for rank, (_, _, group) in enumerate(scored[:limit])
        ]

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
    ) -> dict[str, Any]:
        del include_attributes
        group = self.by_id[category_id]
        return {
            "category_id": category_id,
            "name_original": group["category_name"],
            "path_original": list(group["category_path"]),
            "site": self.resolve_site(site),
        }

    def search(
        self,
        query: str,
        site: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        raise AssertionError("离线 discovery 基线不调用旧 search")


def test_golden_set_has_200_complete_stratified_samples() -> None:
    fixture = _load_fixture()
    samples = _golden_samples(fixture)
    required_fields = {
        "sample_id",
        "product_id",
        "source_language",
        "source_title",
        "source_description",
        "key_attributes",
        "target_platform",
        "target_site",
        "expected_category_id",
        "acceptable_ancestor_ids",
        "hard_negative_category_ids",
        "difficulty",
        "human_reason",
        "adjudicator",
        "taxonomy_version",
    }

    assert fixture["schema_version"] == "category-retrieval-golden.v1"
    assert len(fixture["groups"]) == 20
    assert len(samples) == 200
    assert len({sample["sample_id"] for sample in samples}) == 200
    assert all(required_fields <= sample.keys() for sample in samples)
    assert {sample["target_platform"] for sample in samples} == {
        "mercadolibre",
        "ozon",
    }
    assert {sample["difficulty"] for sample in samples} == {
        "L0",
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
    }
    assert all(
        re.fullmatch(
            r"\d+" if sample["target_platform"] == "ozon" else r"MLM\d+",
            sample["expected_category_id"],
        )
        for sample in samples
    )


def test_golden_retrieval_baseline_meets_shadow_recall_threshold() -> None:
    fixture = _load_fixture()
    samples = _golden_samples(fixture)
    ozon_groups = [
        group
        for group in fixture["groups"]
        if group["target_platform"] == "ozon"
    ]
    ml_groups = [
        group
        for group in fixture["groups"]
        if group["target_platform"] == "mercadolibre"
    ]
    providers = {
        "ozon": OfflineOzonProvider(ozon_groups),
        "mercadolibre": OfflineMercadoLibreProvider(ml_groups),
    }
    retriever = CategoryCandidateRetriever(lambda platform: providers[platform])
    evaluated: list[dict[str, Any]] = []

    for sample in samples:
        result = retriever.retrieve(
            {
                "platform": sample["target_platform"],
                "site": sample["target_site"],
                "query": sample["source_title"],
                "product_type": sample["product_type"],
                "synonyms": sample["synonyms"],
                "key_attributes": sample["key_attributes"],
                "limit": 20,
            }
        )
        evaluated.append(
            {
                **sample,
                "candidate_count": len(result["candidates"]),
                "candidate_ids_valid": all(
                    str(candidate.get("category_id") or "")
                    for candidate in result["candidates"]
                ),
                "recall_at_5": retrieval_recall_at(
                    result["candidates"],
                    sample["expected_category_id"],
                    sample["acceptable_ancestor_ids"],
                    k=5,
                ),
                "recall_at_20": retrieval_recall_at(
                    result["candidates"],
                    sample["expected_category_id"],
                    sample["acceptable_ancestor_ids"],
                    k=20,
                ),
            }
        )

    baseline = aggregate_retrieval_baseline(evaluated)

    assert baseline["sample_count"] == 200
    assert baseline["recall_at_20"] >= 0.9
    assert baseline["zero_retrieval_rate"] <= 0.1
    assert all(
        stratum["recall_at_20"] >= 0.9
        for stratum in baseline["strata"].values()
    )
    assert all(sample["candidate_ids_valid"] for sample in evaluated)

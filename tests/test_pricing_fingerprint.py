"""核价依据经过页面 JSON 往返后仍可验证，真实变化仍使报价失效。"""

from copy import deepcopy
import hashlib
import json

import pytest

from erp_web.services.pricing_service import (
    pricing_calculation_fingerprint,
    pricing_calculation_fingerprint_matches,
)


def _basis():
    return {
        "listing_currency": "CNY", "cost_cny": "14", "battery": False,
        "commission_evidence": {
            "platform": "yandex", "endpoint": "/v2/tariffs/calculate",
            "amount": "19.82", "quoted_at": "2026-10-22T14:17:33Z",
            "request": {"parameters": {"campaignId": 123}, "offers": [{
                "categoryId": 456, "quantity": 1, "price": 82.58,
                "length": 18.0, "width": 18.0, "height": 2.0, "weight": 0.1,
            }]},
        },
    }


def _page_roundtrip(basis):
    # 浏览器 JSON.stringify 将整数值 Number 序列化为不带小数点的 JSON 数字。
    return json.loads(json.dumps(basis).replace('18.0', '18').replace('2.0', '2'))


def _legacy_fingerprint(basis):
    return hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("legacy", [False, True])
def test_quote_remains_verifiable_after_page_save(legacy):
    basis = _basis()
    before = deepcopy(basis)
    fingerprint = _legacy_fingerprint(basis) if legacy else pricing_calculation_fingerprint(basis)
    assert pricing_calculation_fingerprint_matches(basis, fingerprint)
    saved = _page_roundtrip(basis)
    assert pricing_calculation_fingerprint_matches(saved, fingerprint)
    assert basis == before
    assert type(saved["commission_evidence"]["request"]["offers"][0]["height"]) is int
    assert pricing_calculation_fingerprint(saved) == pricing_calculation_fingerprint(basis)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("field,value", [("length", 18.001), ("weight", 0.101), ("price", 82.59), ("categoryId", 457)])
def test_real_changes_cannot_reuse_saved_fingerprint(legacy, field, value):
    basis = _basis()
    fingerprint = _legacy_fingerprint(basis) if legacy else pricing_calculation_fingerprint(basis)
    saved = _page_roundtrip(basis)
    saved["commission_evidence"]["request"]["offers"][0][field] = value
    assert not pricing_calculation_fingerprint_matches(saved, fingerprint)


def test_new_fingerprint_preserves_types_and_small_numeric_changes():
    assert pricing_calculation_fingerprint({"value": 18.0}) == pricing_calculation_fingerprint({"value": 18})
    for value in ("18", True, 18.000001):
        assert pricing_calculation_fingerprint({"value": value}) != pricing_calculation_fingerprint({"value": 18})
    assert pricing_calculation_fingerprint({"value": True}) != pricing_calculation_fingerprint({"value": 1})
    with pytest.raises(ValueError, match="非有限"):
        pricing_calculation_fingerprint({"value": float("nan")})


def test_legacy_recovery_is_limited_to_known_yandex_request():
    basis = _basis()
    basis["commission_evidence"]["platform"] = "ozon"
    fingerprint = _legacy_fingerprint(basis)
    assert pricing_calculation_fingerprint_matches(basis, fingerprint)
    assert not pricing_calculation_fingerprint_matches(_page_roundtrip(basis), fingerprint)
    assert not pricing_calculation_fingerprint_matches(basis, "v3:unknown")

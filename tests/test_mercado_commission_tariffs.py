"""Mercado 新品按 CBT 类目、国家、刊登类型和固定费用独立试算。"""
from decimal import Decimal
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from erp_web.services import mercado_commission_tariffs as tariffs


RATES = {"USD": Decimal(1), "MXN": Decimal(20), "BRL": Decimal(5), "CLP": Decimal(950), "COP": Decimal(4000)}


def convert(amount, currency):
    return amount / RATES[currency]


@pytest.fixture
def official_pages(monkeypatch):
    state = {"calls": [], "percent": 20, "error": False, "site": None, "bad_schema": False}
    pricing = b"""<div>Additional per unit fee</div><span>MXN $33</span><span>For products up to MXN $299</span>
<div>Additional per unit fee</div><span>BRL $9.6</span><span>For products up to BRL $79</span>
<div>Additional per unit fee</div><span>CLP $1.650</span><span>For products up to CLP $15.650</span>
<div>Additional per unit fee</div><span>COP $7.200</span><span>For products up to COP $60.000</span>"""
    def read(url, **kwargs):
        state["calls"].append((url, kwargs))
        if url == tariffs.PRICING_PAGE:
            return b"<html>changed</html>" if state["bad_schema"] else pricing
        parsed = urlsplit(url)
        assert parsed.path.endswith("/CBT32271/CBT-DOG_HOUSES")
        assert kwargs == {"referer": tariffs.CATEGORY_PAGE}
        site = parse_qs(parsed.query)["sites"][0]
        return json.dumps([{"site_id": state["site"] or site,
            "classic": {"listing_type_id": "gold_special", "sale_fee_amount": 12.5},
            "premium": {"listing_type_id": "gold_pro", "sale_fee_amount": state["percent"], "error": state["error"]}}]).encode()
    monkeypatch.setattr(tariffs, "read_official_content", read)
    return state


def test_cbt_category_fee_is_percentage_and_includes_current_low_price_fixed_fee(official_pages):
    fees = tariffs.MercadoCommissionTariffs()
    fee, proof = fees.quote("CBT32271", "CBT-DOG_HOUSES", "MLM", "remote", "gold_pro", Decimal(50), convert)
    assert fee == 10 and proof["percentage_fee"] == "20"
    fee, proof = fees.quote("CBT32271", "CBT-DOG_HOUSES", "MLM", "remote", "gold_pro", Decimal(10), convert)
    assert fee == Decimal("3.65") and proof["fixed_fee"] == "1.65"
    assert proof["fixed_fee_rule"] == {"amount": "33", "maximum_price": "299", "currency": "MXN"}
    assert len(official_pages["calls"]) == 2


def test_countries_logistics_and_listing_types_are_independent(official_pages):
    fees = tariffs.MercadoCommissionTariffs()
    mx, _ = fees.quote("CBT32271", "CBT-DOG_HOUSES", "MLM", "remote", "gold_special", Decimal(50), convert)
    cl, proof = fees.quote("CBT32271", "CBT-DOG_HOUSES", "MLC", "fulfillment", "gold_pro", Decimal(50), convert)
    assert mx == Decimal("6.25") and cl == 10
    params = parse_qs(urlsplit(proof["endpoint"]).query)
    assert params == {"sites": ["MLC"], "logistic_type": ["fulfillment"]}
    assert len(official_pages["calls"]) == 3
    assert proof["fixed_fee_rule"]["amount"] == "1650"


@pytest.mark.parametrize(("key", "value"), [("error", True), ("site", "MLB"), ("percent", "NaN"), ("percent", None)])
def test_wrong_country_or_invalid_official_fee_is_not_accepted(official_pages, key, value):
    official_pages[key] = value
    with pytest.raises(ValueError):
        tariffs.MercadoCommissionTariffs().quote("CBT32271", "CBT-DOG_HOUSES", "MLM", "remote", "gold_pro", Decimal(50), convert)


def test_missing_category_and_unresolved_country_rules_are_blocked(official_pages):
    fees = tariffs.MercadoCommissionTariffs()
    with pytest.raises(ValueError, match="末级 CBT"):
        fees.quote("CBT999", "", "MLM", "remote", "gold_pro", Decimal(50), convert)
    with pytest.raises(ValueError, match="完整固定收费"):
        fees.quote("CBT32271", "CBT-DOG_HOUSES", "MLA", "remote", "gold_special", Decimal(50), convert)
    with pytest.raises(ValueError, match="类目豁免"):
        fees.quote("CBT32271", "CBT-DOG_HOUSES", "MCO", "remote", "gold_pro", Decimal(10), convert)
    assert fees.quote("CBT32271", "CBT-DOG_HOUSES", "MCO", "remote", "gold_pro", Decimal(50), convert)[0] == 10


def test_schema_failure_shared_in_batch_and_next_batch_refreshes_rates(official_pages):
    fees = tariffs.MercadoCommissionTariffs()
    assert fees.quote("CBT32271", "CBT-DOG_HOUSES", "MLM", "remote", "gold_pro", Decimal(50), convert)[0] == 10
    official_pages["percent"] = 25
    assert tariffs.MercadoCommissionTariffs().quote("CBT32271", "CBT-DOG_HOUSES", "MLM", "remote", "gold_pro", Decimal(50), convert)[0] == Decimal("12.5")
    official_pages["bad_schema"] = True
    failed = tariffs.MercadoCommissionTariffs()
    before = len(official_pages["calls"])
    for _ in range(2):
        with pytest.raises(ValueError, match="完整固定收费"):
            failed.quote("CBT32271", "CBT-DOG_HOUSES", "MLM", "remote", "gold_pro", Decimal(50), convert)
    assert len(official_pages["calls"]) == before + 1

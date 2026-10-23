"""用逐分枚举作为独立对照，验证分段解析求解与舍入边界。"""
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from itertools import product

import pytest

from erp_web.services.pricing_solver import PricingGoal, fraction


def rule(**kwargs):
    return {"kind": "relative", "value": "27", "minimum": None, "maximum": None,
            "price_from": "0", "price_to": None, **kwargs}


def oracle(goal, tariff, stop=10000):
    for cents in range(1, stop + 1):
        price = Decimal(cents) / 100
        if price <= Decimal(tariff["price_from"]) or (tariff["price_to"] is not None and price >= Decimal(tariff["price_to"])):
            continue
        fee = price * Decimal(tariff["value"]) / 100 if tariff["kind"] == "relative" else Decimal(tariff["value"])
        if tariff["minimum"] is not None:
            fee = max(fee, Decimal(tariff["minimum"]))
        if tariff["maximum"] is not None:
            fee = min(fee, Decimal(tariff["maximum"]))
        fee = fee.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        if Fraction(price) >= goal.floor and goal.retained * Fraction(price) - Fraction(fee) >= goal.required:
            return price
    return None


@pytest.mark.parametrize("mode,cost,floor,tariff", list(product(
    ["margin", "markup"], [".013", "1.001", "10.337"], ["0", "23.456"],
    [rule(), rule(value="0"), rule(kind="absolute", value="5.123"),
     rule(minimum="8.555"), rule(maximum="2.005"), rule(minimum="2.135", maximum="8.555"),
     rule(value="90", maximum="3.005"), rule(price_from="12.345", price_to="70")],
)))
def test_piecewise_solution_matches_cent_enumeration(mode, cost, floor, tariff):
    goal = PricingGoal(fraction(".67" if mode == "margin" else ".97"),
                       fraction(cost) * (fraction("1.3") if mode == "markup" else 1), fraction(floor))
    expected = oracle(goal, tariff)
    assert expected is not None
    assert goal.solve_rule(tariff) == expected


@pytest.mark.parametrize("retained,value,cost", [
    (".7", "70", ".001"), (".69", "70", ".001"),
    (".7", "69.9999999999", "107"),
])
def test_equal_or_nearly_equal_slopes_do_not_use_an_iteration_budget(retained, value, cost):
    goal = PricingGoal(fraction(retained), fraction(cost))
    result = goal.solve_rule(rule(value=value))
    if Decimal(cost) < 1:
        assert result == oracle(goal, rule(value=value))
    else:
        price = Fraction(result)
        fee = (result * Decimal(value) / 100).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        assert goal.meets(price, fee)


def test_unknown_adjacent_tier_is_returned_for_official_verification():
    goal = PricingGoal(fraction(".7"), fraction("137"))
    assert goal.solve_rule(rule(value="10", price_to="200")) == Decimal("200")


def test_unattainable_goal_fails_without_an_unverified_price():
    with pytest.raises(ValueError, match="无法达到"):
        PricingGoal(fraction(".7"), fraction("107")).solve_rule(rule(value="80"))


def test_currency_conversion_does_not_round_an_exact_solution_up_one_cent():
    assert PricingGoal(fraction(".7"), Fraction(49, 7)).proportional_price(Fraction(0)) == Decimal("10")

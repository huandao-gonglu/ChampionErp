"""Ozon 上架前官方表的版本发现、精确类型、品牌特例及价格档验证。"""
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import json
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from erp_web.services import ozon_commission_tariffs as tariffs
from erp_web.services.pricing_commissions import PricingCommissions
from erp_web.services.pricing_service import calculate_target_pricing, has_official_commission
from tests.test_pricing_commissions import COMMON, CONFIG, TARGET


FILE_URL = "https://cdn.ozone.ru/s3/ozon-disk-api/global-education/Tarifs_CN_current.xlsx"
CATEGORY = {"type_id": "123", "description_category_id": "456", "name_original": "Смартфон",
            "path_original": ["Электроника", "Телефоны", "Смартфон"]}


def workbook_bytes(*, effective="01/12/2025", brand_rule=True):
    headers = {"A": "DescriptiveType", "D": "DescriptiveCategory3", "J": "Brand",
               "K": "RFBS -> 0 - 1500 -> Тариф, %", "L": "RFBS -> 1500.01 - 5000 -> Тариф, %",
               "M": "RFBS -> 5000.01+ -> Тариф, %"}
    rows = [{"J": f"Starts from {effective}"}, headers,
            {"A": "Смартфон", "D": "Телефоны", "J": "All", "K": "0.12", "L": "0.14", "M": "0.18"}]
    if brand_rule:
        rows.append({"A": "Смартфон", "D": "Телефоны", "J": "Apple", "K": "0.07", "L": "0.07", "M": "0.07"})
    sheet_rows = []
    for number, row in enumerate(rows, 1):
        cells = ''.join(f'<c r="{column}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for column, value in row.items())
        sheet_rows.append(f'<row r="{number}">{cells}</row>')
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Full ChinaHK" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(sheet_rows) + '</sheetData></worksheet>')
    return buffer.getvalue()


@pytest.fixture
def official_table(monkeypatch):
    state = {"url": FILE_URL, "xlsx": workbook_bytes(), "calls": []}
    def read(url):
        state["calls"].append(url)
        if url == tariffs.DOCUMENT_URL:
            return json.dumps({"document": {"status": "active", "url": "/commissions/ozon-fees/commissions",
                "updated": "2026-10-18", "contentJson": json.dumps({"marks": [{"attrs": {"href": state["url"]}}]})}}).encode()
        assert url == state["url"]
        return state["xlsx"]
    monkeypatch.setattr(tariffs, "read_official_content", read)
    return state


def brand(name):
    return {"values": [{"dictionary_value_id": "1234", "value": name}]}


@pytest.mark.parametrize(("price", "percent", "column"), [("125", "12", "K"), ("125.01", "14", "L"), ("500", "18", "M")])
def test_ozon_prelisting_uses_current_table_and_ruble_band(official_table, price, percent, column):
    loader_calls = []
    fees = PricingCommissions(CONFIG, ozon_category_loader=lambda: loader_calls.append(True) or [CATEGORY])
    target = {**TARGET, "platform": "ozon", "brand_attribute": brand("Generic")}
    quote = fees(COMMON, target, {"amount": price, "currency": "CNY"})
    assert Decimal(quote["percent"]) == Decimal(percent)
    evidence = quote["evidence"]
    assert evidence["source"] == "official_tariff" and evidence["purpose"] == "prelisting"
    assert evidence["source_column"] == column and evidence["file_url"] == FILE_URL
    assert evidence["type_id"] == "123" and evidence["description_category_id"] == "456"
    assert evidence["file_sha256"] and evidence["effective_from"] == "2025-12-01"
    assert "secret" not in str(evidence)
    fees(COMMON, target, {"amount": "600", "currency": "CNY"})
    assert len(official_table["calls"]) == 2 and len(loader_calls) == 1


def test_special_brand_rate_and_missing_brand_are_distinct(official_table):
    fees = tariffs.OzonCommissionTariffs()
    assert fees.quote(CATEGORY, brand("Apple"), Decimal("6000"))[0] == Decimal("0.07")
    with pytest.raises(ValueError, match="品牌"):
        fees.quote(CATEGORY, None, Decimal("6000"))
    official_table["xlsx"] = workbook_bytes(brand_rule=False)
    assert tariffs.OzonCommissionTariffs().quote(CATEGORY, None, Decimal("6000"))[0] == Decimal("0.18")


def test_type_name_alone_is_not_enough_to_match_official_fee(official_table):
    category = deepcopy(CATEGORY)
    category["path_original"][-2] = "其他类目"
    with pytest.raises(ValueError, match="精确组合"):
        tariffs.OzonCommissionTariffs().quote(category, brand("Generic"), Decimal("6000"))


def test_future_or_untrusted_table_is_not_used(official_table):
    official_table["xlsx"] = workbook_bytes(effective="01/12/2099")
    with pytest.raises(ValueError, match="尚未生效"):
        tariffs.OzonCommissionTariffs().quote(CATEGORY, brand("Generic"), Decimal("6000"))
    official_table["url"] = "https://example.com/Tarifs_CN_current.xlsx"
    with pytest.raises(ValueError, match="唯一"):
        tariffs.OzonCommissionTariffs().quote(CATEGORY, brand("Generic"), Decimal("6000"))


def test_next_batch_discovers_new_official_version_and_price_converges(official_table, tmp_path):
    target = {**TARGET, "platform": "ozon", "brand_attribute": brand("Generic")}
    def quote():
        from erp_web.services.pricing_shipping import PricingShipping
        fees = PricingCommissions(CONFIG, ozon_category_loader=lambda: [CATEGORY])
        shipping = PricingShipping(tmp_path, CONFIG)
        shipping.commission_resolver = fees
        selected = {**target, 'shipping_quote_mode': 'auto'}
        return calculate_target_pricing(COMMON, selected, shipping_quote=shipping(COMMON, selected), commission_resolver=fees)
    first = quote()
    assert first["ok"] and has_official_commission(first["calculation_basis"])
    official_table["url"] = FILE_URL.replace("current", "new")
    official_table["xlsx"] = workbook_bytes(effective="01/01/2026", brand_rule=False)
    second = quote()
    assert second["ok"]
    proof = second["calculation_basis"]["commission_evidence"]
    assert proof["file_url"] != first["calculation_basis"]["commission_evidence"]["file_url"]
    assert proof["price"] == second["applied_price"]

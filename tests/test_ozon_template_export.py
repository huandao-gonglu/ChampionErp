"""离线导出：官方模板结构、源身份、字段事实与生成前的并发校验。"""
import base64
import io
import json
import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from erp_web.context import get_context
from erp_web.schemas.online_products import OnlineListing, PriceScope
from erp_web.services.online_ozon_export import OnlineOzonExportService
from erp_web.services.ozon_category_template import MAIN, OzonCategoryTemplate
from erp_web.stores.online_product_store import OnlineConflict, OnlineProductStore


def template_bytes(*, category="43429543", category_name="圣诞装饰品", type_id="95421", currency="CNY", populated=False, changed_required=False, type_options=None, default_type=None, brand_values=None, oversized=False, no_brand="无品牌"):
    names = {"list_name": "模板", "offer_id": "货号", "name": "商品名称", "price": "非促销最高价格，CNY",
             "barcode": "条形码（序列号/EAN）", "weight": "毛重，克", "width": "包装宽度，毫米",
             "height": "包装高度，毫米", "depth": "包装长度，毫米", "picture": "主图链接",
             "pictures": "附加图片链接", "desc_type": "类型", "review_promo": "加速评价收集", "promotion_no": "否", "no_brand": no_brand}
    info = {"name": category_name, "additional_column_by_name": names,
            "attributes": {"85": {"Name": "品牌", "IsRequired": True, "IsOversized": oversized,
                                   "LookupData": {"Values": {str(i): {"Value": value} for i, value in enumerate(brand_values or [])}}},
                           "9048": {"Name": "型号名称（针对合并为一张商品卡片）", "IsRequired": True},
                           "8229": {"IsRequired": True, "LookupData": {"Values": {type_id: {"ID": int(type_id), "Value": category_name}}}}}}
    if type_options is not None:
        info["attributes"]["8229"]["LookupData"]["Values"] = {str(i): {"ID": i, "Value": value} for i, value in enumerate(type_options)}
    if changed_required:
        info["attributes"]["123"] = {"Name": "套装件数", "IsRequired": True, "Type": "Integer"}
    encoded = base64.b64encode(json.dumps(info, ensure_ascii=False).encode()).decode()
    def sheet(rows, *, main=False):
        root = ET.Element("worksheet", {"xmlns": MAIN})
        if main: ET.SubElement(root, "dimension", {"ref": "A1:AU100"})
        data = ET.SubElement(root, "sheetData")
        for index, values in enumerate(rows, 1):
            row = ET.SubElement(data, "row", {"r": str(index)})
            for column, value in values.items():
                cell = ET.SubElement(row, "c", {"r": f"{column}{index}", "s": "1", "t": "inlineStr"})
                ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
        if main:
            ET.SubElement(root, "autoFilter", {"ref": "A4:AU5"})
            rules = ET.SubElement(root, "dataValidations", {"count": "1"})
            rule = ET.SubElement(rules, "dataValidation", {"sqref": "F5:F500002", "type": "list"})
            ET.SubElement(rule, "formula1").text = "name5"
        return ET.tostring(root)
    headers = {"A": "№", "B": names["offer_id"]+"*", "C": names["name"], "D": names["price"]+"*",
               "F": names["review_promo"], "G": "SKU", "H": names["barcode"], "J": names["weight"]+"*",
               "K": names["width"]+"*", "L": names["height"]+"*", "M": names["depth"]+"*", "N": names["picture"]+"*",
               "O": names["pictures"], "Q": "品牌*", "R": "型号名称（针对合并为一张商品卡片）*", "S": "长度，厘米",
               "U": "颜色名称", "W": names["desc_type"]+"*", "AT": "材料*" if changed_required else "材料", "AU": "套装件数" if changed_required else "缺点"}
    if category_name == "海绵":
        # 海绵模板在促销列后多一列，实际品牌、型号及条码等字段均随之右移。
        shifted = {"G": "H", "H": "I", "J": "K", "K": "L", "L": "M", "M": "N",
                   "N": "O", "O": "P", "Q": "R", "R": "S", "U": "T"}
        headers = {shifted.get(column, column): value for column, value in headers.items() if column != "S"}
        headers["G"] = "收集首批评价"
    configs = [{"A": key, "B": value} for key, value in {
        "CURRENCY": currency, "DESCRIPTION_CATEGORY_ID": category, "PRODUCTS_FIRST_DATA_ROW_INDEX": "5", "PRODUCTS_TITLE_ROW_INDEX": "2"}.items()]
    parts = {"xl/workbook.xml": f'<workbook xmlns="{MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="模板" sheetId="1" r:id="r1"/><sheet name="configs" sheetId="2" r:id="r2" state="hidden"/><sheet name="info" sheetId="3" r:id="r3" state="hidden"/><sheet name="validation" sheetId="4" r:id="r4" state="hidden"/></sheets><definedNames><definedName name="name5">validation!$F$1:$F$3</definedName><definedName localSheetId="0" name="_xlnm._FilterDatabase">模板!$A$4:$AU$5</definedName></definedNames></workbook>'.encode(),
             "xl/_rels/workbook.xml.rels": b'<Relationships>'+b''.join(f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>'.encode() for i in range(1, 5))+b'</Relationships>',
             "xl/worksheets/sheet1.xml": sheet([{"B": "官方说明"}, headers, {"B": "必填字段"}, {"B": "填写帮助"}, {"B": "", "D": "", "F": "Да", "W": category_name if default_type is None else default_type, **({"B": "已有商品"} if populated else {})}], main=True),
             "xl/worksheets/sheet2.xml": sheet(configs),
             "xl/worksheets/sheet3.xml": sheet([{"A": encoded[:80], "B": encoded[80:]}]),
             "xl/worksheets/sheet4.xml": sheet([{"F": ""}, {"F": "是"}, {"F": "否"}]),
             "xl/styles.xml": b'<styleSheet xmlns="'+MAIN.encode()+b'"/>',
             "[Content_Types].xml": b'<Types/>'}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items(): archive.writestr(name, content)
    return output.getvalue()


@pytest.fixture
def exporter():
    store = OnlineProductStore(get_context().db)
    for index in range(2):
        row = OnlineListing(id=f"listing-{index}", platform="yandex", account_id="business:campaign", remote_id=f"SELL-{index}",
            model="business_offer", seller_sku=f"SELL-{index}", title="Новогодняя подвеска DIY 20×20 см", sale_state="active",
            prices=[PriceScope(id="business", label="账号基础价", kind="base_price", amount="37.00", currency="CNY")],
            content={"attributes": [{"parameterId": 23679910, "unitId": 8, "value": "25"},
                                    {"parameterId": 14805336, "unitId": 8, "value": "25"}, {"parameterId": 14871214, "value": "EE031"}]},
            snapshot={"offer": {"vendor": "诚实", "groupId": "圣诞组合", "barcodes": ["0012345678905"] if index else [],
                "weightDimensions": {"length": 26, "width": 26, "height": 2, "weight": 0.1801},
                "mediaFiles": {"pictures": [{"url": "https://origin.example.com/image.jpg", "marketUrl": "https://images.yandex.example/image.jpg", "uploadState": "UPLOADED"},
                                           {"url": "https://images.example.com/failed.jpg", "uploadState": "FAILED"}]}}})
        store.save(row)
    service = OnlineOzonExportService(store, lambda: "business:campaign")
    body = {"account_id": "business:campaign", "listing_ids": ["listing-0", "listing-1"], "template_name": "圣诞装饰品.xlsx", "template_base64": base64.b64encode(template_bytes()).decode()}
    return service, body


def read_cells(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        return {c.get("r"): "".join(c.itertext()) for c in root.findall("{*}sheetData/{*}row/{*}c")}, archive.read("xl/workbook.xml")


def test_selected_single_and_multiple_export_preserve_official_contract_and_sources(exporter, monkeypatch):
    service, body = exporter
    originals = [service.store.get(i).model_dump() for i in body["listing_ids"]]
    # 任何网络或业务写入都不应成为导出的依赖。
    monkeypatch.setattr(service.store, "save", lambda *_a, **_k: pytest.fail("导出不应写回快照"))
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: pytest.fail("导出不应访问网络"))
    preview = service.preview(body)
    assert preview["summary"] == {"total": 2, "ready": 2, "missing": 0, "barcode_missing": 1, "title_changed": 2}
    result = service.download({**body, "preview_fingerprint": preview["preview_fingerprint"]})
    data = base64.b64decode(result["file_base64"])
    cells, workbook = read_cells(data)
    assert cells["B5"] == "SELL-0" and cells["B6"] == "SELL-1"
    assert cells["C5"] == "Новогодняя подвеска DIY 25×25 см"
    assert cells["D5"] == "37" and cells["J5"] == "181" and cells["K5"] == "260"
    assert cells["F5"] == cells["F6"] == "否"
    assert cells["R5"] == cells["R6"] == originals[0]["title"]
    assert "G5" not in cells and "H5" not in cells and cells["H6"] == "0012345678905"
    assert cells["N5"] == "https://images.yandex.example/image.jpg" and "O5" not in cells
    assert "$AU$6".encode() in workbook
    with zipfile.ZipFile(io.BytesIO(data)) as result_zip, zipfile.ZipFile(io.BytesIO(template_bytes())) as original:
        for name in original.namelist():
            if name not in {"xl/workbook.xml", "xl/worksheets/sheet1.xml"}:
                assert result_zip.read(name) == original.read(name)
        root = ET.fromstring(result_zip.read("xl/worksheets/sheet1.xml"))
        assert root.find("{*}autoFilter").get("ref") == "A4:AU6"
        assert root.find("{*}dataValidations/{*}dataValidation/{*}formula1").text == "name5"
        assert root.find('{*}sheetData/{*}row[@r="5"]/{*}c[@r="B5"]').get("s") == "1"
    single = {**body, "listing_ids": ["listing-1"]}
    p = service.preview(single)
    one = service.download({**single, "preview_fingerprint": p["preview_fingerprint"]})
    one_cells, _ = read_cells(base64.b64decode(one["file_base64"]))
    assert one_cells["B5"] == "SELL-1" and "B6" not in one_cells
    assert one_cells["R5"] == cells["R5"]
    assert [service.store.get(i).model_dump() for i in body["listing_ids"]] == originals


def test_sponge_template_uses_its_columns_type_and_original_titles(exporter):
    service, body = exporter
    row = service.store.get("listing-0")
    row.title = "Губка для посуды 20×20 см"
    row.snapshot["offer"]["groupId"] = "Sponge"
    service.store.save(row)
    body.update(listing_ids=[row.id], template_base64=base64.b64encode(template_bytes(
        category="17030271", category_name="海绵", type_id="96009")).decode())
    preview = service.preview(body)
    assert preview["template"]["category"] == "海绵"
    assert preview["summary"]["ready"] == 1 and not preview["rows"][0]["title_changed"]
    assert preview["rows"][0]["fields"]["type"] == "海绵"
    result = service.download({**body, "preview_fingerprint": preview["preview_fingerprint"]})
    assert result["filename"].startswith("Ozon_海绵_1个SKU_")
    cells, _ = read_cells(base64.b64decode(result["file_base64"]))
    assert (cells["C5"], cells["R5"], cells["S5"], cells["W5"]) == (row.title, "诚实", "Sponge", "海绵")
    assert cells["K5"] == "181" and cells["F5"] == "否"
    assert not {"G5", "H5", "T5"} & cells.keys()


def test_new_category_with_extra_fields_and_multiple_types(exporter):
    service, body = exporter
    body.update(listing_ids=["listing-0"], template_base64=base64.b64encode(template_bytes(
        category="888", category_name="餐具", type_options=["汤勺", "餐叉"],
        default_type="", changed_required=True)).decode())
    preview = service.preview(body)
    assert preview["template"]["extra_fields"] == {"AT": "材料", "AU": "套装件数", "type": "类型"}
    assert preview["template"]["type_options"] == ["汤勺", "餐叉"]
    assert {"缺少材料", "缺少套装件数", "缺少类型"} <= set(preview["rows"][0]["errors"])
    with pytest.raises(ValueError, match="必填"):
        service.download({**body, "preview_fingerprint": preview["preview_fingerprint"]})
    extra = {"AT": "钢", "AU": "1.5", "type": "其他"}
    body["overrides"] = {"listing-0": {"template_fields": extra}}
    assert {"套装件数须为整数", "请选择模板字典中的商品类型"} <= set(service.preview(body)["rows"][0]["errors"])
    extra.update(AU="2", type="餐叉")
    preview = service.preview(body)
    assert preview["summary"]["ready"] == 1
    result = service.download({**body, "preview_fingerprint": preview["preview_fingerprint"]})
    cells, _ = read_cells(base64.b64decode(result["file_base64"]))
    assert (cells["AT5"], cells["AU5"], cells["W5"]) == ("钢", "2", "餐叉")
    extra["B"] = "错误货号"
    with pytest.raises(ValueError, match="当前模板"):
        service.preview(body)


@pytest.mark.parametrize("choices,default,expected", [
    (["茶杯", "马克杯"], "马克杯", "马克杯"), (["马克杯"], "", "马克杯"),
    (["茶杯", "马克杯"], "无效", ""), ([], "", "")])
def test_product_type_comes_from_template_not_category(choices, default, expected):
    template = OzonCategoryTemplate(template_bytes(category="998", category_name="杯具",
        type_options=choices, default_type=default))
    assert template.product_type == expected


@pytest.mark.parametrize("kwargs,message", [({"currency": "RUB"}, "CNY"), ({"populated": True}, "空白")])
def test_rejects_incompatible_or_populated_templates(kwargs, message):
    with pytest.raises(ValueError, match=message): OzonCategoryTemplate(template_bytes(**kwargs))


def test_explicit_edits_are_export_only_and_missing_values_block_generation(exporter):
    service, body = exporter
    original = service.store.get("listing-0").model_dump()
    missing = {**body, "overrides": {"listing-0": {"price": "", "brand": ""}}}
    p = service.preview(missing)
    assert p["summary"]["missing"] == 1 and "缺少价格" in p["rows"][0]["errors"]
    with pytest.raises(ValueError, match="必填资料"): service.download({**missing, "preview_fingerprint": p["preview_fingerprint"]})
    corrected = {**body, "overrides": {"listing-0": {"title": '=HYPERLINK("恶意")', "price": "39.50", "barcode": "000123"}}}
    p = service.preview(corrected)
    data = base64.b64decode(service.download({**corrected, "preview_fingerprint": p["preview_fingerprint"]})["file_base64"])
    cells, _ = read_cells(data)
    assert cells["C5"] == '=HYPERLINK("恶意")' and cells["D5"] == "39.5" and cells["H5"] == "000123"
    with zipfile.ZipFile(io.BytesIO(data)) as archive: assert b"<f>" not in archive.read("xl/worksheets/sheet1.xml")
    assert service.store.get("listing-0").model_dump() == original


@pytest.mark.parametrize("changes", [{"weight_g": "NaN"}, {"width_mm": "1.5"}, {"price": "0"}, {"price": "1e99999"}, {"title": "坏\x00名称"}])
def test_invalid_values_cannot_be_exported(exporter, changes):
    service, body = exporter
    preview = service.preview({**body, "overrides": {"listing-0": changes}})
    assert preview["rows"][0]["errors"] and preview["summary"]["missing"] == 1


def test_snapshot_package_change_and_account_switch_require_review(exporter):
    service, body = exporter
    p = service.preview(body)
    current = service.store.get("listing-0")
    current.snapshot["offer"]["weightDimensions"]["weight"] = 0.32
    service.store.save(current)
    with pytest.raises(OnlineConflict, match="已变化"): service.download({**body, "preview_fingerprint": p["preview_fingerprint"]})
    service.current_account = lambda: "another:shop"
    with pytest.raises(OnlineConflict, match="店铺已切换"): service.preview(body)


def test_currency_category_and_image_problems_are_visible_before_export(exporter):
    service, body = exporter
    row = service.store.get("listing-0")
    row.prices[0].currency = "USD"
    row.title = "普通手机支架"
    row.snapshot["offer"]["groupId"] = "普通商品"
    row.snapshot["offer"]["mediaFiles"]["pictures"][0]["uploadState"] = "FAILED"
    service.store.save(row)
    preview = service.preview(body)
    errors = preview["rows"][0]["errors"]
    assert any("币种" in message for message in errors) and any("类目" in message for message in preview["rows"][0]["warnings"]) and "缺少主图" in errors


def test_rejects_wrong_platform_other_account_and_unselected_overrides(exporter):
    service, body = exporter
    with pytest.raises(ValueError): service.preview({**body, "listing_ids": ["listing-0", "listing-0"]})
    with pytest.raises(ValueError, match="本次选中"): service.preview({**body, "overrides": {"unknown": {"price": "1"}}})
    for field, value in (("platform", "ozon"), ("account_id", "other")):
        row = service.store.get("listing-0"); setattr(row, field, value); service.store.save(row)
        with pytest.raises(ValueError, match="当前 Yandex"): service.preview(body)


def test_route_validates_body_and_returns_download_or_conflict(exporter, monkeypatch):
    from erp_web.facades import online_product_facade
    from erp_web.http_route_units.online_product_routes import POST_HANDLERS
    service, body = exporter
    monkeypatch.setattr(online_product_facade, "get_context", lambda: SimpleNamespace(online_products=SimpleNamespace(ozon_export=service)))
    responses = []
    handler = SimpleNamespace(path="/api/online-products/ozon-export/preview", read_body=lambda: body, send_json=lambda data, status: responses.append((data, status)))
    POST_HANDLERS[handler.path](handler)
    preview, status = responses[-1]
    assert status == 200
    handler.path = "/api/online-products/ozon-export/download"
    handler.read_body = lambda: {**body, "preview_fingerprint": preview["preview_fingerprint"]}
    POST_HANDLERS[handler.path](handler)
    assert responses[-1][1] == 200 and responses[-1][0]["filename"].endswith(".xlsx")
    handler.read_body = lambda: {**body, "preview_fingerprint": "stale"}
    POST_HANDLERS[handler.path](handler)
    assert responses[-1][1] == 409


@pytest.mark.parametrize("data", [b"not a zip", b"PK", b"", b"{\"not\":\"a workbook\"}"])
def test_invalid_upload_is_a_validation_error(data):
    with pytest.raises(ValueError): OzonCategoryTemplate(data)


@pytest.mark.parametrize("brand", ["其他", "无品牌", "Без бренда", " Нет бренда "])
def test_no_brand_uses_template_value_without_changing_yandex(exporter, brand):
    service, body = exporter
    row = service.store.get("listing-0")
    row.snapshot["offer"]["vendor"] = brand
    service.store.save(row)
    body["template_base64"] = base64.b64encode(template_bytes(no_brand="Нет бренда")).decode()
    preview = service.preview(body)
    assert preview["rows"][0]["fields"]["brand"] == "Нет бренда"
    data = service.download({**body, "preview_fingerprint": preview["preview_fingerprint"]})
    cells, _ = read_cells(base64.b64decode(data["file_base64"]))
    assert cells["Q5"] == "Нет бренда"
    assert service.store.get("listing-0").snapshot["offer"]["vendor"] == brand


def test_unknown_brand_is_blocked_only_with_complete_dictionary(exporter):
    service, body = exporter
    body["overrides"] = {"listing-0": {"brand": "Acme"}, "listing-1": {"brand": "其他"}}
    for values, oversized, blocked in [(["Acme"], False, False), (["Another"], False, True),
                                       ([], False, False), (["Another"], True, False)]:
        body["template_base64"] = base64.b64encode(template_bytes(brand_values=values, oversized=oversized)).decode()
        p = service.preview(body)
        assert p["rows"][0]["fields"]["brand"] == "Acme"
        assert bool(p["rows"][0]["errors"]) is blocked
        assert p["rows"][1]["fields"]["brand"] == "无品牌"
        if blocked:
            with pytest.raises(ValueError, match="必填资料"):
                service.download({**body, "preview_fingerprint": p["preview_fingerprint"]})
        elif "Acme" not in values:
            assert any("未包含完整品牌字典" in message for message in p["rows"][0]["warnings"])


def test_registered_brand_is_not_replaced_by_no_brand(exporter):
    service, body = exporter
    body["template_base64"] = base64.b64encode(template_bytes(brand_values=["其他"])).decode()
    body["overrides"] = {"listing-0": {"brand": "其他"}}
    assert service.preview(body)["rows"][0]["fields"]["brand"] == "其他"


def test_chinese_model_override_and_long_model_cannot_be_downloaded(exporter):
    service, body = exporter
    for model in ("圣诞组合", "模型\U00020000", "Я" * 256):
        # 超长覆盖值由请求 Schema 拒绝；中文在预览中逐行报告。
        edited = {**body, "overrides": {"listing-0": {"model": model}}}
        if len(model) > 255:
            with pytest.raises(ValueError): service.preview(edited)
            continue
        p = service.preview(edited)
        assert any("型号名称含中文" in message for message in p["rows"][0]["errors"])
        with pytest.raises(ValueError, match="必填资料"):
            service.download({**edited, "preview_fingerprint": p["preview_fingerprint"]})
    edited = {**body, "overrides": {key: {"model": "Сова"} for key in body["listing_ids"]}}
    p = service.preview(edited)
    cells, _ = read_cells(base64.b64decode(service.download({**edited, "preview_fingerprint": p["preview_fingerprint"]})["file_base64"]))
    assert cells["R5"] == cells["R6"] == "Сова"


@pytest.mark.parametrize("title,state", [("Другая новогодняя подвеска", "ready"), ("圣诞装饰", "ready"),
                                        ("", "ready"), ("Новогодняя подвеска DIY 20×20 см", "pending")])
def test_unselected_group_members_prevent_unsafe_title_fallback(exporter, title, state):
    service, body = exporter
    row = service.store.get("listing-1")
    row.title, row.details_state = title, state
    service.store.save(row)
    body["listing_ids"] = ["listing-0"]
    p = service.preview(body)
    assert p["summary"]["missing"] == 1
    assert any("型号名称含中文" in message for message in p["rows"][0]["errors"])


def test_different_groups_with_same_title_are_not_merged(exporter):
    service, body = exporter
    row = service.store.get("listing-1")
    row.snapshot["offer"]["groupId"] = "另一圣诞组合"
    service.store.save(row)
    p = service.preview(body)
    assert p["summary"]["missing"] == 2
    assert p["rows"][0]["fields"]["model"] != p["rows"][1]["fields"]["model"]


@pytest.mark.parametrize("group", ["Новогодняя сова", "MODEL-001", ""])
def test_valid_source_models_and_ungrouped_skus_are_preserved(exporter, group):
    service, body = exporter
    for listing_id in body["listing_ids"]:
        row = service.store.get(listing_id)
        row.snapshot["offer"]["groupId"] = group
        service.store.save(row)
    p = service.preview(body)
    assert p["summary"]["missing"] == 0
    assert [row["fields"]["model"] for row in p["rows"]] == [group or f"SELL-{i}" for i in range(2)]


def test_unselected_group_title_change_invalidates_preview(exporter):
    service, body = exporter
    body["listing_ids"] = ["listing-0"]
    p = service.preview(body)
    row = service.store.get("listing-1")
    row.title = "Другая новогодняя подвеска"
    service.store.save(row)
    with pytest.raises(OnlineConflict, match="已变化"):
        service.download({**body, "preview_fingerprint": p["preview_fingerprint"]})

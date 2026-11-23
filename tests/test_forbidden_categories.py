"""禁售名单完整导入、配置隔离与两种预检拦截回归。"""
import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape
from zipfile import ZipFile

from erp_web.facades.business_data_facade import business_query_payload
from erp_web.http_route_units import business_query_routes

import pytest

from erp_web.context import get_context
from erp_web.facades import auth_config_facade, category_facade
from erp_web.http_route_units import auth_config_routes, get_routes
from erp_web.runtime_units.publish_validation_session import PublishValidationSession
from erp_web.schemas.requests import RequestValidationError, validate_request_payload
from erp_web.services.forbidden_category_service import (
    forbidden_category_issue,
    forbidden_category_match,
    forbidden_category_reason,
)
from erp_web.stores.config_store import ConfigStore


FIXTURE = Path(__file__).parent / "fixtures/yandex_forbidden_categories.xlsx"


def xlsx(rows):
    """生成小型 inlineStr 工作簿，覆盖与官方 sharedStrings 文件不同的编码。"""
    out = BytesIO()
    with ZipFile(out, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="名单" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/></Relationships>')
        cells = "".join(f'<row r="{i}">' + "".join(f'<c r="{col}{i}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>' for col, v in zip("ABC", row)) + '</row>' for i, row in enumerate(rows, 1))
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{cells}</sheetData></worksheet>')
    return out.getvalue()


def import_file(data=None, **overrides):
    body = {"platform": "yandex", "filename": "forbidden-categories.xlsx",
            "content_base64": base64.b64encode(FIXTURE.read_bytes() if data is None else data).decode()}
    return auth_config_facade.import_forbidden_categories_payload({**body, **overrides})


def save_list(category_id="91596", name="测试禁售类目"):
    return import_file(xlsx([("category", "name"), (category_id, name)]))[0]


def test_official_workbook_import_persists_and_preserves_other_settings():
    context = get_context()
    context.config.save_system_settings({"orders_auto_sync_interval_hours": 12})
    before = context.config.load_app_config()
    result, status = import_file()
    assert status == 200
    assert len(result["categories"]) == 555
    assert result["categories"][0] == {"category_id": "6122737", "name": "FM-трансмиттеры"}
    assert result["imported_at"] and result["filename"] == "forbidden-categories.xlsx"
    assert ConfigStore(context.paths, context.db).forbidden_categories() == result
    after = context.config.load_app_config()
    assert {k: v for k, v in after.items() if k != "forbidden_categories"} == {k: v for k, v in before.items() if k != "forbidden_categories"}
    context.config.save_system_settings({"orders_auto_sync_interval_hours": 5})
    merged = context.config.merge_app_config_fields(context.config.load_app_config(), {"forbidden_categories": {}})
    context.config.save_app_config(merged)
    assert context.config.forbidden_categories() == result


@pytest.mark.parametrize("data,overrides", [
    ("不是工作簿".encode(), {}),
    (xlsx([("id", "name"), ("12", "名称")]), {}),
    (xlsx([("category", "name"), ("int64", "string")]), {}),
    (xlsx([("category", "name"), ("12", "有效"), ("bad", "无效")]), {}),
    (xlsx([("category", "name"), ("12", "")]), {}),
    (xlsx([("category", "name"), ("12.5", "非整数")]), {}),
    (xlsx([("category", "name"), ("12", "一个"), ("12", "另一个")]), {}),
    (b"x" * (3 * 1024 * 1024 + 1), {}),
    (b"", {"content_base64": "***"}),
    (None, {"platform": "ozon"}),
    (None, {"filename": "禁售.xls"}),
])
def test_invalid_import_preserves_complete_old_list(data, overrides):
    old = save_list()
    result, status = import_file(data, **overrides)
    assert status == 400 and result["error"]
    assert get_context().config.forbidden_categories() == old


def test_duplicate_rows_type_row_and_numeric_ids_are_normalized_then_replaced():
    save_list()
    result, status = import_file(xlsx([("name", "category"), ("string", "int64"), ("新类目", "00123"), ("新类目", "1.23E2"), ("", "")]))
    assert status == 200
    assert result["categories"] == [{"category_id": "123", "name": "新类目"}]
    assert forbidden_category_issue("yandex", "00123", {"123": "新类目"})["code"] == "CATEGORY_FORBIDDEN"
    assert forbidden_category_issue("ozon", "123", {"123": "新类目"}) is None


def test_import_route_contract_and_get_expose_same_configuration():
    endpoint = "/api/system-settings/forbidden-categories/import"
    assert endpoint in auth_config_routes.HANDLED_PATHS
    assert "/api/system-settings/forbidden-categories" in business_query_routes.GET_API_ROUTES
    with pytest.raises(RequestValidationError):
        validate_request_payload({"platform": "ozon"}, endpoint=endpoint)
    expected = save_list()
    assert business_query_payload('settings.forbidden_categories')[0] == expected


def test_http_import_and_read_handlers_use_validated_configuration():
    endpoint = "/api/system-settings/forbidden-categories/import"
    received = []
    body = {"platform": "yandex", "filename": FIXTURE.name,
            "content_base64": base64.b64encode(FIXTURE.read_bytes()).decode()}
    handler = SimpleNamespace(path=endpoint, read_body=lambda: body,
                              send_json=lambda result, status=200: received.append((result, status)))
    assert auth_config_routes.handle_post(handler, SimpleNamespace(path=endpoint)) is True
    imported, status = received.pop()
    assert status == 200 and len(imported["categories"]) == 555
    business_query_routes.handle_get(handler, SimpleNamespace(path='/api/system-settings/forbidden-categories', query=''))
    assert received.pop() == (imported, 200)


def test_category_precheck_rejects_saved_category_even_if_request_id_differs(monkeypatch):
    save_list()
    context = {"product": {"drafts": {"yandex": {"category_id": "91596"}}},
               "draft": {"draft_id": "draft-yandex"}, "platform": "yandex", "site": "ru"}
    monkeypatch.setattr(category_facade, "load_required_draft_publish_context", lambda body: (context, None, 200))
    monkeypatch.setattr(category_facade, "validate_category_precheck", lambda *args: [])
    saved = {}
    monkeypatch.setattr(category_facade, "save_draft_target_listing_result", lambda context, patch: saved.update(patch) or {})
    result, status = category_facade.category_precheck_payload({"draft_id": "draft-yandex", "platform": "yandex", "category_id": "123", "category_record": {}})
    assert status == 200
    assert "禁售" in result["missing_fields"][0]
    assert saved["category_precheck"]["ok"] is False
    assert saved["validation_errors"] == []


def test_session_loads_once_and_next_validation_reads_replaced_list(monkeypatch):
    save_list()
    original = get_context().config.forbidden_categories
    calls = []
    def load():
        calls.append(True)
        return original()
    monkeypatch.setattr(get_context().config, "forbidden_categories", load)
    session = PublishValidationSession()
    assert "91596" in session.forbidden_categories
    assert "91596" in session.forbidden_categories
    assert len(calls) == 1
    save_list("123", "另一类目")
    assert "91596" not in PublishValidationSession().forbidden_categories
    assert len(calls) == 2


def test_ancestor_id_upward_matching_picks_closest_ancestor_and_ignores_other_platforms():
    categories = {"90401": "根类目", "13793701": "Удобрения и уход за растениями"}
    match = forbidden_category_match(
        "yandex", "60491375", categories, ancestor_ids=("90401", "90719", "13793701", "6433382"),
    )
    assert match == {
        "category_id": "60491375",
        "matched_category_id": "13793701",
        "matched_category_name": "Удобрения и уход за растениями",
        "is_ancestor": True,
    }
    assert "上级类目 13793701（Удобрения и уход за растениями）" in forbidden_category_reason(
        "yandex", "60491375", categories, ancestor_ids=("90401", "90719", "13793701", "6433382"),
    )
    issue = forbidden_category_issue(
        "yandex", "60491375", categories, ancestor_ids=("90401", "90719", "13793701", "6433382"),
    )
    assert issue["code"] == "CATEGORY_FORBIDDEN"
    assert "60491375" in issue["message"] and "13793701" in issue["message"]
    assert forbidden_category_issue("ozon", "60491375", categories, ancestor_ids=("13793701",)) is None


def test_yandex_tree_records_ancestor_ids_and_annotates_search_results(monkeypatch):
    from erp_web.runtime_units import yandex_category_api

    save_list("13793701", "Удобрения и уход за растениями")
    tree = [{
        "id": 90401, "name": "Все товары", "children": [
            {"id": 90719, "name": "Дача, сад и огород", "children": [
                {"id": 13793701, "name": "Удобрения и уход за растениями", "children": [
                    {"id": 6433382, "name": "Отпугиватели и ловушки", "children": [
                        {"id": 60491375, "name": "Отпугиватели птиц/грызунов"},
                    ]},
                ]},
            ]},
            {"id": 90800, "name": "Товары для дома", "children": [
                {"id": 74873567, "name": "Отпугиватели ультразвуковые бытовые"},
            ]},
        ],
    }]
    records = yandex_category_api._flatten_leaf_categories(tree)
    by_id = {row["category_id"]: row for row in records}
    assert by_id["60491375"]["ancestor_ids"] == ["90401", "90719", "13793701", "6433382"]
    assert by_id["74873567"]["ancestor_ids"] == ["90401", "90800"]

    monkeypatch.setattr(
        yandex_category_api,
        "load_yandex_category_corpus",
        lambda **kwargs: (records, {"cache_source": "persistent_cache"}),
    )
    results = yandex_category_api.search_yandex_categories("отпугиватели", limit=10)
    assert [row["category_id"] for row in results] == ["74873567", "60491375"]
    assert results[0]["forbidden"] is False and results[0]["forbidden_reason"] == ""
    assert results[1]["forbidden"] is True
    assert "13793701" in results[1]["forbidden_reason"]


def test_category_precheck_uses_trusted_server_ancestors_not_client_record(monkeypatch):
    save_list("13793701", "Удобрения и уход за растениями")
    context = {"product": {"drafts": {"yandex": {"category_id": "60491375"}}},
               "draft": {"draft_id": "draft-yandex"}, "platform": "yandex", "site": "global"}
    monkeypatch.setattr(category_facade, "load_required_draft_publish_context", lambda body: (context, None, 200))
    monkeypatch.setattr(category_facade, "validate_category_precheck", lambda *args: [])
    monkeypatch.setattr(
        get_context().category_catalog.provider_for("yandex"),
        "cached_category_ancestor_map",
        lambda **kw: {"60491375": ("90401", "90719", "13793701", "6433382")},
    )
    saved = {}
    monkeypatch.setattr(category_facade, "save_draft_target_listing_result", lambda context, patch: saved.update(patch) or {})
    # 客户端伪造空 ancestor_ids 无法绕过服务端祖先链拦截。
    result, status = category_facade.category_precheck_payload({
        "draft_id": "draft-yandex",
        "platform": "yandex",
        "category_id": "60491375",
        "category_record": {"category_id": "60491375", "ancestor_ids": []},
    })
    assert status == 200
    assert saved["category_precheck"]["ok"] is False
    assert any("13793701" in item and "禁售" in item for item in result["missing_fields"])


def test_ai_category_precheck_and_publish_validation_block_forbidden_ancestor(monkeypatch):
    from erp_web.runtime_units import category_query_capabilities as cq
    from erp_web.runtime_units.publish_validation import validate_yandex_draft
    from erp_web.schemas.ai_trace import AiExecutionContext
    from erp_web.schemas.category_query_capabilities import CategoryPrecheckRequest
    from tests.test_yandex_publish import _config, _record, _validatable_product

    save_list("13793701", "Удобрения и уход за растениями")
    ancestors = ("90401", "90719", "13793701", "6433382")
    scope = cq.CategoryQueryCapabilityScope(
        searcher=lambda *a, **kw: [],
        attributes_loader=lambda *a, **kw: {},
        attribute_values_loader=lambda *a, **kw: {},
        record_loader=lambda *a, **kw: {
            "category_id": "60491375",
            "category_path": "Все товары / Дача / Удобрения и уход за растениями / Отпугиватели",
            "ancestor_ids": list(ancestors),
            "attributes": {"required": [], "optional": []},
        },
        draft_context_loader=lambda body: (
            {"product": {"drafts": {"yandex": {"category_id": "60491375"}}}, "platform": "yandex", "site": "global"},
            None,
            200,
        ),
        product_loader=lambda body: ({}, None, 200),
    )
    ai_res = cq.category_precheck(
        CategoryPrecheckRequest(draft_id="d1", platform="yandex", site="global", category_id="60491375"),
        scope,
        AiExecutionContext.create(timeout_seconds=10, budget_profile="main.chat", permissions={"category.read"}),
    )
    assert any("13793701" in item and "禁售" in item for item in ai_res.missing_fields)

    monkeypatch.setattr(
        get_context().category_catalog.provider_for("yandex"),
        "cached_category_ancestor_map",
        lambda **kw: {"60491375": ancestors},
    )
    product = _validatable_product(category_id="60491375")
    pub_res = validate_yandex_draft(product, _config(), _record(category_id="60491375"), image_stage="source")
    assert pub_res["ok"] is False
    issue = next(item for item in pub_res["errors"] if item["code"] == "CATEGORY_FORBIDDEN")
    assert "60491375" in issue["message"] and "13793701" in issue["message"]


def test_ai_category_match_capability_handles_forbidden_only_and_direct_id():
    from erp_web.runtime_units import category_capabilities as cap
    from erp_web.schemas.market_prepare_capabilities import CategoryMatchRequest
    from erp_web.services.capability_errors import BusinessCapabilityError, CapabilityInputRequired

    save_list("13793701", "Удобрения и уход за растениями")

    class FakeStore:
        def load_draft_content(self, draft_id):
            draft = {
                "draft_id": draft_id,
                "product_id": "prod-1",
                "platform": "yandex",
                "site": "global",
                "title": "驱赶器",
                "target_sites": [{"platform": "yandex", "site": "global", "language": "ru-RU"}],
            }
            product = {"product_id": "prod-1", "title": "驱赶器", "drafts": {"yandex": draft}}
            return {"draft": draft, "product": product}, None, 200

    with pytest.raises(BusinessCapabilityError) as err:
        cap.match_category(
            CategoryMatchRequest(draft_id="d1", target_platform="yandex", site="global"),
            product_store=FakeStore(),
            matcher=lambda *args: {
                "ok": True,
                "status": "unresolved",
                "candidates": [{"category_id": "60491375", "name": "Отпугиватели", "forbidden": True}],
                "failure": {"code": "CATEGORY_FORBIDDEN_NO_ALTERNATIVE", "message": "唯一匹配类目属于禁售范围"},
            },
        )
    assert err.value.code == "CATEGORY_FORBIDDEN_NO_ALTERNATIVE"

    with pytest.raises(CapabilityInputRequired) as ask:
        cap.match_category(
            CategoryMatchRequest(draft_id="d1", target_platform="yandex", site="global"),
            product_store=FakeStore(),
            matcher=lambda *args: {
                "ok": True,
                "status": "unresolved",
                "candidates": [
                    {"category_id": "60491375", "name": "Отпугиватели", "forbidden": True},
                    {"category_id": "74873567", "name": "合规类目", "forbidden": False},
                ],
                "failure": {"code": "ABSTAIN_NO_MATCH", "message": "需人工确认"},
            },
        )
    assert [opt.value for opt in ask.value.options] == ["74873567"]

    with pytest.raises(BusinessCapabilityError) as direct_err:
        cap.match_category(
            CategoryMatchRequest(draft_id="d1", target_platform="yandex", site="global", category_id="60491375"),
            product_store=FakeStore(),
            matcher=lambda *args: {},
            category_record_loader=lambda *a, **kw: {
                "category_id": "60491375",
                "category_path": "Удобрения / Отпугиватели",
                "ancestor_ids": ["90401", "13793701"],
                "attributes": {"required": [], "optional": []},
            },
        )
    assert direct_err.value.code == "CATEGORY_FORBIDDEN"

from __future__ import annotations

from copy import deepcopy

import pytest

from erp_web.product_model import default_draft, default_product_model
from erp_web.runtime_units.attribute_fill_capabilities import (
    fill_product_attributes,
)
from erp_web.runtime_units.category_capabilities import match_category
from erp_web.runtime_units.market_prepare_capabilities import prepare_draft_for_market
from erp_web.schemas.market_prepare_capabilities import (
    CategoryMatchCapabilityResult,
    CategoryMatchRequest,
    DraftPrepareForMarketRequest,
    ProductAttributesFillRequest,
    ProductAttributesFillResult,
)
from erp_web.schemas.product_capabilities import ProductImagesPrepareResult
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


def _target_site(platform: str, site: str, currency: str) -> dict:
    return {
        "platform": platform,
        "site": site,
        "language": "es-MX" if platform == "mercadolibre" else "ru-RU",
        "market_currency": currency,
        "listing_currency": currency,
        "currency_resolution": {
            "mode": "registry",
            "listing_currency": currency,
            "allowed_currencies": [currency],
            "source": "test",
            "verified_at": "2026-08-13T00:00:00Z",
        },
    }


def _draft(
    draft_id: str,
    platform: str = "mercadolibre",
    site: str = "MLM",
    currency: str = "MXN",
) -> dict:
    draft = default_draft(platform)
    target = _target_site(platform, site, currency)
    draft.update(
        {
            "draft_id": draft_id,
            "product_id": "product-1",
            "source_product_id": "product-1",
            "platform": platform,
            "platforms": [platform],
            "site": site,
            "language": target["language"],
            "listing_currency": currency,
            "target_sites": [target],
            "title": "Portable fan",
            "description": "Portable fan description",
            "stock": "8",
            "package_dimensions": {
                "length_cm": "20",
                "width_cm": "15",
                "height_cm": "10",
                "weight_kg": "0.5",
            },
            "status": "claimed",
        }
    )
    return draft


class _Products:
    def __init__(self, drafts: list[dict] | None = None) -> None:
        rows = drafts or [_draft("draft-1")]
        self.drafts = {_draft_row["draft_id"]: deepcopy(_draft_row) for _draft_row in rows}
        product = default_product_model()
        product.update(
            {
                "product_id": "product-1",
                "name": "Portable fan",
                "brand": "Generic",
                "model": "F-1",
                "stock": "8",
                "cost": "100",
                "source": {
                    **product["source"],
                    "title": "Portable fan",
                    "description": "Portable fan description",
                    "currency": "CNY",
                    "price": "100",
                    "weight_kg": "0.5",
                    "dimensions": {
                        "length_cm": "20",
                        "width_cm": "15",
                        "height_cm": "10",
                    },
                    "image_pool": [
                        {
                            "id": "image-1",
                            "url": "https://img.example/1.jpg",
                            "origin": "source",
                            "status": "ready",
                            "selected": True,
                            "is_main": True,
                            "order": 0,
                            "platforms": ["mercadolibre"],
                        }
                    ],
                },
                "drafts": {
                    row["platform"]: deepcopy(row)
                    for row in rows
                },
            }
        )
        self.product = product
        self.save_product_calls = 0
        self.save_draft_calls = 0

    def load_product_from_index(self, product_id: str = "", file_path: str = "") -> dict:
        return deepcopy(self.product) if product_id == "product-1" else {}

    def load_draft_detail_from_index(self, draft_id: str):
        draft = self.drafts.get(draft_id)
        if draft is None:
            return {}, {"error": "草稿不存在", "error_code": "DRAFT_NOT_FOUND"}, 404
        return {
            "draft": deepcopy(draft),
            "productContext": {"raw": deepcopy(self.product)},
        }, None, 200

    def save_product(self, data: dict) -> dict:
        self.save_product_calls += 1
        self.product = deepcopy(data)
        for draft in (data.get("drafts") or {}).values():
            if isinstance(draft, dict) and draft.get("draft_id"):
                self.drafts[str(draft["draft_id"])] = deepcopy(draft)
        return deepcopy(self.product)

    def save_draft_detail(self, draft_payload: dict):
        self.save_draft_calls += 1
        draft_id = str(draft_payload.get("draft_id") or "")
        self.drafts[draft_id] = deepcopy(draft_payload)
        platform = str(draft_payload.get("platform") or "")
        self.product.setdefault("drafts", {})[platform] = deepcopy(draft_payload)
        return {"draft": deepcopy(draft_payload)}, None, 200

    def apply_image_assets_to_draft(
        self,
        draft_id: str,
        created_items: list[dict],
        strategy: str = "append",
    ):
        raise AssertionError("本测试使用注入的图片 Capability")

    def draft_workflow_status(self, product: dict, platform: str = "mercadolibre") -> str:
        return "images_ready"


def _category_record(*_args, **_kwargs) -> dict:
    return {
        "category_id": "CAT-1",
        "category_path": "Home > Fans",
        "platform": "mercadolibre",
        "site": "MLM",
        "attributes": {
            "required": [
                {
                    "id": "COLOR",
                    "name": "Color",
                    "required": True,
                    "options": ["Red", "Blue"],
                }
            ],
            "optional": [],
        },
    }


def test_category_match_persists_focused_agent_selection() -> None:
    products = _Products()

    def matcher(product: dict, draft: dict, target: dict) -> dict:
        assert product["drafts"]["mercadolibre"]["draft_id"] == "draft-1"
        assert target["site"] == "MLM"
        return {
            "ok": True,
            "status": "completed",
            "target": {"platform": "mercadolibre", "site": "MLM"},
            "selected_category_id": "CAT-1",
            "query": "portable fan",
            "candidates": [{"category_id": "CAT-1", "name": "Fans"}],
            "decision": {"model_confidence": 0.91},
            "failure": None,
        }

    result = match_category(
        CategoryMatchRequest(
            draft_id="draft-1",
            target_platform="mercadolibre",
            site="MLM",
        ),
        product_store=products,
        matcher=matcher,
        category_record_loader=_category_record,
    )

    assert result.category_id == "CAT-1"
    assert result.platform == "mercadolibre"
    assert products.drafts["draft-1"]["category_id"] == "CAT-1"
    assert products.drafts["draft-1"]["category_attribute_schema"]["required"][0]["id"] == "COLOR"
    assert products.product["local_platform_categories"]["mercadolibre"]["category_id"] == "CAT-1"


def test_category_match_abstain_requires_explicit_category() -> None:
    products = _Products()

    def matcher(*_args, **_kwargs) -> dict:
        return {
            "ok": True,
            "status": "unresolved",
            "selected_category_id": None,
            "query": "fan",
            "candidates": [
                {"category_id": "CAT-1", "name": "Fans"},
                {"category_id": "CAT-2", "name": "Ventilation"},
            ],
            "decision": {"model_confidence": 0.2},
            "failure": {
                "code": "ABSTAIN_NO_MATCH",
                "message": "需要人工确认。",
                "retryable": False,
            },
        }

    with pytest.raises(CapabilityInputRequired) as exc_info:
        match_category(
            CategoryMatchRequest(
                draft_id="draft-1",
                target_platform="mercadolibre",
            ),
            product_store=products,
            matcher=matcher,
            category_record_loader=_category_record,
        )

    assert exc_info.value.key == "category_id"
    assert exc_info.value.options == ("CAT-1", "CAT-2")
    assert exc_info.value.input_type == "select"
    assert products.save_product_calls == 0


def test_category_post_run_failure_preserves_domain_error() -> None:
    products = _Products()

    def matcher(*_args, **_kwargs) -> dict:
        return {
            "ok": True,
            "status": "completed",
            "selected_category_id": "CAT-1",
            "decision": {"model_confidence": 0.9},
        }

    def failing_record_loader(*_args, **_kwargs) -> dict:
        raise BusinessCapabilityError(
            "CATEGORY_RECORD_BROKEN",
            "类目记录不可用。",
        )

    with pytest.raises(BusinessCapabilityError) as exc_info:
        match_category(
            CategoryMatchRequest(
                draft_id="draft-1",
                target_platform="mercadolibre",
            ),
            product_store=products,
            matcher=matcher,
            category_record_loader=failing_record_loader,
        )

    assert exc_info.value.code == "CATEGORY_RECORD_BROKEN"


def test_attribute_fill_persists_partial_result_then_requests_missing_fact() -> None:
    draft = _draft("draft-1")
    draft["category_id"] = "CAT-1"
    products = _Products([draft])

    def record(*_args, **_kwargs) -> dict:
        category = _category_record()
        category["attributes"]["required"].append(
            {
                "id": "BATTERY_TYPE",
                "name": "Battery type",
                "required": True,
                "options": ["AA", "AAA"],
            }
        )
        return category

    def filler(product: dict, platform: str, category: dict | None):
        updated = deepcopy(product)
        updated["drafts"][platform]["attributes"] = {"COLOR": "Red"}
        return updated, {
            "source": "rules",
            "warning": "Agent 未能确定电池型号。",
        }

    with pytest.raises(CapabilityInputRequired) as exc_info:
        fill_product_attributes(
            ProductAttributesFillRequest(
                draft_id="draft-1",
                target_platform="mercadolibre",
            ),
            product_store=products,
            attribute_filler=filler,
            category_record_loader=record,
        )

    assert exc_info.value.key == "BATTERY_TYPE"
    assert exc_info.value.options == ("AA", "AAA")
    assert exc_info.value.input_type == "select"
    assert exc_info.value.input_owner == "provided_attributes"
    assert products.drafts["draft-1"]["attributes"] == {"COLOR": "Red"}
    assert products.drafts["draft-1"]["validation_errors"] == ["BATTERY_TYPE"]


def test_attribute_fill_dictionary_attribute_requests_live_options() -> None:
    """字典属性待输入时必须实时拉取平台合法候选值，而不是空文本框。"""

    draft = _draft("draft-1")
    draft["category_id"] = "CAT-1"
    products = _Products([draft])

    def record(*_args, **_kwargs) -> dict:
        category = _category_record()
        category["attributes"]["required"].append(
            {
                "id": "PURPOSE",
                "name": "Предназначено для",
                "required": True,
                "is_dictionary": True,
                "dictionary_id": "749",
                "options": [],
            }
        )
        return category

    def filler(product: dict, platform: str, category: dict | None):
        updated = deepcopy(product)
        updated["drafts"][platform]["attributes"] = {"COLOR": "Red"}
        return updated, {"source": "rules"}

    calls: list[tuple[str, str, str, str]] = []

    def values_loader(platform, category_id, attribute_id, site="", **_kwargs):
        calls.append((platform, category_id, attribute_id, site))
        return {
            "ok": True,
            "values": [
                {"id": "33746", "value": "Для собак"},
                {"id": "33754", "value": "Для кошек"},
                {"id": "33751", "value": "Для птиц"},
            ],
        }

    with pytest.raises(CapabilityInputRequired) as exc_info:
        fill_product_attributes(
            ProductAttributesFillRequest(
                draft_id="draft-1",
                target_platform="mercadolibre",
            ),
            product_store=products,
            attribute_filler=filler,
            category_record_loader=record,
            attribute_values_loader=values_loader,
        )

    assert exc_info.value.key == "PURPOSE"
    assert exc_info.value.options == ("Для собак", "Для кошек", "Для птиц")
    assert exc_info.value.input_type == "select"
    assert "枚举" in exc_info.value.reason
    assert calls == [("mercadolibre", "CAT-1", "PURPOSE", "MLM")]


def test_attribute_fill_dictionary_lookup_failure_falls_back_to_text() -> None:
    draft = _draft("draft-1")
    draft["category_id"] = "CAT-1"
    products = _Products([draft])

    def record(*_args, **_kwargs) -> dict:
        category = _category_record()
        category["attributes"]["required"].append(
            {
                "id": "PURPOSE",
                "name": "Предназначено для",
                "required": True,
                "is_dictionary": True,
                "dictionary_id": "749",
                "options": [],
            }
        )
        return category

    def filler(product: dict, platform: str, category: dict | None):
        updated = deepcopy(product)
        updated["drafts"][platform]["attributes"] = {"COLOR": "Red"}
        return updated, {"source": "rules"}

    def broken_values_loader(*_args, **_kwargs):
        raise RuntimeError("dictionary unavailable")

    with pytest.raises(CapabilityInputRequired) as exc_info:
        fill_product_attributes(
            ProductAttributesFillRequest(
                draft_id="draft-1",
                target_platform="mercadolibre",
            ),
            product_store=products,
            attribute_filler=filler,
            category_record_loader=record,
            attribute_values_loader=broken_values_loader,
        )

    assert exc_info.value.key == "PURPOSE"
    assert exc_info.value.options == ()
    assert exc_info.value.input_type == "text"


def test_attribute_fill_resolves_user_text_into_dictionary_value() -> None:
    """待输入提交的候选文本必须解析为带 dictionary_value_id 的结构化值。"""

    draft = _draft("draft-1")
    draft["category_id"] = "CAT-1"
    draft["attributes"] = {}
    products = _Products([draft])

    def record(*_args, **_kwargs) -> dict:
        category = _category_record()
        category["attributes"]["required"].append(
            {
                "id": "PURPOSE",
                "name": "Предназначено для",
                "required": True,
                "is_dictionary": True,
                "dictionary_id": "749",
                "is_collection": True,
                "max_value_count": 3,
                "options": [],
            }
        )
        return category

    def filler(product: dict, platform: str, category: dict | None):
        updated = deepcopy(product)
        attrs = dict(updated["drafts"][platform].get("attributes") or {})
        attrs["COLOR"] = "Red"
        updated["drafts"][platform]["attributes"] = attrs
        return updated, {"source": "rules"}

    def values_loader(platform, category_id, attribute_id, site="", **_kwargs):
        return {
            "ok": True,
            "values": [
                {"id": "33746", "value": "Для собак"},
                {"id": "33754", "value": "Для кошек"},
            ],
        }

    result = fill_product_attributes(
        ProductAttributesFillRequest(
            draft_id="draft-1",
            target_platform="mercadolibre",
            provided_attributes={"PURPOSE": "для собак"},
        ),
        product_store=products,
        attribute_filler=filler,
        category_record_loader=record,
        attribute_values_loader=values_loader,
    )

    assert result.attributes["PURPOSE"] == {
        "values": [{"dictionary_value_id": "33746", "value": "Для собак"}]
    }
    assert result.attributes["COLOR"] == "Red"
    assert products.drafts["draft-1"]["validation_errors"] == []


def test_attribute_fill_accepts_explicit_user_value_and_completes() -> None:
    draft = _draft("draft-1")
    draft["category_id"] = "CAT-1"
    products = _Products([draft])

    def filler(product: dict, platform: str, category: dict | None):
        return deepcopy(product), {"source": "rules", "ai_filled": []}

    result = fill_product_attributes(
        ProductAttributesFillRequest(
            draft_id="draft-1",
            target_platform="mercadolibre",
            provided_attributes={"COLOR": "Red"},
        ),
        product_store=products,
        attribute_filler=filler,
        category_record_loader=_category_record,
    )

    assert result.attributes == {"COLOR": "Red"}
    assert result.need_review_attribute_ids == []
    assert result.platform == "mercadolibre"


def test_prepare_claims_target_and_runs_real_owner_boundaries_in_order() -> None:
    origin = _draft("draft-source", "yandex", "global", "RUB")
    products = _Products([origin])
    events: list[str] = []

    def claim(product_ids: list[str], platforms: list[str] | None) -> dict:
        events.append("claim")
        assert product_ids == ["product-1"]
        assert platforms == ["mercadolibre"]
        target = _draft("draft-target")
        target["title"] = "Portable fan"
        target["description"] = "Source description"
        products.drafts["draft-target"] = deepcopy(target)
        products.product["drafts"]["mercadolibre"] = deepcopy(target)
        return {
            "ok": True,
            "items": [
                {
                    "ok": True,
                    "product_id": "product-1",
                    "draft_ids": ["draft-target"],
                }
            ],
        }

    def copy_generator(
        product: dict,
        source_platform: str,
        platform: str,
        language: str,
        mode: str,
        app_config: dict,
    ) -> dict:
        events.append("copy")
        assert product["product_id"] == "product-1"
        assert source_platform == "mercadolibre"
        assert app_config == {"test": True}
        return {
            "ok": True,
            "copy": {
                "title": "Ventilador portátil",
                "description": "Descripción localizada",
                "bullets": ["Compacto"],
            },
            "language": language,
        }

    def images(request, *, product_store):
        events.append("images")
        target = products.drafts[request.draft_id]
        target["images"] = [{"asset_id": "image-1", "role": "main", "order": 0}]
        products.product["drafts"]["mercadolibre"] = deepcopy(target)
        return ProductImagesPrepareResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            image_asset_ids=["image-1"],
            image_count=1,
            changed=True,
        )

    def category(request, *, product_store):
        events.append("category")
        target = products.drafts[request.draft_id]
        target["category_id"] = "CAT-1"
        target["category_path"] = "Home > Fans"
        products.product["drafts"]["mercadolibre"] = deepcopy(target)
        return CategoryMatchCapabilityResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            site="MLM",
            category_id="CAT-1",
            category_path="Home > Fans",
            changed=True,
        )

    def attributes(request, *, product_store):
        events.append("attributes")
        target = products.drafts[request.draft_id]
        target["attributes"] = {"COLOR": "Red"}
        products.product["drafts"]["mercadolibre"] = deepcopy(target)
        return ProductAttributesFillResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            site="MLM",
            attributes={"COLOR": "Red"},
            filled_attribute_ids=["COLOR"],
            changed=True,
        )

    def pricing(payload: dict) -> dict:
        events.append("pricing")
        target = payload["targets"][0]
        assert target["platform"] == "mercadolibre"
        assert target["site"] == "MLM"
        return {
            "ok": True,
            "input": {"common": payload["common"], "targets": [target]},
            "results": [
                {
                    "ok": True,
                    "platform": "mercadolibre",
                    "site": "MLM",
                    "listing_currency": "MXN",
                    "applied_price": {"amount": "299.00", "currency": "MXN"},
                    "calculation_basis": {"cost_cny": "100.00"},
                    "calculation_fingerprint": "fingerprint-1",
                    "errors": [],
                }
            ],
            "errors": [],
            "exchange_rates": {"ok": True, "source": "test"},
        }

    result = prepare_draft_for_market(
        DraftPrepareForMarketRequest(
            draft_id="draft-source",
            target_platform="mercadolibre",
        ),
        product_store=products,
        claim_target_drafts=claim,
        copy_generator=copy_generator,
        app_config_loader=lambda: {"test": True},
        image_capability=images,
        category_capability=category,
        attribute_capability=attributes,
        pricing_calculator=pricing,
    )

    assert events == ["claim", "copy", "images", "category", "attributes", "pricing"]
    assert result.draft_id == "draft-target"
    assert result.source_draft_id == "draft-source"
    assert result.completed_parts == [
        "target_draft",
        "copy",
        "images",
        "category",
        "attributes",
        "pricing",
    ]
    assert result.readiness.image_count == 1
    assert result.readiness.attribute_count == 1
    assert products.drafts["draft-target"]["pricing"]["targets"]["mercadolibre:mlm"]["applied_price"]["amount"] == "299.00"


def test_prepare_returns_input_required_for_unresolved_pricing_fact() -> None:
    draft = _draft("draft-1")
    draft.update(
        {
            "copy_source": "ai",
            "copy_generated_at": "2026-08-13T00:00:00Z",
            "images": [{"asset_id": "image-1", "role": "main", "order": 0}],
            "attributes": {"COLOR": "Red"},
        }
    )
    products = _Products([draft])

    def images(request, *, product_store):
        return ProductImagesPrepareResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            image_asset_ids=["image-1"],
            image_count=1,
            changed=False,
        )

    def category(request, *, product_store):
        target = products.drafts[request.draft_id]
        target["category_id"] = "CAT-1"
        products.product["drafts"]["mercadolibre"] = deepcopy(target)
        return CategoryMatchCapabilityResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            site="MLM",
            category_id="CAT-1",
            changed=True,
        )

    def attributes(request, *, product_store):
        return ProductAttributesFillResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            site="MLM",
            attributes={"COLOR": "Red"},
            filled_attribute_ids=["COLOR"],
            changed=False,
        )

    def pricing(_payload: dict) -> dict:
        return {
            "ok": False,
            "results": [],
            "errors": [
                {
                    "field": "shipping_quote_mode",
                    "message": "请选择物流报价模式",
                }
            ],
        }

    with pytest.raises(CapabilityInputRequired) as exc_info:
        prepare_draft_for_market(
            DraftPrepareForMarketRequest(
                draft_id="draft-1",
                target_platform="mercadolibre",
            ),
            product_store=products,
            image_capability=images,
            category_capability=category,
            attribute_capability=attributes,
            pricing_calculator=pricing,
        )

    assert exc_info.value.code == "PRICING_INPUT_REQUIRED"
    assert exc_info.value.key == "shipping_quote_mode"
    assert exc_info.value.input_owner == "pricing_input"


def test_regenerate_copy_operation_marker_skips_retry_after_domain_save() -> None:
    draft = _draft("draft-1")
    draft.update(
        {
            "images": [{"asset_id": "image-1", "role": "main", "order": 0}],
            "category_id": "CAT-1",
            "attributes": {"COLOR": "Red"},
        }
    )
    products = _Products([draft])
    copy_calls = 0

    def copy_generator(*_args, **_kwargs) -> dict:
        nonlocal copy_calls
        copy_calls += 1
        return {
            "ok": True,
            "copy": {
                "title": "Ventilador regenerado",
                "description": "Descripción regenerada",
            },
            "language": "es-MX",
        }

    def images(request, *, product_store):
        return ProductImagesPrepareResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            image_asset_ids=["image-1"],
            image_count=1,
            changed=False,
        )

    def attributes(request, *, product_store):
        raise CapabilityInputRequired(
            "PRODUCT_ATTRIBUTE_INPUT_REQUIRED",
            "仍缺少品牌。",
            key="BRAND",
            label="品牌",
        )

    request = DraftPrepareForMarketRequest(
        draft_id="draft-1",
        target_platform="mercadolibre",
        regenerate_copy=True,
    )
    operation_key = "global-task:task-1:step:prepare:copy"

    for _attempt in range(2):
        with pytest.raises(CapabilityInputRequired):
            prepare_draft_for_market(
                request,
                product_store=products,
                copy_generator=copy_generator,
                image_capability=images,
                attribute_capability=attributes,
                copy_operation_key=operation_key,
            )

    assert copy_calls == 1
    assert products.drafts["draft-1"]["copy_operation_key"] == operation_key

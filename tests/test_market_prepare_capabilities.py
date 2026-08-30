from __future__ import annotations

from copy import deepcopy

import pytest

from erp_web.product_model import default_draft, default_product_model
from erp_web.runtime_units.attribute_fill_capabilities import (
    fill_product_attributes,
)
from erp_web.runtime_units.category_capabilities import match_category
from erp_web.runtime_units.market_prepare_capabilities import (
    MarketPrepareCapabilityScope,
    _finalize_readiness,
    _prepare_copy,
    draft_prepare_for_market,
    prepare_draft_for_market,
)
from erp_web.runtime_units.market_pricing_capability import (
    _apply_mercadolibre_destination_results,
    _pricing_payload,
    _pricing_target_is_usable,
    prepare_target_pricing,
)
from erp_web.schemas.ai_trace import AiExecutionContext
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
from erp_web.services.capability_input_provenance import encode_user_input_keys
from erp_web.services.listing_currency_service import compute_currency_fingerprint
from erp_web.services.mercadolibre_target_contract import (
    mercadolibre_global_target_contract,
    mercadolibre_sales_target_selectors,
)
from tests.runtime_test_utils import seed_store_currency


def _target_site(platform: str, site: str, currency: str) -> dict:
    return {
        "platform": platform,
        "site": site,
        "language": "es" if platform == "mercadolibre" else "ru-RU",
        "listing_currency": currency,
        "currency_fingerprint": f"sha256:test-{platform}-{site}-{currency}",
    }


def test_sales_target_options_follow_current_copy_language() -> None:
    bindings = [
        {
            "site_id": site_id,
            "logistic_type": "remote",
            "pricing_model": "listing_price" if site_id == "MLM" else "price",
            "user_product": True,
        }
        for site_id in ("MLM", "MLC", "MLB")
    ]

    assert mercadolibre_sales_target_selectors(bindings, language="es") == [
        "MLC:remote",
        "MLM:remote",
    ]
    assert mercadolibre_sales_target_selectors(
        bindings,
        language="pt-BR",
    ) == ["MLB:remote"]
    assert mercadolibre_sales_target_selectors(bindings, language="zh-CN") == []


def _draft(
    draft_id: str,
    platform: str = "mercadolibre",
    site: str = "CBT",
    currency: str = "USD",
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


def _cbt_price_contract(
    operations: list[dict[str, str]],
    *,
    amount: str,
    fingerprint: str,
    listing_model: str = "traditional_global_items",
) -> dict:
    pricing_modes = [
        {
            "site_id": operation["site_id"],
            "logistic_type": operation["logistic_type"],
            "pricing_model": "price",
        }
        for operation in operations
    ]
    return {
        "calculation_basis": {
            "listing_model": listing_model,
            "sites_to_sell": deepcopy(operations),
            "destination_pricing_modes": pricing_modes,
        },
        "destination_results": [
            {
                **mode,
                "price": {"amount": amount, "currency": "USD"},
                "net_proceeds": None,
                "calculation_fingerprint": fingerprint,
            }
            for mode in pricing_modes
        ],
    }


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
        self.save_publish_state_calls = 0
        self.saved_draft_payloads: list[dict] = []

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
        self.saved_draft_payloads.append(deepcopy(draft_payload))
        draft_id = str(draft_payload.get("draft_id") or "")
        self.drafts[draft_id] = deepcopy(draft_payload)
        platform = str(draft_payload.get("platform") or "")
        self.product.setdefault("drafts", {})[platform] = deepcopy(draft_payload)
        return {"draft": deepcopy(draft_payload)}, None, 200

    def save_draft_publish_state(
        self,
        draft_id: str,
        platform: str,
        site: str,
        updates: dict,
    ):
        self.save_publish_state_calls += 1
        draft = deepcopy(self.drafts[draft_id])
        selected_key = (platform.lower(), site.upper())
        targets = []
        for target in draft.get("target_sites", []):
            item = deepcopy(target)
            if (
                str(item.get("platform") or "").lower(),
                str(item.get("site") or "").upper(),
            ) == selected_key:
                item.update(deepcopy(updates))
            targets.append(item)
        draft["target_sites"] = targets
        primary_key = (
            str(draft.get("platform") or "").lower(),
            str(draft.get("site") or "").upper(),
        )
        if primary_key == selected_key:
            draft.update(deepcopy(updates))
        self.drafts[draft_id] = draft
        self.product.setdefault("drafts", {})[platform] = deepcopy(draft)
        return {"draft": deepcopy(draft)}, None, 200

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
        "site": "CBT",
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


def test_finalize_readiness_uses_trusted_publish_state_writer() -> None:
    draft = _draft("draft-finalize", site="CBT", currency="USD")
    draft.update(
        {
            "validation_errors": [{"code": "STALE"}],
            "last_precheck": {"ok": True},
            "publish_status": "ready",
            "status": "ready_to_publish",
        }
    )
    draft["target_sites"][0].update(
        {
            "validation_errors": [{"code": "STALE"}],
            "last_precheck": {"ok": True},
            "publish_status": "ready",
            "status": "ready_to_publish",
        }
    )
    products = _Products([draft])

    result = _finalize_readiness(
        target_draft_id="draft-finalize",
        platform="mercadolibre",
        site="CBT",
        product_store=products,
    )

    saved = products.drafts["draft-finalize"]
    saved_target = saved["target_sites"][0]
    assert products.save_publish_state_calls == 1
    assert products.save_draft_calls == 0
    assert result.workflow_status == "images_ready"
    for item in (saved, saved_target):
        assert item["validation_errors"] == []
        assert item["last_precheck"] == {}
        assert item["publish_status"] == ""
        assert item["status"] == "images_ready"


def test_cbt_pricing_request_uses_canonical_sales_targets_from_draft() -> None:
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    target = draft["target_sites"][0]
    target["sites_to_sell"] = [
        {"site_id": "MLM", "logistic_type": "remote"},
        {"siteId": "mlb", "logisticType": "REMOTE"},
    ]
    products = _Products([draft])

    payload = _pricing_payload(
        {
            "target": {
                # 调用方不能用核价参数篡改草稿当前销售目标。
                "sites_to_sell": [
                    {"site_id": "MLC", "logistic_type": "remote"}
                ]
            }
        },
        product=products.product,
        draft=draft,
        target=target,
    )

    assert payload["targets"][0]["sites_to_sell"] == [
        {"site_id": "MLB", "logistic_type": "remote"},
        {"site_id": "MLM", "logistic_type": "remote"},
    ]


def test_cbt_pricing_without_sales_target_requests_trusted_selector() -> None:
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    products = _Products([draft])

    def pricing(payload: dict) -> dict:
        assert payload["targets"][0]["sites_to_sell"] == []
        return {
            "ok": False,
            "error_code": "MERCADOLIBRE_SITES_TO_SELL_REQUIRED",
            "sales_target_options": ["MLB:fulfillment", "MLM:remote"],
            "results": [],
            "errors": [
                {
                    "field": "sites_to_sell",
                    "message": "CBT 草稿尚未选择实际销售国家与物流方式",
                }
            ],
        }

    with pytest.raises(CapabilityInputRequired) as exc_info:
        prepare_target_pricing(
            target_draft_id="draft-cbt",
            target_platform="mercadolibre",
            site="CBT",
            pricing_input={"common": {"purchase_cost": "100"}},
            product_store=products,
            pricing_calculator=pricing,
        )

    assert exc_info.value.code == "MERCADOLIBRE_SITES_TO_SELL_REQUIRED"
    assert exc_info.value.key == "sales_target"
    assert exc_info.value.input_type == "multi_select"
    assert exc_info.value.input_owner == "step"
    assert [option.value for option in exc_info.value.options] == [
        "MLB:fulfillment",
        "MLM:remote",
    ]
    assert products.save_draft_calls == 0


def test_prepare_for_market_sales_target_rejects_legacy_scalar_selector() -> None:
    with pytest.raises(ValueError):
        DraftPrepareForMarketRequest(
            draft_id="draft-cbt",
            target_platform="mercadolibre",
            site="CBT",
            sales_target="MLM:remote",  # type: ignore[arg-type]
        )


def test_cbt_sales_target_selection_is_saved_before_pricing_result() -> None:
    seed_store_currency(
        "mercadolibre",
        "USD",
        identity={"user_id": "market-prepare-test"},
    )
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    products = _Products([draft])
    canonical_target = [
        {"site_id": "MLB", "logistic_type": "remote"},
        {"site_id": "MLM", "logistic_type": "remote"},
    ]

    def pricing(payload: dict) -> dict:
        target = payload["targets"][0]
        assert target["sites_to_sell"] == canonical_target
        pricing_target = {
            "ok": True,
            "target_key": "mercadolibre:cbt",
            "platform": "mercadolibre",
            "site": "CBT",
            "listing_currency": "USD",
            "applied_price": {"amount": "39.99", "currency": "USD"},
            **_cbt_price_contract(
                canonical_target,
                amount="39.99",
                fingerprint="fingerprint-cbt-mlm-remote",
            ),
            "calculation_fingerprint": "fingerprint-cbt-mlm-remote",
            "errors": [],
        }
        return {
            "ok": True,
            "input": {"common": payload["common"], "targets": [target]},
            "results": [pricing_target],
            "errors": [],
            "exchange_rates": {"ok": True, "source": "test"},
        }

    result = prepare_target_pricing(
        target_draft_id="draft-cbt",
        target_platform="mercadolibre",
        site="CBT",
        sales_target=["MLM:remote", "MLB:remote"],
        pricing_input={"common": {"purchase_cost": "100"}},
        product_store=products,
        pricing_calculator=pricing,
    )

    assert result["applied_price"] == {"amount": "39.99", "currency": "USD"}
    # 第一次写入只保存用户明确选择，让 Store 有机会清理旧核价/预检；
    # 第二次写入再保存与该销售目标绑定的确定性核价结果。
    assert products.save_draft_calls == 2
    selected_snapshot, priced_snapshot = products.saved_draft_payloads
    assert selected_snapshot["target_sites"][0]["sites_to_sell"] == canonical_target
    assert (
        selected_snapshot.get("pricing", {})
        .get("targets", {})
        .get("mercadolibre:cbt")
        is None
    )
    priced_operations = [
        {**operation, "price": "39.99"} for operation in canonical_target
    ]
    assert (
        priced_snapshot["target_sites"][0]["sites_to_sell"]
        == priced_operations
    )
    persisted_pricing = priced_snapshot["pricing"]["targets"]["mercadolibre:cbt"]
    assert persisted_pricing["calculation_basis"]["sites_to_sell"] == canonical_target
    assert (
        products.drafts["draft-cbt"]["target_sites"][0]["sites_to_sell"]
        == priced_operations
    )


def test_cbt_sales_target_remains_saved_when_other_pricing_input_is_missing() -> None:
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    products = _Products([draft])
    canonical_target = [{"site_id": "MLM", "logistic_type": "remote"}]

    def pricing(payload: dict) -> dict:
        assert payload["targets"][0]["sites_to_sell"] == canonical_target
        return {
            "ok": False,
            "error_code": "PRICING_INPUT_REQUIRED",
            "results": [],
            "errors": [
                {
                    "field": "shipping_amount",
                    "message": "缺少物流报价金额",
                }
            ],
        }

    with pytest.raises(CapabilityInputRequired) as exc_info:
        prepare_target_pricing(
            target_draft_id="draft-cbt",
            target_platform="mercadolibre",
            site="CBT",
            sales_target=["MLM:remote"],
            pricing_input={"common": {"purchase_cost": "100"}},
            product_store=products,
            pricing_calculator=pricing,
        )

    assert exc_info.value.key == "shipping_amount"
    assert exc_info.value.input_owner == "pricing_input"
    assert products.save_draft_calls == 1
    assert (
        products.drafts["draft-cbt"]["target_sites"][0]["sites_to_sell"]
        == canonical_target
    )


def test_same_market_multiple_logistics_is_rejected_without_persisting() -> None:
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    products = _Products([draft])
    selected = [
        {"site_id": "MLM", "logistic_type": "fulfillment"},
        {"site_id": "MLM", "logistic_type": "remote"},
    ]

    def pricing(payload: dict) -> dict:
        targets = payload["targets"][0]["sites_to_sell"]
        assert targets == selected
        _canonical, issues = mercadolibre_global_target_contract(
            targets,
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "fulfillment",
                    "pricing_model": "price",
                    "user_product": True,
                },
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "pricing_model": "price",
                    "user_product": True,
                },
            ],
            listing_model="user_products",
        )
        issue = issues[0]
        return {
            "ok": False,
            "error_code": issue["code"],
            "sales_target_options": ["MLM:fulfillment", "MLM:remote"],
            "results": [],
            "errors": [issue],
        }

    with pytest.raises(CapabilityInputRequired) as exc_info:
        prepare_target_pricing(
            target_draft_id="draft-cbt",
            target_platform="mercadolibre",
            site="CBT",
            sales_target=["MLM:fulfillment", "MLM:remote"],
            pricing_input={"common": {"purchase_cost": "100"}},
            product_store=products,
            pricing_calculator=pricing,
        )

    assert exc_info.value.code == "MERCADOLIBRE_MARKET_OPERATION_AMBIGUOUS"
    assert exc_info.value.input_type == "multi_select"
    assert "同一销售市场只能选择一种物流方式" in exc_info.value.reason
    assert products.save_draft_calls == 0


@pytest.mark.parametrize(
    ("selectors", "error_code", "error_field", "expected_targets"),
    [
        (
            ["MLM"],
            "MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED",
            "sites_to_sell[0].logistic_type",
            [{"site_id": "MLM", "logistic_type": ""}],
        ),
        (
            ["MLC:remote"],
            "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED",
            "sites_to_sell[0]",
            [{"site_id": "MLC", "logistic_type": "remote"}],
        ),
    ],
)
def test_invalid_or_unauthorized_cbt_sales_target_is_not_persisted(
    selectors: list[str],
    error_code: str,
    error_field: str,
    expected_targets: list[dict[str, str]],
) -> None:
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    products = _Products([draft])

    def pricing(payload: dict) -> dict:
        assert payload["targets"][0]["sites_to_sell"] == expected_targets
        return {
            "ok": False,
            "error_code": error_code,
            "sales_target_options": ["MLM:remote"],
            "results": [],
            "errors": [
                {
                    "field": error_field,
                    "message": "销售目标不合法或当前账号未开通",
                }
            ],
        }

    with pytest.raises(CapabilityInputRequired) as exc_info:
        prepare_target_pricing(
            target_draft_id="draft-cbt",
            target_platform="mercadolibre",
            site="CBT",
            sales_target=selectors,
            pricing_input={"common": {"purchase_cost": "100"}},
            product_store=products,
            pricing_calculator=pricing,
        )

    assert exc_info.value.code == error_code
    assert exc_info.value.key == "sales_target"
    assert [option.value for option in exc_info.value.options] == ["MLM:remote"]
    assert products.save_draft_calls == 0
    assert products.drafts["draft-cbt"]["target_sites"][0].get("sites_to_sell") in (
        None,
        [],
    )


def test_cbt_existing_pricing_is_not_usable_without_saved_sales_target() -> None:
    selected = {
        "applied_price": {"amount": "39.99", "currency": "USD"},
        "calculation_basis": {"sites_to_sell": []},
        "calculation_fingerprint": "fingerprint-without-sales-target",
    }
    target_draft = {
        "platform": "mercadolibre",
        "site": "CBT",
        "listing_currency": "USD",
        "sites_to_sell": [],
    }

    assert _pricing_target_is_usable(target_draft, selected) is False


def test_cbt_existing_pricing_without_destination_results_is_not_usable() -> None:
    operations = [{"site_id": "MLM", "logistic_type": "remote"}]
    selected = {
        "applied_price": {"amount": "39.99", "currency": "USD"},
        "calculation_basis": {
            "sites_to_sell": operations,
            "destination_pricing_modes": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "pricing_model": "price",
                }
            ],
        },
        "calculation_fingerprint": "fingerprint-without-destination-results",
    }
    target_draft = {
        "platform": "mercadolibre",
        "site": "CBT",
        "listing_currency": "USD",
        "sites_to_sell": operations,
    }

    assert _pricing_target_is_usable(target_draft, selected) is False


@pytest.mark.parametrize(
    "changed_condition",
    [
        {"listing_type_id": "gold_pro", "free_shipping": False},
        {"listing_type_id": "gold_special", "free_shipping": True},
    ],
)
def test_cbt_sales_condition_change_invalidates_existing_pricing(
    changed_condition: dict[str, object],
) -> None:
    basis_operation = {
        "site_id": "MLM",
        "logistic_type": "remote",
        "listing_type_id": "gold_special",
        "free_shipping": False,
    }
    selected = {
        "applied_price": {"amount": "39.99", "currency": "USD"},
        **_cbt_price_contract(
            [basis_operation],
            amount="39.99",
            fingerprint="fingerprint-sales-condition",
        ),
        "calculation_fingerprint": "fingerprint-sales-condition",
    }
    target_draft = {
        "platform": "mercadolibre",
        "site": "CBT",
        "listing_currency": "USD",
        "sites_to_sell": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "price": "39.99",
                **changed_condition,
            }
        ],
    }

    assert _pricing_target_is_usable(target_draft, selected) is False


def test_cbt_binding_pricing_mode_change_forces_recalculation() -> None:
    operation = {"site_id": "MLM", "logistic_type": "remote"}
    current_currency_fingerprint = compute_currency_fingerprint(
        "mercadolibre",
        "seller-current",
        "USD",
        ["USD"],
        "locked",
        "authorization",
    )
    old_selected = {
        "ok": True,
        "target_key": "mercadolibre:cbt",
        "platform": "mercadolibre",
        "site": "CBT",
        "listing_currency": "USD",
        "currency_fingerprint": current_currency_fingerprint,
        "applied_price": {"amount": "50.00", "currency": "USD"},
        "calculation_basis": {
            "listing_model": "traditional_global_items",
            "sites_to_sell": [operation],
            "destination_pricing_modes": [
                {**operation, "pricing_model": "net_proceeds"}
            ],
        },
        "destination_results": [
            {
                **operation,
                "pricing_model": "net_proceeds",
                "price": None,
                "net_proceeds": {"amount": "25.00", "currency": "USD"},
                "calculation_fingerprint": "fingerprint-old-net",
            }
        ],
        "calculation_fingerprint": "fingerprint-old-net",
        "errors": [],
    }
    old_selected["calculation_basis"].update(
        {
            "listing_currency": "USD",
            "currency_fingerprint": current_currency_fingerprint,
        }
    )
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    draft["target_sites"][0]["sites_to_sell"] = [
        {**operation, "net_proceeds": "25.00"}
    ]
    draft["pricing"] = {
        "common": {"purchase_cost_cny": "100"},
        "targets": {"mercadolibre:cbt": old_selected},
    }
    products = _Products([draft])
    pricing_calls = 0

    def pricing(payload: dict) -> dict:
        nonlocal pricing_calls
        pricing_calls += 1
        pricing_target = {
            "ok": True,
            "target_key": "mercadolibre:cbt",
            "platform": "mercadolibre",
            "site": "CBT",
            "listing_currency": "USD",
            "applied_price": {"amount": "55.00", "currency": "USD"},
            **_cbt_price_contract(
                [operation],
                amount="55.00",
                fingerprint="fingerprint-new-price",
            ),
            "calculation_fingerprint": "fingerprint-new-price",
            "errors": [],
        }
        return {
            "ok": True,
            "input": payload,
            "results": [pricing_target],
            "errors": [],
            "exchange_rates": {"ok": True, "source": "test"},
        }

    result = prepare_target_pricing(
        target_draft_id="draft-cbt",
        target_platform="mercadolibre",
        site="CBT",
        product_store=products,
        pricing_calculator=pricing,
        store_config_loader=lambda: {
            "mercadolibre": {
                "user_id": "seller-current",
                "listing_model": "traditional_global_items",
                "listing_currency": "USD",
                "allowed_currencies": ["USD"],
                "currency_mode": "locked",
                "currency_status": "ready",
                "currency_source": "authorization",
                "marketplace_bindings": [
                    {
                        **operation,
                        "pricing_model": "listing_price",
                        "user_product": False,
                    }
                ],
            }
        },
    )

    assert pricing_calls == 1
    assert result["applied_price"] == {"amount": "55.00", "currency": "USD"}
    assert products.drafts["draft-cbt"]["target_sites"][0]["sites_to_sell"] == [
        {**operation, "price": "55.00"}
    ]


def test_cbt_same_sales_target_preserves_existing_non_amount_conditions() -> None:
    operation = {
        "site_id": "MLM",
        "logistic_type": "remote",
        "listing_type_id": "gold_special",
        "free_shipping": True,
        "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "No warranty"}],
    }
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    draft["target_sites"][0]["sites_to_sell"] = [
        {**operation, "price": "39.99"}
    ]
    products = _Products([draft])

    def pricing(payload: dict) -> dict:
        target = payload["targets"][0]
        assert target["sites_to_sell"] == [operation]
        pricing_target = {
            "ok": True,
            "target_key": "mercadolibre:cbt",
            "platform": "mercadolibre",
            "site": "CBT",
            "listing_currency": "USD",
            "applied_price": {"amount": "41.00", "currency": "USD"},
            **_cbt_price_contract(
                [operation],
                amount="41.00",
                fingerprint="fingerprint-preserved-conditions",
            ),
            "calculation_fingerprint": "fingerprint-preserved-conditions",
            "errors": [],
        }
        return {
            "ok": True,
            "input": payload,
            "results": [pricing_target],
            "errors": [],
            "exchange_rates": {"ok": True, "source": "test"},
        }

    prepare_target_pricing(
        target_draft_id="draft-cbt",
        target_platform="mercadolibre",
        site="CBT",
        sales_target=["MLM:remote"],
        pricing_input={"common": {"purchase_cost": "100"}},
        product_store=products,
        pricing_calculator=pricing,
    )

    assert products.drafts["draft-cbt"]["target_sites"][0]["sites_to_sell"] == [
        {**operation, "price": "41.00"}
    ]


def test_cbt_store_identity_change_forces_recalculation() -> None:
    operation = {"site_id": "MLM", "logistic_type": "remote"}
    old_currency_fingerprint = compute_currency_fingerprint(
        "mercadolibre",
        "seller-old",
        "USD",
        ["USD"],
        "locked",
        "authorization",
    )
    selected = {
        "ok": True,
        "target_key": "mercadolibre:cbt",
        "platform": "mercadolibre",
        "site": "CBT",
        "listing_currency": "USD",
        "currency_fingerprint": old_currency_fingerprint,
        "applied_price": {"amount": "50.00", "currency": "USD"},
        **_cbt_price_contract(
            [operation],
            amount="50.00",
            fingerprint="fingerprint-old-account",
        ),
        "calculation_fingerprint": "fingerprint-old-account",
        "errors": [],
    }
    selected["calculation_basis"].update(
        {
            "listing_currency": "USD",
            "currency_fingerprint": old_currency_fingerprint,
        }
    )
    draft = _draft("draft-cbt", site="CBT", currency="USD")
    draft["target_sites"][0]["sites_to_sell"] = [
        {**operation, "price": "50.00"}
    ]
    draft["pricing"] = {
        "common": {"purchase_cost_cny": "100"},
        "targets": {"mercadolibre:cbt": selected},
    }
    products = _Products([draft])
    pricing_calls = 0

    def pricing(payload: dict) -> dict:
        nonlocal pricing_calls
        pricing_calls += 1
        pricing_target = {
            **selected,
            "applied_price": {"amount": "51.00", "currency": "USD"},
            **_cbt_price_contract(
                [operation],
                amount="51.00",
                fingerprint="fingerprint-current-account",
            ),
            "calculation_fingerprint": "fingerprint-current-account",
        }
        return {
            "ok": True,
            "input": payload,
            "results": [pricing_target],
            "errors": [],
            "exchange_rates": {"ok": True, "source": "test"},
        }

    result = prepare_target_pricing(
        target_draft_id="draft-cbt",
        target_platform="mercadolibre",
        site="CBT",
        product_store=products,
        pricing_calculator=pricing,
        store_config_loader=lambda: {
            "mercadolibre": {
                "user_id": "seller-current",
                "listing_model": "traditional_global_items",
                "listing_currency": "USD",
                "allowed_currencies": ["USD"],
                "currency_mode": "locked",
                "currency_status": "ready",
                "currency_source": "authorization",
                "marketplace_bindings": [
                    {
                        **operation,
                        "pricing_model": "listing_price",
                        "user_product": False,
                    }
                ],
            }
        },
    )

    assert pricing_calls == 1
    assert result["applied_price"] == {"amount": "51.00", "currency": "USD"}


def test_cbt_net_proceeds_result_applies_scalar_amount_to_market_operation() -> None:
    target = {
        "platform": "mercadolibre",
        "site": "CBT",
        "listing_currency": "USD",
        "sites_to_sell": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "price": "50.00",
                "listing_type_id": "gold_special",
            }
        ],
    }
    pricing_target = {
        "listing_currency": "USD",
        "calculation_fingerprint": "fingerprint-net",
        "destination_results": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "pricing_model": "net_proceeds",
                "price": None,
                "net_proceeds": {"amount": "25.00", "currency": "USD"},
                "calculation_fingerprint": "fingerprint-net",
            }
        ],
    }

    assert _apply_mercadolibre_destination_results(target, pricing_target) == [
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "net_proceeds": "25.00",
            "listing_type_id": "gold_special",
        }
    ]


def test_prepare_for_market_only_accepts_user_submitted_sales_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[list[str]] = []

    def fake_prepare(request: DraftPrepareForMarketRequest, **_kwargs):
        received.append(list(request.sales_target))
        return object()

    monkeypatch.setattr(
        "erp_web.runtime_units.market_prepare_capabilities.prepare_draft_for_market",
        fake_prepare,
    )
    scope = MarketPrepareCapabilityScope(
        products=_Products([_draft("draft-cbt", site="CBT", currency="USD")]),
        category_matcher=lambda *_args, **_kwargs: {},
        claim_target_drafts=lambda *_args, **_kwargs: {},
        copy_generator=lambda *_args, **_kwargs: {},
        app_config_loader=lambda: {},
    )
    request = DraftPrepareForMarketRequest(
        draft_id="draft-cbt",
        target_platform="mercadolibre",
        site="CBT",
        sales_target=["MLM:remote", "MLB:remote"],
    )

    def execution(*, trusted: bool) -> AiExecutionContext:
        business_scope = {"task_id": "task-1", "step_id": "step-1"}
        if trusted:
            business_scope["user_input_keys"] = encode_user_input_keys(
                ["sales_target"]
            )
        return AiExecutionContext.create(
            timeout_seconds=30,
            budget_profile="test",
            task_run_id="task-1",
            attempt_id="attempt-1",
            permissions=frozenset({"product.write"}),
            business_scope=business_scope,
            idempotency_context={"operation_key": "op-prepare"},
            allow_write=True,
        )

    draft_prepare_for_market(
        request,
        scope=scope,
        execution=execution(trusted=False),
    )
    draft_prepare_for_market(
        request,
        scope=scope,
        execution=execution(trusted=True),
    )

    assert received == [[], ["MLM:remote", "MLB:remote"]]


def test_category_match_persists_focused_agent_selection() -> None:
    products = _Products()

    def matcher(product: dict, draft: dict, target: dict) -> dict:
        assert product["drafts"]["mercadolibre"]["draft_id"] == "draft-1"
        assert target["site"] == "CBT"
        return {
            "ok": True,
            "status": "completed",
            "target": {"platform": "mercadolibre", "site": "CBT"},
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
            site="CBT",
        ),
        product_store=products,
        matcher=matcher,
        category_record_loader=_category_record,
    )

    assert result.category_id == "CAT-1"
    assert result.platform == "mercadolibre"
    assert products.drafts["draft-1"]["category_id"] == "CAT-1"
    assert products.drafts["draft-1"]["category_path"] == "Home > Fans"
    # 平台规则不再持久化：草稿不含 Schema，商品不含完整 category record。
    assert "category_attribute_schema" not in products.drafts["draft-1"]
    assert not products.product.get("local_platform_categories")


def test_category_match_abstain_requires_explicit_category() -> None:
    products = _Products()

    def matcher(*_args, **_kwargs) -> dict:
        return {
            "ok": True,
            "status": "unresolved",
            "selected_category_id": None,
            "query": "fan",
            "candidates": [
                {
                    "category_id": "CAT-1",
                    "name": "Fans",
                    "path_segments": ["Home", "Ventilation", "Fans"],
                },
                {"category_id": "CAT-2", "name": "Ventilation"},
                {"category_id": "CAT-3", "name": "", "path_segments": []},
                {"category_id": "CAT-1", "name": "Duplicate"},
                {"category_id": "", "name": "Missing ID"},
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
    assert [option.value for option in exc_info.value.options] == [
        "CAT-1",
        "CAT-2",
        "CAT-3",
    ]
    assert [option.label for option in exc_info.value.options] == [
        "Home › Ventilation › Fans（CAT-1）",
        "Ventilation（CAT-2）",
        "CAT-3",
    ]
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
    assert [option.value for option in exc_info.value.options] == ["AA", "AAA"]
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
    assert [option.value for option in exc_info.value.options] == [
        "Для собак",
        "Для кошек",
        "Для птиц",
    ]
    assert exc_info.value.input_type == "select"
    assert "枚举" in exc_info.value.reason
    assert calls == [("mercadolibre", "CAT-1", "PURPOSE", "CBT")]


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


def test_attribute_fill_completes_for_category_without_required_attributes() -> None:
    """零必填参数类目：没有可填的必填属性时正常完成，不得 needs_input。"""

    draft = _draft("draft-1")
    draft["category_id"] = "CAT-1"
    products = _Products([draft])

    def record(*_args, **_kwargs) -> dict:
        category = _category_record()
        category["attributes"]["required"] = []
        category["attributes"]["optional"] = [
            {
                "id": "PURPOSE",
                "name": "Предназначено для",
                "required": False,
                "is_dictionary": True,
                "dictionary_id": "749",
                "options": [],
            }
        ]
        return category

    def filler(product: dict, platform: str, category: dict | None):
        return deepcopy(product), {"source": "rules", "ai_filled": []}

    result = fill_product_attributes(
        ProductAttributesFillRequest(
            draft_id="draft-1",
            target_platform="mercadolibre",
        ),
        product_store=products,
        attribute_filler=filler,
        category_record_loader=record,
    )

    assert result.draft_id == "draft-1"
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
    seed_store_currency(
        "mercadolibre",
        "USD",
        identity={"user_id": "market-prepare-test"},
    )
    origin = _draft("draft-source", "yandex", "global", "RUB")
    products = _Products([origin])
    events: list[str] = []

    def claim(product_ids: list[str], platforms: list[str] | None) -> dict:
        events.append("claim")
        assert product_ids == ["product-1"]
        assert platforms == ["mercadolibre"]
        target = _draft("draft-target")
        target["target_sites"][0]["sites_to_sell"] = [
            {"site_id": "MLM", "logistic_type": "remote"}
        ]
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
            site="CBT",
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
            site="CBT",
            attributes={"COLOR": "Red"},
            filled_attribute_ids=["COLOR"],
            changed=True,
        )

    def pricing(payload: dict) -> dict:
        events.append("pricing")
        target = payload["targets"][0]
        assert target["platform"] == "mercadolibre"
        assert target["site"] == "CBT"
        return {
            "ok": True,
            "input": {"common": payload["common"], "targets": [target]},
            "results": [
                {
                    "ok": True,
                    "platform": "mercadolibre",
                    "site": "CBT",
                    "listing_currency": "USD",
                    "applied_price": {"amount": "299.00", "currency": "USD"},
                    **_cbt_price_contract(
                        target["sites_to_sell"],
                        amount="299.00",
                        fingerprint="fingerprint-1",
                    ),
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
    assert products.drafts["draft-target"]["pricing"]["targets"]["mercadolibre:cbt"]["applied_price"]["amount"] == "299.00"


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
            site="CBT",
            category_id="CAT-1",
            changed=True,
        )

    def attributes(request, *, product_store):
        return ProductAttributesFillResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            site="CBT",
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


def test_cbt_ready_copy_does_not_generate_per_market_copy() -> None:
    draft = _draft("draft-cbt")
    draft.update(
        {
            "copy_source": "ai",
            "copy_generated_at": "2026-08-27T00:00:00Z",
        }
    )
    target = draft["target_sites"][0]
    target.update(
        {
            "sites_to_sell": [
                {"site_id": "MLM", "logistic_type": "remote"}
            ],
            "validation_errors": [{"field": "title", "message": "stale"}],
            "last_precheck": {"ok": True},
            "publish_status": "ready",
        }
    )
    products = _Products([draft])
    _prepare_copy(
        DraftPrepareForMarketRequest(
            draft_id="draft-cbt",
            target_platform="mercadolibre",
        ),
        target_draft_id="draft-cbt",
        product_store=products,
        copy_generator=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("已完成的当前语言文案不应再次调用模型")
        ),
        app_config_loader=lambda: (_ for _ in ()).throw(
            AssertionError("已完成的当前语言文案不应加载模型配置")
        ),
        copy_operation_key="",
    )

    saved = products.drafts["draft-cbt"]
    saved_target = saved["target_sites"][0]
    assert saved["title"] == "Portable fan"
    assert saved["description"] == "Portable fan description"
    assert "marketplace_titles" not in saved_target
    assert products.save_draft_calls == 0


def test_cbt_fully_ready_copy_does_not_call_generator_or_save() -> None:
    draft = _draft("draft-cbt")
    draft.update(
        {
            "copy_source": "ai",
            "copy_generated_at": "2026-08-27T00:00:00Z",
        }
    )
    target = draft["target_sites"][0]
    target["sites_to_sell"] = [
        {"site_id": "MLM", "logistic_type": "remote"},
        {"site_id": "MLC", "logistic_type": "remote"},
    ]
    products = _Products([draft])

    _prepare_copy(
        DraftPrepareForMarketRequest(
            draft_id="draft-cbt",
            target_platform="mercadolibre",
        ),
        target_draft_id="draft-cbt",
        product_store=products,
        copy_generator=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("完整文案不应再次调用模型")
        ),
        app_config_loader=lambda: (_ for _ in ()).throw(
            AssertionError("完整文案不应加载模型配置")
        ),
        copy_operation_key="",
    )

    assert products.save_draft_calls == 0


def test_cbt_ready_copy_does_not_depend_on_listing_model() -> None:
    draft = _draft("draft-cbt")
    draft.update(
        {
            "copy_source": "ai",
            "copy_generated_at": "2026-08-27T00:00:00Z",
        }
    )
    draft["target_sites"][0]["sites_to_sell"] = [
        {"site_id": "MLM", "logistic_type": "remote"},
        {"site_id": "MLC", "logistic_type": "remote"},
    ]
    products = _Products([draft])

    def stop_after_copy(*_args, **_kwargs):
        raise BusinessCapabilityError("TEST_STOP", "copy 步骤之后停止测试。")

    with pytest.raises(BusinessCapabilityError) as exc_info:
        prepare_draft_for_market(
            DraftPrepareForMarketRequest(
                draft_id="draft-cbt",
                target_platform="mercadolibre",
            ),
            product_store=products,
            copy_generator=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("已完成的当前语言文案不应再次生成")
            ),
            app_config_loader=lambda: (_ for _ in ()).throw(
                AssertionError("已完成的当前语言文案不应加载模型配置")
            ),
            image_capability=stop_after_copy,
        )

    assert exc_info.value.code == "TEST_STOP"
    assert "marketplace_titles" not in products.drafts["draft-cbt"][
        "target_sites"
    ][0]
    assert products.save_draft_calls == 0


def test_cbt_only_generates_current_language_copy() -> None:
    draft = _draft("draft-cbt")
    draft["target_sites"][0]["sites_to_sell"] = [
        {"site_id": "MLM", "logistic_type": "remote"},
        {"site_id": "MLC", "logistic_type": "remote"},
    ]
    products = _Products([draft])
    languages: list[str] = []

    def copy_generator(
        _product: dict,
        _source_platform: str,
        _platform: str,
        language: str,
        _mode: str,
        _app_config: dict,
    ) -> dict:
        languages.append(language)
        return {
            "ok": True,
            "copy": {
                "title": "Portable fan AI",
                "description": "Portable fan AI description",
            },
            "language": language,
        }

    _prepare_copy(
        DraftPrepareForMarketRequest(
            draft_id="draft-cbt",
            target_platform="mercadolibre",
        ),
        target_draft_id="draft-cbt",
        product_store=products,
        copy_generator=copy_generator,
        app_config_loader=lambda: {"test": True},
        copy_operation_key="",
    )

    saved = products.drafts["draft-cbt"]
    assert languages == ["es"]
    assert saved["title"] == "Portable fan AI"
    assert saved["description"] == "Portable fan AI description"
    assert not saved["target_sites"][0].get("marketplace_titles")
    assert products.save_draft_calls == 1


def test_cbt_sales_target_input_does_not_generate_additional_copy() -> None:
    seed_store_currency(
        "mercadolibre",
        "USD",
        identity={"user_id": "market-prepare-test"},
    )
    draft = _draft("draft-cbt")
    draft.update(
        {
            "copy_source": "ai",
            "copy_generated_at": "2026-08-27T00:00:00Z",
            "images": [{"asset_id": "image-1", "role": "main", "order": 0}],
            "category_id": "CAT-1",
            "attributes": {"COLOR": "Red"},
        }
    )
    products = _Products([draft])
    languages: list[str] = []

    def copy_generator(
        _product: dict,
        _source_platform: str,
        _platform: str,
        language: str,
        _mode: str,
        _app_config: dict,
    ) -> dict:
        languages.append(language)
        return {"ok": True, "copy": {"title": f"Title {language}"}}

    def images(request, *, product_store):
        return ProductImagesPrepareResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            image_asset_ids=["image-1"],
            image_count=1,
            changed=False,
        )

    def attributes(request, *, product_store):
        return ProductAttributesFillResult(
            draft_id=request.draft_id,
            platform="mercadolibre",
            site="CBT",
            attributes={"COLOR": "Red"},
            filled_attribute_ids=[],
            changed=False,
        )

    def pricing(payload: dict) -> dict:
        target = payload["targets"][0]
        pricing_target = {
            "ok": True,
            "target_key": "mercadolibre:cbt",
            "platform": "mercadolibre",
            "site": "CBT",
            "listing_currency": "USD",
            "applied_price": {"amount": "39.99", "currency": "USD"},
            **_cbt_price_contract(
                target["sites_to_sell"],
                amount="39.99",
                fingerprint="fingerprint-cbt",
            ),
            "calculation_fingerprint": "fingerprint-cbt",
            "errors": [],
        }
        return {
            "ok": True,
            "input": {"common": payload["common"], "targets": [target]},
            "results": [pricing_target],
            "errors": [],
            "exchange_rates": {"ok": True, "source": "test"},
        }

    result = prepare_draft_for_market(
        DraftPrepareForMarketRequest(
            draft_id="draft-cbt",
            target_platform="mercadolibre",
            sales_target=["MLM:remote", "MLC:remote"],
            pricing_input={"common": {"purchase_cost": "100"}},
        ),
        product_store=products,
        copy_generator=copy_generator,
        app_config_loader=lambda: {"test": True},
        image_capability=images,
        attribute_capability=attributes,
        pricing_calculator=pricing,
    )

    target = products.drafts["draft-cbt"]["target_sites"][0]
    assert result.completed_parts.count("copy") == 1
    assert languages == []
    assert target["sites_to_sell"] == [
        {"site_id": "MLC", "logistic_type": "remote", "price": "39.99"},
        {"site_id": "MLM", "logistic_type": "remote", "price": "39.99"},
    ]
    assert "marketplace_titles" not in target


def test_cbt_current_language_copy_failure_does_not_persist_partial_copy() -> None:
    draft = _draft("draft-cbt")
    draft["target_sites"][0]["sites_to_sell"] = [
        {"site_id": "MLM", "logistic_type": "remote"},
    ]
    original = deepcopy(draft)
    products = _Products([draft])
    languages: list[str] = []

    def copy_generator(
        _product: dict,
        _source_platform: str,
        _platform: str,
        language: str,
        _mode: str,
        _app_config: dict,
    ) -> dict:
        languages.append(language)
        return {"ok": False, "error": "当前语言模型暂时不可用"}

    with pytest.raises(BusinessCapabilityError) as exc_info:
        _prepare_copy(
            DraftPrepareForMarketRequest(
                draft_id="draft-cbt",
                target_platform="mercadolibre",
            ),
            target_draft_id="draft-cbt",
            product_store=products,
            copy_generator=copy_generator,
            app_config_loader=lambda: {"test": True},
            copy_operation_key="",
        )

    assert exc_info.value.code == "DRAFT_COPY_GENERATION_FAILED"
    assert languages == ["es"]
    assert products.save_draft_calls == 0
    assert products.drafts["draft-cbt"] == original

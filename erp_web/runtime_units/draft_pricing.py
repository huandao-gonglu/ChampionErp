"""页面、AI 和市场准备共用的逐 SKU 核价业务入口。"""
from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from time import perf_counter
from uuid import uuid4
from typing import Any, Protocol

from erp_web.context import get_context
from erp_web.product_model import mercadolibre_sales_condition_basis
from erp_web.product_model.sku_model import selected_skus
from erp_web.runtime_units.draft_publish_context import draft_for_publish_target
from erp_web.runtime_units.market_capability_support import load_draft, raise_store_error
from erp_web.runtime_units.pricing_batch import calculate_sku_prices
from erp_web.runtime_units.pricing_results import _apply_mercadolibre_destination_results
from erp_web.runtime_units.pricing_runtime import PricingSession
from erp_web.schemas.draft_pricing import (
    DraftPricingBatchApplyRequest,
    DraftPricingBatchPreviewRequest,
    DraftPricingRequest,
    PricingCommonPatch,
    PricingTargetPatch,
)
from erp_web.services.ai_run_cancellation import check_cancellation
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.pricing_service import pricing_calculation_fingerprint
from erp_web.services.yandex_pricing_selection import unify_yandex_warehouse

BatchCalculator = Callable[[dict[str, Any]], dict[str, Any]]


class DraftPricingStore(Protocol):
    def mutation_scope(self, arguments: dict[str, Any]) -> AbstractContextManager: ...
    def load_draft_content(self, draft_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, int]: ...
    def save_draft_content(self, draft_payload: dict[str, Any], *, calculated_pricing: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, int]: ...


def target_key(target: dict[str, Any]) -> str:
    return f"{target.get('platform', '')}:{target.get('site', '')}".lower()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _store_config() -> dict[str, Any]:
    return get_context().config.load_store_config()


def _configuration_basis(config: dict[str, Any]) -> dict[str, Any]:
    # 刷新访问令牌不改变报价语义；账户、币种、物流与授权变化仍参与并发检查。
    ignored = {"access_token", "refresh_token", "expires_at", "token_updated_at", "updated_at", "last_auth_at",
               "checked_at", "currency_verified_at", "auth_checked_at"}
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in ignored}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return clean(config)


def _pricing_environment() -> str:
    """应用沿用预览的汇率快照，但配置或物流费率版本改变必须重算。"""
    context = get_context()
    return _fingerprint([
        context.config.load_app_config().get("pricing_defaults"),
        context.tariffs.versions(),
    ])


def _patch_fields(model: Any) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True, exclude_none=True)


def _saved_fields(source: dict[str, Any], model: Any) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in source.items() if key in model.model_fields and value is not None}


def _default_template(request: DraftPricingRequest, draft: dict[str, Any]) -> dict[str, Any] | None:
    """仅为尚无核价配置的新草稿选择默认模板；显式来源（含清除）优先。"""
    stored = draft.get("pricing") or {}
    if ("template_source" in request.model_fields_set
            or any(stored.get(key) for key in ("template_source", "common", "targets", "updated_at"))
            or any((row.get("pricing") or {}).get("targets") for row in draft.get("sku_items", []))):
        return None
    settings = get_context().config.load_app_config()["pricing_templates"]
    return next((item for item in settings["items"] if item["id"] == settings["default_template_id"]), None)


def _prepare(request: DraftPricingRequest, draft: dict[str, Any], product: dict[str, Any],
             default_template: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows = {row["sku_id"]: row for row in draft.get("sku_items", [])}
    seen: set[str] = set()
    for update in getattr(request, "sku_updates", []):
        if update.sku_id not in rows or update.sku_id in seen:
            raise BusinessCapabilityError("PRICING_SKU_SCOPE_INVALID", "核价编辑包含重复或不属于草稿的 SKU。")
        seen.add(update.sku_id)
        row = rows[update.sku_id]
        row["selected"] = update.selected
        row["pricing_overrides"] = _patch_fields(update.pricing_overrides)
    targets = {target_key(target): target for target in draft.get("target_sites", [])}
    requested = [key.lower() for key in request.target_keys] or list(targets)
    patches = {key.lower(): value for key, value in request.targets.items()}
    selections = {key.lower(): value for key, value in getattr(request, "target_selections", {}).items()}
    if any(key != "mercadolibre:cbt" for key in selections):
        raise BusinessCapabilityError("SALES_TARGET_NOT_APPLICABLE", "销售国家与物流方式选择只适用于 Mercado Libre CBT 草稿。")
    if (not requested or len(set(requested)) != len(requested) or not set(requested) <= targets.keys()
            or len(patches) != len(request.targets) or not (patches.keys() | selections.keys()) <= set(requested)):
        raise BusinessCapabilityError("PRICING_TARGET_SCOPE_INVALID", "核价市场必须属于当前草稿，参数只能修改本次核价的市场。")
    for key, values in selections.items():
        targets[key]["sites_to_sell"] = deepcopy(values)
    if "mercadolibre:cbt" in requested:
        target = targets["mercadolibre:cbt"]
        target["sites_to_sell"] = mercadolibre_sales_condition_basis(target.get("sites_to_sell"))
    selected_targets = [targets[key] for key in requested]
    projections = {key: draft_for_publish_target(draft, targets[key]) for key in requested}
    stored = draft.get("pricing") or {}
    defaults = default_template["parameters"] if default_template else {}
    common = {"domestic_freight_cny": 0, "packaging_cost_cny": 0, "other_cost_cny": 0,
              "exchange_rate_mode": "live"}
    common.update(_saved_fields(defaults.get("common") or {}, PricingCommonPatch))
    common.update(_saved_fields(stored.get("common") or {}, PricingCommonPatch))
    common.update(_patch_fields(request.common))
    templates: dict[str, Any] = {}
    for key in requested:
        target = targets[key]
        saved = (stored.get("targets") or {}).get(key) or {}
        template = {"payment_fee_percent": 0, "other_fee_percent": 0, "pricing_mode": "margin",
                    "target_margin_percent": 30, "markup_percent": 30, "shipping_quote_mode": "auto",
                    "shipping_currency": "USD" if target["platform"] == "mercadolibre" else "CNY", "shipping_amount": 0}
        template.update(_saved_fields((defaults.get("targets") or {}).get(key) or {}, PricingTargetPatch))
        template.update(_saved_fields(saved, PricingTargetPatch))
        if saved.get("pricing_mode") == "manual" and saved.get("applied_price"):
            template.setdefault("manual_price", deepcopy(saved["applied_price"]))
        if key in patches:
            template.update(_patch_fields(patches[key]))
            if "shipping_selection" in patches[key].model_fields_set:
                selection = patches[key].shipping_selection
                template["shipping_selection"] = selection.model_dump() if selection is not None else None
            if "manual_price" in patches[key].model_fields_set:
                manual = patches[key].manual_price
                template["manual_price"] = manual.model_dump() if manual is not None else None
        if target["platform"] == "ozon":
            template.update(shipping_quote_mode="auto", shipping_amount=0)
        templates[key] = template
    try:
        selected = selected_skus(product, draft)
    except ValueError as exc:
        raise BusinessCapabilityError("PRICING_SKU_SCOPE_INVALID", str(exc)) from exc
    if not selected:
        raise BusinessCapabilityError("PRICING_SKU_SELECTION_REQUIRED", "请先勾选需要核价的 SKU。")
    items, errors = [], []
    for fact, row in selected:
        own = row.get("pricing_overrides") or {}
        sku_common = {**common, **(own.get("common") or {})}
        facts = {"cost_cny": fact.get("cost_cny"), "battery": fact.get("battery", False), "liquid": fact.get("liquid", False), **(fact.get("package_dimensions") or {})}
        for field in ("cost_cny", "length_cm", "width_cm", "height_cm", "weight_kg"):
            value = facts.get(field)
            try:
                valid = value not in (None, "") and math.isfinite(float(value)) and float(value) >= 0
                if field != "cost_cny":
                    valid = valid and float(value) > 0
            except (TypeError, ValueError):
                valid = False
            if not valid:
                label = {"cost_cny": "采购成本", "length_cm": "包装长", "width_cm": "包装宽", "height_cm": "包装高", "weight_kg": "包装重量"}[field]
                errors.append({"sku_id": row["sku_id"], "field": field, "message": f"SKU {row['sku_id']} 缺少有效的{label}。"})
        # 明确映射确定性引擎字段，避免采购主档值或国际运费覆盖国内物流。
        sku_common.update(facts)
        sku_common.update(freight_cny=sku_common["domestic_freight_cny"], prep_fee_cny=sku_common["packaging_cost_cny"])
        sku_targets = []
        for key, template in templates.items():
            own_target = deepcopy((own.get("targets") or {}).get(key) or {})
            if "shipping_amount" in own_target:
                own_target["shipping_quote_mode"] = "manual"
            if own_target.get("manual_price"):
                own_target["pricing_mode"] = "manual"
            target = projections[key]
            calculated_target = {**template, **own_target, **{field: deepcopy(target.get(field, "")) for field in (
                "platform", "site", "language", "category_id", "listing_currency", "currency_fingerprint", "sites_to_sell")}, "target_key": key}
            if target["platform"] == "ozon":
                calculated_target["shipping_quote_mode"] = "auto"
                calculated_target.pop("shipping_amount", None)
                calculated_target.pop("shipping_tariff_version", None)
                attributes = {**(target.get("attributes") or {}), **((row.get("attributes_by_target") or {}).get(key) or {})}
                calculated_target["brand_attribute"] = deepcopy(attributes.get("85"))
            sku_targets.append(calculated_target)
        items.append({"sku_id": row["sku_id"], "input": {**sku_common, "common": sku_common, "targets": sku_targets}})
    if errors:
        raise BusinessCapabilityError("PRICING_INPUT_INVALID", "部分 SKU 的采购成本或包装资料缺失。",
                                      details={"draft_id": request.draft_id, "errors": errors})
    source = deepcopy(stored.get("template_source"))
    if default_template:
        source = {"id": default_template["id"], "name": default_template["name"]}
    if "template_source" in request.model_fields_set:
        source = request.template_source.model_dump() if request.template_source is not None else None
    return items, {"common": common, "targets": templates, "template_source": source}, selected_targets


def prepare_pricing_inputs(request: DraftPricingRequest, draft: dict[str, Any], product: dict[str, Any]):
    """按当前草稿与默认模板构造试算输入，不创建可应用预览。"""
    return _prepare(request, draft, product, _default_template(request, draft))


def _validate_results(batch: dict[str, Any], inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = batch.get("items") or []
    expected = {item["sku_id"]: {target_key(t) for t in item["input"]["targets"]} for item in inputs}
    if len(rows) != len(expected) or {item.get("sku_id") for item in rows} != set(expected):
        raise BusinessCapabilityError("PRICING_RESULT_INVALID", "核价返回的 SKU 与本次选择不一致。")
    errors = []
    for row in rows:
        result = row.get("result") or {}
        targets = result.get("results") or []
        issues = list(result.get("errors") or [])
        if result.get("error"):
            issues.append({"code": result.get("error_code", ""), "message": result["error"]})
        if result.get("ok") is False and not issues and not any(t.get("errors") for t in targets):
            issues.append({"message": "核价未成功完成。"})
        if (targets or not issues) and (len(targets) != len(expected[row["sku_id"]]) or {target_key(t) for t in targets} != expected[row["sku_id"]]):
            issues.append({"message": "核价结果未完整覆盖本次选择的市场。"})
        for target in targets:
            issues.extend({**issue, "target_key": target_key(target)} if isinstance(issue, dict) else {"message": str(issue)} for issue in target.get("errors") or [])
            if target.get("errors"):
                continue
            money = target.get("applied_price") or {}
            try:
                amount = float(money.get("amount", 0))
                valid = math.isfinite(amount) and amount > 0
            except (TypeError, ValueError):
                valid = False
            if (not valid or not target.get("calculation_basis") or not target.get("calculation_fingerprint")
                    or not target.get("currency_fingerprint") or not money.get("currency")
                    or money.get("currency") != target.get("listing_currency")):
                issues.append({"target_key": target_key(target), "message": "核价缺少有效售价、币种或计算依据。"})
            if target.get("is_loss"):
                issues.append({"target_key": target_key(target), "message": "该售价会亏损。"})
        errors.extend({"sku_id": row["sku_id"], **(issue if isinstance(issue, dict) else {"message": str(issue)})} for issue in issues)
    return errors


def _build_pricing_rows(
    batch: dict[str, Any],
    inputs: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    *,
    errors: list[dict[str, Any]],
    now: str,
    applied: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    by_input = {item["sku_id"]: item["input"] for item in inputs}
    by_target = {target_key(target): target for target in targets}
    pricing_rows: dict[str, Any] = {}
    for item in batch["items"]:
        result = item["result"]
        quotes = {}
        for quote in result.get("results") or []:
            key = target_key(quote)
            if key not in by_target:
                continue
            quote = deepcopy(quote)
            quote["target_key"] = key
            if not errors and key == "mercadolibre:cbt":
                quote["sites_to_sell"] = _apply_mercadolibre_destination_results(by_target[key], quote)
            quotes[key] = quote
        pricing_rows[item["sku_id"]] = {
            "common": by_input[item["sku_id"]]["common"],
            "targets": quotes,
            "exchange_rates": result.get("exchange_rates", {}),
            "updated_at": now,
            "applied": applied and not errors,
        }
    return pricing_rows, by_target


def _verify_draft_unchanged(
    request: DraftPricingRequest,
    *,
    product_store: DraftPricingStore,
    store_config_loader: Callable[[], dict[str, Any]],
    routing: str | None,
    snapshot: str,
    config: dict[str, Any],
    environment: str,
    default_template: dict[str, Any] | None,
    pricing_rows: dict[str, Any],
    errors: list[dict[str, Any]],
) -> None:
    if routing is not None:
        logistics = get_context().yandex_logistics
        if routing != logistics.configuration_fingerprint():
            raise BusinessCapabilityError("DRAFT_CHANGED", "核价期间仓库物流配置已改变，请重新核价。")
        if not errors:
            current_plans = logistics.plans()
            for row in pricing_rows.values():
                for quote in row["targets"].values():
                    if quote.get("platform") == "yandex":
                        plan = quote.get("calculation_basis", {}).get("shipping_evidence", {}).get("fulfillment_plan", {})
                        if not plan or plan not in current_plans:
                            raise BusinessCapabilityError("PRICING_LOGISTICS_INVALID", "核价使用的仓库物流方案已失效，请重新核价。")
    latest, latest_product = load_draft(product_store, request.draft_id)
    if (
        snapshot != _fingerprint([latest, latest_product])
        or config != _configuration_basis(store_config_loader())
        or environment != _pricing_environment()
        or default_template != _default_template(request, latest)
    ):
        raise BusinessCapabilityError("DRAFT_CHANGED", "核价期间 SKU、草稿或店铺配置已改变，请重新核价。")


def _single_request_from_batch(
    request: DraftPricingBatchPreviewRequest | DraftPricingBatchApplyRequest,
    *,
    draft_id: str,
    preview_id: str = "",
    expected_updated_at: str = "",
) -> DraftPricingRequest:
    kwargs: dict[str, Any] = {
        "draft_id": draft_id,
        "preview_id": preview_id,
        "expected_updated_at": expected_updated_at,
        "common": request.common,
        "targets": request.targets,
        "target_keys": list(request.target_keys),
    }
    if "template_source" in request.model_fields_set:
        kwargs["template_source"] = request.template_source
    return DraftPricingRequest(**kwargs)


def price_draft(request: DraftPricingRequest, *, product_store: DraftPricingStore, apply: bool = False, restore: bool = False,
                calculator: BatchCalculator = calculate_sku_prices,
                store_config_loader: Callable[[], dict[str, Any]] = _store_config) -> dict[str, Any]:
    """预览在锁外计算并暂存；应用仅校验并保存相同输入对应的服务端预览。"""
    started = perf_counter()
    with product_store.mutation_scope({"draft_id": request.draft_id}):
        original, product = load_draft(product_store, request.draft_id)
        if request.expected_updated_at and request.expected_updated_at != original.get("updated_at"):
            raise BusinessCapabilityError("DRAFT_CHANGED", "草稿已更新，请重新读取后核价。")
        snapshot = _fingerprint([original, product])
        draft = deepcopy(original)
        default_template = _default_template(request, draft)
        inputs, template, targets = _prepare(request, draft, product, default_template)
    config = _configuration_basis(store_config_loader())
    routing = get_context().yandex_logistics.configuration_fingerprint() if any(t.get('platform') == 'yandex' for t in targets) else None
    environment = _pricing_environment()
    # 普通保存、文案和预检不改变报价；版本快照仍保护本次操作的并发读写。
    binding = pricing_calculation_fingerprint({"draft_id": request.draft_id, "inputs": inputs,
        "template": template, "config": config, "routing": routing, "environment": environment})
    cache = get_context().pricing_cache.previews
    restored_id = ""
    if restore:
        latest = cache.latest(lambda entry: entry.get("draft_id") == request.draft_id)
        if latest is None or latest[1]["binding"] != binding:
            return {"applied": False, "preview_id": ""}
        restored_id, cached = latest
        batch = cached["batch"]
    elif apply:
        cached = cache.get(request.preview_id) if request.preview_id else None
        if cached is None:
            raise BusinessCapabilityError("PRICING_PREVIEW_REQUIRED", "请先计算核价；预览有效期为十分钟，过期后需重新计算。")
        if cached["binding"] != binding:
            raise BusinessCapabilityError("PRICING_PREVIEW_CHANGED", "SKU、核价参数或配置已改变，请重新计算后应用售价。")
        batch = cached["batch"]
    else:
        batch = calculator({"items": inputs})
    errors = _validate_results(batch, inputs)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    pricing_rows, by_target = _build_pricing_rows(
        batch,
        inputs,
        targets,
        errors=errors,
        now=now,
        applied=apply,
    )
    response = {**batch, "draft_id": request.draft_id, "applied": False, "errors": errors,
                "sku_count": len(inputs), "target_count": len(targets), "sku_pricing": pricing_rows,
                "updated_at": original.get("updated_at", "")}
    with product_store.mutation_scope({"draft_id": request.draft_id}):
        _verify_draft_unchanged(
            request,
            product_store=product_store,
            store_config_loader=store_config_loader,
            routing=routing,
            snapshot=snapshot,
            config=config,
            environment=environment,
            default_template=default_template,
            pricing_rows=pricing_rows,
            errors=errors,
        )
        if not apply or errors:
            if not errors:
                response["preview_id"] = restored_id or uuid4().hex
                if not restore:
                    cache.put(response["preview_id"], {"draft_id": request.draft_id, "binding": binding, "batch": batch})
            return response
        # 保留未参与本次核价的市场；发布时仍逐市场校验输入依据是否过期。
        shared = deepcopy(draft.get("pricing") or {})
        shared.update(common=template["common"], updated_at=now)
        shared["template_source"] = template["template_source"]
        shared.setdefault("targets", {}).update(template["targets"])
        for row in draft.get("sku_items", []):
            if row["sku_id"] in pricing_rows:
                calculated = pricing_rows[row["sku_id"]]
                for key, quote in calculated["targets"].items():
                    manual = ((row.get("pricing_overrides") or {}).get("targets") or {}).get(key, {}).get("manual_price")
                    if manual and not manual.get("currency"):
                        manual["currency"] = quote["listing_currency"]
                old_targets = deepcopy((row.get("pricing") or {}).get("targets") or {})
                old_targets.update(calculated["targets"])
                calculated["targets"] = old_targets
        first_quotes = pricing_rows[inputs[0]["sku_id"]]["targets"]
        for key, target in by_target.items():
            quote = first_quotes[key]
            for field in ("listing_currency", "currency_fingerprint"):
                target[field] = quote[field]
                shared["targets"][key][field] = quote[field]
            if shared["targets"][key].get("pricing_mode") == "manual":
                manual = deepcopy(shared["targets"][key].get("manual_price"))
                if manual and not manual.get("currency"):
                    manual["currency"] = quote["listing_currency"]
                shared["targets"][key]["manual_price"] = manual
                shared["targets"][key]["applied_price"] = deepcopy(manual)
        saved, error, _status = product_store.save_draft_content(draft, calculated_pricing={"pricing": shared, "skus": pricing_rows})
        raise_store_error(error, default_code="DRAFT_PRICING_SAVE_FAILED", default_message="核价保存失败。")
        response.update(applied=True, updated_at=saved["draft"].get("updated_at", ""), draft=saved["draft"])
        cache.discard(request.preview_id)
        response["metrics"] = {**batch.get("metrics", {}), "duration_ms": round((perf_counter() - started) * 1000, 2),
                               "ozon_discovery_ms": 0, "preview_reused": True}
    return response


def price_drafts_batch(
    request: DraftPricingBatchPreviewRequest,
    *,
    product_store: DraftPricingStore,
    session_factory: Callable[[], PricingSession] | None = None,
    store_config_loader: Callable[[], dict[str, Any]] = _store_config,
) -> dict[str, Any]:
    """跨草稿共享同一 PricingSession 合批计算预览；每份草稿独立选仓、独立校验并发与独立缓存 preview_id。"""
    started = perf_counter()
    draft_ids = list(dict.fromkeys(str(draft_id or "").strip() for draft_id in request.draft_ids))
    outcomes: dict[str, dict[str, Any]] = {}
    prepared: list[dict[str, Any]] = []

    for draft_id in draft_ids:
        check_cancellation()
        if not draft_id:
            outcomes[draft_id] = {
                "draft_id": draft_id,
                "ok": False,
                "applied": False,
                "sku_count": 0,
                "target_count": 0,
                "updated_at": "",
                "items": [],
                "error_code": "DRAFT_NOT_FOUND",
                "error": "草稿 ID 不能为空。",
                "errors": [],
            }
            continue
        expected_updated_at = str(request.expected_updated_at_by_draft.get(draft_id) or "")
        single_req = _single_request_from_batch(
            request,
            draft_id=draft_id,
            expected_updated_at=expected_updated_at,
        )
        loaded_updated_at = ""
        try:
            with product_store.mutation_scope({"draft_id": draft_id}):
                original, product = load_draft(product_store, draft_id)
                loaded_updated_at = str(original.get("updated_at") or "")
                if single_req.expected_updated_at and single_req.expected_updated_at != loaded_updated_at:
                    raise BusinessCapabilityError("DRAFT_CHANGED", "草稿已更新，请重新读取后核价。")
                snapshot = _fingerprint([original, product])
                draft = deepcopy(original)
                default_template = _default_template(single_req, draft)
                inputs, template, targets = _prepare(single_req, draft, product, default_template)
        except BusinessCapabilityError as exc:
            details_errors = (exc.details or {}).get("errors")
            outcomes[draft_id] = {
                "draft_id": draft_id,
                "ok": False,
                "applied": False,
                "sku_count": 0,
                "target_count": 0,
                "updated_at": loaded_updated_at,
                "items": [],
                "error_code": exc.code,
                "error": str(exc),
                "errors": list(details_errors) if isinstance(details_errors, list) else [],
            }
            continue
        prepared.append({
            "draft_id": draft_id,
            "request": single_req,
            "updated_at": loaded_updated_at,
            "snapshot": snapshot,
            "default_template": default_template,
            "inputs": inputs,
            "template": template,
            "targets": targets,
        })

    if prepared:
        check_cancellation()
        config = _configuration_basis(store_config_loader())
        any_yandex = any(
            target.get("platform") == "yandex"
            for entry in prepared
            for target in entry["targets"]
        )
        shared_routing = get_context().yandex_logistics.configuration_fingerprint() if any_yandex else None
        environment = _pricing_environment()
        cache = get_context().pricing_cache.previews

        flat_inputs: list[dict[str, Any]] = []
        slices: list[tuple[int, int]] = []
        for entry in prepared:
            start = len(flat_inputs)
            flat_inputs.extend(entry["inputs"])
            slices.append((start, len(flat_inputs)))

        session = (session_factory or PricingSession)()
        all_calculated = session.calculate_many([item["input"] for item in flat_inputs])
        check_cancellation()

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        batch_id = uuid4().hex[:12]
        for entry, (start, end) in zip(prepared, slices, strict=True):
            check_cancellation()
            draft_id = entry["draft_id"]
            inputs = entry["inputs"]
            targets = entry["targets"]
            template = entry["template"]
            draft_calculated = unify_yandex_warehouse(session, inputs, all_calculated[start:end])
            batch_items = [
                {"sku_id": item["sku_id"], "result": result}
                for item, result in zip(inputs, draft_calculated, strict=True)
            ]
            failed_skus = sum(1 for item in batch_items if not item["result"].get("ok"))
            batch = {
                "ok": True,
                "items": batch_items,
                "metrics": {
                    "batch_id": batch_id,
                    "sku_count": len(inputs),
                    "target_count": sum(len(item["input"]["targets"]) for item in inputs),
                    "failed_sku_count": failed_skus,
                    "duration_ms": round((perf_counter() - started) * 1000, 1),
                    "ozon_discovery_ms": round(getattr(session.shipping.module, "ozon_discovery_ms", 0.0), 1),
                },
            }
            draft_routing = shared_routing if any(t.get("platform") == "yandex" for t in targets) else None
            binding = pricing_calculation_fingerprint({
                "draft_id": draft_id,
                "inputs": inputs,
                "template": template,
                "config": config,
                "routing": draft_routing,
                "environment": environment,
            })
            errors = _validate_results(batch, inputs)
            pricing_rows, _by_target = _build_pricing_rows(
                batch,
                inputs,
                targets,
                errors=errors,
                now=now,
                applied=False,
            )
            try:
                with product_store.mutation_scope({"draft_id": draft_id}):
                    _verify_draft_unchanged(
                        entry["request"],
                        product_store=product_store,
                        store_config_loader=store_config_loader,
                        routing=draft_routing,
                        snapshot=entry["snapshot"],
                        config=config,
                        environment=environment,
                        default_template=entry["default_template"],
                        pricing_rows=pricing_rows,
                        errors=errors,
                    )
                    preview_id = ""
                    if not errors:
                        preview_id = uuid4().hex
                        cache.put(preview_id, {"draft_id": draft_id, "binding": binding, "batch": batch})
            except BusinessCapabilityError as exc:
                details_errors = (exc.details or {}).get("errors")
                outcomes[draft_id] = {
                    "draft_id": draft_id,
                    "ok": False,
                    "applied": False,
                    "sku_count": len(inputs),
                    "target_count": len(targets),
                    "updated_at": entry["updated_at"],
                    "items": batch_items,
                    "error_code": exc.code,
                    "error": str(exc),
                    "errors": list(details_errors) if isinstance(details_errors, list) else errors,
                }
                continue
            outcomes[draft_id] = {
                "draft_id": draft_id,
                "ok": not errors,
                "preview_id": preview_id,
                "applied": False,
                "sku_count": len(inputs),
                "target_count": len(targets),
                "updated_at": entry["updated_at"],
                "items": batch_items,
                "error_code": "PRICING_RESULT_INVALID" if errors else "",
                "error": str(errors[0].get("message") or "核价未成功完成。") if errors else "",
                "errors": errors,
            }

    ordered_items = [outcomes[draft_id] for draft_id in draft_ids]
    succeeded_count = sum(1 for item in ordered_items if item["ok"])
    return {
        "total": len(ordered_items),
        "succeeded_count": succeeded_count,
        "failed_count": len(ordered_items) - succeeded_count,
        "applied": False,
        "items": ordered_items,
    }


def apply_draft_pricing_batch(
    request: DraftPricingBatchApplyRequest,
    *,
    product_store: DraftPricingStore,
    store_config_loader: Callable[[], dict[str, Any]] = _store_config,
) -> dict[str, Any]:
    """逐草稿原子应用服务端预览；单项冲突或失效仅记录该项失败，不影响其他有效草稿保存。"""
    seen: set[str] = set()
    for item in request.items:
        if item.draft_id in seen:
            raise BusinessCapabilityError(
                "PRICING_BATCH_DUPLICATE_DRAFT",
                f"批量应用核价包含重复的草稿 ID：{item.draft_id}，请去重后提交。",
            )
        seen.add(item.draft_id)

    results: list[dict[str, Any]] = []
    for item in request.items:
        check_cancellation()
        single_req = _single_request_from_batch(
            request,
            draft_id=item.draft_id,
            preview_id=item.preview_id,
            expected_updated_at=item.expected_updated_at,
        )
        try:
            applied = price_draft(
                single_req,
                product_store=product_store,
                apply=True,
                store_config_loader=store_config_loader,
            )
            errors = list(applied.get("errors") or [])
            ok = bool(applied.get("applied")) and not errors
            results.append({
                "draft_id": item.draft_id,
                "ok": ok,
                "preview_id": item.preview_id if not ok else "",
                "applied": bool(applied.get("applied")),
                "sku_count": int(applied.get("sku_count") or 0),
                "target_count": int(applied.get("target_count") or 0),
                "updated_at": str(applied.get("updated_at") or ""),
                "items": list(applied.get("items") or []),
                "error_code": "" if ok else "PRICING_RESULT_INVALID",
                "error": "" if ok else str((errors[0] if errors else {}).get("message") or "核价结果未应用。"),
                "errors": errors,
            })
        except BusinessCapabilityError as exc:
            details_errors = (exc.details or {}).get("errors")
            results.append({
                "draft_id": item.draft_id,
                "ok": False,
                "preview_id": item.preview_id,
                "applied": False,
                "sku_count": 0,
                "target_count": 0,
                "updated_at": "",
                "items": [],
                "error_code": exc.code,
                "error": str(exc),
                "errors": list(details_errors) if isinstance(details_errors, list) else [],
            })

    succeeded_count = sum(1 for item in results if item["ok"])
    failed_count = len(results) - succeeded_count
    return {
        "total": len(results),
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "applied": succeeded_count > 0 and failed_count == 0,
        "items": results,
    }


__all__ = [
    "BatchCalculator",
    "DraftPricingStore",
    "apply_draft_pricing_batch",
    "prepare_pricing_inputs",
    "price_draft",
    "price_drafts_batch",
]

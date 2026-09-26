"""页面、AI 和市场准备共用的逐 SKU 核价业务入口。"""
from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Protocol

from erp_web.context import get_context
from erp_web.product_model import mercadolibre_sales_condition_basis
from erp_web.product_model.sku_model import selected_skus
from erp_web.runtime_units.draft_publish_context import draft_for_publish_target
from erp_web.runtime_units.market_capability_support import load_draft, raise_store_error
from erp_web.runtime_units.pricing_batch import calculate_sku_prices
from erp_web.runtime_units.pricing_results import _apply_mercadolibre_destination_results
from erp_web.schemas.draft_pricing import DraftPricingRequest, PricingCommonPatch, PricingTargetPatch
from erp_web.services.capability_errors import BusinessCapabilityError

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


def _patch_fields(model: Any) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True, exclude_none=True)


def _saved_fields(source: dict[str, Any], model: Any) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in source.items() if key in model.model_fields and value is not None}


def _prepare(request: DraftPricingRequest, draft: dict[str, Any], product: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows = {row["sku_id"]: row for row in draft.get("sku_items", [])}
    seen: set[str] = set()
    for update in getattr(request, "sku_updates", []):
        if update.sku_id not in rows or update.sku_id in seen:
            raise BusinessCapabilityError("PRICING_SKU_SCOPE_INVALID", "核价编辑包含重复或不属于草稿的 SKU。")
        seen.add(update.sku_id)
        row = rows[update.sku_id]
        row["selected"] = update.selected
        # 页面发的是这些可编辑字段的完整当前值；移除覆盖后应重新读取商品 SKU。
        overrides = row.setdefault("overrides", {})
        for field in ("cost_cny", "package_dimensions"):
            overrides.pop(field, None)
        overrides.update(_patch_fields(update.overrides))
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
    common = {"domestic_freight_cny": 0, "packaging_cost_cny": 0, "other_cost_cny": 0,
              "battery": False, "liquid": False, "exchange_rate_mode": "live"}
    common.update(_saved_fields(stored.get("common") or {}, PricingCommonPatch))
    common.update(_patch_fields(request.common))
    templates: dict[str, Any] = {}
    for key in requested:
        target = targets[key]
        saved = (stored.get("targets") or {}).get(key) or {}
        template = {"commission_percent": 20 if target["platform"] in {"yandex", "ozon"} else 16,
                    "payment_fee_percent": 0, "other_fee_percent": 0, "pricing_mode": "margin",
                    "target_margin_percent": 30, "markup_percent": 30, "shipping_quote_mode": "auto",
                    "shipping_currency": "USD" if target["platform"] == "mercadolibre" else "CNY", "shipping_amount": 0}
        template.update(_saved_fields(saved, PricingTargetPatch))
        if saved.get("pricing_mode") == "manual" and saved.get("applied_price"):
            template.setdefault("manual_price", deepcopy(saved["applied_price"]))
        if key in patches:
            template.update(_patch_fields(patches[key]))
            if "manual_price" in patches[key].model_fields_set:
                manual = patches[key].manual_price
                template["manual_price"] = manual.model_dump() if manual is not None else None
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
        facts = {"cost_cny": fact.get("cost_cny"), **(fact.get("package_dimensions") or {})}
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
            sku_targets.append({**template, **own_target, **{field: deepcopy(target.get(field, "")) for field in (
                "platform", "site", "language", "category_id", "listing_currency", "currency_fingerprint", "sites_to_sell")}, "target_key": key})
        items.append({"sku_id": row["sku_id"], "input": {**sku_common, "common": sku_common, "targets": sku_targets}})
    if errors:
        raise BusinessCapabilityError("PRICING_INPUT_INVALID", "部分 SKU 的采购成本或包装资料缺失，请只补充列出的字段。", details={"errors": errors})
    return items, {"common": common, "targets": templates}, selected_targets


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


def price_draft(request: DraftPricingRequest, *, product_store: DraftPricingStore, apply: bool = False,
                calculator: BatchCalculator = calculate_sku_prices,
                store_config_loader: Callable[[], dict[str, Any]] = _store_config) -> dict[str, Any]:
    """读取快照→锁外计算→锁内比较并保存；预览不会保存草稿或已应用价格。"""
    with product_store.mutation_scope({"draft_id": request.draft_id}):
        original, product = load_draft(product_store, request.draft_id)
        if request.expected_updated_at and request.expected_updated_at != original.get("updated_at"):
            raise BusinessCapabilityError("DRAFT_CHANGED", "草稿已更新，请重新读取后核价。")
        snapshot = _fingerprint([original, product])
        draft = deepcopy(original)
        inputs, template, targets = _prepare(request, draft, product)
    config = _configuration_basis(store_config_loader())
    batch = calculator({"items": inputs})
    errors = _validate_results(batch, inputs)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    by_input = {item["sku_id"]: item["input"] for item in inputs}
    by_target = {target_key(target): target for target in targets}
    pricing_rows = {}
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
        pricing_rows[item["sku_id"]] = {"common": by_input[item["sku_id"]]["common"], "targets": quotes,
            "exchange_rates": result.get("exchange_rates", {}), "updated_at": now, "applied": apply and not errors}
    response = {**batch, "draft_id": request.draft_id, "applied": False, "errors": errors,
                "sku_count": len(inputs), "target_count": len(targets), "sku_pricing": pricing_rows,
                "updated_at": original.get("updated_at", "")}
    with product_store.mutation_scope({"draft_id": request.draft_id}):
        latest, latest_product = load_draft(product_store, request.draft_id)
        if snapshot != _fingerprint([latest, latest_product]) or config != _configuration_basis(store_config_loader()):
            raise BusinessCapabilityError("DRAFT_CHANGED", "核价期间 SKU、草稿或店铺配置已改变，请重新核价。")
        if not apply or errors:
            return response
        # 保留未参与本次核价的市场；发布时仍逐市场校验输入依据是否过期。
        shared = deepcopy(draft.get("pricing") or {})
        shared.update(common=template["common"], updated_at=now)
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
    return response


__all__ = ["BatchCalculator", "DraftPricingStore", "price_draft"]

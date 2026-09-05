"""SKU 组发布入口：编译冻结清单，逐 SKU 留存结果，重试复用远端身份。"""

from copy import deepcopy
from typing import Any

from erp_web.context import get_context
from erp_web.marketplaces.publisher import PlatformPublisher, PublishAdapterError
from erp_web.product_model.sku_model import record, selected_skus, sku_fingerprint, text
from .collect_helpers import collect_time_iso
from .publish_context import PreparedPublishContext
from .sku_publish_projection import grouping_contract, sku_context, sku_quote_errors, target_key, validate_grouping

PENDING = {"pending_confirmation", "publish_pending_confirmation"}
SUCCESS = {"published", "imported", "real_publish_success", "success"}


def remote_status(result: dict[str, Any]) -> str:
    status = text(result.get("status")).lower()
    if status in PENDING:
        return "pending_confirmation"
    if status in SUCCESS and result.get("ok") is not False:
        return "published"
    if status == "outcome_unknown" or result.get("outcome_unknown"):
        return "outcome_unknown"
    return "failed"


def remote_identity(result: dict[str, Any]) -> dict[str, Any]:
    body = record(result.get("result")) or result
    publication = record(body.get("publication"))
    return {key: body.get(key) or publication.get(key) or "" for key in (
        "external_id", "item_id", "offer_id", "task_id", "siteless_user_product_id", "siteless_family_id", "group_id",
    )}


def remote_task_ids(result: dict[str, Any]) -> list[str]:
    body = record(result.get("result"))
    values = [result.get("task_id"), body.get("task_id")]
    for node in (result, body):
        values.extend(node.get("task_ids") if isinstance(node.get("task_ids"), list) else [])
    return list(dict.fromkeys(text(value) for value in values if text(value)))


class SkuGroupPublishingAdapter:
    """平台叶子适配器仅处理一个实际销售规格，本入口拥有组内编排。"""

    def __init__(self, item_adapter: PlatformPublisher) -> None:
        self.item_adapter = item_adapter
        self.platform = item_adapter.platform
        self.prepare_is_local_only = getattr(item_adapter, "prepare_is_local_only", False)

    def prepare_product(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        prepared = deepcopy(product)
        draft = record(record(prepared.get("drafts")).get(self.platform))
        images = deepcopy(draft.get("images", []))
        pool = record(prepared.get("source")).get("image_pool", [])
        refs = {item.get("asset_id") for item in images}
        for fact, _ in selected_skus(prepared, draft):
            image = text(fact.get("image"))
            if not image:
                continue
            asset = next((item for item in pool if image in {text(item.get(field)) for field in ("id", "url", "path", "preview_url")}), None)
            if asset and asset["id"] not in refs:
                draft.setdefault("images", []).append({"asset_id": asset["id"], "role": "gallery", "order": len(refs)})
                refs.add(asset["id"])
        prepared = self.item_adapter.prepare_product(prepared, config)
        prepared["drafts"][self.platform]["images"] = images
        return prepared

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return self.item_adapter.resolve_category(product, config)

    def required_attributes_missing(self, context: PreparedPublishContext, config: dict[str, Any]) -> list[str]:
        grouping = grouping_contract(context)
        return list(dict.fromkeys(f"{row['sku']}：{field}" for fact, row in selected_skus(context.product, context.draft)
            for field in self.item_adapter.required_attributes_missing(sku_context(context, fact, row, grouping), config)))

    def validate_draft(self, context: PreparedPublishContext, config: dict[str, Any]) -> dict[str, Any]:
        errors, warnings, rows, projections = [], [], [], []
        grouping = grouping_contract(context)
        try:
            selected = selected_skus(context.product, context.draft)
            if not selected:
                raise ValueError("请先在 SKU 页选择需要发布的规格")
            codes = [row["sku"] for _, row in selected]
            if len(codes) != len(set(codes)):
                raise ValueError("平台卖家编码重复，请为各 SKU 设置不同的编码")
            for fact, row in selected:
                own_errors = sku_quote_errors(fact, row, context.draft, target_key(context))
                if not text(row.get("stock")).isdigit():
                    own_errors.append("请填写此 SKU 的可售库存")
                projected = sku_context(context, fact, row, grouping)
                projections.append(projected)
                check = self.item_adapter.validate_draft(projected, config)
                for issue in check.get("errors", []):
                    errors.append({**issue, "field": f"sku_items.{row['sku_id']}.{issue.get('field', '')}", "message": f"{fact.get('name') or row['sku']}：{issue.get('message', '')}"})
                errors.extend(self._issue(message, row["sku_id"]) for message in own_errors)
                warnings.extend(check.get("warnings", []))
                rows.append({"sku_id": row["sku_id"], "sku": row["sku"], "precheck": check})
            errors.extend(self._issue(message) for message in validate_grouping(context, grouping, projections))
            if len(selected) > 1 and grouping["mode"] == "combined" and self.platform == "mercadolibre":
                # User Products 每个变体可独立定价；传统模型要求同价，不能悄悄拆成多个商品。
                if record(config.get("mercadolibre")).get("listing_model") != "user_products":
                    errors.append(self._issue("当前 Mercado 店铺未启用 User Products，组合内独立定价不可用；请选择独立刊登"))
        except ValueError as exc:
            errors.append(self._issue(str(exc)))
        return {"ok": not errors, "platform": self.platform, "errors": errors, "warnings": warnings, "sku_results": rows, "grouping": grouping}

    @staticmethod
    def _issue(message: str, sku_id: str = "") -> dict[str, Any]:
        return {"code": "SKU_PUBLISH_INVALID", "field": f"sku_items.{sku_id}" if sku_id else "sku_items", "message": message, "severity": "error", "next_action": "检查 SKU 选品、平台属性与核价"}

    def build_payload(self, context: PreparedPublishContext, config: dict[str, Any]) -> dict[str, Any]:
        grouping = grouping_contract(context)
        selected = selected_skus(context.product, context.draft)
        if not selected:
            raise ValueError("没有选择发布 SKU")
        items = []
        projections = []
        for fact, row in selected:
            projected = sku_context(context, fact, row, grouping)
            projections.append(projected)
            payload = self.item_adapter.build_payload(projected, config)
            if self.platform == "mercadolibre" and "family_name" in payload:
                payload["family_name"] = grouping["name"] if grouping["mode"] == "combined" else f"{grouping['name']} {row['sku']}"
            content = deepcopy(payload)
            content.pop("_publication", None)
            items.append({"sku_id": row["sku_id"], "sku": row["sku"], "payload": payload, "fingerprint": sku_fingerprint(content)})
        errors = validate_grouping(context, grouping, projections)
        if errors:
            raise ValueError("；".join(errors))
        return {"kind": "sku_group", "platform": self.platform, "product_id": context.product.get("product_id"), "draft_id": context.draft.get("draft_id"), "target_key": target_key(context), "grouping": grouping, "items": items}

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        payload = record(payload)
        if payload.get("kind") != "sku_group" or not payload.get("items") or not payload.get("draft_id"):
            return ["发布清单缺少草稿身份或 SKU"]
        errors = []
        ids, codes = set(), set()
        for row in payload["items"]:
            if not row.get("sku_id") or row["sku_id"] in ids or not row.get("sku") or row["sku"] in codes:
                errors.append("发布 SKU 身份或编码为空、重复")
            ids.add(row.get("sku_id")); codes.add(row.get("sku"))
            errors.extend(f"{row.get('sku')}：{error}" for error in self.item_adapter.validate_payload(row.get("payload"), config))
        return errors

    def _save(self, envelope: dict[str, Any], item: dict[str, Any], result: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        state = {"sku_id": item["sku_id"], "sku": item["sku"], "fingerprint": item["fingerprint"], "status": status or remote_status(result),
                 **remote_identity(result), "task_ids": remote_task_ids(result), "result": deepcopy(result), "error": text(result.get("error")), "updated_at": collect_time_iso()}
        get_context().products.save_sku_publication(envelope["draft_id"], item["sku_id"], envelope["target_key"], state)
        return state

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        states = []
        for item in payload["items"]:
            existing = get_context().products.sku_publication(payload["draft_id"], item["sku_id"], payload["target_key"])
            status = existing.get("status")
            if status in {"dispatching", "outcome_unknown", "pending_confirmation"}:
                states.append({**existing, "status": "outcome_unknown" if status == "dispatching" else status})
                continue
            if status == "published" and existing.get("fingerprint") == item["fingerprint"]:
                states.append(existing)
                continue
            outgoing = deepcopy(item["payload"])
            publication = record(record(existing.get("result")).get("publication"))
            if self.platform == "mercadolibre" and publication:
                outgoing["_publication"] = deepcopy(publication)
            # 写前落盘：进程在请求期间退出，也不能把未知结果当作未发布再次创建。
            self._save(payload, item, record(existing.get("result")), "dispatching")
            try:
                result = self.item_adapter.publish_payload(outgoing, config)
            except Exception as exc:
                mapped = self.item_adapter.map_publish_error(exc)
                known_failure = isinstance(exc, PublishAdapterError) and not exc.details.get("outcome_unknown") and exc.details.get("remote_write_dispatched") is not True
                known_failure = known_failure or str(exc).startswith("Ozon 商品导入失败：")
                result = {"ok": False, "status": "failed" if known_failure else "outcome_unknown", "error": mapped.get("summary") or str(exc), "error_map": mapped}
                if publication:
                    result["publication"] = publication
            states.append(self._save(payload, item, result))
        return self._aggregate(payload, states)

    def poll_publish_status(self, result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        states = []
        envelope = result["envelope"]
        for state in result["sku_results"]:
            # 只读对账可继续确认已有任务；未知且没有任务身份的结果仍禁止重发。
            can_reconcile = state["status"] == "outcome_unknown" and remote_task_ids(state["result"])
            if state["status"] != "pending_confirmation" and not can_reconcile:
                states.append(state)
                continue
            try:
                polled = self.item_adapter.poll_publish_status(state["result"], config)
            except Exception as exc:
                polled = {**deepcopy(state["result"]), "ok": False, "status": "outcome_unknown", "error": str(exc)}
            states.append(self._save(envelope, state, polled))
        return self._aggregate(envelope, states)

    def _aggregate(self, envelope: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any]:
        statuses = {state["status"] for state in states}
        status = "published" if statuses == {"published"} else "pending_confirmation" if "pending_confirmation" in statuses else "outcome_unknown" if "outcome_unknown" in statuses else "partial" if "published" in statuses else "failed"
        grouping = deepcopy(envelope["grouping"])
        group_ids = [text(state.get("siteless_family_id") or state.get("group_id")) for state in states]
        grouping["status"] = "not_requested" if grouping["mode"] != "combined" or len(states) == 1 else "confirmed" if all(group_ids) and len(set(group_ids)) == 1 else "mismatch" if len(set(filter(None, group_ids))) > 1 else "awaiting_remote_confirmation"
        grouping["remote_group_ids"] = sorted(set(filter(None, group_ids)))
        return {"ok": status in {"published", "pending_confirmation"}, "status": status, "sku_results": states, "grouping": grouping,
                "error": "；".join(f"{row['sku']}：{row.get('error') or row['status']}" for row in states if row["status"] not in {"published", "pending_confirmation"}),
                "task_ids": list(dict.fromkeys(task for row in states for task in remote_task_ids(row["result"]))),
                "envelope": {key: deepcopy(envelope[key]) for key in ("draft_id", "target_key", "grouping")}}

    def map_publish_error(self, error: Exception) -> dict[str, Any]:
        return self.item_adapter.map_publish_error(error)

    def publish_poll_interval_seconds(self, config: dict[str, Any]) -> float:
        return self.item_adapter.publish_poll_interval_seconds(config)

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        from .runtime_api import publish_product
        return publish_product(product, platform, config)


__all__ = ["SkuGroupPublishingAdapter"]

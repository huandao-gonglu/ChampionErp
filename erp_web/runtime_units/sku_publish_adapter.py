"""SKU 组发布入口：编译冻结清单，逐 SKU 留存结果，重试复用远端身份。"""

from copy import deepcopy
from dataclasses import replace
import time
from typing import Any

from erp_web.product_model.task_queue_model import sku_can_check, sku_can_retry, sku_submission_status
from erp_web.schemas.publish import PublishPreparationObserver
from erp_web.context import get_context
from erp_web.stores.product_store import normalize_product_fields
from erp_web.marketplaces.publisher import PlatformPublisher, PublishAdapterError
from erp_web.product_model.sku_model import record, selected_skus, sku_fingerprint, text
from erp_web.product_model.sku_image_model import sku_image_asset
from erp_web.product_model.precheck_model import PrecheckIssues
from .collect_helpers import collect_time_iso
from .publish_context import PreparedPublishContext
from .publish_validation_session import PublishValidationSession
from .sku_precheck import summarize_sku_precheck
from .sku_publish_projection import SkuGroupingMember, grouping_contract, sku_context, sku_quote_errors, target_key, validate_grouping

PENDING = {"pending_confirmation", "publish_pending_confirmation"}
SUCCESS = {"published", "imported", "real_publish_success", "success"}


def remote_status(result: dict[str, Any]) -> str:
    status = text(result.get("status")).lower()
    if status in {"pending_submission", "submission_paused"}:
        return status
    if status in PENDING:
        return "pending_confirmation"
    if status == "needs_attention":
        return status
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

    def prepare_product(self, product: dict[str, Any], config: dict[str, Any], *, on_progress: PublishPreparationObserver | None = None) -> dict[str, Any]:
        if self.platform in {"yandex", "ozon"}:
            return self.item_adapter.prepare_product(product, config, on_progress=on_progress)
        prepared = deepcopy(product)
        draft = record(record(prepared.get("drafts")).get(self.platform))
        images = deepcopy(draft.get("images", []))
        refs = {item.get("asset_id") for item in images}
        for fact, _ in selected_skus(prepared, draft):
            asset = sku_image_asset(prepared, fact)
            if asset and asset["id"] not in refs:
                draft.setdefault("images", []).append({"asset_id": asset["id"], "role": "gallery", "order": len(refs)})
                refs.add(asset["id"])
        prepared = self.item_adapter.prepare_product(prepared, config, on_progress=on_progress)
        prepared["drafts"][self.platform]["images"] = images
        return prepared

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return self.item_adapter.resolve_category(product, config)

    def required_attributes_missing(self, context: PreparedPublishContext, config: dict[str, Any]) -> list[str]:
        grouping = grouping_contract(context)
        return list(dict.fromkeys(f"{row['sku']}：{field}" for fact, row in selected_skus(context.product, context.draft)
            for field in self.item_adapter.required_attributes_missing(sku_context(context, fact, row, grouping), config)))

    def validate_draft(self, context: PreparedPublishContext, config: dict[str, Any], *, image_stage: str = "final") -> dict[str, Any]:
        # 每次校验新建作用域；最终校验不能沿用基础预检的文件或配置结果。
        context = replace(context, validation_session=PublishValidationSession(sku_inputs_checked=True))
        if self.platform == "yandex":
            context = context.with_product(normalize_product_fields(context.product))
        errors, warnings = PrecheckIssues(), PrecheckIssues()
        members: list[SkuGroupingMember] = []
        grouping_issues = []
        selected_sku_ids: set[str] = set()
        grouping = grouping_contract(context)
        try:
            selected = selected_skus(context.product, context.draft)
            if not selected:
                raise ValueError("请先在 SKU 页选择需要发布的规格")
            selected_sku_ids = {row["sku_id"] for _, row in selected}
            codes = [row["sku"] for _, row in selected]
            if len(codes) != len(set(codes)):
                raise ValueError("平台卖家编码重复，请为各 SKU 设置不同的编码")
            for fact, row in selected:
                own_errors = [self._issue(message, "pricing") for message in sku_quote_errors(fact, row, context.draft, target_key(context))]
                if not text(row.get("stock")).isdigit():
                    own_errors.append(self._issue("请填写此 SKU 的可售库存", "stock"))
                try:
                    projected = sku_context(context, fact, row, grouping)
                except ValueError as exc:
                    own_errors.append({**self._issue(str(exc), "image_asset_id"), "next_action": "在 SKU 页从图片池重新选择图片"})
                    errors.extend(own_errors, sku_id=row["sku_id"])
                    continue
                members.append(SkuGroupingMember.from_projection(projected))
                check = (self.item_adapter.validate_draft(projected, config) if image_stage == "final"
                         else self.item_adapter.validate_draft(projected, config, image_stage=image_stage))
                errors.extend(check.get("errors", []), sku_id=row["sku_id"])
                errors.extend(own_errors, sku_id=row["sku_id"])
                warnings.extend(check.get("warnings", []), sku_id=row["sku_id"], severity="warning")
            grouping_issues = validate_grouping(context, grouping, members)
            if len(selected) > 1 and grouping["mode"] == "combined" and self.platform == "mercadolibre":
                # User Products 每个变体可独立定价；传统模型要求同价，不能悄悄拆成多个商品。
                if record(config.get("mercadolibre")).get("listing_model") != "user_products":
                    errors.add(self._issue("当前 Mercado 店铺未启用 User Products，组合内独立定价不可用；请选择独立刊登"))
        except ValueError as exc:
            errors.add(self._issue(str(exc)))
        errors, warnings = summarize_sku_precheck(
            errors.values(), warnings.values(), grouping_issues,
            definition=context.category_definition, selected_sku_ids=selected_sku_ids,
        )
        return {"ok": not errors, "platform": self.platform, "errors": errors, "warnings": warnings, "grouping": grouping}

    @staticmethod
    def _issue(message: str, field: str = "sku_items") -> dict[str, Any]:
        return {"code": "SKU_PUBLISH_INVALID", "field": field, "message": message, "severity": "error", "next_action": "检查 SKU 选品、平台属性与核价"}

    def build_payload(self, context: PreparedPublishContext, config: dict[str, Any]) -> dict[str, Any]:
        grouping = grouping_contract(context)
        selected = selected_skus(context.product, context.draft)
        if not selected:
            raise ValueError("没有选择发布 SKU")
        items = []
        members: list[SkuGroupingMember] = []
        for fact, row in selected:
            projected = sku_context(context, fact, row, grouping)
            members.append(SkuGroupingMember.from_projection(projected))
            payload = self.item_adapter.build_payload(projected, config)
            if self.platform == "mercadolibre" and "family_name" in payload:
                payload["family_name"] = grouping["name"] if grouping["mode"] == "combined" else f"{grouping['name']} {row['sku']}"
            content = deepcopy(payload)
            content.pop("_publication", None)
            items.append({"sku_id": row["sku_id"], "sku": row["sku"], "payload": payload, "fingerprint": sku_fingerprint(content)})
        errors = validate_grouping(context, grouping, members)
        if errors:
            raise ValueError("；".join(issue.message for issue in errors))
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
        if self.platform == 'yandex':
            warehouses = {tuple(str(value) for value in record(row.get('payload')).get('stock', {}).get('warehouse_ids', []))
                          for row in payload['items']}
            if len(warehouses) > 1:
                errors.append('同一商品的所选 SKU 必须使用同一个发布仓库，请统一重新核价')
        return errors

    def _save(self, envelope: dict[str, Any], item: dict[str, Any], result: dict[str, Any], status: str | None = None, *, confirming=False) -> dict[str, Any]:
        state = {"sku_id": item["sku_id"], "sku": item["sku"], "fingerprint": item["fingerprint"], "status": status or remote_status(result),
                 **remote_identity(result), "task_ids": remote_task_ids(result), "result": deepcopy(result), "error": text(result.get("error")), "updated_at": collect_time_iso()}
        state["status"] = sku_submission_status(state)
        store = get_context().products
        store.save_sku_publication(envelope["draft_id"], item["sku_id"], envelope["target_key"], state,
                                   expected=item if confirming else None)
        return state

    def saved_publish_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """从逐项持久化记录重建进度，不执行平台请求。"""
        states = []
        for item in payload["items"]:
            saved = get_context().products.sku_publication(payload["draft_id"], item["sku_id"], payload["target_key"])
            if saved:
                state = deepcopy(saved)
                if state["status"] == "dispatching":
                    previous = {**deepcopy(state["result"]), "ok": False, "status": "outcome_unknown",
                                "error": "提交过程中服务中断，请先核对平台结果。"}
                    state = self._save(payload, item, previous, "outcome_unknown")
            else:
                state = {**{key: item[key] for key in ("sku_id", "sku", "fingerprint")}, "status": "queued", "result": {}}
            states.append(state)
        return self._aggregate(payload, states)

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        if callable(getattr(self.item_adapter, 'publish_payloads', None)):
            return self._publish_native_batch(payload, config)
        states = []
        for item in payload["items"]:
            existing = get_context().products.sku_publication(payload["draft_id"], item["sku_id"], payload["target_key"])
            status = existing.get("status")
            if status in {"dispatching", "outcome_unknown", "pending_confirmation", "pending_submission", "needs_attention"}:
                states.append({**existing, "status": "outcome_unknown" if status == "dispatching" else status})
                continue
            if status == "published" and existing.get("fingerprint") == item["fingerprint"]:
                states.append(existing)
                continue
            if status == "failed":
                restore = getattr(self.item_adapter, "restore_publish_result", None)
                previous = record(existing.get("result"))
                if callable(restore) and previous.get("plan") and previous.get("checkpoint"):
                    states.append(self._save(payload, item, restore({**previous, "status": "pending_submission"})))
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

    def retry_submission(self, payload, result, config):
        """原任务只续提明确可执行的成员；草稿进度变化或丢失时禁止回放。"""
        rows = {row["sku_id"]: row for row in result["sku_results"]}
        if len(rows) != len(result["sku_results"]) or set(rows) != {item["sku_id"] for item in payload["items"]}:
            raise ValueError("原任务的 SKU 回执与批准清单不一致，需先核对。")
        retry_items = []
        for item in payload["items"]:
            row = rows[item["sku_id"]]
            if not sku_can_retry(row):
                continue
            saved = get_context().products.sku_publication(payload["draft_id"], item["sku_id"], payload["target_key"])
            if saved and (saved.get("fingerprint") != item["fingerprint"] or saved.get("sku") != item["sku"]):
                raise ValueError("SKU 已有其他发布内容，请核对最新任务。")
            if row["status"] != "queued" and (
                not saved or sku_submission_status(saved) != sku_submission_status(row)
                or saved.get("result") != row.get("result")
            ):
                raise ValueError("SKU 的发布回执已变更，请核对最新任务。")
            retry_items.append(item)
        if not retry_items:
            raise ValueError("没有可继续的提交步骤。")
        resumed = self.publish_payload({**payload, "items": retry_items}, config)
        rows.update({row["sku_id"]: row for row in resumed["sku_results"]})
        states = [rows[item["sku_id"]] for item in payload["items"]]
        aggregate = self._aggregate(payload, states)
        if resumed["status"] == "needs_attention":
            aggregate.update(ok=False, status="needs_attention")
        return aggregate

    def advance_submission(self, result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        if callable(getattr(self.item_adapter, 'advance_submissions', None)):
            states = deepcopy(result['sku_results'])
            indices = [index for index, state in enumerate(states) if state['status'] == 'pending_submission'
                       and (record(record(state['result']).get('result')) or state['result']).get('checkpoint', {}).get('next_poll_at', 0) <= time.time()]
            return self._native_mutations(result['envelope'], states, indices, [states[index]['result'] for index in indices],
                                          self.item_adapter.advance_submissions, config)
        states = []
        for index, state in enumerate(result["sku_results"]):
            if state["status"] != "pending_submission":
                states.append(state)
                continue
            previous = state["result"]
            inner = record(previous.get("result")) or previous
            checkpoint = record(inner.get("checkpoint"))
            if checkpoint.get("next_poll_at", 0) > time.time():
                states.append(state)
                continue
            self._save(result["envelope"], state, previous, "dispatching")
            try:
                advanced = self.item_adapter.advance_submission(previous, config)
            except Exception as exc:
                mapped = self.item_adapter.map_publish_error(exc)
                unknown = not isinstance(exc, PublishAdapterError) or exc.details.get("outcome_unknown") or exc.details.get("remote_write_dispatched")
                advanced = {**deepcopy(previous), "ok": False, "status": "outcome_unknown" if unknown else "failed",
                            "error": mapped.get("summary") or str(exc), "error_map": mapped}
            saved = self._save(result["envelope"], state, advanced)
            states.append(saved)
            if saved["status"] in {"failed", "outcome_unknown", "needs_attention"}:
                states.extend(deepcopy(result["sku_results"][index + 1:]))
                return {**self._aggregate(result["envelope"], states), "ok": False, "status": "needs_attention"}
        return self._aggregate(result["envelope"], states)

    def poll_publish_status(self, result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        if callable(getattr(self.item_adapter, 'poll_publish_statuses', None)):
            states = deepcopy(result['sku_results'])
            indices = [index for index, state in enumerate(states) if sku_can_check(state)
                       and (state['status'] != 'needs_attention' or result.get('status') == 'needs_attention')]
            values = self.item_adapter.poll_publish_statuses([states[index]['result'] for index in indices], config)
            errors = []
            for index, value in zip(indices, values, strict=True):
                if value.get('check_error'):
                    errors.append(f"{states[index]['sku']}：{value['check_error']}")
                    continue
                previous = states[index]
                if previous['status'] == 'outcome_unknown' and remote_status(value) not in {'published', 'outcome_unknown'}:
                    # 在售观察不能证明结果未知的价格或库存写入生效。
                    states[index] = self._save(result['envelope'], previous, value, 'outcome_unknown', confirming=True)
                else:
                    states[index] = self._save(result['envelope'], previous, value, confirming=True)
            return {**self._aggregate(result['envelope'], states), 'check_error': '；'.join(errors)}
        states, errors = [], []
        envelope = result["envelope"]
        for state in result["sku_results"]:
            if state["status"] == "needs_attention" and result.get("status") != "needs_attention":
                # 隔离项保留待处理，其他 SKU 的正常查询继续；不重复轮询隔离项。
                states.append(state)
                continue
            can_reconcile = state["status"] == "outcome_unknown" and remote_task_ids(state["result"])
            if state["status"] not in {"pending_confirmation", "needs_attention"} and not can_reconcile:
                states.append(state)
                continue
            try:
                polled = self.item_adapter.poll_publish_status(state["result"], config)
                if polled.get("status") == "outcome_unknown":
                    raise ValueError(polled.get("error") or "本次查询未返回可验证的结果。")
            except Exception as exc:
                # 只读查询失败不能把已受理 SKU 改成失败或提交结果不明。
                errors.append(f"{state['sku']}：{exc}")
                states.append(state)
                continue
            states.append(self._save(envelope, state, polled, confirming=True))
        return {**self._aggregate(envelope, states), "check_error": "；".join(errors)}

    def _publish_native_batch(self, payload, config):
        states, indices, outgoing = [], [], []
        for item in payload['items']:
            previous = get_context().products.sku_publication(payload['draft_id'], item['sku_id'], payload['target_key'])
            status = previous.get('status')
            if status in {'dispatching', 'outcome_unknown', 'pending_confirmation', 'pending_submission', 'needs_attention'} or status == 'published' and previous.get('fingerprint') == item['fingerprint']:
                states.append({**previous, 'status': 'outcome_unknown' if status == 'dispatching' else status})
            elif status in {'failed', 'submission_paused'} and record(previous.get('result')).get('plan') and record(previous.get('result')).get('checkpoint'):
                restored = self.item_adapter.restore_publish_result({**previous['result'], 'status': 'pending_submission'})
                states.append(self._save(payload, item, restored))
            else:
                indices.append(len(states))
                outgoing.append(item['payload'])
                states.append({**item, 'status': 'queued', 'result': previous.get('result', {})})
        return self._native_mutations(payload, states, indices, outgoing, self.item_adapter.publish_payloads, config)

    def _native_mutations(self, envelope, states, indices, values, execute, config):
        if not indices:
            return self._aggregate(envelope, states)

        def dispatch(batch):
            for position in batch:
                index = indices[position]
                self._save(envelope, states[index], states[index].get('result', {}), 'dispatching')

        received = set()

        def saved(position, value):
            received.add(position)
            index = indices[position]
            states[index] = self._save(envelope, states[index], value)

        results = execute(values, config, on_dispatch=dispatch, on_result=saved)
        for position, value in enumerate(results):
            if position not in received:
                saved(position, value)
        aggregate = self._aggregate(envelope, states)
        if any(state['status'] in ('failed', 'outcome_unknown', 'needs_attention') for state in states) and any(state['status'] == 'pending_submission' for state in states):
            aggregate.update(ok=False, status='needs_attention')
        return aggregate

    def _aggregate(self, envelope: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any]:
        statuses = {state["status"] for state in states}
        if "submission_paused" in statuses:
            status = "needs_attention"
        elif "pending_submission" in statuses:
            status = "pending_submission"
        elif statuses == {"published"}:
            status = "published"
        else:
            status = next((value for value in ("pending_confirmation", "needs_attention", "outcome_unknown")
                           if value in statuses), "partial" if "published" in statuses else "failed")
        grouping = deepcopy(envelope["grouping"])
        group_ids = [text(state.get("siteless_family_id") or state.get("group_id")) for state in states]
        grouping["status"] = "not_requested" if grouping["mode"] != "combined" or len(states) == 1 else "confirmed" if all(group_ids) and len(set(group_ids)) == 1 else "mismatch" if len(set(filter(None, group_ids))) > 1 else "awaiting_remote_confirmation"
        grouping["remote_group_ids"] = sorted(set(filter(None, group_ids)))
        return {"ok": status in {"published", "pending_confirmation", "pending_submission"}, "status": status, "sku_results": states, "grouping": grouping,
                "error": "；".join(f"{row['sku']}：{row.get('error') or row['status']}" for row in states if row["status"] not in {"published", "pending_confirmation", "pending_submission"}),
                "task_ids": list(dict.fromkeys(task for row in states for task in remote_task_ids(row["result"]))),
                "envelope": {key: deepcopy(envelope[key]) for key in ("draft_id", "target_key", "grouping")}}

    def map_publish_error(self, error: Exception) -> dict[str, Any]:
        return self.item_adapter.map_publish_error(error)

    def submission_step_interval_seconds(self, config: dict[str, Any]) -> float:
        return self.item_adapter.submission_step_interval_seconds(config)

    def next_submission_wait_seconds(self, result: dict[str, Any], config: dict[str, Any]) -> float:
        now = time.time()
        deadlines: list[float] = []
        for state in result.get("sku_results") or []:
            if not isinstance(state, dict) or state.get("status") != "pending_submission":
                continue
            item_result = record(state.get("result"))
            inner = record(item_result.get("result")) or item_result
            next_poll_at = float(record(inner.get("checkpoint")).get("next_poll_at") or 0)
            if next_poll_at > 0:
                deadlines.append(next_poll_at)
        if deadlines:
            return max(0.0, min(deadlines) - now)
        return self.submission_step_interval_seconds(config)

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        from .runtime_api import publish_product
        return publish_product(product, platform, config)


__all__ = ["SkuGroupPublishingAdapter"]

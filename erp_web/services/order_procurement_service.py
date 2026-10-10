"""订单详情与人工采购流程；不发起采购平台下单或变更订单平台状态。"""

from __future__ import annotations

from collections import Counter

from erp_web.schemas.order_procurement import (
    CancelPurchaseRequest,
    OrderDetail,
    ProcurementLine,
    ProcurementSource,
    RecordPurchaseRequest,
    SelectSourceRequest,
    SourceSelection,
    order_line_key,
)
from erp_web.schemas.orders import OrderView
from erp_web.stores.order_procurement_store import OrderProcurementStore, line_signature


class OrderProcurementService:
    def __init__(
        self, store: OrderProcurementStore, accounts_provider, identity_provider, *, yandex_images_provider
    ):
        self.store = store
        self.accounts_provider = accounts_provider
        self.identity_provider = identity_provider
        self.yandex_images_provider = yandex_images_provider

    def present_orders(self, orders: list[OrderView]) -> list[OrderView]:
        """为列表和详情补充图片及本地采购进度，不修改平台快照，不发起远端请求。"""
        tracking = self.store.tracking_summaries(orders)
        identities = {
            platform: self.identity_provider(platform)
            for platform in {order.platform for order in orders if order.platform != "yandex"}
        }
        yandex_ids = {}
        for order in orders:
            if order.platform == "yandex":
                yandex_ids.setdefault(order.account_id, set()).update(
                    line.remote_id for line in order.items if line.remote_id and line.remote_id == line.sku
                )
        yandex_images = {
            account: self.yandex_images_provider(account, ids)
            for account, ids in yandex_ids.items()
        }
        images = {}
        result = []
        for order in orders:
            lines = []
            for line in order.items:
                if order.platform == "yandex":
                    image = yandex_images[order.account_id].get(line.remote_id, "") if line.remote_id == line.sku else ""
                    lines.append(line.model_copy(update={"image_url": image}))
                    continue
                key = (order.platform, line.sku, line.remote_id, line.variant_id)
                if key not in images:
                    identity = identities[order.platform]
                    candidates = (
                        self.store.candidates(order.platform, identity, line)
                        if identity
                        else []
                    )
                    matches = {
                        (row.product_id, row.sku_id, row.image_url)
                        for row in candidates
                    }
                    images[key] = next(iter(matches))[2] if len(matches) == 1 else ""
                lines.append(line.model_copy(update={"image_url": images[key]}))
            result.append(order.model_copy(update={"items": lines, "purchase_tracking": tracking.get(order.id)}))
        return result

    def detail(self, order_id: str) -> dict:
        accounts = self.accounts_provider()
        order = self.present_orders([self.store.order(order_id, accounts)])[0]
        counts = Counter(order_line_key(line) for line in order.items)
        lines = []
        identity = self.identity_provider(order.platform)
        for line in order.items:
            key = order_line_key(line)
            selection = SourceSelection(line_key=key)
            records, purchased = [], 0
            if not key or counts[key] != 1:
                selection.reason = "平台订单行缺少唯一身份，暂不能关联或记录采购"
            else:
                saved, records, purchased = self.store.line_state(
                    order_id, key, accounts
                )
                candidates = (
                    self.store.candidates(order.platform, identity, line)
                    if identity
                    else []
                )
                # 同一来源在多个站点有刊登时，不应凭重复证据制造歧义。
                unique = {}
                for candidate in candidates:
                    token = (
                        candidate.product_id,
                        candidate.sku_id,
                        candidate.source.model_dump_json(),
                    )
                    unique[token] = candidate
                selection.candidates = list(unique.values())
                if saved:
                    selection.revision = saved["revision"]
                if saved and saved["line_signature"] == line_signature(line):
                    selection.status = "confirmed"
                    selection.source = ProcurementSource.model_validate_json(
                        saved["source_json"]
                    )
                elif len(unique) == 1:
                    selection.status = "matched"
                    selection.source = selection.candidates[0].source
                    selection.reason = "已匹配发布时的来源规格，请核对后确认采购来源"
                elif len(unique) > 1:
                    selection.status = "ambiguous"
                    selection.reason = "找到多个发布来源，请人工选择本次采购规格"
                else:
                    selection.reason = "没有可验证的发布来源，请人工关联采购商品及规格"
            lines.append(
                ProcurementLine(
                    line=line,
                    selection=selection,
                    records=records,
                    purchased_quantity=purchased,
                    remaining_quantity=max(0, line.quantity - purchased),
                )
            )
        return OrderDetail(order=order, lines=lines).model_dump(mode="json")

    def select_source(self, body):
        request = SelectSourceRequest.model_validate(body)
        source = request.source
        if request.candidate_id:
            detail = self.detail(request.order_id)
            line = next(
                (
                    row
                    for row in detail["lines"]
                    if row["selection"]["line_key"] == request.line_key
                ),
                None,
            )
            candidate = next(
                (
                    row
                    for row in (line or {}).get("selection", {}).get("candidates", [])
                    if row["id"] == request.candidate_id
                ),
                None,
            )
            if not candidate:
                raise ValueError("该来源不属于当前订单规格，请刷新后选择")
            source = ProcurementSource.model_validate(candidate["source"])
        self.store.select(request, source, self.accounts_provider())
        return self.detail(request.order_id)

    def record_purchase(self, body):
        request = RecordPurchaseRequest.model_validate(body)
        self.store.purchase(request, self.accounts_provider())
        return self.detail(request.order_id)

    def cancel_purchase(self, body):
        request = CancelPurchaseRequest.model_validate(body)
        self.store.cancel(request, self.accounts_provider())
        return self.detail(request.order_id)

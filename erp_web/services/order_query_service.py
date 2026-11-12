"""面向订单业务的本地检索：货号、冻结来源、证据与覆盖范围一起返回。"""

from collections import Counter
import json
import re

from erp_web.schemas.order_procurement import ProcurementSource, order_line_key
from erp_web.schemas.orders import PLATFORMS, account_pairs, utc_iso
from erp_web.schemas.platform_query_capabilities import OrderQueryCoverage, PlatformOrdersQueryRequest
from erp_web.services.order_source_resolution import resolve_source_selection
from erp_web.stores.order_procurement_store import line_signature


class OrderQueryService:
    def __init__(self, procurement, notifications):
        self.procurement = procurement
        self.notifications = notifications

    def query(self, request: PlatformOrdersQueryRequest):
        requested = [request.platform] if request.platform else list(PLATFORMS)
        accounts = {p: account for p, account in self.notifications.accounts().items() if p in requested}
        identities = {(p, account): self.procurement.identity_provider(p, account) for p, account in account_pairs(accounts)}
        terms = tuple(dict.fromkeys(request.article_numbers)) or ((request.q,) if request.q else ())
        patterns = {term: re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", re.I) for term in request.article_numbers}

        def matches(term, value):
            return bool(patterns[term].search(value)) if term in patterns else term.casefold() in value.casefold()

        items, total = [], 0
        scanned_orders = scanned_lines = unresolved = ambiguous = 0
        oldest = newest = ""
        for order, saved, purchases, bindings in self.procurement.store.iter_query_facts(accounts, identities, state=request.state):
            scanned_orders += 1
            oldest = min(oldest, order.checked_at) if oldest else order.checked_at
            newest = max(newest, order.checked_at)
            counts = Counter(order_line_key(line) for line in order.items)
            order_match = bool(request.q and any(matches(request.q, value) for value in (order.id, order.order_id, order.title)))
            lines = []
            for line in order.items:
                scanned_lines += 1
                key, signature = order_line_key(line), line_signature(line)
                evidence = []
                searchable = [("sku", line.sku), ("remote_id", line.remote_id), ("variant_id", line.variant_id)]
                if not request.article_numbers:
                    searchable.append(("title", line.title))
                for field, value in searchable:
                    if value:
                        evidence.append({"kind": "order_line", "field": field, "value": value, "certainty": "verified"})
                has_source = False
                if key and counts[key] == 1:
                    selection = resolve_source_selection(line, key, signature, saved.get((order.id, key)), bindings.get((order.platform, order.account_id, line.sku), []))
                    if selection.status == "confirmed":
                        has_source = True
                        evidence.extend(_source_evidence("confirmed_source", selection.source, revision=selection.revision))
                    if len(selection.candidates) > 1:
                        ambiguous += 1
                    for candidate in selection.candidates:
                        certain = len(selection.candidates) == 1
                        has_source |= certain
                        evidence.extend(_source_evidence("published_source", candidate.source,
                            certainty="verified" if certain else "ambiguous", publication_id=candidate.publication_id, binding_id=candidate.id))
                    for record in purchases.get((order.id, key), []):
                        if record["line_signature"] != signature:
                            continue
                        frozen = record
                        has_source = True
                        evidence.extend(_source_evidence("purchase_record", ProcurementSource.model_validate(frozen["source"]),
                            record_id=record["record_id"], purchase_order_number=frozen["purchase_order_number"], purchase_quantity=record["quantity"]))
                if not has_source:
                    unresolved += 1
                hits = [{"term": term, **entry} for term in terms for entry in evidence if matches(term, entry["value"])]
                if not terms or order_match or hits:
                    lines.append({"line_id": line.line_id, "line_key": key, "sku": line.sku, "remote_id": line.remote_id,
                                  "variant_id": line.variant_id, "title": line.title, "quantity": line.quantity, "matches": hits})
            if terms and not order_match and not lines:
                continue
            if request.offset <= total < request.offset + request.limit:
                items.append({"id": order.id, "platform": order.platform, "account_id": order.account_id,
                    "order_id": order.order_id, "title": order.title, "state": order.state, "status": order.status,
                    "shipping_status": order.shipping_status, "fulfillment": order.fulfillment,
                    "amount": order.amount, "currency": order.currency,
                    "shipment_deadline": order.shipment_deadline, "checked_at": order.checked_at, "items": lines,
                    "order_keyword_match": order_match})
            total += 1
        coverage = OrderQueryCoverage(queried_at=utc_iso(), platforms=tuple(accounts),
            unavailable_platforms=tuple(p for p in requested if p not in accounts), scanned_orders=scanned_orders,
            scanned_lines=scanned_lines, unresolved_source_lines=unresolved, ambiguous_source_lines=ambiguous,
            oldest_snapshot_at=oldest, newest_snapshot_at=newest, has_more=request.offset + len(items) < total)
        return {"ok": True, "items": items, "pagination": {"total": total, "limit": request.limit, "offset": request.offset},
                "sync_status": [row for row in self.notifications.sync_status() if row["platform"] in accounts],
                "coverage": coverage.model_dump(mode="json"),
                "notifications": self.notifications.store.read_notifications(accounts) if request.include_notifications else []}


def _source_evidence(kind, source, *, certainty="verified", **reference):
    return [{"kind": kind, "field": field, "value": value, "certainty": certainty, **reference}
            for field in ("specification", "source_sku_id", "source_offer_id", "source_spec_id")
            if (value := getattr(source, field))]

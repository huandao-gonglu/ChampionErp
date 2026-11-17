from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from erp_web.schemas.managed_settings import ALIBABA_APPLICATION_TYPES


class RequestValidationError(ValueError):
    """HTTP 请求不满足公开契约，并携带稳定的 HTTP 错误状态。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error_code: str = "INVALID_REQUEST",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


@dataclass(frozen=True)
class FieldRule:
    """单个公开请求字段的基础 JSON 类型与归一化规则。"""

    kind: str
    choices: frozenset[str] = field(default_factory=frozenset)
    minimum: int | None = None
    maximum: int | None = None
    allow_empty: bool = False


@dataclass(frozen=True)
class RequestContract:
    """某个 POST 端点在 HTTP 边界拥有的字段契约。"""

    fields: Mapping[str, FieldRule] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    required_any: tuple[tuple[str, ...], ...] = ()


OBJECT = FieldRule("object")
ARRAY = FieldRule("array")
STRING = FieldRule("string")
STRING_ARRAY = FieldRule("string_array")
STRING_OR_ARRAY = FieldRule("string_or_array")
UPLOADS = FieldRule("uploads")
BOOLEAN = FieldRule("boolean")
LIMIT = FieldRule("integer", minimum=1, maximum=500)
PORT = FieldRule("integer", minimum=1, maximum=65535)
ALIBABA_PROVIDER = FieldRule('enum', choices=ALIBABA_APPLICATION_TYPES)
COLLECT_MODE = FieldRule("enum", choices=frozenset({"browser", "api", "playwright", "browser-session", "browser_session"}))
IMAGE_ACTION = FieldRule(
    "enum",
    choices=frozenset({"upload", "sort", "delete", "replace", "set_main", "filter"}),
)
DRAFT_IMAGE_STRATEGY = FieldRule(
    "enum",
    choices=frozenset({"pool_only", "append", "replace_selected", "replace_all"}),
)


# 这些字段跨多个端点共享相同 JSON 形状。只在调用方实际传入字段时校验，
# 因此配置类端点仍可按白名单消费自己的扩展字段。
_COMMON_FIELD_RULES: dict[str, FieldRule] = {
    "browser_tab_id": STRING,
    "sku_id": STRING,
    "1688_api": OBJECT,
    "appConfig": OBJECT,
    "category_record": OBJECT,
    "common": OBJECT,
    "config": OBJECT,
    "content": OBJECT,
    "dimensions": OBJECT,
    "draft": OBJECT,
    "model": OBJECT,
    "order": OBJECT,
    "options": OBJECT,
    "payload": OBJECT,
    "product": OBJECT,
    "provider": OBJECT,
    "replacement": OBJECT,
    "storeConfig": OBJECT,
    "target": OBJECT,
    "inputs": OBJECT,
    "attributes": ARRAY,
    "categories": ARRAY,
    "draft_ids": STRING_OR_ARRAY,
    "draftIds": STRING_OR_ARRAY,
    "image_ids": STRING_ARRAY,
    "image_pool": ARRAY,
    "images": ARRAY,
    "ids": STRING_ARRAY,
    "ordered_ids": STRING_ARRAY,
    "platforms": STRING_ARRAY,
    "product_ids": STRING_ARRAY,
    "products": ARRAY,
    "selected_image_ids": STRING_ARRAY,
    "source_image_ids": STRING_ARRAY,
    "targets": ARRAY,
    "uploads": UPLOADS,
    "urls": STRING_OR_ARRAY,
    "values": STRING_ARRAY,
    "action": STRING,
    "app_id": STRING,
    "browser": STRING,
    "category_id": STRING,
    "category_path": STRING,
    "code": STRING,
    "code_or_url": STRING,
    "country": STRING,
    "display_title": STRING,
    "initial_user_message": STRING,
    "draft_id": STRING,
    "draftId": STRING,
    "html": STRING,
    "item_id": STRING,
    "job_id": STRING,
    "siteless_user_product_id": STRING,
    "goal": STRING,
    "message": STRING,
    "task_id": STRING,
    "draft_query_snapshot_id": STRING,
    "kind": STRING,
    "keyword": STRING,
    "language": STRING,
    "mode": STRING,
    "platform": STRING,
    "product_file_path": STRING,
    "product_id": STRING,
    "prompt": STRING,
    "query": STRING,
    "redirect_uri": STRING,
    "site": STRING,
    "site_id": STRING,
    "source_url": STRING,
    "tab_url": STRING,
    "target_language": STRING,
    "text": STRING,
    "url": STRING,
    "validation_digest": STRING,
    "limit": LIMIT,
    "port": PORT,
    "apply_to_draft": BOOLEAN,
    "confirm": BOOLEAN,
    "delete_files": BOOLEAN,
    "include_description": BOOLEAN,
    "probe_capabilities": BOOLEAN,
    "save_only": BOOLEAN,
    "selected_only": BOOLEAN,
    "stream": BOOLEAN,
    "draft_image_strategy": DRAFT_IMAGE_STRATEGY,
    "draftImageStrategy": DRAFT_IMAGE_STRATEGY,
}


def _contract(
    *,
    fields: Mapping[str, FieldRule] | None = None,
    required: tuple[str, ...] = (),
    required_any: tuple[tuple[str, ...], ...] = (),
) -> RequestContract:
    return RequestContract(
        fields=dict(fields or {}),
        required=required,
        required_any=required_any,
    )


_EMPTY = _contract()
_PRODUCT = _contract(required=("product_id",))
_DRAFT = _contract(required_any=(("draft_id", "draftId"),))
_PRODUCT_OR_DRAFT = _contract(required_any=(("product_id", "draft_id", "draftId"),))


# 路由键是合同的一部分。即使端点当前没有额外必填项，也显式登记，便于新增
# 写入口时由测试发现遗漏，而不是悄悄退化到“任意 dict”。
REQUEST_CONTRACTS: dict[str, RequestContract] = {
    "/api/business-data/read": _contract(fields={"resource": STRING, "params": OBJECT, "path": FieldRule("string", allow_empty=True), "fields": STRING_ARRAY, "filters": OBJECT, "offset": FieldRule("integer", minimum=0), "limit": LIMIT}, required=("resource",)),
    "/api/admin/settings": _contract(fields={"revision": STRING, "exchange_rates": OBJECT, "alibaba": OBJECT,
        "alibaba_crossborder": OBJECT, "onebound": OBJECT,
        "alibaba_api_application": ALIBABA_PROVIDER,
        "crossborderbus_client_secret": STRING},
        required=("revision", "exchange_rates", "alibaba_crossborder")),
    "/api/shipping/tariffs/import": _contract(fields={"platform": STRING, "scope": STRING, "filename": STRING,
        "content_base64": STRING, "effective_date": STRING, "expected_version": FieldRule("string", allow_empty=True), "preview": BOOLEAN},
        required=("platform", "scope", "filename", "content_base64", "expected_version")),
    "/api/shipping/tariffs/upgrade": _contract(fields={"platform": STRING, "shared_version": STRING, "expected_version": STRING},
        required=("platform", "shared_version", "expected_version")),
    "/api/account/newapi/launch": _contract(fields={"target": STRING, "source_origin": STRING}, required=("target",)),
    "/api/internal/newapi/launch": _contract(fields={"ticket": STRING, "origin": STRING}, required=("ticket", "origin")),
    "/api/account/newapi/preview": _contract(fields={"code": STRING}, required=("code",)),
    "/api/account/newapi/authorize": _contract(fields={"code": STRING, "password": STRING}, required=("code",)),
    "/api/internal/newapi/exchange": _contract(fields={"ticket": STRING, "verifier": STRING, "flow_id": STRING, "target": STRING}, required=("ticket", "verifier", "flow_id", "target")),
    "/api/internal/newapi/session": _contract(fields={"user_id": STRING, "session_ref": STRING}, required=("user_id", "session_ref")),
    "/api/internal/newapi/user": _contract(fields={"user_id": STRING}, required=("user_id",)),
    "/api/account/registration-code": _contract(fields={"email": STRING, "human_token": STRING}, required=("email", "human_token")),
    "/api/account/register": _contract(fields={"email": STRING, "display_name": STRING, "password": STRING, "email_code": STRING}, required=("email", "display_name", "password", "email_code")),
    "/api/account/login": _contract(fields={"email": STRING, "password": STRING}, required=("email", "password")),
    "/api/account/logout": _EMPTY,
    "/api/account/password": _contract(fields={"current_password": STRING, "password": STRING}, required=("current_password", "password")),
    "/api/admin/users": _contract(fields={"email": STRING, "display_name": STRING, "password": STRING}, required=("email", "display_name", "password")),
    "/api/admin/users/status": _contract(fields={"user_id": STRING, "status": FieldRule("enum", choices=frozenset({"enabled", "disabled"}))}, required=("user_id", "status")),
    "/api/admin/users/password": _contract(fields={"user_id": STRING, "password": STRING}, required=("user_id", "password")),
    '/api/desktop/connect': RequestContract(),
    '/api/desktop-handoff/create': RequestContract(fields={'target': STRING, 'state': OBJECT}, required=('target',)),
    '/api/desktop-handoff/status': RequestContract(fields={'request_id': STRING}, required=('request_id',)),
    '/api/desktop-handoff/resolve': RequestContract(fields={'request_id': STRING}, required=('request_id',)),
    '/api/desktop-handoff/acknowledge': RequestContract(fields={'request_id': STRING}, required=('request_id',)),
    '/api/desktop-handoff/installers': RequestContract(),
    '/api/desktop/poll': RequestContract(),
    '/api/desktop/disconnect': RequestContract(),
    '/api/desktop/result': RequestContract(fields={'id': STRING, 'ok': BOOLEAN, 'result': OBJECT, 'error': STRING, 'error_code': STRING, 'outcome_unknown': BOOLEAN}, required=('id', 'ok')),
    '/api/desktop/asset': RequestContract(fields={'id': STRING, 'asset': OBJECT}, required=('id', 'asset')),

    "/api/system-settings/forbidden-categories/import": _contract(
        fields={"platform": FieldRule("enum", choices=frozenset({"yandex"})), "filename": STRING, "content_base64": STRING},
        required=("platform", "filename", "content_base64"),
    ),
    "/api/tasks/check": _contract(fields={"task_id": STRING}, required=("task_id",)),
    "/api/wechat/login": RequestContract(),
    "/api/wechat/verify": RequestContract(fields={"code": STRING}, required=("code",)),
    "/api/wechat/unbind": RequestContract(),
    # 空列表表示全部关闭，不能当成未提供字段。
    "/api/wechat/settings": RequestContract(fields={"forward_types": FieldRule("string_array", allow_empty=True)}, required=("forward_types",)),
    "/api/orders/address-note": RequestContract(fields={"order_id": STRING, "shipment_id": STRING, "address_key": STRING, "revision": FieldRule("integer", minimum=0), "note": STRING}, required=("order_id", "shipment_id", "address_key", "revision", "note")),
    '/api/yandex/warehouses/refresh': _EMPTY,
    '/api/yandex/warehouses/logistics': _contract(fields={'business_id': STRING, 'campaign_id': STRING, 'revision': FieldRule('integer', minimum=0), 'bindings': ARRAY}, required=('business_id', 'campaign_id', 'revision', 'bindings')),
    '/api/logistics/refresh': _EMPTY,
    '/api/logistics/parse-address': RequestContract(fields={'text': STRING}, required=('text',)),
    '/api/logistics/address': _contract(fields={'id': STRING, 'revision': FieldRule('integer', minimum=0), 'warehouse_id': FieldRule('integer', minimum=1), 'name': STRING, 'address': OBJECT, 'enabled': BOOLEAN, 'is_default': BOOLEAN, 'remark': STRING, 'rules': ARRAY}, required=('revision', 'warehouse_id', 'name', 'address', 'rules')),
    "/api/orders/receiving-warehouse": RequestContract(fields={"order_id": STRING, "revision": FieldRule("integer", minimum=0), "section_id": FieldRule("integer", minimum=1), "address_id": STRING, "address_revision": FieldRule('integer', minimum=1), "delivery_key": STRING, "confirmed": BOOLEAN, "existing_purchase_confirmed": BOOLEAN, "fulfillment_revision": FieldRule("integer", minimum=0)}, required=("order_id", "revision", "section_id", "address_id", "address_revision", "delivery_key", "confirmed")),
    "/api/crossborderbus/authorize": _contract(fields={"user_name": STRING, "password": STRING}),
    "/api/crossborderbus/catalog": _EMPTY,
    "/api/crossborderbus/save-rule": _contract(fields={"section_id": FieldRule("integer", minimum=1), "warehouse_id": FieldRule("integer", minimum=1), "service_ids": ARRAY, "compatible_warehouse_ids": ARRAY, "confirmed": BOOLEAN, "auto_submit": BOOLEAN}, required=("platform", "account_id", "fulfillment", "platform_warehouse_id", "delivery_method_id", "delivery_method_name", "country", "section_id", "warehouse_id", "compatible_warehouse_ids", "confirmed")),
    "/api/crossborderbus/delete-rule": _contract(fields={"id": STRING}, required=("id",)),
    "/api/orders/fulfillment/upload-label": _contract(fields={"order_id": STRING, "revision": FieldRule("integer", minimum=0), "content_base64": STRING}, required=("order_id", "revision", "content_base64")),
    "/api/orders/fulfillment/fetch-label": _contract(fields={"order_id": STRING, "revision": FieldRule("integer", minimum=0)}, required=("order_id", "revision")),
    **{f"/api/orders/fulfillment/{action}": _contract(fields={"order_id": STRING, "revision": FieldRule("integer", minimum=0), "plan": OBJECT, "receiving_revision": FieldRule("integer", minimum=0), **({"remark": STRING} if action == "plan" else {}), "parcels": ARRAY, "confirm_parcels": BOOLEAN, "label": OBJECT}, required=("order_id", "revision")) for action in ("label", "parcels", "plan", "pause", "resume", "submit", "sync", "associate", "retry", "cancel")},
    "/api/image-hosting/save": _contract(fields={"profile": OBJECT}, required=("profile",)),
    "/api/image-hosting/test": _contract(fields={"profile": OBJECT}, required=("profile",)),
    "/api/image-hosting/default": _contract(fields={"id": STRING}),
    "/api/image-hosting/delete": _contract(fields={"id": STRING}, required=("id",)),
    "/api/online-products/ozon-export/preview": _contract(fields={"account_id": STRING, "listing_ids": STRING_ARRAY, "template_name": STRING, "template_base64": STRING, "overrides": OBJECT, "preview_fingerprint": STRING}, required=("account_id", "listing_ids", "template_name", "template_base64")),
    "/api/online-products/ozon-export/download": _contract(fields={"account_id": STRING, "listing_ids": STRING_ARRAY, "template_name": STRING, "template_base64": STRING, "overrides": OBJECT, "preview_fingerprint": STRING}, required=("account_id", "listing_ids", "template_name", "template_base64", "preview_fingerprint")),
    "/api/online-products/sync": _contract(fields={"platform": FieldRule("enum", choices=frozenset({"mercadolibre", "ozon", "yandex"})), "catalog": FieldRule("enum", choices=frozenset({"current", "archived"})), "idempotency_key": STRING}, required=("platform", "idempotency_key")),
    "/api/online-products/refresh-sales": _contract(fields={"account_id": STRING, "listing_ids": STRING_ARRAY}, required=("account_id", "listing_ids")),
    "/api/online-products/refresh-status": _contract(fields={"listing_id": STRING}, required=("listing_id",)),
    "/api/online-products/refresh-detail": _contract(fields={"listing_id": STRING}, required=("listing_id",)),
    "/api/online-products/change": _contract(fields={"listing_id": STRING, "version": STRING, "operation": STRING, "scope_id": STRING, "changes": OBJECT, "idempotency_key": STRING}, required=("listing_id", "version", "operation", "changes", "idempotency_key")),
    "/api/online-products/change-batch/preview": _contract(fields={"platform": STRING, "catalog": STRING, "group_ids": STRING_ARRAY, "listing_ids": STRING_ARRAY, "operation": STRING, "changes": OBJECT}, required=("platform", "operation", "changes")),
    "/api/online-products/change-batch": _contract(fields={"items": ARRAY, "operation": STRING, "changes": OBJECT, "idempotency_key": STRING}, required=("items", "operation", "changes", "idempotency_key")),
    "/api/online-products/reconcile": _contract(fields={"job_id": STRING}, required=("job_id",)),
    "/api/online-products/close": _contract(fields={"job_id": STRING, "expected_updated_at": STRING, "acknowledge": BOOLEAN}, required=("job_id", "expected_updated_at", "acknowledge")),
    "/api/online-products/retry": _contract(fields={"job_id": STRING, "idempotency_key": STRING}, required=("job_id", "idempotency_key")),
    "/api/ai/approval-mode": _contract(
        fields={"mode": FieldRule("enum", choices=frozenset({"ask", "full"}))},
        required=("mode",),
    ),
    "/api/ai-config/save": _EMPTY,
    "/api/v1/ai-presentations": _EMPTY,
    "/api/assign-upc": RequestContract(fields={"product_id": STRING, "sku_ids": STRING_ARRAY}, required=("product_id",)),
    "/api/draft-pricing/preview": _contract(fields={"draft_id": STRING, "expected_updated_at": STRING, "target_keys": STRING_ARRAY, "common": OBJECT, "targets": OBJECT, "sku_updates": ARRAY, "target_selections": OBJECT}, required=("draft_id",)),
    "/api/draft-pricing/restore": _contract(fields={"draft_id": STRING, "expected_updated_at": STRING, "target_keys": STRING_ARRAY, "common": OBJECT, "targets": OBJECT, "sku_updates": ARRAY, "target_selections": OBJECT}, required=("draft_id",)),
    "/api/pricing/templates/save": RequestContract(fields={"id": STRING, "name": STRING, "parameters": OBJECT, "set_default": BOOLEAN}, required=("name", "parameters")),
    "/api/pricing/templates/default": RequestContract(fields={"id": STRING}, required=("id",)),
    "/api/pricing/templates/delete": RequestContract(fields={"id": STRING}, required=("id",)),
    "/api/draft-pricing/apply": _contract(fields={"draft_id": STRING, "expected_updated_at": STRING, "target_keys": STRING_ARRAY, "common": OBJECT, "targets": OBJECT, "sku_updates": ARRAY, "target_selections": OBJECT}, required=("draft_id",)),
    "/api/v1/category-match": _contract(
        required=("platform",),
        required_any=(
            ("product_id", "draft_id", "draftId"),
            ("site", "site_id", "country"),
        ),
    ),
    "/api/category-attribute-values": _contract(
        required=("platform", "category_id", "attribute_id"),
    ),
    "/api/category-attrs": _contract(required=("category_id",)),
    "/api/category-precheck": _contract(
        required_any=(
            ("product_id", "draft_id", "draftId"),
            ("category_id", "category_record"),
        )
    ),
    "/api/category-search": _contract(required_any=(("query", "keyword"),)),
    "/api/claim-products": _contract(required=("product_ids", "targets")),
    "/api/collect-1688": _contract(fields={"mode": COLLECT_MODE}, required=("url",)),
    "/api/collection-links/manage": RequestContract(fields={"action": FieldRule("enum", choices=frozenset({"add", "edit", "remove", "import"})), "items": ARRAY, "id": STRING, "revision": FieldRule("integer", minimum=1)}, required=("action",)),
    "/api/collection-links/run": RequestContract(fields={"id": STRING, "revision": FieldRule("integer", minimum=1), "receipt_id": STRING}, required=("id", "revision", "receipt_id")),
    "/api/collection-links/verify": RequestContract(fields={"id": STRING, "revision": FieldRule("integer", minimum=1)}, required=("id", "revision")),
    "/api/collect-receipt": RequestContract(fields={"receipt_id": STRING}, required=("receipt_id",)),
    "/api/collect-batch": _contract(fields={"mode": COLLECT_MODE, "receipt_id": STRING}, required_any=(("urls", "url"),)),
    "/api/collect-resume": RequestContract(fields={"receipt_id": STRING, "browser_tab_id": STRING, "source_url": STRING, "platform": STRING}, required=("browser_tab_id", "source_url")),
    "/api/collect-verification": _contract(required=("browser_tab_id", "source_url")),
    "/api/collect-source": _contract(fields={"mode": COLLECT_MODE}, required=("url",)),
    "/api/delete-draft": _contract(
        required_any=(("draft_ids", "draftIds", "draft_id", "draftId"),)
    ),
    "/api/delete-products": _contract(required=("product_ids",)),
    "/api/duplicate-draft": _contract(required=("draft_id",)),
    "/api/generate-copy": _PRODUCT_OR_DRAFT,
    "/api/generate-copy-batch": _contract(required=("product_ids",)),
    "/api/generate-image-prompts": _PRODUCT,
    "/api/draft-images/state": _contract(required=("draft_id",)),
    "/api/draft-images/plan-set": _contract(
        required=("product_id", "draft_id", "updated_at"),
        required_any=(("source_image_ids", "sku_ids"),),
        fields={"count": FieldRule("integer", minimum=2, maximum=8), "sku_ids": STRING_ARRAY},
    ),
    "/api/draft-images/generate-set": _contract(required=("product_id", "draft_id", "updated_at", "request_id"), fields={"request_id": STRING, "plan": OBJECT, "base_request_id": STRING, "retry_page_ids": STRING_ARRAY}),
    "/api/draft-images/process": _contract(required=("product_id", "draft_id", "request_id", "updated_at", "operation", "source_image_ids"), fields={"request_id": STRING, "operation": FieldRule("enum", choices=frozenset({"translate", "edit"}))}),
    "/api/image-edit": _contract(required=("product_id", "prompt", "source_image_ids")),
    "/api/image-pool/action": _contract(
        fields={"action": IMAGE_ACTION},
        required=("product_id", "action"),
    ),
    "/api/image-pool/save": _PRODUCT,
    "/api/image-pool/sync-generated": _PRODUCT,
    "/api/image-pool/upload": _contract(required=("product_id", "uploads")),
    "/api/image-translate": _contract(required=("product_id", "source_image_ids")),
    "/api/load-draft": _DRAFT,
    "/api/load-product": _contract(required_any=(("product_id", "product_file_path"),)),
    "/api/mercadolibre/auth-checklist": _EMPTY,
    "/api/mercadolibre/auth-link": _contract(required=("app_id", "redirect_uri")),
    "/api/mercadolibre/exchange-code": _contract(
        required_any=(("code_or_url", "code"),)
    ),
    "/api/mercadolibre/notifications": _EMPTY,
    "/api/ozon/notifications": _EMPTY,
    "/api/yandex/notifications": _EMPTY,
    "/api/external-requests/recover": RequestContract(fields={"block_id": STRING, "reason": STRING}, required=("block_id",)),
    "/api/external-requests/history/clear": RequestContract(fields={"before": FieldRule("integer", minimum=1)}, required=("before",)),
    "/api/orders/chat/history": RequestContract(fields={"order_id": STRING}, required=("order_id",)),
    "/api/orders/chat/send": RequestContract(fields={"order_id": STRING, "message": STRING}, required=("order_id", "message")),
    "/api/orders/sync": RequestContract(fields={"platform": STRING, "automatic": BOOLEAN}),
    "/api/system-settings/user-agents": RequestContract(fields={"content": FieldRule("string", allow_empty=True), "expected_revision": STRING}, required=("content", "expected_revision")),
    "/api/system-settings": RequestContract(fields={"sync_policies": OBJECT, "orders_auto_sync_interval_hours": FieldRule("integer", minimum=5), "watermark": OBJECT, "wechat_forward_types": FieldRule("string_array", allow_empty=True)}, required_any=(("orders_auto_sync_interval_hours", "watermark", "wechat_forward_types", "sync_policies"),)),
    "/api/system-settings/watermark-preview": RequestContract(fields={"watermark": OBJECT}, required=("watermark",)),
    "/api/orders/retry": RequestContract(fields={"event_id": FieldRule("integer", minimum=1)}, required=("event_id",)),
    "/api/orders/acknowledge": RequestContract(fields={"through_id": FieldRule("integer", minimum=0)}, required=("through_id",)),
    "/api/orders/select-source": RequestContract(fields={"order_id": STRING, "line_key": STRING, "revision": FieldRule("integer", minimum=0), "candidate_id": STRING, "source": OBJECT}, required=("order_id", "line_key", "revision")),
    "/api/orders/record-purchase": RequestContract(fields={"order_id": STRING, "source_platform": STRING, "request_id": STRING, "purchase_order_number": STRING, "items": ARRAY, "note": STRING, "receiving_warehouse": OBJECT}, required=("order_id", "source_platform", "request_id", "purchase_order_number")),
    "/api/orders/cancel-purchase": RequestContract(fields={"order_id": STRING, "record_id": STRING}, required=("order_id", "record_id")),
    "/api/orders/alibaba-purchase/preview": RequestContract(fields={"order_id": STRING, "line_key": STRING, "candidate_id": STRING, "quantity": FieldRule("integer", minimum=1, maximum=10000), "address_id": STRING, "address": OBJECT, "address_confirmed": BOOLEAN, "receiving_revision": FieldRule("integer", minimum=0)}, required=("order_id", "line_key", "candidate_id", "quantity")),
    "/api/orders/alibaba-purchase/parse-address": RequestContract(fields={"order_id": STRING, "line_key": STRING, "text": STRING}, required=("order_id", "line_key", "text")),
    "/api/orders/alibaba-purchase/batch-preview": RequestContract(fields={"order_id": STRING, "receiving_revision": FieldRule("integer", minimum=1), "items": ARRAY}, required=("order_id", "receiving_revision", "items")),
    "/api/orders/alibaba-purchase/create": RequestContract(fields={"preview_id": STRING, "pay_channel": FieldRule("enum", choices=frozenset({"alipay", "shegou"}))}, required=("preview_id", "pay_channel")),
    "/api/orders/alibaba-purchase/reconcile": RequestContract(fields={"preview_id": STRING}, required=("preview_id",)),
    "/api/orders/alibaba-purchase/cashier": RequestContract(fields={"preview_id": STRING}, required=("preview_id",)),
    "/api/orders/alibaba-purchase/payment-status": RequestContract(fields={"pay_channel": FieldRule("enum", choices=frozenset({"alipay", "shegou"}))}, required=("pay_channel",)),
    "/api/orders/alibaba-purchase/pay": RequestContract(fields={"preview_id": STRING, "confirmed_total_fen": FieldRule("integer", minimum=1)}, required=("preview_id", "confirmed_total_fen")),
    "/api/orders/purchase-sync": RequestContract(fields={"order_id": STRING, "record_id": STRING}, required=("order_id", "record_id")),
    "/api/orders/purchase-query": RequestContract(fields={"order_id": STRING, "record_id": STRING, "kind": FieldRule("enum", choices=frozenset({"order", "logistics"}))}, required=("order_id", "record_id")),
    "/api/orders/configure": RequestContract(fields={"public_url": STRING}, required=("public_url",)),
    "/api/mercadolibre/real-auth-test": _PRODUCT,
    "/api/mercadolibre/refresh-token": _EMPTY,
    "/api/alibaba/crossborder/authorization/start": _EMPTY,
    "/api/alibaba/authorization/start": RequestContract({"provider": ALIBABA_PROVIDER}),
    "/api/alibaba/authorization/complete": RequestContract({"callback_url": STRING, "provider": ALIBABA_PROVIDER}, required=("callback_url",)),
    "/api/alibaba/authorization/self-use-token": RequestContract({"access_token": STRING}, required=("access_token",)),
    "/api/alibaba/authorization/onebound": RequestContract({"access_token": STRING, "session": STRING}, required_any=(("access_token", "session"),)),
    "/api/open-auth-link": _contract(required=("url",)),
    "/api/publish-bus/enqueue": _contract(
        # 人工确认摘要指纹：HTTP 边界声明字符串类型；业务判空与绑定校验
        # 在发布 Capability 完成。
        fields={"validation_digest": STRING},
        required_any=(("draft_id", "draftId"),),
    ),
    "/api/publish-bus/reconcile": _contract(
        fields={"trigger": STRING},
        required=("job_id", "platform"),
    ),
    "/api/publish-bus/retry": _contract(
        fields={"job_id": STRING, "platform": STRING},
        required=("job_id", "platform"),
    ),
    "/api/publish-payload-preview": _DRAFT,
    "/api/publish-precheck": _DRAFT,
    "/api/save-draft": _contract(required_any=(("draft", "draft_id", "draftId"),)),
    "/api/save-product": _contract(required=("product",)),
    "/api/save-settings": _EMPTY,
    "/api/store-auth/clear": _contract(required=("platform",)),
    "/api/store-auth/currency": _contract(
        fields={"listing_currency": STRING},
        required=("platform", "listing_currency"),
    ),
    "/api/test-ai-model": _contract(required_any=(("model", "config"),)),
    "/api/test-api-config": _contract(required=("kind",)),
    "/api/test-store-auth": _contract(required=("platform",)),
    "/api/text-translate": _contract(required=("target_language", "content")),
    "/api/upc-pool/import": _contract(required=("values",)),
    "/api/v1/ai-chat/runs": _contract(
        fields={
            "trigger": STRING,
            "id": STRING,
            "messages": ARRAY,
            "target_draft_ids": ARRAY,
            "page_context": OBJECT,
            "queue_if_busy": BOOLEAN,
            "answer_to": STRING,
        },
        required=("id", "messages"),
    ),
    "/api/v1/ai-chat/cancel": _contract(
        fields={"id": STRING, "message_id": STRING}, required=("id", "message_id"),
    ),
    "/api/v1/ai-chat/manage": _contract(
        fields={"id": STRING, "action": STRING, "title": STRING}, required=("id", "action"),
    ),
    "/api/v1/ai-chat/queue": _contract(
        fields={"id": STRING, "message_id": STRING, "action": STRING}, required=("id", "message_id", "action"),
    ),
    "/api/v1/ai-chat/fork": _contract(
        fields={"id": STRING, "message_id": STRING}, required=("id", "message_id"),
    ),
    "/api/v1/product-research/keywords/search": _contract(
        fields={"market_id": STRING, "keyword": STRING, "refresh": BOOLEAN},
        required=("market_id", "keyword")),
    "/api/v1/product-research/hot-products/search": _contract(required=("keywords",)),
    "/api/v1/product-research/suppliers/search": _contract(required=("run_id", "candidate_id")),
    "/api/v1/product-research/suppliers/import": _contract(required=("run_id", "candidate_id", "supplier_id", "confirmed")),
    "/api/v1/product-research/search-providers/test": _contract(required=("provider",)),
    "/api/v1/product-research/source-registry/save": _EMPTY,
}


def _is_present(value: Any, rule: FieldRule | None = None) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) or bool(rule and rule.allow_empty)
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        return bool(value) or bool(rule and rule.allow_empty)
    return True


def _type_error(field_name: str, expected: str) -> RequestValidationError:
    return RequestValidationError(
        f"{field_name} 必须是{expected}",
        error_code="INVALID_FIELD_TYPE",
    )


def _normalize_integer(field_name: str, value: Any, rule: FieldRule) -> int:
    if isinstance(value, bool):
        raise _type_error(field_name, "整数")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, float) and value.is_integer():
        normalized = int(value)
    elif isinstance(value, str):
        try:
            normalized = int(value.strip(), 10)
        except ValueError as exc:
            raise _type_error(field_name, "整数") from exc
    else:
        raise _type_error(field_name, "整数")
    if rule.minimum is not None and normalized < rule.minimum:
        raise RequestValidationError(
            f"{field_name} 不能小于 {rule.minimum}",
            error_code="INVALID_FIELD_VALUE",
        )
    if rule.maximum is not None and normalized > rule.maximum:
        raise RequestValidationError(
            f"{field_name} 不能大于 {rule.maximum}",
            error_code="INVALID_FIELD_VALUE",
        )
    return normalized


def _normalize_boolean(field_name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise _type_error(field_name, "布尔值")


def _normalize_field(field_name: str, value: Any, rule: FieldRule) -> Any:
    if rule.kind == "object":
        if not isinstance(value, Mapping):
            raise _type_error(field_name, "JSON 对象")
        return deepcopy(dict(value))
    if rule.kind == "array":
        if not isinstance(value, list):
            raise _type_error(field_name, "JSON 数组")
        return value
    if rule.kind == "string":
        if not isinstance(value, str):
            raise _type_error(field_name, "字符串")
        return value
    if rule.kind == "string_array":
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise _type_error(field_name, "字符串数组")
        return value
    if rule.kind == "string_or_array":
        if isinstance(value, str):
            return value
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return value
        raise _type_error(field_name, "字符串或字符串数组")
    if rule.kind == "uploads":
        items = [value] if isinstance(value, Mapping) else value
        if not isinstance(items, list) or any(
            not isinstance(item, Mapping) for item in items
        ):
            raise _type_error(field_name, "上传对象或上传对象数组")
        return value
    if rule.kind == "integer":
        return _normalize_integer(field_name, value, rule)
    if rule.kind == "boolean":
        return _normalize_boolean(field_name, value)
    if rule.kind == "enum":
        if not isinstance(value, str):
            raise _type_error(field_name, "字符串")
        normalized = value.strip().lower()
        if normalized not in rule.choices:
            choices = "、".join(sorted(rule.choices))
            raise RequestValidationError(
                f"{field_name} 必须是以下值之一：{choices}",
                error_code="INVALID_FIELD_VALUE",
            )
        return normalized
    raise RuntimeError(f"未知请求字段规则：{rule.kind}")


def validate_request_payload(
    payload: Any,
    *,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """校验、归一化并隔离 HTTP 写请求。

    ``endpoint`` 让同名字段按实际路由解释。例如 ``limit`` 在类目检索入口会
    被稳定转换为整数，非法对象不会再流入 ``int(...)`` 形成 500；图片动作
    则在调用领域服务前完成枚举校验。
    """

    if not isinstance(payload, Mapping):
        raise RequestValidationError("请求体必须是 JSON 对象")

    normalized = deepcopy(dict(payload))
    endpoint_path = str(endpoint or "").partition("?")[0]
    contract = REQUEST_CONTRACTS.get(endpoint_path, _EMPTY)
    rules = {**_COMMON_FIELD_RULES, **dict(contract.fields)}
    for field_name, rule in rules.items():
        if field_name not in normalized or normalized[field_name] is None:
            continue
        normalized[field_name] = _normalize_field(
            field_name,
            normalized[field_name],
            rule,
        )

    for field_name in contract.required:
        if not _is_present(normalized.get(field_name), rules.get(field_name)):
            raise RequestValidationError(
                f"缺少必填字段：{field_name}",
                error_code="MISSING_REQUIRED_FIELD",
            )
    for alternatives in contract.required_any:
        if any(_is_present(normalized.get(name), rules.get(name)) for name in alternatives):
            continue
        names = " / ".join(alternatives)
        raise RequestValidationError(
            f"缺少必填字段：{names}",
            error_code="MISSING_REQUIRED_FIELD",
        )
    return normalized


__all__ = [
    "FieldRule",
    "REQUEST_CONTRACTS",
    "RequestContract",
    "RequestValidationError",
    "validate_request_payload",
]

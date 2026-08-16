from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


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
IMAGE_ACTION = FieldRule(
    "enum",
    choices=frozenset(
        {"upload", "sort", "delete", "replace", "set_main", "set_sku", "filter"}
    ),
)
DRAFT_IMAGE_STRATEGY = FieldRule(
    "enum",
    choices=frozenset(
        {"pool_only", "append", "replace_selected", "replace_all"}
    ),
)


# 这些字段跨多个端点共享相同 JSON 形状。只在调用方实际传入字段时校验，
# 因此配置类端点仍可按白名单消费自己的扩展字段。
_COMMON_FIELD_RULES: dict[str, FieldRule] = {
    "1688_api": OBJECT,
    "appConfig": OBJECT,
    "category_record": OBJECT,
    "common": OBJECT,
    "config": OBJECT,
    "content": OBJECT,
    "dimensions": OBJECT,
    "draft": OBJECT,
    "mock_snapshot": OBJECT,
    "model": OBJECT,
    "order": OBJECT,
    "options": OBJECT,
    "payload": OBJECT,
    "product": OBJECT,
    "provider": OBJECT,
    "replacement": OBJECT,
    "shipment": OBJECT,
    "storeConfig": OBJECT,
    "target": OBJECT,
    "inputs": OBJECT,
    "yunexpress": OBJECT,
    "attributes": ARRAY,
    "categories": ARRAY,
    "draft_ids": STRING_OR_ARRAY,
    "draftIds": STRING_OR_ARRAY,
    "image_ids": STRING_ARRAY,
    "image_pool": ARRAY,
    "images": ARRAY,
    "ids": STRING_ARRAY,
    "mock_tabs": ARRAY,
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
    "draft_id": STRING,
    "draftId": STRING,
    "html": STRING,
    "item_id": STRING,
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
    "limit": LIMIT,
    "port": PORT,
    "apply_to_draft": BOOLEAN,
    "confirm": BOOLEAN,
    "confirm_real_publish": BOOLEAN,
    "delete_files": BOOLEAN,
    "include_bullets": BOOLEAN,
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
_PRODUCT_OR_DRAFT = _contract(
    required_any=(("product_id", "draft_id", "draftId"),)
)
_SHIPMENT = _contract(
    required_any=(("shipment", "order", "payload"),)
)


# 路由键是合同的一部分。即使端点当前没有额外必填项，也显式登记，便于新增
# 写入口时由测试发现遗漏，而不是悄悄退化到“任意 dict”。
REQUEST_CONTRACTS: dict[str, RequestContract] = {
    "/api/ai-config/save": _EMPTY,
    "/api/v1/ai-presentations": _EMPTY,
    "/api/assign-upc": _EMPTY,
    "/api/browser-debug/open-profile": _EMPTY,
    "/api/calculate-price": _EMPTY,
    "/api/global-task-start": _contract(required=("goal",)),
    "/api/global-task-state": _contract(required=("task_id",)),
    "/api/global-task-input": _contract(
        required=("task_id",),
        required_any=(("message", "inputs"),),
    ),
    "/api/global-task-publish-confirm": _contract(required=("task_id",)),
    "/api/global-task-cancel": _contract(required=("task_id",)),
    "/api/category-ai-fill": _contract(
        required_any=(
            ("product_id", "draft_id", "draftId"),
            ("category_id", "category_record"),
        )
    ),
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
    "/api/category-search": _contract(
        required_any=(("query", "keyword"),)
    ),
    "/api/claim-products": _contract(required=("product_ids",)),
    "/api/collect-1688": _contract(required=("url",)),
    "/api/collect-1688-clean": _contract(
        required_any=(("text", "html"),)
    ),
    "/api/collect-batch": _contract(
        required_any=(("urls", "url"),)
    ),
    "/api/collect-extension-payload": _EMPTY,
    "/api/collect-from-browser-tab": _EMPTY,
    "/api/collect-source": _contract(required=("url",)),
    "/api/delete-draft": _contract(
        required_any=(("draft_ids", "draftIds", "draft_id", "draftId"),)
    ),
    "/api/delete-products": _contract(required=("product_ids",)),
    "/api/generate-copy": _PRODUCT_OR_DRAFT,
    "/api/generate-copy-batch": _contract(required=("product_ids",)),
    "/api/generate-image-prompts": _PRODUCT,
    "/api/image-edit": _contract(
        required=("product_id", "prompt", "source_image_ids")
    ),
    "/api/image-pool/action": _contract(
        fields={"action": IMAGE_ACTION},
        required=("product_id", "action"),
    ),
    "/api/image-pool/save": _PRODUCT,
    "/api/image-pool/sync-generated": _PRODUCT,
    "/api/image-pool/upload": _contract(
        required=("product_id", "uploads")
    ),
    "/api/image-translate": _contract(
        required=("product_id", "source_image_ids")
    ),
    "/api/load-draft": _DRAFT,
    "/api/load-product": _contract(
        required_any=(("product_id", "product_file_path"),)
    ),
    "/api/logistics/yunexpress/create-shipment": _SHIPMENT,
    "/api/logistics/yunexpress/preview": _SHIPMENT,
    "/api/mercadolibre/auth-checklist": _EMPTY,
    "/api/mercadolibre/auth-link": _contract(
        required=("app_id", "redirect_uri")
    ),
    "/api/mercadolibre/close-item": _contract(
        fields={"id": STRING},
        required_any=(("item_id", "id"),)
    ),
    "/api/mercadolibre/confirm-real-publish": _PRODUCT,
    "/api/mercadolibre/exchange-code": _contract(
        required_any=(("code_or_url", "code"),)
    ),
    "/api/mercadolibre/notifications": _EMPTY,
    "/api/mercadolibre/real-auth-test": _PRODUCT,
    "/api/mercadolibre/refresh-token": _EMPTY,
    "/api/open-1688-browser": _EMPTY,
    "/api/open-auth-link": _contract(required=("url",)),
    "/api/publish-bus/enqueue": _DRAFT,
    "/api/publish-payload-preview": _DRAFT,
    "/api/publish-precheck": _DRAFT,
    "/api/publish-product": _PRODUCT,
    "/api/save-draft": _contract(
        required_any=(("draft", "draft_id", "draftId"),)
    ),
    "/api/save-product": _contract(required=("product",)),
    "/api/save-settings": _EMPTY,
    "/api/store-auth/clear": _contract(required=("platform",)),
    "/api/test-ai-model": _contract(
        required_any=(("model", "config"),)
    ),
    "/api/test-api-config": _contract(required=("kind",)),
    "/api/test-store-auth": _contract(required=("platform",)),
    "/api/text-translate": _contract(
        required=("target_language", "content")
    ),
    "/api/upc-pool/import": _contract(required=("values",)),
    "/api/v1/ai-chat/runs": _contract(
        fields={
            "trigger": STRING,
            "id": STRING,
            "messages": ARRAY,
        },
        required=("id", "messages"),
    ),
    "/api/v1/product-research/hot-products/search": _EMPTY,
    "/api/v1/product-research/search-providers/test": _contract(
        required=("provider",)
    ),
    "/api/v1/product-research/source-registry/save": _EMPTY,
}


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        return bool(value)
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
        if isinstance(value, list) and all(
            isinstance(item, str) for item in value
        ):
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
        if not _is_present(normalized.get(field_name)):
            raise RequestValidationError(
                f"缺少必填字段：{field_name}",
                error_code="MISSING_REQUIRED_FIELD",
            )
    for alternatives in contract.required_any:
        if any(_is_present(normalized.get(name)) for name in alternatives):
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

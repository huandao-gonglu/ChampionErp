from __future__ import annotations

from typing import Any, TypedDict


API_SCHEMA_VERSION = 1


class ApiResponse(TypedDict, total=False):
    schemaVersion: int
    ok: bool
    error: str
    error_code: str
    message: str
    data: Any
    items: list[Any]
    product: dict[str, Any]
    productsIndex: list[dict[str, Any]]
    draftsIndex: list[dict[str, Any]]
    task: dict[str, Any]


class AppStateResponse(ApiResponse, total=False):
    schemaVersion: int
    appConfig: dict[str, Any]
    storeConfig: dict[str, Any]
    storeAuthSummary: dict[str, Any]
    mercadolibreAuthChecklist: dict[str, Any]
    imagePool: list[dict[str, Any]]
    sourceImages: list[str]
    generatedImages: list[dict[str, Any]]
    publishLogs: list[dict[str, Any]]
    mercadolibreOrderNotifications: list[dict[str, Any]]
    platformOptions: list[dict[str, Any]]
    outputDir: str


def validate_app_state_response(payload: dict[str, Any]) -> AppStateResponse:
    """对 `/api/state` 的稳定契约做运行时校验。

    TypedDict 继续服务静态类型；这个入口确保路由不会悄悄发出缺字段、错类型或
    未标版本的 god-object。
    """

    if not isinstance(payload, dict):
        raise TypeError("state response 必须是对象")
    if payload.get("schemaVersion") != API_SCHEMA_VERSION:
        raise ValueError(
            f"state response schemaVersion 必须为 {API_SCHEMA_VERSION}"
        )
    if payload.get("ok") is not True:
        raise ValueError("state response ok 必须为 true")
    object_fields = (
        "product",
        "appConfig",
        "storeConfig",
        "storeAuthSummary",
        "mercadolibreAuthChecklist",
    )
    list_fields = (
        "imagePool",
        "sourceImages",
        "generatedImages",
        "publishLogs",
        "mercadolibreOrderNotifications",
        "productsIndex",
        "draftsIndex",
        "platformOptions",
    )
    for field in object_fields:
        if not isinstance(payload.get(field), dict):
            raise TypeError(f"state response {field} 必须是对象")
    for field in list_fields:
        if not isinstance(payload.get(field), list):
            raise TypeError(f"state response {field} 必须是数组")
    if not isinstance(payload.get("outputDir"), str):
        raise TypeError("state response outputDir 必须是字符串")
    return payload


__all__ = [
    "API_SCHEMA_VERSION",
    "ApiResponse",
    "AppStateResponse",
    "validate_app_state_response",
]

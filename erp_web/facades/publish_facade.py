"""发布 HTTP 输入适配器。

业务编排位于 :mod:`erp_web.runtime_units.publish_workflows`。facade 只保留
HTTP payload 入口，不再平移旧 runtime 持久化包装。
"""

from __future__ import annotations

from typing import Any

from erp_web.runtime_units import publish_workflows
from erp_web.runtime_units.draft_publish_context import (
    load_required_draft_publish_context,
)
from erp_web.schemas.api import ApiResponse

ResponseWithStatus = tuple[ApiResponse, int]


def precheck_publish_payload(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.precheck_publish_payload(body)


def preview_publish_payload(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.preview_publish_payload(body)


def publish_product_payload(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.publish_product_payload(body)


def confirm_mercadolibre_real_publish(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.confirm_mercadolibre_real_publish(body)


def close_mercadolibre_item(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.close_mercadolibre_item(body)


def enqueue_publish_job(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.enqueue_publish_job(body)


__all__ = [
    "close_mercadolibre_item",
    "confirm_mercadolibre_real_publish",
    "enqueue_publish_job",
    "precheck_publish_payload",
    "preview_publish_payload",
    "publish_product_payload",
]

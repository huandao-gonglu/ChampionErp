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


def pause_mercadolibre_user_product(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.pause_mercadolibre_user_product(body)


def reconcile_publish_job(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.reconcile_publish_job(body)


def enqueue_publish_job(body: dict[str, Any]) -> ResponseWithStatus:
    return publish_workflows.enqueue_publish_job(body)


__all__ = [
    "enqueue_publish_job",
    "pause_mercadolibre_user_product",
    "precheck_publish_payload",
    "preview_publish_payload",
    "publish_product_payload",
    "reconcile_publish_job",
]

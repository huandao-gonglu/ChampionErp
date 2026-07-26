# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import threading
from typing import Any

from erp_web.runtime_units.publishing_bus_core import PublishingBus

from .product_store import normalize_product_fields
from .runtime_api import publish_product
from .runtime_common import PUBLISHING_JOB_DIR

logger = logging.getLogger(__name__)

class ProjectPublishingAdapter:
    def resolve_category(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        product = normalize_product_fields(product)
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        platform_category = local_categories.get(platform) if isinstance(local_categories, dict) else None
        if isinstance(platform_category, dict):
            category_id = str(platform_category.get("category_id") or platform_category.get("platform_category_id") or "").strip()
        else:
            category_id = str(platform_category or "").strip()
        if not category_id:
            if platform == "mercadolibre":
                category_id = str(product.get("category_id") or config.get("mercadolibre", {}).get("category_id") or "").strip()
            elif platform == "yandex":
                category_id = str(product.get("yandex_category_id") or config.get("yandex", {}).get("category_id") or "").strip()
            elif platform == "ozon":
                category_id = str(product.get("ozon_category_id") or config.get("ozon", {}).get("category_id") or "").strip()
        if category_id:
            if platform == "mercadolibre":
                product["category_id"] = category_id
            elif platform == "yandex":
                product["yandex_category_id"] = category_id
            elif platform == "ozon":
                product["ozon_category_id"] = category_id
        return product

    def validate_required_attributes(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> list[str]:
        return []

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        return publish_product(product, platform, config)


# Singleton state lives in a mutable container on purpose: erp_web.runtime's
# namespace-injection mechanism (_sync_runtime_units) re-copies stale module
# globals into this module on every wrapped call, so a rebound module-level
# variable would be clobbered back to None. The dict is shared by reference, so
# mutations survive the injection. Collapse this back to a plain module global
# once the runtime.py aggregator is removed.
_BUS_STATE: dict[str, PublishingBus | None] = {"bus": None}
_PUBLISHING_BUS_LOCK = threading.Lock()


def get_publishing_bus() -> PublishingBus:
    """Return the process-wide publishing bus, creating it lazily.

    The bus used to be instantiated at import time with ``auto_resume_pending=True``,
    which meant merely importing this module (e.g. from tests or tooling) could
    replay real pending publish jobs. Creation is now deferred to first use and
    pending-job recovery must be triggered explicitly via
    :func:`resume_pending_publish_jobs` (done by ``erp_web.server.main``).
    """
    bus = _BUS_STATE.get("bus")
    if bus is None:
        with _PUBLISHING_BUS_LOCK:
            bus = _BUS_STATE.get("bus")
            if bus is None:
                bus = PublishingBus(
                    PUBLISHING_JOB_DIR,
                    adapters={
                        "mercadolibre": ProjectPublishingAdapter(),
                        "yandex": ProjectPublishingAdapter(),
                        "ozon": ProjectPublishingAdapter(),
                    },
                    auto_resume_pending=False,
                )
                _BUS_STATE["bus"] = bus
    return bus


def resume_pending_publish_jobs() -> None:
    """Explicitly recover publish jobs left queued/running by a previous run."""
    try:
        get_publishing_bus().recover_pending_jobs()
    except Exception:
        logger.exception("Failed to resume pending publish jobs")


__all__ = [
    "ProjectPublishingAdapter",
    "get_publishing_bus",
    "resume_pending_publish_jobs",
]

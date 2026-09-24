"""SKU 批量核价：本轮共享资料，每个 SKU × 市场独立报价。"""
from __future__ import annotations

import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from erp_web.schemas.pricing_batch import (
    PricingBatchMetrics,
    PricingBatchResult,
    SkuPricingResult,
    validate_pricing_items,
)
from .pricing_runtime import PricingSession

logger = logging.getLogger("erp.pricing")


def calculate_sku_prices(body: dict[str, Any]) -> PricingBatchResult:
    items = validate_pricing_items(body)
    batch_id = uuid4().hex[:12]
    started = perf_counter()
    target_count = sum(len(item["input"]["targets"]) for item in items)
    logger.info("核价批次 %s 开始：%d 个 SKU，%d 项市场报价", batch_id, len(items), target_count)
    session = PricingSession()
    results: list[SkuPricingResult] = []
    failed = 0
    for index, item in enumerate(items, 1):
        result = session.calculate(item["input"])
        if not result.get("ok"):
            failed += 1
            messages = [str(error.get("message") or "") for error in result.get("errors", []) if isinstance(error, dict)]
            for target in result.get("results", []):
                messages.extend(str(error.get("message") or "") for error in target.get("errors", []) if isinstance(error, dict))
            logger.warning("核价批次 %s SKU %s 失败：%s", batch_id, item["sku_id"], result.get("error") or "；".join(dict.fromkeys(messages)))
        results.append({"sku_id": item["sku_id"], "result": result})
        if index == 1 or index % 25 == 0 or index == len(items):
            logger.info("核价批次 %s 进度 %d/%d，失败 %d，已用 %.2f 秒", batch_id, index, len(items), failed, perf_counter() - started)
    metrics: PricingBatchMetrics = {
        "batch_id": batch_id,
        "sku_count": len(items),
        "target_count": target_count,
        "failed_sku_count": failed,
        "duration_ms": round((perf_counter() - started) * 1000, 1),
        "ozon_discovery_ms": round(session.shipping.module.ozon_discovery_ms, 1),
    }
    logger.info("核价批次 %s 完成：%d 个 SKU，%d 项市场报价，失败 %d，总耗时 %.2f 秒，Ozon 公共渠道查询 %.2f 秒", batch_id, len(items), target_count, failed, metrics["duration_ms"] / 1000, metrics["ozon_discovery_ms"] / 1000)
    return {"ok": True, "items": results, "metrics": metrics}


__all__ = ["calculate_sku_prices"]

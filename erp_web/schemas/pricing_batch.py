"""整批 SKU 核价的内部引擎输入与结果契约。"""
from typing import Any, TypedDict

from .requests import RequestValidationError


class SkuPricingInput(TypedDict):
    sku_id: str
    input: dict[str, Any]


class SkuPricingResult(TypedDict):
    sku_id: str
    result: dict[str, Any]


class PricingBatchMetrics(TypedDict):
    batch_id: str
    sku_count: int
    target_count: int
    failed_sku_count: int
    duration_ms: float
    ozon_discovery_ms: float


class PricingBatchResult(TypedDict):
    ok: bool
    items: list[SkuPricingResult]
    metrics: PricingBatchMetrics


def validate_pricing_items(body: dict[str, Any]) -> list[SkuPricingInput]:
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise RequestValidationError("核价必须提供非空的 SKU items 数组。")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise RequestValidationError("核价 items 中每项必须是对象。")
        sku_id = item.get("sku_id")
        if not isinstance(sku_id, str) or not sku_id.strip() or sku_id in seen:
            raise RequestValidationError("每个核价 SKU 必须有唯一、非空的 sku_id。")
        seen.add(sku_id)
        source = item.get("input")
        if not isinstance(source, dict):
            raise RequestValidationError(f"SKU {sku_id} 缺少核价 input 对象。")
        targets = source.get("targets")
        if not isinstance(targets, list) or not targets or not all(isinstance(target, dict) for target in targets):
            raise RequestValidationError(f"SKU {sku_id} 必须提供非空的目标市场对象数组。")
    return items

"""原生 Agent 纵向验收使用的隔离商品和平台 mock。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


from erp_web.marketplaces.category_provider import CategoryProvider
from erp_web.runtime_units.category_definition_support import (
    definition_from_legacy_attributes,
    paginate_value_candidates,
)
from erp_web.runtime_units.publish_adapter import OzonPublishingAdapter
from erp_web.schemas.category_definition import (
    CategoryAttributeValuePage,
    CategoryDefinition,
    CategoryDetail,
)
from erp_web.services.listing_currency_service import compute_currency_fingerprint
from erp_web.services.pricing_service import pricing_calculation_fingerprint

#: 纵向流程 Ozon 店铺的发布币种指纹（身份 client_id=vertical-client）。
_VERTICAL_STORE_CURRENCY_FINGERPRINT = compute_currency_fingerprint(
    "ozon", "vertical-client", "RUB", ["RUB"], "locked", "account_api"
)


class _PlatformNetworkBoundary(OzonPublishingAdapter):
    """保留真实 Ozon 确定性逻辑，只替代最终外部网络提交。"""

    def __init__(self, *, succeed: bool) -> None:
        self.succeed = succeed
        self.publish_calls = 0

    def publish(
        self,
        product: dict[str, Any],
        platform: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        raise AssertionError("确认发布不得重新从 product 构建外发 payload")

    def publish_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.publish_calls += 1
        assert payload["items"][0]["description_category_id"] == 17027949
        assert config["ozon"]["client_id"] == "vertical-client"
        if self.succeed:
            return {
                "ok": True,
                "status": "real_publish_success",
                "external_id": "ozon-vertical-item",
            }
        return {
            "ok": False,
            "status": "real_publish_failed",
            "error": "平台拒绝纵向测试商品",
            "error_code": "VERTICAL_REMOTE_REJECTED",
        }


class _FakeCategoryProvider(CategoryProvider):
    """类目明细边界的可信替代：只覆盖 detail 与定义读取。"""

    platform = "ozon"

    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self.records = records
        self.detail_calls: list[tuple[str, str, bool]] = []

    def _record(self, category_id: str) -> dict[str, Any]:
        record = self.records.get(str(category_id))
        if record is None:
            raise ValueError(f"未找到类目 {category_id}")
        return deepcopy(record)

    def resolve_site(self, site: str = "") -> str:
        return "global"

    def category_detail(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDetail:
        self.detail_calls.append((category_id, site, False))
        record = self._record(category_id)
        return CategoryDetail(
            platform=self.platform,
            site="global",
            category_id=str(record.get("category_id") or category_id),
            path=str(record.get("category_path") or ""),
            is_leaf=True,
        )

    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDefinition:
        self.detail_calls.append((category_id, site, True))
        record = self._record(category_id)
        attributes = (
            record.get("attributes")
            if isinstance(record.get("attributes"), dict)
            else {}
        )
        return definition_from_legacy_attributes(
            platform=self.platform,
            site="global",
            category_id=str(record.get("category_id") or category_id),
            category_path=str(record.get("category_path") or ""),
            description_category_id=str(record.get("description_category_id") or ""),
            required=list(attributes.get("required") or []),
            optional=list(attributes.get("optional") or []),
        )

    def attribute_values(
        self,
        category_id: str,
        attribute_id: str,
        *,
        site: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> CategoryAttributeValuePage:
        return paginate_value_candidates(
            [],
            platform=self.platform,
            site="global",
            category_id=str(category_id),
            attribute_id=str(attribute_id),
            query=query,
            cursor=cursor,
            limit=limit,
        )


def _category_record() -> dict[str, Any]:
    return {
        "category_id": "94765",
        "description_category_id": "17027949",
        "category_path": "Электроника / Вентиляторы",
        "platform": "ozon",
        "site": "global",
        "source": "vertical-test-boundary",
        "attributes": {
            "required": [
                {
                    "id": "85",
                    "name": "Бренд",
                    "required": True,
                    "dictionary_id": 0,
                    "raw": {"dictionary_id": 0},
                },
                {
                    "id": "4191",
                    "name": "Аннотация",
                    "required": True,
                },
            ],
            "optional": [],
        },
    }


def _source_product() -> dict[str, Any]:
    basis = {
        "cost_cny": "100",
        "listing_currency": "RUB",
        "currency_fingerprint": _VERTICAL_STORE_CURRENCY_FINGERPRINT,
        "length_cm": "12.3",
        "width_cm": "4.5",
        "height_cm": "6.7",
        "weight_kg": "0.25",
    }
    return {
        "product_id": "product-vertical-ozon",
        "name": "Portable fan",
        "brand": "Champion",
        "model": "V1",
        "sku": "OZON-VERTICAL-1",
        "cost": "100",
        "stock": "5",
        "upc": "123456789012",
        "source": {
            "title": "Portable fan",
            "description": "Source description",
            "source_platform": "1688",
            "source_url": "https://example.com/vertical-product",
            "currency": "CNY",
            "price": "100",
            "weight_kg": "0.25",
            "dimensions": {
                "length_cm": "12.3",
                "width_cm": "4.5",
                "height_cm": "6.7",
            },
            "image_pool": [
                {
                    "id": "image-vertical-1",
                    "url": "https://cdn.example.com/vertical-ozon.jpg",
                    "origin": "source",
                    "status": "ready",
                    "selected": True,
                    "is_main": True,
                    "order": 0,
                    "platforms": ["ozon"],
                }
            ],
        },
        "drafts": {
            "ozon": {
                "enabled": True,
                "platform": "ozon",
                "platforms": ["ozon"],
                "site": "global",
                "language": "ru-RU",
                "target_sites": [
                    {
                        "platform": "ozon",
                        "site": "global",
                        "language": "ru-RU",
                        "listing_currency": "RUB",
                    }
                ],
                "title": "Portable fan",
                "description": "Source description",
                "brand": "Champion",
                "model": "V1",
                "sku": "OZON-VERTICAL-1",
                "upc": "123456789012",
                "stock": "5",
                "vat": "0",
                "images": [],
                "attributes": {},
                "package_dimensions": {
                    "length_cm": "12.3",
                    "width_cm": "4.5",
                    "height_cm": "6.7",
                    "weight_kg": "0.25",
                },
                "pricing": {
                    "targets": {
                        "ozon:global": {
                            "listing_currency": "RUB",
                            "suggested_price": {
                                "amount": "1999.90",
                                "currency": "RUB",
                            },
                            "applied_price": {
                                "amount": "1999.90",
                                "currency": "RUB",
                            },
                            "calculation_basis": basis,
                            "calculation_fingerprint": (
                                pricing_calculation_fingerprint(basis)
                            ),
                        }
                    }
                },
                "status": "claimed",
            }
        },
    }

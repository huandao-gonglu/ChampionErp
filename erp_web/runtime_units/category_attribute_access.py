"""属性查询与写入共用的纯作用域规则，不读取数据或调用平台。"""

from typing import Any, Mapping, Literal

from erp_web.schemas.category_grouping import is_listing_grouping_attribute
from erp_web.services.mercadolibre_attribute_contract import MERCADOLIBRE_DERIVED_ATTRIBUTE_IDS


def attribute_write_scope(
    platform: str, definition: Mapping[str, Any],
) -> Literal["common", "sku", "managed", "read_only"]:
    if is_listing_grouping_attribute(platform, definition) or definition.get("managed_by"):
        return "managed"
    if platform == "mercadolibre" and definition.get("id") in MERCADOLIBRE_DERIVED_ATTRIBUTE_IDS:
        return "managed"
    if definition.get("read_only"):
        return "read_only"
    return "sku" if definition.get("variation_role") == "variant" else "common"


__all__ = ["attribute_write_scope"]

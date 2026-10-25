"""在线 Yandex 属性读取与校验；类目事实只由注入的 Catalog 提供。"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from erp_web.schemas.category import category_attribute_text_format_error
from erp_web.schemas.category_grouping import is_listing_grouping_attribute
from erp_web.product_model.online_attribute_model import yandex_category_id


def _definition(catalog, listing):
    category = yandex_category_id(listing)
    definition = catalog.attribute_definitions("yandex", category, site="global")
    if definition.platform != "yandex" or definition.category_id != category or definition.cache.stale:
        raise ValueError("类目属性规则不可用或已过期，请重新读取后再修改")
    return definition


def _candidate(catalog, category, attribute, row):
    """核对完整候选中的 ID 与原文；不能把预览缺失当作非法选项。"""
    cursor, seen = "", set()
    for _ in range(100):
        page = catalog.attribute_values("yandex", category, attribute.id, site="global",
                                        query=row["value"], cursor=cursor, limit=100)
        if (page.platform, page.category_id, page.attribute_id) != ("yandex", category, attribute.id):
            raise ValueError("平台返回的属性候选身份不一致")
        if any(option.dictionary_value_id == str(row["valueId"]) and option.value == row["value"]
               for option in page.values):
            return
        if not page.has_more or not page.next_cursor or page.next_cursor in seen:
            break
        cursor = page.next_cursor
        seen.add(cursor)
    raise ValueError(f"{attribute.name} 的选项编号与值无法核实，请重新选择平台候选")


def validate_yandex_attributes(listing, rows, catalog):
    if catalog is None:
        raise ValueError("缺少类目属性校验服务")
    definition = _definition(catalog, listing)
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row["id"])].append(row)
    for key, values in grouped.items():
        attribute = definition.attribute_by_id(key)
        if (attribute is None or attribute.read_only
                or is_listing_grouping_attribute("yandex", {"id": key})):
            raise ValueError(f"属性 {key} 不属于当前类目的可编辑属性")
        if ((not attribute.is_collection and len(values) > 1)
                or (attribute.max_value_count and len(values) > attribute.max_value_count)):
            raise ValueError(f"{attribute.name} 的取值数量超过平台限制")
        identities = set()
        for row in values:
            value = row.get("value")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"请填写 {attribute.name}，不能提交空值")
            identity = (str(row.get("valueId") or ""), value, row.get("unitId"))
            if identity in identities:
                raise ValueError(f"{attribute.name} 不能重复选择同一个值")
            identities.add(identity)
            enum = attribute.value_type.lower() == "enum" or attribute.is_dictionary
            if "valueId" in row:
                if not enum or row["valueId"] is None:
                    raise ValueError(f"{attribute.name} 的枚举编号无效")
                _candidate(catalog, definition.category_id, attribute, row)
            elif enum and not attribute.allow_custom_values:
                raise ValueError(f"{attribute.name} 必须选择平台候选值")
            units = {unit.id for unit in attribute.unit_options} | set(attribute.unit_ids)
            if attribute.default_unit_id:
                units.add(attribute.default_unit_id)
            if units:
                if type(row.get("unitId")) is not int or str(row["unitId"]) not in units:
                    raise ValueError(f"请选择 {attribute.name} 的有效单位")
            elif "unitId" in row:
                raise ValueError(f"{attribute.name} 不接受单位")
            constraints = attribute.constraints
            if len(value) > int(constraints.get("maxLength") or 2000):
                raise ValueError(f"{attribute.name} 超过平台允许的长度")
            if attribute.value_type.lower() in {"numeric", "number", "integer", "float"}:
                try:
                    number = Decimal(value)
                    if not number.is_finite():
                        raise InvalidOperation
                    if attribute.value_type == "integer" and number != number.to_integral_value():
                        raise InvalidOperation
                    if ("minValue" in constraints and number < Decimal(constraints["minValue"])) or (
                            "maxValue" in constraints and number > Decimal(constraints["maxValue"])):
                        raise InvalidOperation
                except InvalidOperation as exc:
                    raise ValueError(f"{attribute.name} 必须是平台允许范围内的数值") from exc
            if attribute.value_type.lower() == "boolean" and value not in {"true", "false"}:
                raise ValueError(f"{attribute.name} 必须选择 true 或 false")
            format_error = category_attribute_text_format_error({"constraints": constraints}, value)
            if format_error:
                raise ValueError(f"{attribute.name}：{format_error}")

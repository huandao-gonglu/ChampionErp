from __future__ import annotations

"""Mercado Libre 类目属性的纯校验与 wire 编译契约。"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from erp_web.schemas.category_definition import (
    CategoryAttributeDefinition,
    CategoryDefinition,
)
from erp_web.services.mercadolibre_listing_model import (
    MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS,
)


_PACKAGE_FIELDS: dict[str, tuple[str, str]] = {
    "PACKAGE_LENGTH": ("length_cm", "cm"),
    "PACKAGE_WIDTH": ("width_cm", "cm"),
    "PACKAGE_HEIGHT": ("height_cm", "cm"),
    "PACKAGE_WEIGHT": ("weight_kg", "g"),
    "SELLER_PACKAGE_LENGTH": ("length_cm", "cm"),
    "SELLER_PACKAGE_WIDTH": ("width_cm", "cm"),
    "SELLER_PACKAGE_HEIGHT": ("height_cm", "cm"),
    "SELLER_PACKAGE_WEIGHT": ("weight_kg", "g"),
}
_ROOT_DRAFT_FIELDS = {
    "BRAND": "brand",
    "MODEL": "model",
}
_SYSTEM_OWNED_ATTRIBUTE_IDS = {
    *_PACKAGE_FIELDS,
    *_ROOT_DRAFT_FIELDS,
    "SELLER_SKU",
    "GTIN",
    "UPC",
    "UNIVERSAL_PRODUCT_CODE",
    "EMPTY_GTIN_REASON",
    "ITEM_CONDITION",
}
_EMPTY_GTIN_REASON_ID = "17055160"
_EMPTY_GTIN_REASON_NAME = "The product does not have registered code"


@dataclass(frozen=True)
class MercadoLibreAttributeIssue:
    code: str
    field: str
    message: str


@dataclass(frozen=True)
class MercadoLibreAttributeCompilation:
    attributes: tuple[dict[str, Any], ...]
    issues: tuple[MercadoLibreAttributeIssue, ...]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _value_present(value: Any) -> bool:
    if isinstance(value, dict):
        if isinstance(value.get("values"), list):
            return bool(value["values"])
        return bool(_text(value.get("value")))
    return bool(_text(value))


def _single_selected_value_name(value: Any) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("values"), list):
        return ""
    selected = [item for item in value["values"] if isinstance(item, dict)]
    if len(selected) != 1:
        return ""
    return _text(selected[0].get("value") or selected[0].get("name"))


def _decimal_text(value: Any) -> str:
    raw = _text(value).replace(",", ".")
    number = Decimal(raw)
    if not number.is_finite() or number <= 0:
        raise InvalidOperation
    normalized = format(number.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _number_text(value: Any) -> str:
    raw = _text(value).replace(",", ".")
    number = Decimal(raw)
    if not number.is_finite():
        raise InvalidOperation
    if number == 0:
        return "0"
    normalized = format(number.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _max_length_issue(
    definition: CategoryAttributeDefinition,
    value: str,
) -> MercadoLibreAttributeIssue | None:
    raw_limit = definition.constraints.get("max_length")
    try:
        limit = int(raw_limit or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit > 0 and len(value) > limit:
        return MercadoLibreAttributeIssue(
            "ATTRIBUTE_VALUE_TOO_LONG",
            f"attributes.{definition.id}",
            f"Mercado Libre 属性 {definition.id} 最多允许 {limit} 个字符",
        )
    return None


def _package_candidate(
    package: dict[str, Any],
    attribute_id: str,
) -> dict[str, str] | None:
    field, unit = _PACKAGE_FIELDS[attribute_id]
    raw = package.get(field)
    if raw in (None, ""):
        return None
    if field == "weight_kg":
        number = Decimal(_text(raw).replace(",", ".")) * Decimal("1000")
        value = _decimal_text(number)
    else:
        value = _decimal_text(raw)
    return {"value": value, "unit": unit}


def _option_maps(
    definition: CategoryAttributeDefinition,
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    by_name: dict[str, tuple[str, str]] = {}
    by_id: dict[str, tuple[str, str]] = {}
    for option in definition.options:
        value = _text(option.value)
        value_id = _text(option.dictionary_value_id)
        if value:
            by_name[value.casefold()] = (value_id, value)
        if value_id:
            by_id[value_id] = (value_id, value)
    return by_name, by_id


def _wire_option(
    attribute_id: str,
    value_id: str,
    value_name: str,
    *,
    listing_model: str,
) -> dict[str, Any]:
    if (
        listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
        and attribute_id == "ITEM_CONDITION"
    ):
        return {
            "id": attribute_id,
            "values": [{"id": value_id, "name": value_name}],
        }
    return {
        "id": attribute_id,
        "value_id": value_id,
        "value_name": value_name,
    }


def _compile_attribute_value(
    definition: CategoryAttributeDefinition,
    raw_value: Any,
    *,
    listing_model: str,
) -> tuple[dict[str, Any] | None, MercadoLibreAttributeIssue | None]:
    attribute_id = definition.id
    field = f"attributes.{attribute_id}"
    by_name, by_id = _option_maps(definition)
    requires_platform_candidate = bool(
        definition.value_type == "string"
        and not definition.allow_custom_values
        and (by_name or by_id or definition.has_more_values)
    )

    if definition.value_type == "number_unit":
        if not isinstance(raw_value, dict) or isinstance(raw_value.get("values"), list):
            return None, MercadoLibreAttributeIssue(
                "ATTRIBUTE_UNIT_REQUIRED",
                field,
                f"Mercado Libre 属性 {attribute_id} 必须明确填写数值和单位",
            )
        try:
            number = _number_text(raw_value.get("value"))
        except (InvalidOperation, ValueError):
            return None, MercadoLibreAttributeIssue(
                "ATTRIBUTE_NUMBER_INVALID",
                field,
                f"Mercado Libre 属性 {attribute_id} 必须是有限数值",
            )
        unit = _text(raw_value.get("unit"))
        allowed_units = {
            _text(option.name)
            for option in definition.unit_options
            if _text(option.name)
        }
        if not unit:
            return None, MercadoLibreAttributeIssue(
                "ATTRIBUTE_UNIT_REQUIRED",
                field,
                f"Mercado Libre 属性 {attribute_id} 缺少单位",
            )
        if allowed_units and unit not in allowed_units:
            return None, MercadoLibreAttributeIssue(
                "ATTRIBUTE_UNIT_INVALID",
                field,
                f"Mercado Libre 属性 {attribute_id} 不允许单位 {unit}",
            )
        value_name = f"{number} {unit}"
        length_issue = _max_length_issue(definition, value_name)
        if length_issue is not None:
            return None, length_issue
        if definition.is_collection:
            return {
                "id": attribute_id,
                "values": [{"name": value_name}],
            }, None
        return {
            "id": attribute_id,
            "value_name": value_name,
        }, None

    if isinstance(raw_value, dict) and isinstance(raw_value.get("values"), list):
        selected = [item for item in raw_value["values"] if isinstance(item, dict)]
        if not selected:
            return None, MercadoLibreAttributeIssue(
                "ATTRIBUTE_ENUM_VALUE_REQUIRED",
                field,
                f"Mercado Libre 属性 {attribute_id} 必须选择平台枚举值",
            )
        max_value_count = (
            definition.max_value_count
            if definition.max_value_count is not None
            and definition.max_value_count > 0
            else None
        )
        if not definition.is_collection:
            max_value_count = 1
        if max_value_count is not None and len(selected) > max_value_count:
            return None, MercadoLibreAttributeIssue(
                "ATTRIBUTE_ENUM_TOO_MANY_VALUES",
                field,
                f"Mercado Libre 属性 {attribute_id} 最多允许 {max_value_count} 个值",
            )
        compiled_values: list[dict[str, str]] = []
        for item in selected:
            selected_id = _text(
                item.get("dictionary_value_id")
                or item.get("dictionaryValueId")
                or item.get("id")
            )
            selected_name = _text(item.get("value") or item.get("name"))
            known_by_id = by_id.get(selected_id) if selected_id else None
            known_by_name = by_name.get(selected_name.casefold()) if selected_name else None
            if not selected_name:
                return None, MercadoLibreAttributeIssue(
                    "ATTRIBUTE_ENUM_VALUE_INVALID",
                    field,
                    f"Mercado Libre 属性 {attribute_id} 的枚举值缺少文案",
                )
            if (
                known_by_id
                and known_by_id[1].casefold() != selected_name.casefold()
            ):
                return None, MercadoLibreAttributeIssue(
                    "ATTRIBUTE_ENUM_VALUE_INVALID",
                    field,
                    f"Mercado Libre 属性 {attribute_id} 的枚举 ID 与文案不匹配",
                )
            if (
                known_by_name
                and selected_id
                and known_by_name[0]
                and selected_id != known_by_name[0]
            ):
                return None, MercadoLibreAttributeIssue(
                    "ATTRIBUTE_ENUM_VALUE_INVALID",
                    field,
                    f"Mercado Libre 属性 {attribute_id} 的枚举 ID 与文案不匹配",
                )
            if (
                selected_id
                and not known_by_id
                and by_id
                and not definition.has_more_values
            ):
                return None, MercadoLibreAttributeIssue(
                    "ATTRIBUTE_ENUM_VALUE_INVALID",
                    field,
                    f"Mercado Libre 属性 {attribute_id} 的枚举 ID 不属于当前类目",
                )
            resolved_id = selected_id or (known_by_name[0] if known_by_name else "")
            resolved_name = (
                known_by_id[1]
                if known_by_id
                else known_by_name[1]
                if known_by_name
                else selected_name
            )
            if (
                requires_platform_candidate
                and known_by_id is None
                and known_by_name is None
                and not (selected_id and definition.has_more_values)
            ):
                return None, MercadoLibreAttributeIssue(
                    "ATTRIBUTE_ENUM_VALUE_INVALID",
                    field,
                    f"Mercado Libre 属性 {attribute_id} 必须使用平台候选值",
                )
            if definition.value_type in {"list", "boolean"} and not resolved_id:
                return None, MercadoLibreAttributeIssue(
                    "ATTRIBUTE_ENUM_VALUE_INVALID",
                    field,
                    f"Mercado Libre 属性 {attribute_id} 必须使用当前类目的平台枚举值",
                )
            length_issue = _max_length_issue(definition, resolved_name)
            if length_issue is not None:
                return None, length_issue
            compiled_values.append({
                **({"id": resolved_id} if resolved_id else {}),
                "name": resolved_name,
            })
        # collection 在草稿中无论当前只有一项还是多项，wire 都保持稳定的
        # values 数组；不能因用户删到一项就改变 payload shape。
        if definition.is_collection:
            return {"id": attribute_id, "values": compiled_values}, None
        if len(compiled_values) == 1 and compiled_values[0].get("id"):
            value = compiled_values[0]
            return _wire_option(
                attribute_id,
                value["id"],
                value["name"],
                listing_model=listing_model,
            ), None
        return {
            "id": attribute_id,
            "value_name": compiled_values[0]["name"],
        }, None

    if isinstance(raw_value, (dict, list, tuple, set)):
        return None, MercadoLibreAttributeIssue(
            "ATTRIBUTE_VALUE_INVALID",
            field,
            f"Mercado Libre 属性 {attribute_id} 必须填写单个文本值",
        )

    if definition.value_type in {"number", "integer", "float"}:
        try:
            value_name = _number_text(raw_value)
        except (InvalidOperation, ValueError):
            return None, MercadoLibreAttributeIssue(
                "ATTRIBUTE_NUMBER_INVALID",
                field,
                f"Mercado Libre 属性 {attribute_id} 必须是有限数值",
            )
        if definition.value_type == "integer" and "." in value_name:
            return None, MercadoLibreAttributeIssue(
                "ATTRIBUTE_NUMBER_INVALID",
                field,
                f"Mercado Libre 属性 {attribute_id} 必须是整数",
            )
    else:
        value_name = _text(raw_value)
    known = by_name.get(value_name.casefold())
    if known:
        if definition.is_collection:
            return {
                "id": attribute_id,
                "values": [
                    {
                        **({"id": known[0]} if known[0] else {}),
                        "name": known[1],
                    }
                ],
            }, None
        if known[0]:
            return _wire_option(
                attribute_id,
                known[0],
                known[1],
                listing_model=listing_model,
            ), None
        return {"id": attribute_id, "value_name": known[1]}, None
    if definition.value_type in {"list", "boolean"}:
        return None, MercadoLibreAttributeIssue(
            "ATTRIBUTE_ENUM_VALUE_INVALID",
            field,
            f"Mercado Libre 属性 {attribute_id} 必须使用当前类目的平台枚举值",
        )
    if requires_platform_candidate:
        return None, MercadoLibreAttributeIssue(
            "ATTRIBUTE_ENUM_VALUE_INVALID",
            field,
            f"Mercado Libre 属性 {attribute_id} 必须使用平台候选值",
        )
    length_issue = _max_length_issue(definition, value_name)
    if length_issue is not None:
        return None, length_issue
    if definition.is_collection:
        return {
            "id": attribute_id,
            "values": [{"name": value_name}],
        }, None
    return {"id": attribute_id, "value_name": value_name}, None


def compile_mercadolibre_attributes(
    draft: dict[str, Any],
    definition: CategoryDefinition,
    *,
    listing_model: str,
) -> MercadoLibreAttributeCompilation:
    """只从当前 Mercado 草稿和当次类目定义生成属性，不读取商品来源属性。"""

    definitions = {
        attribute.id: attribute
        for attribute in (*definition.required, *definition.optional)
    }
    draft_attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    issues: list[MercadoLibreAttributeIssue] = []
    candidates: dict[str, Any] = {}

    for raw_id, value in draft_attributes.items():
        attribute_id = _text(raw_id)
        if not attribute_id or not _value_present(value):
            continue
        # 旧草稿可能持有由程序派生的影子字段；这些字段无论当前定义是否返回，
        # 都不能作为用户类目属性提交，也不应被当成未知业务属性阻断。
        if attribute_id in _SYSTEM_OWNED_ATTRIBUTE_IDS:
            continue
        attribute = definitions.get(attribute_id)
        if attribute is None:
            issues.append(
                MercadoLibreAttributeIssue(
                    "ATTRIBUTE_NOT_IN_CATEGORY",
                    f"attributes.{attribute_id}",
                    f"属性 {attribute_id} 不属于当前 Mercado Libre 类目",
                )
            )
            continue
        if attribute.read_only:
            # 平台规则可能把历史可编辑字段改成只读；wire 必须丢弃旧值，
            # 但不能产生一个前端无法编辑、也无法消除的阻断错误。
            continue
        candidates[attribute_id] = value

    for attribute_id, draft_field in _ROOT_DRAFT_FIELDS.items():
        if attribute_id in definitions and not _value_present(candidates.get(attribute_id)):
            root_value = draft.get(draft_field)
            structured_value = draft_attributes.get(attribute_id)
            # BRAND/MODEL 的业务文案仍由草稿根字段所有；平台选择 ID 只作为
            # 同名结构化元数据使用。文案不一致说明元数据已经陈旧，必须忽略，
            # 再按当前根文案执行候选校验。
            if (
                _text(root_value)
                and _single_selected_value_name(structured_value)
                == _text(root_value)
            ):
                candidates[attribute_id] = structured_value
            else:
                candidates[attribute_id] = root_value

    if "SELLER_SKU" in definitions:
        candidates["SELLER_SKU"] = draft.get("sku")

    gtin = _text(draft.get("upc"))
    gtin_attribute_id = next(
        (
            attribute_id
            for attribute_id in ("GTIN", "UPC", "UNIVERSAL_PRODUCT_CODE")
            if attribute_id in definitions
        ),
        "",
    )
    if gtin and gtin_attribute_id:
        candidates[gtin_attribute_id] = gtin
    elif not gtin and bool(draft.get("allow_gtin_exemption")):
        empty_definition = definitions.get("EMPTY_GTIN_REASON")
        if empty_definition is not None:
            empty_option = next(
                (
                    option
                    for option in empty_definition.options
                    if option.dictionary_value_id == _EMPTY_GTIN_REASON_ID
                    or option.value.casefold() == _EMPTY_GTIN_REASON_NAME.casefold()
                ),
                None,
            )
            if empty_option is None:
                issues.append(
                    MercadoLibreAttributeIssue(
                        "EMPTY_GTIN_REASON_UNAVAILABLE",
                        "attributes.EMPTY_GTIN_REASON",
                        "当前类目没有可用于 GTIN 豁免的平台原因",
                    )
                )
            else:
                candidates["EMPTY_GTIN_REASON"] = {
                    "values": [
                        {
                            "dictionary_value_id": empty_option.dictionary_value_id,
                            "value": empty_option.value,
                        }
                    ]
                }

    package = (
        draft.get("package_dimensions")
        if isinstance(draft.get("package_dimensions"), dict)
        else {}
    )
    for attribute_id in _PACKAGE_FIELDS:
        if attribute_id not in definitions:
            continue
        try:
            candidate = _package_candidate(package, attribute_id)
        except (InvalidOperation, ValueError):
            field = _PACKAGE_FIELDS[attribute_id][0]
            issues.append(
                MercadoLibreAttributeIssue(
                    "PACKAGE_DIMENSION_INVALID",
                    f"package_dimensions.{field}",
                    f"包装字段 {field} 必须是大于 0 的数值",
                )
            )
            continue
        if candidate is not None:
            candidates[attribute_id] = candidate

    item_condition = definitions.get("ITEM_CONDITION")
    if item_condition is not None:
        option = next(
            (
                option
                for option in item_condition.options
                if option.dictionary_value_id == "2230284"
                or option.value.casefold() == "new"
            ),
            None,
        )
        if option is None:
            issues.append(
                MercadoLibreAttributeIssue(
                    "ITEM_CONDITION_UNAVAILABLE",
                    "attributes.ITEM_CONDITION",
                    "当前类目不允许 New 商品状态",
                )
            )
        else:
            candidates["ITEM_CONDITION"] = {
                "values": [
                    {
                        "dictionary_value_id": option.dictionary_value_id,
                        "value": option.value,
                    }
                ]
            }

    for required in definition.required:
        if required.read_only:
            continue
        attribute_id = required.id
        if attribute_id == "EMPTY_GTIN_REASON" and gtin:
            continue
        if (
            attribute_id in {"GTIN", "UPC", "UNIVERSAL_PRODUCT_CODE"}
            and _value_present(candidates.get("EMPTY_GTIN_REASON"))
        ):
            continue
        if not _value_present(candidates.get(attribute_id)):
            issues.append(
                MercadoLibreAttributeIssue(
                    "REQUIRED_ATTRIBUTE_MISSING",
                    f"attributes.{attribute_id}",
                    f"缺少 Mercado Libre 必填属性 {attribute_id}",
                )
            )

    compiled: list[dict[str, Any]] = []
    for definition_item in (*definition.required, *definition.optional):
        if definition_item.read_only:
            continue
        raw_value = candidates.get(definition_item.id)
        if not _value_present(raw_value):
            continue
        wire, issue = _compile_attribute_value(
            definition_item,
            raw_value,
            listing_model=listing_model,
        )
        if issue is not None:
            issues.append(issue)
        elif wire is not None:
            compiled.append(wire)

    return MercadoLibreAttributeCompilation(
        attributes=tuple(compiled),
        issues=tuple(issues),
    )


__all__ = [
    "MercadoLibreAttributeCompilation",
    "MercadoLibreAttributeIssue",
    "compile_mercadolibre_attributes",
]

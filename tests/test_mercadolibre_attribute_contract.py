from __future__ import annotations

from erp_web.runtime_units.category_definition_support import (
    definition_from_legacy_attributes,
)
from erp_web.runtime_units.category_refresh import normalize_ml_attribute
from erp_web.schemas.category_definition import CategoryAttributeOptionPreview
from erp_web.services.mercadolibre_attribute_contract import (
    compile_mercadolibre_attributes,
)


def _definition():
    raw_attributes = [
        {
            "id": "BRAND",
            "name": "Brand",
            "tags": {"required": True},
            "value_type": "string",
        },
        {
            "id": "MODEL",
            "name": "Model",
            "tags": {"required": True},
            "value_type": "string",
        },
        {
            "id": "VOLTAGE",
            "name": "Voltage",
            "tags": {"required": True},
            "value_type": "string",
            "values": [
                {"id": "198813", "name": "220V"},
                {"id": "39205163", "name": "110/220V"},
            ],
        },
        {
            "id": "WEIGHT",
            "name": "Weight",
            "tags": {},
            "value_type": "number_unit",
            "allowed_units": [
                {"id": "g", "name": "g"},
                {"id": "kg", "name": "kg"},
                {"id": "lb", "name": "lb"},
            ],
            "default_unit": "kg",
        },
        {
            "id": "PACKAGE_LENGTH",
            "name": "Package length",
            "tags": {"required": True},
            "value_type": "number_unit",
            "allowed_units": [{"id": "cm", "name": "cm"}],
            "default_unit": "cm",
        },
        {
            "id": "PACKAGE_WIDTH",
            "name": "Package width",
            "tags": {"required": True},
            "value_type": "number_unit",
            "allowed_units": [{"id": "cm", "name": "cm"}],
            "default_unit": "cm",
        },
        {
            "id": "PACKAGE_HEIGHT",
            "name": "Package height",
            "tags": {"required": True},
            "value_type": "number_unit",
            "allowed_units": [{"id": "cm", "name": "cm"}],
            "default_unit": "cm",
        },
        {
            "id": "PACKAGE_WEIGHT",
            "name": "Package weight",
            "tags": {"required": True},
            "value_type": "number_unit",
            "allowed_units": [
                {"id": "g", "name": "g"},
                {"id": "kg", "name": "kg"},
            ],
            "default_unit": "g",
        },
        {
            "id": "GTIN",
            "name": "GTIN",
            "tags": {},
            "value_type": "string",
        },
        {
            "id": "EMPTY_GTIN_REASON",
            "name": "Empty GTIN reason",
            "tags": {},
            "value_type": "list",
            "values": [
                {
                    "id": "17055160",
                    "name": "The product does not have registered code",
                }
            ],
        },
        {
            "id": "ITEM_CONDITION",
            "name": "Item condition",
            "tags": {},
            "value_type": "list",
            "values": [{"id": "2230284", "name": "New"}],
        },
        {
            "id": "SELLER_SKU",
            "name": "SKU",
            "tags": {},
            "value_type": "string",
        },
    ]
    normalized = [normalize_ml_attribute(item) for item in raw_attributes]
    required = [item for item in normalized if item["required"]]
    optional = [item for item in normalized if not item["required"]]
    return definition_from_legacy_attributes(
        platform="mercadolibre",
        site="CBT",
        category_id="CBT455865",
        required=required,
        optional=optional,
    )


def _draft() -> dict:
    return {
        "category_id": "CBT455865",
        "brand": "Wei Xiao Dian",
        "model": "X05",
        "sku": "ML-X05",
        "upc": "123456789012",
        "allow_gtin_exemption": False,
        "attributes": {
            "VOLTAGE": "110/220V",
            "WEIGHT": {"value": "182", "unit": "g"},
            "00": "来源污染",
            "产品净重": "182g",
        },
        "package_dimensions": {
            "length_cm": "5.5",
            "width_cm": "6",
            "height_cm": "15.5",
            "weight_kg": "0.182",
        },
    }


def test_compile_uses_category_whitelist_and_preserves_enum_and_units() -> None:
    result = compile_mercadolibre_attributes(
        _draft(),
        _definition(),
        listing_model="traditional_global_items",
    )

    rows = {item["id"]: item for item in result.attributes}
    assert {issue.field for issue in result.issues} == {
        "attributes.00",
        "attributes.产品净重",
    }
    assert rows["VOLTAGE"] == {
        "id": "VOLTAGE",
        "value_id": "39205163",
        "value_name": "110/220V",
    }
    assert rows["WEIGHT"]["value_name"] == "182 g"
    assert rows["PACKAGE_LENGTH"]["value_name"] == "5.5 cm"
    assert rows["PACKAGE_WEIGHT"]["value_name"] == "182 g"
    assert rows["GTIN"]["value_name"] == "123456789012"
    assert "00" not in rows
    assert "产品净重" not in rows


def test_compile_gtin_exemption_uses_exact_platform_option() -> None:
    draft = _draft()
    draft["upc"] = ""
    draft["allow_gtin_exemption"] = True
    draft["attributes"] = {"VOLTAGE": "110/220V"}

    result = compile_mercadolibre_attributes(
        draft,
        _definition(),
        listing_model="traditional_global_items",
    )

    rows = {item["id"]: item for item in result.attributes}
    assert not result.issues
    assert "GTIN" not in rows
    assert rows["EMPTY_GTIN_REASON"] == {
        "id": "EMPTY_GTIN_REASON",
        "value_id": "17055160",
        "value_name": "The product does not have registered code",
    }


def test_compile_rejects_ambiguous_number_unit_and_invalid_enum() -> None:
    draft = _draft()
    draft["attributes"] = {
        "VOLTAGE": "380V",
        "WEIGHT": "182",
    }

    result = compile_mercadolibre_attributes(
        draft,
        _definition(),
        listing_model="traditional_global_items",
    )

    assert {issue.code for issue in result.issues} == {
        "ATTRIBUTE_UNIT_REQUIRED",
    }
    # VOLTAGE 是带建议值的 string，Mercado 允许 value_name 自定义；不会猜 ID。
    voltage = next(item for item in result.attributes if item["id"] == "VOLTAGE")
    assert voltage == {"id": "VOLTAGE", "value_name": "380V"}


def test_compile_rejects_missing_required_attribute_at_payload_boundary() -> None:
    draft = _draft()
    draft["attributes"] = {
        "WEIGHT": {"value": "182", "unit": "g"},
    }
    draft["package_dimensions"]["height_cm"] = ""

    result = compile_mercadolibre_attributes(
        draft,
        _definition(),
        listing_model="traditional_global_items",
    )

    assert {
        (issue.code, issue.field)
        for issue in result.issues
    } == {
        ("REQUIRED_ATTRIBUTE_MISSING", "attributes.VOLTAGE"),
        ("REQUIRED_ATTRIBUTE_MISSING", "attributes.PACKAGE_HEIGHT"),
    }


def test_compile_ignores_seller_package_shadow_attributes() -> None:
    draft = _draft()
    draft["attributes"]["SELLER_PACKAGE_WIDTH"] = "来自旧草稿的重复尺寸"

    result = compile_mercadolibre_attributes(
        draft,
        _definition(),
        listing_model="traditional_global_items",
    )

    assert not any(
        issue.field == "attributes.SELLER_PACKAGE_WIDTH"
        for issue in result.issues
    )
    assert not any(
        item["id"] == "SELLER_PACKAGE_WIDTH"
        for item in result.attributes
    )


def test_compile_derives_required_seller_package_aliases_from_draft_package() -> None:
    definition = _definition()
    aliases = {
        "PACKAGE_LENGTH": "SELLER_PACKAGE_LENGTH",
        "PACKAGE_WIDTH": "SELLER_PACKAGE_WIDTH",
        "PACKAGE_HEIGHT": "SELLER_PACKAGE_HEIGHT",
        "PACKAGE_WEIGHT": "SELLER_PACKAGE_WEIGHT",
    }
    seller_package_required = tuple(
        definition.attribute_by_id(source_id).model_copy(update={"id": alias_id})
        for source_id, alias_id in aliases.items()
    )
    definition = definition.model_copy(
        update={"required": seller_package_required, "optional": ()}
    )
    draft = _draft()
    draft["attributes"] = {
        "SELLER_PACKAGE_LENGTH": {"value": "999", "unit": "cm"},
        "SELLER_PACKAGE_WIDTH": {"value": "999", "unit": "cm"},
        "SELLER_PACKAGE_HEIGHT": {"value": "999", "unit": "cm"},
        "SELLER_PACKAGE_WEIGHT": {"value": "999", "unit": "g"},
    }

    result = compile_mercadolibre_attributes(
        draft,
        definition,
        listing_model="traditional_global_items",
    )

    rows = {item["id"]: item for item in result.attributes}
    assert not result.issues
    assert len(rows) == len(result.attributes) == 4
    assert set(rows) == set(aliases.values())
    assert rows["SELLER_PACKAGE_LENGTH"]["value_name"] == "5.5 cm"
    assert rows["SELLER_PACKAGE_WIDTH"]["value_name"] == "6 cm"
    assert rows["SELLER_PACKAGE_HEIGHT"]["value_name"] == "15.5 cm"
    assert rows["SELLER_PACKAGE_WEIGHT"]["value_name"] == "182 g"


def test_compile_rejects_structured_value_for_scalar_attribute() -> None:
    draft = _draft()
    draft["attributes"]["VOLTAGE"] = {"value": "110/220V", "unit": "V"}

    result = compile_mercadolibre_attributes(
        draft,
        _definition(),
        listing_model="traditional_global_items",
    )

    assert any(
        issue.code == "ATTRIBUTE_VALUE_INVALID"
        and issue.field == "attributes.VOLTAGE"
        for issue in result.issues
    )
    assert not any(item["id"] == "VOLTAGE" for item in result.attributes)


def test_compile_enforces_category_max_value_count() -> None:
    definition = _definition()
    voltage = definition.attribute_by_id("VOLTAGE")
    assert voltage is not None
    limited_voltage = voltage.model_copy(
        update={
            "value_type": "list",
            "is_collection": True,
            "max_value_count": 1,
        }
    )
    definition = definition.model_copy(
        update={
            "required": tuple(
                limited_voltage if item.id == "VOLTAGE" else item
                for item in definition.required
            )
        }
    )
    draft = _draft()
    draft["attributes"]["VOLTAGE"] = {
        "values": [
            {"dictionary_value_id": "198813", "value": "220V"},
            {"dictionary_value_id": "39205163", "value": "110/220V"},
        ]
    }

    result = compile_mercadolibre_attributes(
        draft,
        definition,
        listing_model="traditional_global_items",
    )

    assert any(
        issue.code == "ATTRIBUTE_ENUM_TOO_MANY_VALUES"
        and issue.field == "attributes.VOLTAGE"
        for issue in result.issues
    )
    assert not any(item["id"] == "VOLTAGE" for item in result.attributes)


def test_compile_collection_always_uses_values_array_even_with_one_item() -> None:
    definition = _definition()
    voltage = definition.attribute_by_id("VOLTAGE")
    assert voltage is not None
    collection_voltage = voltage.model_copy(
        update={
            "value_type": "list",
            "is_collection": True,
            "max_value_count": 3,
        }
    )
    definition = definition.model_copy(
        update={
            "required": tuple(
                collection_voltage if item.id == "VOLTAGE" else item
                for item in definition.required
            )
        }
    )
    draft = _draft()
    draft["attributes"] = {
        "VOLTAGE": {
            "values": [
                {"dictionary_value_id": "39205163", "value": "110/220V"},
            ]
        },
        "WEIGHT": {"value": "182", "unit": "g"},
    }

    result = compile_mercadolibre_attributes(
        draft,
        definition,
        listing_model="traditional_global_items",
    )

    assert not result.issues
    voltage_wire = next(
        item for item in result.attributes if item["id"] == "VOLTAGE"
    )
    assert voltage_wire == {
        "id": "VOLTAGE",
        "values": [{"id": "39205163", "name": "110/220V"}],
    }


def test_compile_open_collection_accepts_custom_and_known_values() -> None:
    definition = _definition()
    voltage = definition.attribute_by_id("VOLTAGE")
    assert voltage is not None
    collection_voltage = voltage.model_copy(
        update={"value_type": "string", "is_collection": True}
    )
    definition = definition.model_copy(
        update={
            "required": tuple(
                collection_voltage if item.id == "VOLTAGE" else item
                for item in definition.required
            )
        }
    )
    draft = _draft()
    draft["attributes"] = {
        "VOLTAGE": {
            "values": [
                {"value": "自定义电压"},
                {"dictionary_value_id": "39205163", "value": "110/220V"},
            ]
        },
        "WEIGHT": {"value": "182", "unit": "g"},
    }

    result = compile_mercadolibre_attributes(
        draft,
        definition,
        listing_model="user_products",
    )

    assert not result.issues
    voltage_wire = next(
        item for item in result.attributes if item["id"] == "VOLTAGE"
    )
    assert voltage_wire == {
        "id": "VOLTAGE",
        "values": [
            {"name": "自定义电压"},
            {"id": "39205163", "name": "110/220V"},
        ],
    }


def test_compile_brand_and_model_only_use_root_draft_fields() -> None:
    draft = _draft()
    draft["attributes"].update({"BRAND": "旧重复品牌", "MODEL": "旧重复型号"})

    result = compile_mercadolibre_attributes(
        draft,
        _definition(),
        listing_model="traditional_global_items",
    )

    rows = {item["id"]: item for item in result.attributes}
    assert rows["BRAND"]["value_name"] == "Wei Xiao Dian"
    assert rows["MODEL"]["value_name"] == "X05"


def _restricted_brand_definition(*, has_more_values: bool = False):
    definition = _definition()
    brand = definition.attribute_by_id("BRAND")
    assert brand is not None
    restricted = brand.model_copy(
        update={
            "allow_custom_values": False,
            "value_mode": "strict_enum",
            "is_dictionary": True,
            "options": (
                CategoryAttributeOptionPreview(
                    value="Generic",
                    dictionary_value_id="35977846",
                ),
            ),
            "has_more_values": has_more_values,
        }
    )
    return definition.model_copy(
        update={
            "required": tuple(
                restricted if item.id == "BRAND" else item
                for item in definition.required
            )
        }
    )


def test_compile_restricted_brand_uses_exact_platform_candidate() -> None:
    draft = _draft()
    draft["brand"] = "Generic"

    result = compile_mercadolibre_attributes(
        draft,
        _restricted_brand_definition(),
        listing_model="traditional_global_items",
    )

    assert not any(issue.field == "attributes.BRAND" for issue in result.issues)
    brand = next(item for item in result.attributes if item["id"] == "BRAND")
    assert brand == {
        "id": "BRAND",
        "value_id": "35977846",
        "value_name": "Generic",
    }


def test_compile_restricted_brand_validates_selected_id_and_name_pair() -> None:
    draft = _draft()
    draft["brand"] = {
        "values": [
            {
                "dictionary_value_id": "35977846",
                "value": "Generic",
            }
        ]
    }
    accepted = compile_mercadolibre_attributes(
        draft,
        _restricted_brand_definition(),
        listing_model="traditional_global_items",
    )
    assert not any(issue.field == "attributes.BRAND" for issue in accepted.issues)

    draft["brand"] = {
        "values": [
            {
                "dictionary_value_id": "35977846",
                "value": "Wrong brand name",
            }
        ]
    }
    rejected = compile_mercadolibre_attributes(
        draft,
        _restricted_brand_definition(),
        listing_model="traditional_global_items",
    )
    assert any(
        issue.code == "ATTRIBUTE_ENUM_VALUE_INVALID"
        and issue.field == "attributes.BRAND"
        for issue in rejected.issues
    )


def test_compile_restricted_brand_rejects_unlisted_name_without_id() -> None:
    for value in ("蔚小电", "Unlisted Brand"):
        draft = _draft()
        draft["brand"] = value

        result = compile_mercadolibre_attributes(
            draft,
            _restricted_brand_definition(),
            listing_model="traditional_global_items",
        )

        assert any(
            issue.code == "ATTRIBUTE_ENUM_VALUE_INVALID"
            and issue.field == "attributes.BRAND"
            for issue in result.issues
        )
        assert not any(item["id"] == "BRAND" for item in result.attributes)


def test_compile_restricted_brand_accepts_selected_id_beyond_preview() -> None:
    draft = _draft()
    draft["brand"] = "Remote exact value"
    draft["attributes"]["BRAND"] = {
        "values": [
            {
                "dictionary_value_id": "remote-99",
                "value": "Remote exact value",
            }
        ]
    }

    result = compile_mercadolibre_attributes(
        draft,
        _restricted_brand_definition(has_more_values=True),
        listing_model="traditional_global_items",
    )

    brand_wire = next(
        item for item in result.attributes if item["id"] == "BRAND"
    )
    assert brand_wire == {
        "id": "BRAND",
        "value_id": "remote-99",
        "value_name": "Remote exact value",
    }


def test_compile_ignores_stale_brand_selection_metadata() -> None:
    draft = _draft()
    draft["brand"] = "Generic"
    draft["attributes"]["BRAND"] = {
        "values": [
            {
                "dictionary_value_id": "remote-99",
                "value": "Old selected brand",
            }
        ]
    }

    result = compile_mercadolibre_attributes(
        draft,
        _restricted_brand_definition(has_more_values=True),
        listing_model="traditional_global_items",
    )

    brand_wire = next(item for item in result.attributes if item["id"] == "BRAND")
    assert brand_wire == {
        "id": "BRAND",
        "value_id": "35977846",
        "value_name": "Generic",
    }


def test_compile_does_not_restrict_string_without_platform_candidates() -> None:
    definition = _definition()
    definition = definition.model_copy(
        update={
            "required": tuple(
                item.model_copy(update={"allow_custom_values": False})
                if item.id == "MODEL"
                else item
                for item in definition.required
            ),
            "optional": tuple(
                item.model_copy(update={"allow_custom_values": False})
                if item.id == "GTIN"
                else item
                for item in definition.optional
            ),
        }
    )

    result = compile_mercadolibre_attributes(
        _draft(),
        definition,
        listing_model="traditional_global_items",
    )

    assert not any(
        issue.field in {"attributes.MODEL", "attributes.GTIN"}
        for issue in result.issues
    )
    rows = {item["id"]: item for item in result.attributes}
    assert rows["MODEL"]["value_name"] == "X05"
    assert rows["GTIN"]["value_name"] == "123456789012"


def test_compile_ignores_stale_read_only_value() -> None:
    definition = _definition()
    voltage = definition.attribute_by_id("VOLTAGE")
    assert voltage is not None
    definition = definition.model_copy(
        update={
            "required": tuple(
                voltage.model_copy(update={"read_only": True})
                if item.id == "VOLTAGE"
                else item
                for item in definition.required
            )
        }
    )

    result = compile_mercadolibre_attributes(
        _draft(),
        definition,
        listing_model="traditional_global_items",
    )

    assert not any(issue.field == "attributes.VOLTAGE" for issue in result.issues)
    assert not any(item["id"] == "VOLTAGE" for item in result.attributes)


def test_compile_validates_numeric_attribute_values() -> None:
    definition = _definition()
    voltage = definition.attribute_by_id("VOLTAGE")
    assert voltage is not None
    numeric_voltage = voltage.model_copy(
        update={"value_type": "number", "options": ()}
    )
    definition = definition.model_copy(
        update={
            "required": tuple(
                numeric_voltage if item.id == "VOLTAGE" else item
                for item in definition.required
            )
        }
    )

    for invalid in ("abc", "NaN", "Infinity"):
        draft = _draft()
        draft["attributes"]["VOLTAGE"] = invalid
        result = compile_mercadolibre_attributes(
            draft,
            definition,
            listing_model="traditional_global_items",
        )
        assert any(
            issue.code == "ATTRIBUTE_NUMBER_INVALID"
            and issue.field == "attributes.VOLTAGE"
            for issue in result.issues
        )

    draft = _draft()
    draft["attributes"]["VOLTAGE"] = "1.250"
    result = compile_mercadolibre_attributes(
        draft,
        definition,
        listing_model="traditional_global_items",
    )
    voltage_wire = next(item for item in result.attributes if item["id"] == "VOLTAGE")
    assert voltage_wire["value_name"] == "1.25"

    draft = _draft()
    draft["attributes"]["VOLTAGE"] = 0
    result = compile_mercadolibre_attributes(
        draft,
        definition,
        listing_model="traditional_global_items",
    )
    voltage_wire = next(item for item in result.attributes if item["id"] == "VOLTAGE")
    assert voltage_wire["value_name"] == "0"


def test_compile_number_unit_accepts_finite_zero_and_negative_values() -> None:
    for value, expected in ((0, "0 g"), (-5, "-5 g")):
        draft = _draft()
        draft["attributes"]["WEIGHT"] = {"value": value, "unit": "g"}

        result = compile_mercadolibre_attributes(
            draft,
            _definition(),
            listing_model="traditional_global_items",
        )

        weight_wire = next(
            item for item in result.attributes if item["id"] == "WEIGHT"
        )
        assert weight_wire["value_name"] == expected
        assert not any(
            issue.field == "attributes.WEIGHT" for issue in result.issues
        )


def test_compile_still_rejects_non_positive_package_dimensions() -> None:
    draft = _draft()
    draft["package_dimensions"]["width_cm"] = 0

    result = compile_mercadolibre_attributes(
        draft,
        _definition(),
        listing_model="traditional_global_items",
    )

    assert any(
        issue.code == "PACKAGE_DIMENSION_INVALID"
        and issue.field == "package_dimensions.width_cm"
        for issue in result.issues
    )
    assert not any(
        item["id"] == "PACKAGE_WIDTH" for item in result.attributes
    )


def test_compile_enforces_scalar_max_length() -> None:
    definition = _definition()
    brand = definition.attribute_by_id("BRAND")
    assert brand is not None
    limited_brand = brand.model_copy(update={"constraints": {"max_length": "2"}})
    definition = definition.model_copy(
        update={
            "required": tuple(
                limited_brand if item.id == "BRAND" else item
                for item in definition.required
            )
        }
    )

    result = compile_mercadolibre_attributes(
        _draft(),
        definition,
        listing_model="traditional_global_items",
    )

    assert any(
        issue.code == "ATTRIBUTE_VALUE_TOO_LONG"
        and issue.field == "attributes.BRAND"
        for issue in result.issues
    )
    assert not any(item["id"] == "BRAND" for item in result.attributes)

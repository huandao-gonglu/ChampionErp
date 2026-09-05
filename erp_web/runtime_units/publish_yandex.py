# -*- coding: utf-8 -*-
"""Yandex Market 商品创建/编辑发布实现。

职责：

- ``build_yandex_publish_payload()``：把已通过共享校验的草稿编译为确定性
  复合 payload，按“目录商品 / 上架条件 / 价格 / 库存”分组；
- ``validate_yandex_publish_payload()``：结构、凭证与店铺身份校验；
- ``publish_yandex_payload()``：只执行第一个尚未完成的远端 mutation；
- ``poll_yandex_publish_status()``：依据已持久化 checkpoint 执行下一个
  mutation 或只读终态回读；
- ``map_yandex_publish_error()``：类型化错误 → 用户可见摘要。

两个执行入口都不访问 PublishingBus 数据库、确认 digest 或 facade；
凭据在每次执行时从当前保存的店铺配置重新解析，不信任 payload 中复制的
店铺身份之外的任何凭证来源。checkpoint 可持久化且绝不包含凭据。
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import time
from typing import Any

from erp_web.marketplaces.yandex_currency import yandex_wire_currency
from erp_web.marketplaces.yandex_http import (
    YandexApiError,
    fetch_yandex_campaign_offer,
    fetch_yandex_offer_mapping,
    fetch_yandex_price_quarantine,
    update_yandex_campaign_offer,
    update_yandex_offer_mapping,
    update_yandex_price,
    update_yandex_stock,
)
from erp_web.schemas.category import (
    category_attribute_unit_is_valid,
    category_attribute_value_mode,
)
from erp_web.schemas.yandex import YandexPublishCheckpoint
from erp_web.stores.product_store import normalize_product_fields

from .collect_helpers import collect_time_iso
from .publish_helpers import (
    _draft_for_platform,
    _draft_images,
    _required_attribute_summary,
    _selected_price_and_currency,
)


# 远端写步骤顺序；confirmation 是只读回读，不属于 mutation。
YANDEX_PUBLISH_STEPS: tuple[str, ...] = (
    "offer_mapping",
    "campaign_offer",
    "price",
    "stock",
)

_SUCCESS_CAMPAIGN_STATUS = "PUBLISHED"
_PENDING_CAMPAIGN_STATUSES = frozenset({"CHECKING", "CREATING_CARD", ""})
_FAILED_CAMPAIGN_STATUSES = frozenset(
    {"REJECTED_BY_MARKET", "DISABLED_AUTOMATICALLY", "NO_CARD"}
)
_NO_STOCKS_CAMPAIGN_STATUS = "NO_STOCKS"

# 官方 OfferCardStatusType（offerMappings[].offer.cardStatus）。
# 注意：官方枚举中不存在 "PUBLISHED"；发布与否由 Campaign 商品状态表达，
# cardStatus 表达卡片/变更是否被接受。
_CARD_STATUS_ACCEPTED = frozenset(
    {"HAS_CARD_CAN_UPDATE", "HAS_CARD_CAN_NOT_UPDATE"}
)
# «Изменения не приняты» / «Не создана из-за ошибки»：本次变更被拒绝。
_CARD_STATUS_FAILED = frozenset(
    {"HAS_CARD_CAN_UPDATE_ERRORS", "NO_CARD_ERRORS"}
)
# «Изменения на проверке» / «Проверяем данные» / «Создаст Маркет»：仍在处理。
_CARD_STATUS_PROCESSING = frozenset(
    {
        "HAS_CARD_CAN_UPDATE_PROCESSING",
        "NO_CARD_PROCESSING",
        "NO_CARD_MARKET_WILL_CREATE",
    }
)

DEFAULT_STEP_RETRY_LIMIT = 8
DEFAULT_CONFIRMATION_POLL_LIMIT = 30
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
MAX_POLL_INTERVAL_SECONDS = 30.0
MAX_PICTURES = 30


def _positive_decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value or "").strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} 必须是有效数字") from exc
    if number <= 0:
        raise ValueError(f"{field} 必须大于 0")
    return number


def _price_text(value: Any, field: str) -> str:
    number = _positive_decimal(value, field)
    rendered = format(number, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _price_number(value: Any, field: str = "价格") -> int | float:
    """官方价格字段为 JSON number；整数值输出 int，避免多余小数位。"""

    number = _positive_decimal(value, field)
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _yandex_store(config: dict[str, Any]) -> dict[str, Any]:
    store = config.get("yandex")
    return store if isinstance(store, dict) else {}


def _yandex_publish_credentials(
    config: dict[str, Any],
) -> tuple[str, str, str]:
    """每次执行时重新解析当前已保存凭证，不信任草稿中复制的店铺身份。"""

    store = _yandex_store(config)
    api_token = str(store.get("api_token") or "").strip()
    campaign_id = str(store.get("campaign_id") or "").strip()
    business_id = str(store.get("business_id") or "").strip()
    if not api_token:
        raise YandexApiError(
            "YANDEX_CREDENTIALS_MISSING",
            "请先填写 Yandex API-Key Token。",
            retryable=False,
        )
    if not campaign_id:
        raise YandexApiError(
            "YANDEX_CREDENTIALS_MISSING",
            "请先填写 Yandex Campaign ID。",
            retryable=False,
        )
    if not business_id:
        raise YandexApiError(
            "YANDEX_BUSINESS_ID_MISSING",
            "Yandex business_id 尚未通过在线授权校验，请先在授权页测试授权。",
            retryable=False,
        )
    return api_token, campaign_id, business_id


def _record_schema_definitions(
    category_record: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """当次临时类目定义 → 按属性 ID 索引；不再读取草稿持久化 Schema。"""

    attributes = (
        category_record.get("attributes")
        if isinstance(category_record, dict)
        and isinstance(category_record.get("attributes"), dict)
        else {}
    )
    definitions: dict[str, dict[str, Any]] = {}
    for group in ("required", "optional"):
        rows = attributes.get(group) if isinstance(attributes.get(group), list) else []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            attr_id = str(raw.get("id") or "").strip()
            if attr_id:
                definitions[attr_id] = raw
    return definitions


def yandex_offer_identity_conflict(draft: dict[str, Any]) -> str:
    """比较当前 ``draft.sku`` 与历史 ``last_publish_task.offer_id``。

    稳定 offerId 是 Yandex 编辑同一远端商品的唯一身份；不一致必须阻断，
    前端锁定输入只是交互提示，不能充当身份约束。
    """

    offer_id = str(draft.get("sku") or "").strip()
    last_publish_task = (
        draft.get("last_publish_task")
        if isinstance(draft.get("last_publish_task"), dict)
        else {}
    )
    previous = str(last_publish_task.get("offer_id") or "").strip()
    if offer_id and previous and previous != offer_id:
        return (
            f"SKU 已变化（{previous} → {offer_id}），"
            "Yandex 发布会创建新的远端商品；请先确认身份或恢复原 SKU。"
        )
    return ""


def yandex_required_attributes_missing(
    product: dict[str, Any],
    category_record: dict[str, Any] | None = None,
) -> list[str]:
    product = normalize_product_fields(product)
    return list(
        _required_attribute_summary(product, "yandex", category_record).get("missing")
        or []
    )


def yandex_invalid_dictionary_attributes(
    product: dict[str, Any],
    category_record: dict[str, Any] | None = None,
) -> list[str]:
    """返回草稿中缺少平台枚举选择的 strict_enum 属性 ID。"""

    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "yandex")
    definitions = _record_schema_definitions(category_record)
    attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    invalid: list[str] = []
    for attr_id, definition in definitions.items():
        if category_attribute_value_mode(definition) != "strict_enum":
            continue
        if attr_id not in attributes:
            continue
        raw_value = attributes.get(attr_id)
        values = (
            raw_value.get("values")
            if isinstance(raw_value, dict)
            and isinstance(raw_value.get("values"), list)
            else []
        )
        if not values or any(
            not isinstance(item, dict)
            or not str(item.get("dictionary_value_id") or "").strip()
            or not str(item.get("value") or "").strip()
            for item in values
        ):
            invalid.append(attr_id)
    return sorted(invalid)


def yandex_invalid_unit_attributes(
    product: dict[str, Any],
    category_record: dict[str, Any] | None = None,
) -> list[str]:
    """返回草稿值单位不在类目允许范围内的属性 ID。"""

    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "yandex")
    definitions = _record_schema_definitions(category_record)
    attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    invalid: list[str] = []
    for attr_id, definition in definitions.items():
        raw_value = attributes.get(attr_id)
        if not isinstance(raw_value, dict):
            continue
        unit = str(raw_value.get("unit") or "").strip()
        if not unit:
            continue
        if not category_attribute_unit_is_valid(definition, unit):
            invalid.append(attr_id)
    return sorted(invalid)


def _yandex_attribute_value_empty(raw_value: Any) -> bool:
    if isinstance(raw_value, dict):
        values = raw_value.get("values")
        if isinstance(values, list) and values:
            return False
        return not str(raw_value.get("value") or "").strip()
    if isinstance(raw_value, (list, tuple, set)):
        return not bool(raw_value)
    return not str(raw_value or "").strip()


def yandex_mapped_parameter_count(
    product: dict[str, Any],
    category_record: dict[str, Any] | None = None,
) -> int:
    """草稿中可映射为 Yandex ``parameterValues`` 的已填参数数量。

    与 ``_compile_parameter_values`` 同源的映射规则：属性 ID 必须是当前
    类目参数定义中的正整数且值非空。Yandex 发布要求至少 1 个参数值
    （即使类目没有任何必填参数），预检与发布边界共用该计数。
    """

    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "yandex")
    definitions = _record_schema_definitions(category_record)
    attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    count = 0
    for raw_id, raw_value in attributes.items():
        attr_id = str(raw_id or "").strip()
        if not attr_id.isdigit() or int(attr_id) <= 0 or attr_id not in definitions:
            continue
        if _yandex_attribute_value_empty(raw_value):
            continue
        count += 1
    return count


def _resolve_unit_id(definition: dict[str, Any], unit: Any) -> int | None:
    """把选中的单位名称解析为 Yandex ``unitId``。

    未选择单位时省略 ``unitId``（官方行为：回落 ``unit.defaultUnitId``）；
    选中单位与默认单位一致且无 ID 映射时同样允许省略；选中非默认单位但
    定义中没有单位 ID 时视为类目定义过期，必须阻断而不是静默用默认单位。
    """

    text = str(unit or "").strip()
    if not text:
        return None
    unit_ids = (
        definition.get("unit_ids") if isinstance(definition.get("unit_ids"), dict) else {}
    )
    raw_id = str(unit_ids.get(text) or "").strip()
    if raw_id.isdigit() and int(raw_id) > 0:
        return int(raw_id)
    default_unit = str(definition.get("default_unit") or "").strip()
    if default_unit and text == default_unit:
        return None
    raise ValueError(
        f"Yandex 属性 {definition.get('id') or ''} 的单位 {text} "
        "在当前类目定义中缺少单位 ID，请刷新平台属性后重新选择"
    )


def _validate_text_constraints(
    definition: dict[str, Any],
    text: str,
    attr_id: str,
) -> None:
    """按官方 ``constraints`` 校验 NUMERIC 取值范围与 TEXT 长度。"""

    constraints = (
        definition.get("constraints")
        if isinstance(definition.get("constraints"), dict)
        else {}
    )
    value_type = str(definition.get("value_type") or "").strip().lower()
    if value_type == "numeric":
        try:
            number = Decimal(text.replace(",", "."))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Yandex 数值属性 {attr_id} 必须是有效数字") from exc
        min_value = constraints.get("min_value")
        max_value = constraints.get("max_value")
        if min_value is not None and number < Decimal(str(min_value)):
            raise ValueError(f"Yandex 数值属性 {attr_id} 不能小于 {min_value}")
        if max_value is not None and number > Decimal(str(max_value)):
            raise ValueError(f"Yandex 数值属性 {attr_id} 不能大于 {max_value}")
    max_length = constraints.get("max_length")
    if max_length is not None:
        try:
            limit = int(max_length)
        except (TypeError, ValueError):
            limit = 0
        if limit > 0 and len(text) > limit:
            raise ValueError(f"Yandex 属性 {attr_id} 超过最大长度 {limit}")


def _compile_parameter_values(
    draft: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """把共享规范属性编译为 Yandex ``parameterValues`` wire 行。

    官方 DTO：``{parameterId: int, value: string, valueId?: int,
    unitId?: int}``。平台枚举值携带 ``valueId``（来自类目参数
    ``values[].id``）；开放枚举允许只携带自定义 ``value``；多值属性为
    多个共享同一 ``parameterId`` 的对象；带单位属性在非默认单位时携带
    ``unitId``。只发送当前实时类目定义中的参数。
    """

    attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    entries: list[dict[str, Any]] = []
    for raw_id, raw_value in attributes.items():
        attr_id = str(raw_id or "").strip()
        if not attr_id.isdigit() or int(attr_id) <= 0 or attr_id not in definitions:
            continue
        definition = definitions.get(attr_id) or {}
        parameter_id = int(attr_id)
        value_mode = category_attribute_value_mode(definition)
        rows: list[dict[str, Any]] = []
        if value_mode == "strict_enum":
            values = (
                raw_value.get("values")
                if isinstance(raw_value, dict)
                and isinstance(raw_value.get("values"), list)
                else []
            )
            if not values:
                raise ValueError(
                    f"Yandex 属性 {attr_id} 必须从平台枚举值中选择"
                )
            unit_id = _resolve_unit_id(
                definition,
                raw_value.get("unit") if isinstance(raw_value, dict) else None,
            )
            for item in values:
                value_id_text = str(
                    (item or {}).get("dictionary_value_id") or ""
                ).strip() if isinstance(item, dict) else ""
                if not value_id_text.isdigit() or int(value_id_text) <= 0:
                    raise ValueError(
                        f"Yandex 枚举属性 {attr_id} 缺少有效的 dictionary_value_id，"
                        "不能手动输入"
                    )
                text = str((item or {}).get("value") or "").strip() if isinstance(item, dict) else ""
                row: dict[str, Any] = {
                    "parameterId": parameter_id,
                    "valueId": int(value_id_text),
                }
                if text:
                    row["value"] = text
                if unit_id is not None:
                    row["unitId"] = unit_id
                rows.append(row)
        elif value_mode == "open_enum":
            # 开放枚举：平台枚举值携带 valueId；自定义文本只携带 value。
            selected = (
                raw_value.get("values")
                if isinstance(raw_value, dict)
                and isinstance(raw_value.get("values"), list)
                and raw_value.get("values")
                else []
            )
            unit_id = _resolve_unit_id(
                definition,
                raw_value.get("unit") if isinstance(raw_value, dict) else None,
            )
            if selected:
                for item in selected:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("value") or "").strip()
                    if not text:
                        continue
                    value_id_text = str(item.get("dictionary_value_id") or "").strip()
                    row = {"parameterId": parameter_id, "value": text}
                    if value_id_text.isdigit() and int(value_id_text) > 0:
                        row["valueId"] = int(value_id_text)
                    if unit_id is not None:
                        row["unitId"] = unit_id
                    rows.append(row)
            else:
                texts: list[str] = []
                if isinstance(raw_value, dict):
                    text = str(raw_value.get("value") or "").strip()
                    if text:
                        texts.append(text)
                elif isinstance(raw_value, (list, tuple)):
                    texts.extend(
                        str(item or "").strip()
                        for item in raw_value
                        if str(item or "").strip()
                    )
                else:
                    text = str(raw_value or "").strip()
                    if text:
                        texts.append(text)
                for text in texts:
                    _validate_text_constraints(definition, text, attr_id)
                    row = {"parameterId": parameter_id, "value": text}
                    if unit_id is not None:
                        row["unitId"] = unit_id
                    rows.append(row)
        else:
            unit = ""
            texts = []
            if isinstance(raw_value, dict):
                unit = str(raw_value.get("unit") or "").strip()
                text = str(raw_value.get("value") or "").strip()
                if text:
                    texts.append(text)
            elif isinstance(raw_value, (list, tuple)):
                texts.extend(
                    str(item or "").strip()
                    for item in raw_value
                    if str(item or "").strip()
                )
            else:
                text = str(raw_value or "").strip()
                if text:
                    texts.append(text)
            if unit and not category_attribute_unit_is_valid(definition, unit):
                raise ValueError(
                    f"Yandex 属性 {attr_id} 的单位 {unit} 不在类目允许范围内"
                )
            unit_id = _resolve_unit_id(definition, unit)
            for text in texts:
                _validate_text_constraints(definition, text, attr_id)
                row = {"parameterId": parameter_id, "value": text}
                if unit_id is not None:
                    row["unitId"] = unit_id
                rows.append(row)
        if not rows:
            continue
        if not definition.get("is_collection") and len(rows) > 1:
            raise ValueError(f"Yandex 属性 {attr_id} 只允许一个值")
        maximum = int(definition.get("max_value_count") or 0)
        if maximum > 0 and len(rows) > maximum:
            raise ValueError(
                f"Yandex 属性 {attr_id} 超过最大多值数量 {maximum}"
            )
        entries.extend(rows)
    entries.sort(
        key=lambda item: (
            int(item["parameterId"]),
            int(item.get("valueId") or 0),
            str(item.get("value") or ""),
        )
    )
    return entries


def _weight_dimensions(draft: dict[str, Any]) -> dict[str, float]:
    package = (
        draft.get("package_dimensions")
        if isinstance(draft.get("package_dimensions"), dict)
        else {}
    )

    def convert(value: Any, field: str) -> float:
        # Yandex weightDimensions：重量千克、长宽高厘米，均为 number 且允许小数。
        number = _positive_decimal(value, field)
        return float(number.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))

    return {
        "weight": convert(package.get("weight_kg"), "包装重量"),
        "length": convert(package.get("length_cm"), "包装长度"),
        "width": convert(package.get("width_cm"), "包装宽度"),
        "height": convert(package.get("height_cm"), "包装高度"),
    }


def _resolved_price(draft: dict[str, Any]) -> tuple[str, str]:
    selected_price, listing_currency = _selected_price_and_currency(
        draft, "yandex", str(draft.get("site") or "global")
    )
    if not listing_currency:
        raise ValueError("Yandex 发布币种尚未核验")
    if not selected_price:
        raise ValueError("Yandex 发布目标没有有效核价结果")
    return _price_text(selected_price, "价格"), listing_currency


def _stock_plan(
    draft: dict[str, Any],
    store: dict[str, Any],
) -> dict[str, Any]:
    placement_type = str(store.get("placement_type") or "").strip().upper()
    stock_update_mode = str(store.get("stock_update_mode") or "").strip()
    warehouse_ids = sorted(
        {
            str(item).strip()
            for item in (store.get("warehouse_ids") or [])
            if str(item or "").strip()
        }
    )
    if placement_type == "FBY":
        mode = "none"
    elif stock_update_mode in {"campaign_warehouses", "business", "none"}:
        mode = stock_update_mode
    elif stock_update_mode:
        raise ValueError(f"Yandex 库存写入方式不受支持：{stock_update_mode}")
    else:
        raise ValueError("Yandex 库存写入方式尚未通过在线授权校验，请先测试授权")
    if mode == "campaign_warehouses" and not warehouse_ids:
        raise ValueError("Yandex 已声明仓库组库存，但授权元数据缺少 warehouse_ids")
    if mode == "business" and len(warehouse_ids) != 1:
        # Business 级库存按仓库写入；草稿只有一个库存数，若复制到多个无分组
        # 仓库会造成可售库存成倍放大。授权探测必须恰好选择一个发布仓库。
        if not warehouse_ids:
            raise ValueError(
                "Yandex 已声明无分组仓库库存，但授权元数据缺少发布仓库，请重新测试授权"
            )
        raise ValueError(
            "Yandex 无分组仓库模式检测到多个候选仓库，请重新测试授权以选定唯一发布仓库"
        )
    try:
        count = int(float(str(draft.get("stock") or "0").strip() or "0"))
    except (TypeError, ValueError) as exc:
        raise ValueError("库存必须是有效整数") from exc
    if count < 0:
        raise ValueError("库存不能为负数")
    return {"mode": mode, "warehouse_ids": warehouse_ids, "count": count}


def build_yandex_publish_payload(
    product: dict[str, Any],
    config: dict[str, Any],
    category_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造确定性复合 payload：目录商品 / 上架条件 / 价格 / 库存。"""

    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "yandex")
    store = _yandex_store(config)
    conflict = yandex_offer_identity_conflict(draft)
    if conflict:
        raise ValueError(conflict)

    _api_token, campaign_id, business_id = _yandex_publish_credentials(config)
    offer_id = str(draft.get("sku") or "").strip()
    if not offer_id:
        raise ValueError("缺少 SKU / offerId")
    category_id = str(draft.get("category_id") or "").strip()
    if not category_id.isdigit() or int(category_id) <= 0:
        raise ValueError("Yandex 类目 ID 必须是正整数（只能选择叶子类目）")
    title = str(draft.get("title") or product.get("name") or "").strip()
    description = str(draft.get("description") or "").strip()
    vendor = str(draft.get("brand") or product.get("brand") or "").strip()
    images = _draft_images(product, "yandex", draft)[:MAX_PICTURES]
    if not images:
        raise ValueError("缺少商品图片")

    definitions = _record_schema_definitions(category_record)
    parameter_values = _compile_parameter_values(draft, definitions)
    if not parameter_values:
        raise ValueError(
            "Yandex 发布至少需要 1 个类目参数值，请先填写类目属性再发布"
        )
    price_value_text, currency = _resolved_price(draft)
    wire_currency = yandex_wire_currency(currency)
    price_value = _price_number(price_value_text)
    stock = _stock_plan(draft, store)

    catalog_offer: dict[str, Any] = {
        "offerId": offer_id,
        "name": title,
        "marketCategoryId": int(category_id),
        "pictures": list(images),
        "vendor": vendor,
        "description": description,
        "parameterValues": parameter_values,
        "weightDimensions": _weight_dimensions(draft),
        # 首次写入可携带 basicPrice；价格同步仍是独立、可观测步骤。
        # 官方 basicPrice.value 为 number，currencyId 为平台枚举（RUR）。
        "basicPrice": {"value": price_value, "currencyId": wire_currency},
    }

    conditions_offer: dict[str, Any] = {"offerId": offer_id}
    vat = str(store.get("vat") or "").strip()
    if vat:
        conditions_offer["vat"] = vat

    only_default_price = bool(store.get("only_default_price"))
    price: dict[str, Any] = {
        "level": "business" if only_default_price else "campaign",
        "offers": [
            {
                "offerId": offer_id,
                "price": {"value": price_value, "currencyId": wire_currency},
            }
        ],
    }

    return {
        "platform": "yandex",
        "offer_id": offer_id,
        "campaign_id": campaign_id,
        "business_id": business_id,
        "catalog": {"offer": catalog_offer},
        "offer_conditions": {"offer": conditions_offer},
        "price": price,
        "stock": stock,
    }


def _public_picture_invalid(url: Any) -> bool:
    text = str(url or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if lowered.startswith("data:") or lowered.startswith("file:"):
        return True
    if lowered.startswith("blob:"):
        return True
    if not lowered.startswith("https://") and not lowered.startswith("http://"):
        return True
    return False


def validate_yandex_publish_payload(
    payload: Any,
    config: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    store = _yandex_store(config)
    if not str(store.get("api_token") or "").strip():
        errors.append("Yandex API-Key Token")
    if not str(store.get("campaign_id") or "").strip():
        errors.append("Yandex Campaign ID")
    if not str(store.get("business_id") or "").strip():
        errors.append("Yandex business_id（在线授权校验派生）")
    payload = payload if isinstance(payload, dict) else {}
    if str(payload.get("campaign_id") or "").strip() != str(
        store.get("campaign_id") or ""
    ).strip() or str(payload.get("business_id") or "").strip() != str(
        store.get("business_id") or ""
    ).strip():
        errors.append("店铺身份已变化，payload 与当前授权绑定不一致")
    catalog = payload.get("catalog") if isinstance(payload.get("catalog"), dict) else {}
    offer = catalog.get("offer") if isinstance(catalog.get("offer"), dict) else {}
    for key, label in (
        ("offerId", "SKU / offerId"),
        ("name", "标题"),
        ("marketCategoryId", "marketCategoryId"),
        ("vendor", "品牌 / vendor"),
        ("description", "描述"),
    ):
        if offer.get(key) in (None, "", 0):
            errors.append(label)
    try:
        if int(offer.get("marketCategoryId") or 0) <= 0:
            errors.append("marketCategoryId 必须是正整数")
    except (TypeError, ValueError):
        errors.append("marketCategoryId 必须是正整数")
    pictures = offer.get("pictures") if isinstance(offer.get("pictures"), list) else []
    if not pictures:
        errors.append("图片")
    elif any(_public_picture_invalid(url) for url in pictures):
        errors.append("图片必须是平台可访问的 HTTPS 公网 URL")
    parameter_values = (
        offer.get("parameterValues")
        if isinstance(offer.get("parameterValues"), list)
        else []
    )
    for row in parameter_values:
        if not isinstance(row, dict):
            errors.append("parameterValues 必须是对象")
            continue
        try:
            parameter_id = int(row.get("parameterId") or 0)
        except (TypeError, ValueError):
            parameter_id = 0
        if parameter_id <= 0:
            errors.append("parameterValues 缺少有效 parameterId")
            continue
        # 官方 DTO：value 为字符串；多值属性为多个同 parameterId 对象。
        if "value" in row and not isinstance(row.get("value"), str):
            errors.append(f"parameterId {parameter_id} 的 value 必须是字符串")
        if "valueId" in row:
            try:
                value_id = int(row.get("valueId") or 0)
            except (TypeError, ValueError):
                value_id = 0
            if value_id <= 0:
                errors.append(f"parameterId {parameter_id} 的 valueId 无效")
        if "unitId" in row:
            try:
                unit_id = int(row.get("unitId") or 0)
            except (TypeError, ValueError):
                unit_id = 0
            if unit_id <= 0:
                errors.append(f"parameterId {parameter_id} 的 unitId 无效")
        if not str(row.get("value") or "").strip() and not row.get("valueId"):
            errors.append(f"parameterId {parameter_id} 缺少值")
    basic_price = (
        offer.get("basicPrice") if isinstance(offer.get("basicPrice"), dict) else {}
    )
    if basic_price and not isinstance(basic_price.get("value"), (int, float)):
        errors.append("basicPrice.value 必须是数值")
    conditions = (
        payload.get("offer_conditions")
        if isinstance(payload.get("offer_conditions"), dict)
        else {}
    )
    conditions_offer = (
        conditions.get("offer") if isinstance(conditions.get("offer"), dict) else {}
    )
    if not str(conditions_offer.get("offerId") or "").strip():
        errors.append("上架条件缺少 offerId")
    price = payload.get("price") if isinstance(payload.get("price"), dict) else {}
    if str(price.get("level") or "") not in {"business", "campaign"}:
        errors.append("价格级别必须是 business 或 campaign")
    price_offers = price.get("offers") if isinstance(price.get("offers"), list) else []
    if len(price_offers) != 1 or not isinstance(price_offers[0], dict):
        errors.append("价格写入必须包含一个商品")
    else:
        price_item = (
            price_offers[0].get("price")
            if isinstance(price_offers[0].get("price"), dict)
            else {}
        )
        # 官方 price.value 为 number；字符串价格在 wire 边界即视为无效。
        if not isinstance(price_item.get("value"), (int, float)):
            errors.append("价格必须是数值")
        elif float(price_item.get("value")) <= 0:
            errors.append("价格")
        if not str(price_item.get("currencyId") or "").strip():
            errors.append("价格币种")
    stock = payload.get("stock") if isinstance(payload.get("stock"), dict) else {}
    if str(stock.get("mode") or "") not in {"campaign_warehouses", "business", "none"}:
        errors.append("库存写入方式无效")
    elif str(stock.get("mode")) == "campaign_warehouses" and not (
        stock.get("warehouse_ids") or []
    ):
        errors.append("仓库组库存缺少 warehouse_ids")
    elif str(stock.get("mode")) == "business" and len(
        stock.get("warehouse_ids") or []
    ) != 1:
        # 无分组仓库模式只允许一个发布仓库，避免单一库存数被复制放大。
        errors.append("无分组仓库库存必须恰好指定一个发布仓库")
    return errors


def _publish_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """从已批准 payload 提取状态机执行计划；不含凭据。"""

    catalog = payload.get("catalog") if isinstance(payload.get("catalog"), dict) else {}
    conditions = (
        payload.get("offer_conditions")
        if isinstance(payload.get("offer_conditions"), dict)
        else {}
    )
    price = payload.get("price") if isinstance(payload.get("price"), dict) else {}
    stock = payload.get("stock") if isinstance(payload.get("stock"), dict) else {}
    return {
        "offer_id": str(payload.get("offer_id") or ""),
        "campaign_id": str(payload.get("campaign_id") or ""),
        "business_id": str(payload.get("business_id") or ""),
        "catalog_offer": deepcopy(catalog.get("offer") or {}),
        "campaign_offer": deepcopy(conditions.get("offer") or {}),
        "price": deepcopy(price),
        "stock": deepcopy(stock),
    }


def _assert_plan_binding(
    plan: dict[str, Any],
    campaign_id: str,
    business_id: str,
) -> None:
    if str(plan.get("campaign_id") or "") != campaign_id or str(
        plan.get("business_id") or ""
    ) != business_id:
        raise YandexApiError(
            "YANDEX_STORE_BINDING_CHANGED",
            "当前店铺绑定与已批准发布计划不一致，已阻止外发。",
            retryable=False,
        )


def _timeout_seconds(config: dict[str, Any]) -> float:
    store = _yandex_store(config)
    return max(1.0, float(store.get("publish_timeout_seconds") or 30))


def _poll_interval_seconds(config: dict[str, Any]) -> float:
    store = _yandex_store(config)
    return min(
        MAX_POLL_INTERVAL_SECONDS,
        max(0.5, float(store.get("publish_poll_interval_seconds") or DEFAULT_POLL_INTERVAL_SECONDS)),
    )


def _step_retry_limit(config: dict[str, Any]) -> int:
    store = _yandex_store(config)
    return max(1, int(store.get("publish_step_retry_limit") or DEFAULT_STEP_RETRY_LIMIT))


def _confirmation_poll_limit(config: dict[str, Any]) -> int:
    store = _yandex_store(config)
    return max(
        1,
        int(store.get("publish_confirmation_poll_limit") or DEFAULT_CONFIRMATION_POLL_LIMIT),
    )


def _backoff_at(checkpoint: YandexPublishCheckpoint, config: dict[str, Any]) -> float:
    interval = _poll_interval_seconds(config)
    factor = 2 ** min(max(0, checkpoint.retries), 5)
    return time.time() + min(MAX_POLL_INTERVAL_SECONDS, interval * factor)


def _pending_result(
    plan: dict[str, Any],
    checkpoint: YandexPublishCheckpoint,
) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "publish_pending_confirmation",
        "result": {
            "platform": "yandex",
            "status": "pending_confirmation",
            "offer_id": checkpoint.offer_id,
            "plan": deepcopy(plan),
            "checkpoint": checkpoint.model_dump(),
        },
    }


def _terminal_failure(
    plan: dict[str, Any],
    checkpoint: YandexPublishCheckpoint,
    mapped: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "real_publish_failed",
        "offer_id": checkpoint.offer_id or plan.get("offer_id", ""),
        "campaign_id": checkpoint.campaign_id or plan.get("campaign_id", ""),
        "business_id": checkpoint.business_id or plan.get("business_id", ""),
        "error": mapped.get("summary") or "Yandex 发布失败",
        "error_code": mapped.get("error_code") or "YANDEX_PUBLISH_FAILED",
        "error_map": mapped,
        "checkpoint": checkpoint.model_dump(),
        "checked_at": collect_time_iso(),
    }


def _terminal_success(
    plan: dict[str, Any],
    checkpoint: YandexPublishCheckpoint,
    *,
    campaign_status: str,
    card_status: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "real_publish_success",
        "offer_id": checkpoint.offer_id,
        # remote_publish_identity() 提取 offer_id/external_id 完成终态回写。
        "external_id": checkpoint.offer_id,
        "campaign_id": checkpoint.campaign_id,
        "business_id": checkpoint.business_id,
        "campaign_status": campaign_status,
        "card_status": card_status,
        "group_id": checkpoint.evidence.get("confirmation", {}).get("group_id", ""),
        "checked_at": collect_time_iso(),
        "checkpoint": checkpoint.model_dump(),
        "warnings": deepcopy(checkpoint.warnings),
    }


def _record_response(
    checkpoint: YandexPublishCheckpoint,
    step: str,
    body: dict[str, Any],
) -> None:
    warnings = body.get("warnings") if isinstance(body.get("warnings"), list) else []
    checkpoint.warnings.extend(
        item if isinstance(item, dict) else {"message": str(item)}
        for item in warnings
    )
    checkpoint.last_response_summary = {
        "step": step,
        "status": str(body.get("status") or ""),
        "checked_at": collect_time_iso(),
    }
    checkpoint.evidence[step] = {
        "status": str(body.get("status") or ""),
        "at": collect_time_iso(),
    }


def _execute_mutation(
    step: str,
    plan: dict[str, Any],
    checkpoint: YandexPublishCheckpoint,
    api_token: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """执行单个写步骤；返回 None 表示成功并已更新 checkpoint。

    可重试错误转换为 checkpoint 退避（返回 pending 结果）；确定性错误
    转换为终态失败结果。绝不抛出已分类的 Yandex API 错误。
    """

    timeout = _timeout_seconds(config)
    try:
        if step == "offer_mapping":
            body = update_yandex_offer_mapping(
                api_token,
                checkpoint.business_id,
                plan["catalog_offer"],
                timeout_seconds=timeout,
            )
        elif step == "campaign_offer":
            body = update_yandex_campaign_offer(
                api_token,
                checkpoint.campaign_id,
                plan["campaign_offer"],
                timeout_seconds=timeout,
            )
        elif step == "price":
            price = plan.get("price") if isinstance(plan.get("price"), dict) else {}
            offers = price.get("offers") if isinstance(price.get("offers"), list) else []
            if str(price.get("level") or "") == "business":
                body = update_yandex_price(
                    api_token,
                    business_id=checkpoint.business_id,
                    offers=deepcopy(offers),
                    timeout_seconds=timeout,
                )
            else:
                body = update_yandex_price(
                    api_token,
                    campaign_id=checkpoint.campaign_id,
                    offers=deepcopy(offers),
                    timeout_seconds=timeout,
                )
        elif step == "stock":
            stock = plan.get("stock") if isinstance(plan.get("stock"), dict) else {}
            mode = str(stock.get("mode") or "")
            if mode == "none":
                checkpoint.evidence["stock"] = {
                    "skipped": "FBY 库存由 Yandex 履约侧管理",
                    "at": collect_time_iso(),
                }
                checkpoint.completed_steps.append("stock")
                checkpoint.phase = "confirmation"
                checkpoint.last_response_summary = {
                    "step": "stock",
                    "status": "SKIPPED",
                    "checked_at": collect_time_iso(),
                }
                return None
            body = update_yandex_stock(
                api_token,
                mode=mode,
                campaign_id=checkpoint.campaign_id,
                business_id=checkpoint.business_id,
                warehouse_ids=list(stock.get("warehouse_ids") or []),
                offer_id=checkpoint.offer_id,
                count=int(stock.get("count") or 0),
                timeout_seconds=timeout,
            )
        else:
            raise YandexApiError(
                "YANDEX_PUBLISH_STEP_UNKNOWN",
                f"Yandex 发布状态机步骤未知：{step}",
                retryable=False,
            )
    except YandexApiError as exc:
        if exc.retryable and checkpoint.retries < _step_retry_limit(config):
            checkpoint.retries += 1
            checkpoint.next_poll_at = _backoff_at(checkpoint, config)
            checkpoint.last_response_summary = {
                "step": step,
                "status": "RETRYING",
                "error_code": exc.code,
                "checked_at": collect_time_iso(),
            }
            return _pending_result(plan, checkpoint)
        return _terminal_failure(plan, checkpoint, map_yandex_publish_error(exc))
    _record_response(checkpoint, step, body)
    checkpoint.retries = 0
    checkpoint.next_poll_at = time.time() + _poll_interval_seconds(config)
    checkpoint.completed_steps.append(step)
    checkpoint.phase = _phase_after(step)
    return None


def _phase_after(step: str) -> str:
    order = list(YANDEX_PUBLISH_STEPS) + ["confirmation"]
    index = order.index(step)
    return order[index + 1] if index + 1 < len(order) else "terminal"


def _next_pending_step(checkpoint: YandexPublishCheckpoint) -> str | None:
    for step in YANDEX_PUBLISH_STEPS:
        if not checkpoint.step_done(step):
            return step
    return None


def publish_yandex_payload(
    payload: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """执行第一个尚未完成的远端 mutation 并返回带 checkpoint 的 pending。"""

    payload = payload if isinstance(payload, dict) else {}
    api_token, campaign_id, business_id = _yandex_publish_credentials(config)
    plan = _publish_plan(payload)
    if (
        str(plan.get("campaign_id") or "") != campaign_id
        or str(plan.get("business_id") or "") != business_id
    ):
        raise YandexApiError(
            "YANDEX_STORE_BINDING_CHANGED",
            "已批准 payload 的店铺身份与当前授权绑定不一致，已阻止外发。",
            retryable=False,
        )
    if not str(plan.get("offer_id") or "").strip():
        raise YandexApiError(
            "YANDEX_OFFER_ID_MISSING",
            "发布 payload 缺少稳定 offerId。",
            retryable=False,
        )
    checkpoint = YandexPublishCheckpoint(
        phase="offer_mapping",
        offer_id=str(plan.get("offer_id")),
        campaign_id=campaign_id,
        business_id=business_id,
        next_poll_at=time.time() + _poll_interval_seconds(config),
    )
    step = _next_pending_step(checkpoint)
    if step is None:
        raise YandexApiError(
            "YANDEX_PUBLISH_PLAN_EMPTY",
            "发布计划没有可执行的写步骤。",
            retryable=False,
        )
    pending = _execute_mutation(step, plan, checkpoint, api_token, config)
    if pending is not None:
        return pending
    return _pending_result(plan, checkpoint)


def _quarantined_offer(
    plan: dict[str, Any],
    api_token: str,
    checkpoint: YandexPublishCheckpoint,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """价格隔离区回读；命中即阻断，绝不自动确认异常价格。

    按已批准 payload 的价格级别选择官方隔离区接口：Campaign 级价格查
    ``/v2/campaigns/{campaignId}/price-quarantine``，Business 级价格查
    ``/v2/businesses/{businessId}/price-quarantine``。
    """

    price = plan.get("price") if isinstance(plan.get("price"), dict) else {}
    level = str(price.get("level") or "").strip()
    offers = fetch_yandex_price_quarantine(
        api_token,
        business_id="" if level == "campaign" else checkpoint.business_id,
        campaign_id=checkpoint.campaign_id if level == "campaign" else "",
        offer_ids=[checkpoint.offer_id],
        timeout_seconds=_timeout_seconds(config),
    )
    for row in offers:
        if str(row.get("offerId") or "").strip() == checkpoint.offer_id:
            return row
    return None


def _confirmation_readback(
    plan: dict[str, Any],
    checkpoint: YandexPublishCheckpoint,
    api_token: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """只读回读 Business 商品映射与 Campaign 商品状态。"""

    timeout = _timeout_seconds(config)
    try:
        mappings = fetch_yandex_offer_mapping(
            api_token,
            checkpoint.business_id,
            [checkpoint.offer_id],
            timeout_seconds=timeout,
        )
        offers = fetch_yandex_campaign_offer(
            api_token,
            checkpoint.campaign_id,
            offer_ids=[checkpoint.offer_id],
            timeout_seconds=timeout,
        )
        quarantined = _quarantined_offer(plan, api_token, checkpoint, config)
    except YandexApiError as exc:
        if exc.retryable and checkpoint.retries < _confirmation_poll_limit(config):
            checkpoint.retries += 1
            checkpoint.next_poll_at = _backoff_at(checkpoint, config)
            return _pending_result(plan, checkpoint)
        return _terminal_failure(plan, checkpoint, map_yandex_publish_error(exc))

    if quarantined is not None:
        checkpoint.evidence["quarantine"] = deepcopy(quarantined)
        return _terminal_failure(
            plan,
            checkpoint,
            {
                "summary": (
                    f"Yandex 价格进入隔离区，已阻断自动确认；"
                    "请人工检查价格后在 Yandex 后台处理。"
                ),
                "field_errors": {"price": ["价格进入隔离区（quarantine），需要人工检查"]},
                "error_code": "YANDEX_PRICE_QUARANTINED",
                "retryable": False,
                "raw": "",
            },
        )

    card_status = ""
    group_id = ""
    if mappings:
        first = mappings[0] if isinstance(mappings[0], dict) else {}
        # 官方响应中 cardStatus 位于 offerMappings[].offer.cardStatus。
        nested_offer = first.get("offer") if isinstance(first.get("offer"), dict) else {}
        card_status = str(nested_offer.get("cardStatus") or "").strip()
        group_id = str(nested_offer.get("groupId") or "")
    campaign_status = ""
    if offers:
        first_offer = offers[0] if isinstance(offers[0], dict) else {}
        campaign_status = str(first_offer.get("status") or "").strip().upper()

    checkpoint.evidence["confirmation"] = {
        "campaign_status": campaign_status,
        "card_status": card_status,
        "group_id": group_id,
        "at": collect_time_iso(),
    }
    checkpoint.last_response_summary = {
        "step": "confirmation",
        "status": campaign_status or "NO_RECORD",
        "checked_at": collect_time_iso(),
    }

    # cardStatus 先于 Campaign 状态裁决：编辑存量商品时 Campaign 可以一直
    # 保持 PUBLISHED，而本次变更被拒绝（HAS_CARD_CAN_UPDATE_ERRORS），
    # 仅凭 Campaign 状态会把失败的修改误报为成功。
    if card_status in _CARD_STATUS_FAILED:
        return _terminal_failure(
            plan,
            checkpoint,
            {
                "summary": (
                    f"Yandex 未接受本次商品变更（cardStatus={card_status}），"
                    "请根据卖家后台的卡片错误提示修正后重新发布。"
                ),
                "field_errors": {
                    "card_status": [
                        f"card 状态：{card_status}；campaign 状态：{campaign_status or '未知'}"
                    ]
                },
                "error_code": "YANDEX_CARD_UPDATE_REJECTED",
                "retryable": False,
                "raw": "",
            },
        )
    if card_status in _CARD_STATUS_PROCESSING:
        # 卡片变更仍在审核：即使 Campaign 已显示 PUBLISHED 也不能确认本次
        # 变更生效，继续有界轮询。
        if checkpoint.retries >= _confirmation_poll_limit(config):
            return _terminal_failure(
                plan,
                checkpoint,
                {
                    "summary": (
                        "Yandex 卡片变更审核超时（cardStatus="
                        f"{card_status}），请稍后在店铺后台核对变更结果。"
                    ),
                    "field_errors": {},
                    "error_code": "YANDEX_CONFIRMATION_TIMEOUT",
                    "retryable": True,
                    "raw": "",
                },
            )
        checkpoint.retries += 1
        checkpoint.next_poll_at = _backoff_at(checkpoint, config)
        return _pending_result(plan, checkpoint)

    if campaign_status == _SUCCESS_CAMPAIGN_STATUS:
        if card_status and card_status not in _CARD_STATUS_ACCEPTED:
            # Campaign 已上架但卡片状态要求补充内容/重新放置等，属于
            # 官方枚举中的非接受态，不能自动确认成功。
            return _terminal_failure(
                plan,
                checkpoint,
                {
                    "summary": (
                        f"Yandex Campaign 状态为 {campaign_status}，但卡片状态为 "
                        f"{card_status}（需要补充内容或在店铺重新放置商品），"
                        "请在卖家后台处理后重试。"
                    ),
                    "field_errors": {"card_status": [card_status]},
                    "error_code": "YANDEX_CARD_STATUS_UNEXPECTED",
                    "retryable": False,
                    "raw": "",
                },
            )
        checkpoint.phase = "terminal"
        return _terminal_success(
            plan,
            checkpoint,
            campaign_status=campaign_status,
            card_status=card_status,
        )
    if campaign_status in _FAILED_CAMPAIGN_STATUSES or campaign_status == _NO_STOCKS_CAMPAIGN_STATUS:
        # NO_STOCKS 不能视为成功：FBY 之外库存未到位、或 FBY 尚未入库。
        reason = (
            "Yandex 商品无库存，发布未确认成功"
            if campaign_status == _NO_STOCKS_CAMPAIGN_STATUS
            else f"Yandex 店铺商品状态异常：{campaign_status}"
        )
        return _terminal_failure(
            plan,
            checkpoint,
            {
                "summary": reason,
                "field_errors": {
                    "campaign_status": [
                        f"campaign 状态：{campaign_status}；card 状态：{card_status or '未知'}"
                    ]
                },
                "error_code": f"YANDEX_CAMPAIGN_{campaign_status}"
                if campaign_status
                else "YANDEX_CAMPAIGN_STATUS_INVALID",
                "retryable": False,
                "raw": "",
            },
        )
    if campaign_status in _PENDING_CAMPAIGN_STATUSES:
        if checkpoint.retries >= _confirmation_poll_limit(config):
            return _terminal_failure(
                plan,
                checkpoint,
                {
                    "summary": (
                        "Yandex 发布确认超时：平台仍在处理，"
                        "请稍后在店铺后台核对商品状态。"
                    ),
                    "field_errors": {},
                    "error_code": "YANDEX_CONFIRMATION_TIMEOUT",
                    "retryable": True,
                    "raw": "",
                },
            )
        checkpoint.retries += 1
        checkpoint.next_poll_at = _backoff_at(checkpoint, config)
        return _pending_result(plan, checkpoint)
    return _terminal_failure(
        plan,
        checkpoint,
        {
            "summary": f"Yandex 店铺商品状态不受支持：{campaign_status}",
            "field_errors": {"campaign_status": [campaign_status]},
            "error_code": "YANDEX_CAMPAIGN_STATUS_INVALID",
            "retryable": False,
            "raw": "",
        },
    )


def poll_yandex_publish_status(
    result: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """依据已持久化 checkpoint 执行下一个 mutation 或只读确认。

    重启恢复时 PublishingBus 直接把持久化的 pending result 交回本函数；
    已完成步骤记录在 checkpoint 中，不会重复执行。
    """

    inner = result.get("result") if isinstance(result.get("result"), dict) else result
    plan = inner.get("plan") if isinstance(inner.get("plan"), dict) else None
    checkpoint_raw = (
        inner.get("checkpoint")
        if isinstance(inner.get("checkpoint"), dict)
        else None
    )
    if not plan or not checkpoint_raw:
        raise RuntimeError("Yandex 待确认发布结果缺少 checkpoint")
    try:
        checkpoint = YandexPublishCheckpoint(**checkpoint_raw)
    except Exception as exc:  # pydantic ValidationError 等
        raise RuntimeError(f"Yandex 发布 checkpoint 无效：{exc}") from exc

    api_token, campaign_id, business_id = _yandex_publish_credentials(config)
    _assert_plan_binding(plan, campaign_id, business_id)

    # 有界退避：未到允许轮询时间时不发起远端请求。
    if checkpoint.next_poll_at > time.time():
        return _pending_result(plan, checkpoint)

    step = _next_pending_step(checkpoint)
    if step is None:
        return _confirmation_readback(plan, checkpoint, api_token, config)
    pending = _execute_mutation(step, plan, checkpoint, api_token, config)
    if pending is not None:
        return pending
    return _pending_result(plan, checkpoint)


def map_yandex_publish_error(error: Exception) -> dict[str, Any]:
    """类型化错误 → 摘要、字段错误、平台 code 与 retryable。"""

    if isinstance(error, YandexApiError):
        field_errors: dict[str, list[str]] = {}
        for row in error.errors:
            message = str(row.get("message") or row.get("code") or "").strip()
            if not message:
                continue
            field = str(row.get("field") or "").strip() or "publish"
            field_errors.setdefault(field, []).append(message)
        if not field_errors:
            field_errors["publish"] = [str(error)]
        next_action = ""
        if isinstance(error.details, dict):
            next_action = str(error.details.get("next_action") or "")
        return {
            "summary": str(error),
            "field_errors": field_errors,
            "error_code": error.code,
            "retryable": bool(error.retryable),
            "next_action": next_action,
            "raw": str(error),
        }
    raw = str(error)
    return {
        "summary": raw[:500] or "Yandex 发布失败",
        "field_errors": {"publish": [raw[:500] or "Yandex 发布失败"]},
        "error_code": "YANDEX_PUBLISH_FAILED",
        "retryable": False,
        "raw": raw,
    }


__all__ = [
    "YANDEX_PUBLISH_STEPS",
    "build_yandex_publish_payload",
    "map_yandex_publish_error",
    "poll_yandex_publish_status",
    "publish_yandex_payload",
    "validate_yandex_publish_payload",
    "yandex_invalid_dictionary_attributes",
    "yandex_invalid_unit_attributes",
    "yandex_offer_identity_conflict",
    "yandex_required_attributes_missing",
]

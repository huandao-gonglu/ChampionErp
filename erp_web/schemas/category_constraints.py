"""类目属性的公开值限制与纯校验；不猜测平台未声明的限制，不改写原值。"""
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


def attribute_value_limits(constraints: Mapping[str, Any]) -> dict[str, Any]:
    result = {"max_length": None, "min_value": None, "max_value": None}
    for key in result:
        raw = constraints.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            number = Decimal(str(raw))
            if not number.is_finite() or (key == "max_length" and (number < 0 or number != number.to_integral_value())):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            raise ValueError(f"平台属性的 {key} 规则无效，请重新读取类目规则") from None
        result[key] = int(number) if key == "max_length" else str(raw).strip()
    return result


def category_attribute_constraint_error(definition: Mapping[str, Any], value: Any) -> str:
    """标量、带单位值和枚举集合共用逐值检查，空值交给必填规则。"""
    limits = attribute_value_limits(definition.get("constraints") or {})
    if isinstance(value, dict):
        values = value.get("values") if isinstance(value.get("values"), list) else [value.get("value")]
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        values = [value]
    kind = str(definition.get("value_type") or "").lower()
    for index, item in enumerate(values, 1):
        raw = item.get("value") if isinstance(item, dict) else item
        if raw is None or str(raw).strip() == "":
            continue
        text = str(raw).strip()
        prefix = f"第 {index} 个值：" if len(values) > 1 else ""
        maximum = limits["max_length"]
        if maximum is not None and len(text) > maximum:
            return f"{prefix}当前 {len(text)} 个字符，超过最大长度 {maximum}，需减少 {len(text) - maximum} 个字符"
        if kind in {"numeric", "number", "integer", "float"}:
            try:
                number = Decimal(text.replace(",", "."))
                if not number.is_finite() or (kind == "integer" and number != number.to_integral_value()):
                    raise InvalidOperation
            except InvalidOperation:
                return f"{prefix}必须是有效的有限{'整数' if kind == 'integer' else '数字'}"
            for key, label in (("min_value", "小于"), ("max_value", "大于")):
                bound = limits[key]
                if bound is not None and ((key == "min_value" and number < Decimal(bound)) or (key == "max_value" and number > Decimal(bound))):
                    return f"{prefix}当前值 {text} 不能{label} {bound}"
    return ""


__all__ = ["attribute_value_limits", "category_attribute_constraint_error"]

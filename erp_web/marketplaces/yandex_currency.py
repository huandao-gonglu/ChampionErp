from __future__ import annotations

"""Yandex 协议币种转换（wire 编码边界）。

内部统一使用 ISO 4217 代码（``RUB``）；Yandex HTTP 协议的价格/设置字段使用
``RUR``。转换只发生在最终 payload 写入（内部 → wire）与 Business settings 发现
（wire → 内部）两个边界，属于 wire 编码而不是币种来源：店铺配置为 CNY 时不会
触发该转换。
"""

from typing import Any

_YANDEX_WIRE_CURRENCY_MAP = {"RUB": "RUR"}
_YANDEX_INTERNAL_CURRENCY_MAP = {"RUR": "RUB"}


def yandex_wire_currency(currency: Any) -> str:
    """内部币种 → Yandex 平台 CurrencyType 枚举。"""

    code = str(currency or "").strip().upper()
    return _YANDEX_WIRE_CURRENCY_MAP.get(code, code)


def yandex_internal_currency(currency: Any) -> str:
    """Yandex wire 币种 → 内部 ISO 4217 代码。"""

    code = str(currency or "").strip().upper()
    return _YANDEX_INTERNAL_CURRENCY_MAP.get(code, code)


__all__ = [
    "yandex_internal_currency",
    "yandex_wire_currency",
]

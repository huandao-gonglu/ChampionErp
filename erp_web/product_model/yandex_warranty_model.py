"""Yandex 保修期限的纯校验与官方字段编译，发布和在线修改共用。"""
from __future__ import annotations

from typing import Any


def compile_yandex_guarantee_period(value: Any) -> dict[str, Any] | None:
    """将保修期限编译为官方 TimePeriodDTO，不补猜保修承诺。"""
    if value is None or value == {}:
        return None
    if not isinstance(value, dict):
        raise ValueError("Yandex 保修期限必须包含时长和单位")
    if set(value) - {"timePeriod", "timeUnit", "comment"}:
        raise ValueError("Yandex 保修期限包含不支持的字段")
    duration = value.get("timePeriod")
    if isinstance(duration, bool) or not str(duration).isascii() or not str(duration).isdigit():
        raise ValueError("Yandex 保修时长必须是非负整数")
    unit = value.get("timeUnit")
    if unit not in ("HOUR", "DAY", "WEEK", "MONTH", "YEAR"):
        raise ValueError("请选择 Yandex 保修期限单位")
    comment = value.get("comment", "")
    if not isinstance(comment, str) or len(comment) > 500:
        raise ValueError("Yandex 保修说明不能超过 500 个字符")
    return {"timePeriod": int(duration), "timeUnit": unit, **({"comment": comment} if comment else {})}


__all__ = ["compile_yandex_guarantee_period"]

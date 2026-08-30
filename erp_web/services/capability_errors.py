from __future__ import annotations

"""普通业务 Capability 的稳定错误边界。

领域 service 不依赖全局任务 schema。Controller/facade 可以把这里的错误
机械转换为 ``CapabilityResult.failed`` 或 ``CapabilityResult.needs_input``。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_INPUT_OWNERS = frozenset({"step", "provided_attributes", "pricing_input"})


@dataclass(frozen=True, slots=True)
class CapabilityInputOption:
    """待补字段的可选项；展示文案与真正提交值明确分离。"""

    value: str
    label: str

    def __post_init__(self) -> None:
        value = str(self.value or "").strip()
        label = str(self.label or value).strip()
        if not value:
            raise ValueError("Capability 输入选项 value 不能为空")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "label", label or value)


class BusinessCapabilityError(RuntimeError):
    """可由上层稳定映射的业务 Capability 错误。

    ``details`` 允许能力附带结构化语义，例如副作用已发出但结果未知的
    ``{"outcome_unknown": True}``；Controller 必须据此区分普通失败与
    不可自动重试的结果未知。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code or "CAPABILITY_FAILED").strip() or "CAPABILITY_FAILED"
        self.retryable = bool(retryable)
        self.details: Mapping[str, Any] | None = (
            dict(details) if isinstance(details, Mapping) and details else None
        )
        super().__init__(str(message or "业务能力执行失败").strip())


class CapabilityInputRequired(BusinessCapabilityError):
    """表示同一步骤可以在获得明确字段后继续。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        key: str,
        label: str,
        reason: str = "",
        options: Sequence[str | CapabilityInputOption] = (),
        input_type: str = "text",
        input_owner: str = "step",
    ) -> None:
        self.key = str(key or "").strip()
        self.label = str(label or self.key).strip()
        self.reason = str(reason or message).strip()
        normalized_options: list[CapabilityInputOption] = []
        seen_values: set[str] = set()
        for option in options:
            if (
                not isinstance(option, CapabilityInputOption)
                and not str(option).strip()
            ):
                continue
            normalized = (
                option
                if isinstance(option, CapabilityInputOption)
                else CapabilityInputOption(value=str(option), label=str(option))
            )
            if normalized.value in seen_values:
                continue
            seen_values.add(normalized.value)
            normalized_options.append(normalized)
        self.options = tuple(normalized_options)
        normalized_type = str(input_type or "text").strip().lower()
        allowed_types = {
            "text",
            "select",
            "multi_select",
            "json_object",
            "string_list",
        }
        if normalized_type not in allowed_types:
            raise ValueError(f"未知 Capability 输入类型：{normalized_type}")
        self.input_type = normalized_type
        normalized_owner = str(input_owner or "step").strip().lower()
        if normalized_owner not in _INPUT_OWNERS:
            raise ValueError(f"未知 Capability 输入 owner：{normalized_owner}")
        self.input_owner = normalized_owner
        super().__init__(
            code,
            message,
        )

    def set_input_owner(self, input_owner: str) -> None:
        normalized_owner = str(input_owner or "step").strip().lower()
        if normalized_owner not in _INPUT_OWNERS:
            raise ValueError(f"未知 Capability 输入 owner：{normalized_owner}")
        self.input_owner = normalized_owner


__all__ = [
    "BusinessCapabilityError",
    "CapabilityInputOption",
    "CapabilityInputRequired",
]

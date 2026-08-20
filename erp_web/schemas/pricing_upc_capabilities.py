from __future__ import annotations

"""定价计算与 UPC 分配/导入的 Capability 契约。"""

from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
)


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


def _normalize_numeric_input(value: object) -> object:
    """兼容部分 Provider 把 JSON 数字参数序列化为字符串的行为。"""

    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            try:
                return float(normalized)
            except ValueError:
                return value
    return value


NumericInput = Annotated[
    float,
    BeforeValidator(
        _normalize_numeric_input,
        json_schema_input_type=float | str,
    ),
]


class PricingCalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    targets: Annotated[
        tuple[dict[str, JsonValue], ...],
        Field(
            min_length=1,
            description=(
                "发布目标数组。每项使用 platform、site；手动运费必须使用 "
                "shipping_quote_mode='manual'、shipping_currency='CNY' 或 "
                "'USD'、shipping_amount；目标利润率使用 "
                "target_margin_percent。"
            ),
        ),
    ]
    exchange_rate_mode: Annotated[TrimmedText, StringConstraints(max_length=40)] = ""
    usd_cny_rate: NumericInput | None = Field(
        default=None,
        description="USD/CNY 手动汇率；可传 JSON number 或数字字符串。",
    )
    mxn_usd_rate: NumericInput | None = Field(
        default=None,
        description="MXN/USD 手动汇率；可传 JSON number 或数字字符串。",
    )
    rub_usd_rate: NumericInput | None = Field(
        default=None,
        description="RUB/USD 手动汇率；可传 JSON number 或数字字符串。",
    )
    rub_cny_rate: NumericInput | None = Field(
        default=None,
        description="RUB/CNY 手动汇率；可传 JSON number 或数字字符串。",
    )
    force_exchange_rate_refresh: bool = False
    common: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            "所有目标共享的核价输入。采购成本字段必须是 cost_cny；可选字段 "
            "包括 weight_kg、length_cm、width_cm、height_cm。不要使用 cost、"
            "target_profit_pct、pricing_input 等别名。"
        ),
    )


class PricingCalculateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    targets: tuple[dict[str, JsonValue], ...] = ()
    exchange_rates: dict[str, JsonValue] = Field(default_factory=dict)
    exchange_rate_mode: TrimmedText = ""


class UpcAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]


class UpcAssignResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: TrimmedText
    upc: TrimmedText
    upc_pool: dict[str, JsonValue] = Field(default_factory=dict)


class UpcImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    values: Annotated[tuple[str, ...], Field(min_length=1)]


class UpcImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    imported: int = Field(default=0, ge=0)
    upc_pool: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "PricingCalculateRequest",
    "PricingCalculateResult",
    "UpcAssignRequest",
    "UpcAssignResult",
    "UpcImportRequest",
    "UpcImportResult",
]

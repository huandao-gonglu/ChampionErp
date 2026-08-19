from __future__ import annotations

"""定价计算与 UPC 分配/导入的 Capability 契约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class PricingCalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    targets: Annotated[
        tuple[dict[str, JsonValue], ...],
        Field(min_length=1),
    ]
    exchange_rate_mode: Annotated[TrimmedText, StringConstraints(max_length=40)] = ""
    usd_cny_rate: float | None = None
    mxn_usd_rate: float | None = None
    rub_usd_rate: float | None = None
    rub_cny_rate: float | None = None
    force_exchange_rate_refresh: bool = False
    common: dict[str, JsonValue] = Field(default_factory=dict)


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

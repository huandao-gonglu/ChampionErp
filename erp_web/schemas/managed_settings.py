"""管理员共享设置及国际物流导入的请求契约。"""
from datetime import date
from typing import Any, Literal, TypedDict, get_args
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

EXCHANGE_RATE_FIELDS = (
    "exchange_rate_api_url", "exchange_rate_timeout_seconds", "exchange_rate_cache_ttl_seconds",
)
AlibabaApplicationType = Literal['alibaba', 'alibaba_crossborder', 'onebound']
ALIBABA_APPLICATION_TYPES = frozenset(get_args(AlibabaApplicationType))


class PublicEnterpriseApplication(TypedDict):
    app_key: str
    redirect_uri: str
    secret_configured: bool


class PublicOneBoundApplication(TypedDict):
    api_key: str
    secret_configured: bool


class PublicExchangeRateSettings(TypedDict):
    exchange_rate_api_url: str
    exchange_rate_timeout_seconds: str
    exchange_rate_cache_ttl_seconds: str


class ManagedSettings(TypedDict):
    ok: bool
    revision: str
    exchange_rates: PublicExchangeRateSettings
    alibaba: PublicEnterpriseApplication
    alibaba_crossborder: PublicEnterpriseApplication
    onebound: PublicOneBoundApplication
    alibaba_api_application: AlibabaApplicationType
    crossborderbus_secret_configured: bool


class TariffVersion(TypedDict):
    version: str
    effective_date: str
    rules_count: int
    error: str


class TariffSettings(TariffVersion):
    platform: Literal['yandex', 'ozon']
    source: Literal['personal', 'shared']
    shared: TariffVersion
    update_available: bool


class TariffSettingsResponse(TypedDict):
    ok: bool
    items: list[TariffSettings]


class TariffRule(TypedDict):
    id: str
    name: str
    carrier: str
    service: str
    transport: str
    group: str
    currency: str
    fixed: str
    per_g: str
    step_g: str
    min_g: str
    max_g: str
    price_min: str
    price_max: str
    price_currency: str
    max_edge_cm: str
    max_sum_cm: str
    volume_divisor_g: str
    battery: bool | None
    liquid: bool | None
    source_cell: str


class TariffRulesResponse(TypedDict):
    ok: bool
    platform: Literal['yandex', 'ozon']
    source: Literal['personal', 'shared']
    version: str
    filename: str
    sheet: str
    imported_at: str
    effective_date: str
    warnings: list[str]
    calculation_notes: list[str]
    rules: list[TariffRule]


class TariffRulesQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['yandex', 'ozon']
    scope: Literal['effective', 'shared'] = 'effective'


class TariffFieldChange(TypedDict):
    before: Any
    after: Any


class TariffRuleChange(TypedDict):
    id: str
    name: str
    fields: dict[str, TariffFieldChange]


class TariffDiff(TypedDict):
    after_version: str
    added: list[dict[str, Any]]
    removed: list[dict[str, Any]]
    changed: list[TariffRuleChange]
    policy_change: Any
    status: str


class ExchangeRateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    exchange_rate_api_url: str = Field(min_length=1, max_length=2048)
    exchange_rate_timeout_seconds: int = Field(ge=1, le=120)
    exchange_rate_cache_ttl_seconds: int = Field(ge=0, le=86400)

    @field_validator("exchange_rate_api_url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("请填写有效的汇率 API 地址")
        return value


class AlibabaApplicationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    app_key: str = Field(max_length=256)
    app_secret: str | None = Field(default=None, max_length=4096)
    redirect_uri: str = Field(max_length=2048)

    @field_validator("redirect_uri")
    @classmethod
    def valid_callback(cls, value):
        if not value:
            return value
        parsed = urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.fragment
                or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}))):
            raise ValueError("回调地址须使用 HTTPS 或本机 HTTP")
        return value


class OneBoundApplicationSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    api_key: str = Field(max_length=256)
    api_secret: str | None = Field(default=None, max_length=4096)


class ManagedSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: str
    exchange_rates: ExchangeRateSettings
    alibaba: AlibabaApplicationSettings | None = None
    alibaba_crossborder: AlibabaApplicationSettings
    onebound: OneBoundApplicationSettings | None = None
    alibaba_api_application: AlibabaApplicationType | None = None
    crossborderbus_client_secret: str | None = Field(default=None, max_length=4096)


class TariffImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform: Literal["yandex", "ozon"]
    scope: Literal["personal", "shared"]
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=12 * 1024 * 1024)
    effective_date: date | None = None
    expected_version: str = Field(max_length=80)
    preview: bool = True

    @field_validator("filename")
    @classmethod
    def valid_filename(cls, value):
        if not value.lower().endswith(".xlsx") or "/" in value or "\\" in value:
            raise ValueError("请选择 XLSX 运费表")
        return value


class TariffUpgradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform: Literal["yandex", "ozon"]
    shared_version: str = Field(min_length=1, max_length=80)
    expected_version: str = Field(min_length=1, max_length=80)


__all__ = ["ALIBABA_APPLICATION_TYPES", "EXCHANGE_RATE_FIELDS", "AlibabaApplicationType", "ManagedSettings", "PublicExchangeRateSettings", "PublicEnterpriseApplication", "PublicOneBoundApplication", "OneBoundApplicationSettings",
           "TariffRule", "TariffRulesResponse", "TariffRulesQuery",
           "TariffVersion", "TariffSettings", "TariffSettingsResponse", "TariffDiff", "TariffFieldChange", "TariffRuleChange", "ExchangeRateSettings", "AlibabaApplicationSettings",
           "ManagedSettingsRequest", "TariffImportRequest", "TariffUpgradeRequest"]

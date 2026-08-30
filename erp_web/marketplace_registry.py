"""后端唯一的平台及站点注册表。

授权按一级平台保存；商品、类目、定价等市场化数据使用 ``platform + site``。
站点默认语言、市场展示币种和刊登币种规则集中维护，避免各业务页面重复硬编码。

每个平台一条 :class:`MarketplaceSpec`：能力集合（capabilities）、字段映射
（preset_key / title_limit / description_limit / language）和
凭据描述符（字段清单 + secret 标记 + test_auth 回调名）。通用逻辑一律查这里，
不允许再散落 ``if platform == ...`` 分支；平台细节只出现在对应适配器里。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Marketplace(StrEnum):
    MERCADO_LIBRE = "mercadolibre"
    YANDEX = "yandex"
    OZON = "ozon"


# 能力常量：发布、payload 预览、类目实时检索、类目属性、订单同步。
CAP_PUBLISH = "publish"
CAP_PREVIEW_PAYLOAD = "preview_payload"
CAP_CATEGORY_SEARCH = "category_search"
CAP_CATEGORY_ATTRIBUTES = "category_attributes"
CAP_ORDERS = "orders"


@dataclass(frozen=True)
class CredentialField:
    """平台凭据字段描述符（secret 字段绝不回显明文）。"""

    key: str
    label: str
    secret: bool = False


@dataclass(frozen=True)
class MarketplaceSpec:
    """一级平台注册项：能力 + 字段映射 + 凭据描述符。"""

    key: str
    label: str
    sites: tuple[dict[str, Any], ...]
    capabilities: frozenset[str]
    preset_key: str
    title_limit: int
    description_limit: int
    credential_fields: tuple[CredentialField, ...]
    # ``module:attribute`` 在线校验器入口；空字符串 = 平台未接入在线授权校验。
    test_auth: str = ""
    # summarize_store_auth 兜底 masked_account 的取值字段顺序。
    masked_account_fields: tuple[str, ...] = ()
    # store_auth_failure_code 的平台兜底错误码。
    auth_failure_code: str = "auth_failed"
    # 发布确认的稳定店铺身份字段组：非空时要求组内字段全部存在，
    # identity 由全部字段共同构成；空 = 由 publish_confirmation 兜底规则解析。
    store_binding_fields: tuple[str, ...] = ()

    @property
    def language(self) -> str:
        return str(self.sites[0]["language"]) if self.sites else ""

    def credential_keys(self) -> tuple[str, ...]:
        return tuple(field.key for field in self.credential_fields)

    def secret_credential_keys(self) -> tuple[str, ...]:
        return tuple(field.key for field in self.credential_fields if field.secret)

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


MARKETPLACE_SPECS: tuple[MarketplaceSpec, ...] = (
    MarketplaceSpec(
        key=Marketplace.MERCADO_LIBRE.value,
        label="美客多",
        sites=(
            {
                "key": "CBT",
                "code": "CBT",
                "label": "Global Selling 全局刊登",
                "language": "es",
            },
            {"key": "MLM", "code": "MLM", "label": "墨西哥", "language": "es"},
            {"key": "MLB", "code": "MLB", "label": "巴西", "language": "pt-BR"},
            {"key": "MLC", "code": "MLC", "label": "智利", "language": "es"},
            {"key": "MCO", "code": "MCO", "label": "哥伦比亚", "language": "es"},
            {"key": "MLA", "code": "MLA", "label": "阿根廷", "language": "es"},
            {"key": "MLU", "code": "MLU", "label": "乌拉圭", "language": "es"},
        ),
        capabilities=frozenset({CAP_PUBLISH, CAP_PREVIEW_PAYLOAD, CAP_CATEGORY_SEARCH, CAP_CATEGORY_ATTRIBUTES, CAP_ORDERS}),
        preset_key="mercadolibre",
        title_limit=60,
        description_limit=50000,
        credential_fields=(
            CredentialField("app_id", "App ID / Client ID"),
            CredentialField("app_secret", "Client Secret", secret=True),
            CredentialField("redirect_uri", "Redirect URI"),
            CredentialField("access_token", "Access Token", secret=True),
            CredentialField("refresh_token", "Refresh Token", secret=True),
        ),
        test_auth="erp_web.runtime_units.store_credentials:_test_mercadolibre_auth",
        masked_account_fields=("shop_name", "user_id"),
        auth_failure_code="mercadolibre_auth_failed",
        store_binding_fields=("user_id",),
    ),
    MarketplaceSpec(
        key=Marketplace.YANDEX.value,
        label="Yandex",
        sites=(
            {"key": "global", "code": "global", "label": "俄罗斯", "language": "ru-RU"},
        ),
        capabilities=frozenset(
            {
                CAP_PUBLISH,
                CAP_PREVIEW_PAYLOAD,
                CAP_CATEGORY_SEARCH,
                CAP_CATEGORY_ATTRIBUTES,
            }
        ),
        preset_key="yandex",
        title_limit=120,
        description_limit=6000,
        credential_fields=(
            CredentialField("api_token", "API-Key Token", secret=True),
            CredentialField("campaign_id", "Campaign ID"),
        ),
        test_auth="erp_web.runtime_units.store_credentials:_test_yandex_auth",
        masked_account_fields=("shop_name", "api_token"),
        auth_failure_code="yandex_auth_failed",
        # 发布确认必须同时绑定在线校验派生的 business_id 与用户输入的
        # campaign_id；shop_name、脱敏 token 或单独 business_id 都不算稳定身份。
        store_binding_fields=("business_id", "campaign_id"),
    ),
    MarketplaceSpec(
        key=Marketplace.OZON.value,
        label="Ozon",
        sites=(
            {"key": "global", "code": "global", "label": "俄罗斯", "language": "ru-RU"},
        ),
        capabilities=frozenset(
            {
                CAP_PUBLISH,
                CAP_PREVIEW_PAYLOAD,
                CAP_CATEGORY_SEARCH,
                CAP_CATEGORY_ATTRIBUTES,
            }
        ),
        preset_key="yandex",
        title_limit=120,
        description_limit=6000,
        credential_fields=(
            CredentialField("client_id", "Client ID"),
            CredentialField("api_key", "API Key", secret=True),
        ),
        test_auth="erp_web.runtime_units.store_credentials:_test_ozon_auth",
        masked_account_fields=("shop_name", "client_id"),
        auth_failure_code="ozon_auth_failed",
    ),
)

_SPECS_BY_KEY: dict[str, MarketplaceSpec] = {spec.key: spec for spec in MARKETPLACE_SPECS}

MARKETPLACE_OPTIONS = tuple(
    {
        "key": spec.key,
        "label": spec.label,
        "title_limit": spec.title_limit,
        "sites": spec.sites,
    }
    for spec in MARKETPLACE_SPECS
)
PLATFORMS = tuple(spec.key for spec in MARKETPLACE_SPECS)


def marketplace_spec(platform: str) -> MarketplaceSpec | None:
    """按平台 key 返回注册项；未知平台返回 None。"""

    return _SPECS_BY_KEY.get(str(platform or "").strip().lower())


def require_marketplace_spec(platform: str) -> MarketplaceSpec:
    spec = marketplace_spec(platform)
    if spec is None:
        raise RuntimeError(f"未注册的平台：{platform or '(空)'}")
    return spec


def platform_label(platform: str) -> str:
    spec = marketplace_spec(platform)
    return spec.label if spec else str(platform or "")


def platform_capabilities(platform: str) -> frozenset[str]:
    spec = marketplace_spec(platform)
    return spec.capabilities if spec else frozenset()


def platform_has_capability(platform: str, capability: str) -> bool:
    return capability in platform_capabilities(platform)


def platforms_with_capability(capability: str) -> tuple[str, ...]:
    return tuple(spec.key for spec in MARKETPLACE_SPECS if capability in spec.capabilities)


def platform_title_limit(platform: str, default: int = 120) -> int:
    spec = marketplace_spec(platform)
    return spec.title_limit if spec else int(default)


def platform_preset_key(platform: str, default: str = "mercadolibre") -> str:
    spec = marketplace_spec(platform)
    return spec.preset_key if spec else default


def marketplace_options() -> list[dict[str, object]]:
    """返回可直接下发给前端的平台注册表副本。"""

    return [{**option, "sites": [dict(site) for site in option["sites"]]} for option in MARKETPLACE_OPTIONS]


def marketplace_site(platform: str, site: str = "") -> dict[str, str]:
    """返回平台站点配置；未指定或未知站点时回退到该平台默认站点。

    注册表只维护站点身份、标签与语言；发布币种唯一事实源是店铺授权配置，
    注册表不再携带 market_currency/listing_currency。
    """

    spec = marketplace_spec(platform)
    if not spec:
        return {"key": "", "code": "", "label": "", "language": ""}
    site_key = str(site or "").strip().lower()
    selected = next(
        (item for item in spec.sites if item["key"].lower() == site_key or item["code"].lower() == site_key),
        spec.sites[0],
    )
    return dict(selected)


def default_marketplace_site(platform: str) -> dict[str, str]:
    """返回一级平台的默认子站点。"""

    return marketplace_site(platform)


__all__ = [
    "CAP_CATEGORY_ATTRIBUTES",
    "CAP_CATEGORY_SEARCH",
    "CAP_ORDERS",
    "CAP_PREVIEW_PAYLOAD",
    "CAP_PUBLISH",
    "CredentialField",
    "MARKETPLACE_OPTIONS",
    "MARKETPLACE_SPECS",
    "Marketplace",
    "MarketplaceSpec",
    "PLATFORMS",
    "default_marketplace_site",
    "marketplace_options",
    "marketplace_site",
    "marketplace_spec",
    "platform_capabilities",
    "platform_has_capability",
    "platform_label",
    "platform_preset_key",
    "platform_title_limit",
    "platforms_with_capability",
    "require_marketplace_spec",
]

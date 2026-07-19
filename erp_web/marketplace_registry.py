"""后端唯一的平台及站点注册表。

授权按一级平台保存；商品、类目、定价等市场化数据使用 ``platform + site``。
站点默认语言和币种集中维护，避免各业务页面重复硬编码。
"""

from __future__ import annotations

from enum import StrEnum


class Marketplace(StrEnum):
    MERCADO_LIBRE = "mercadolibre"
    YANDEX = "yandex"
    OZON = "ozon"


MARKETPLACE_OPTIONS = (
    {
        "key": Marketplace.MERCADO_LIBRE.value,
        "label": "美客多",
        "sites": (
            {"key": "CBT", "code": "CBT", "label": "全局", "language": "es", "currency": "USD"},
            {"key": "MLM", "code": "MLM", "label": "墨西哥", "language": "es", "currency": "MXN"},
            {"key": "MLB", "code": "MLB", "label": "巴西", "language": "pt-BR", "currency": "BRL"},
            {"key": "MLC", "code": "MLC", "label": "智利", "language": "es", "currency": "CLP"},
            {"key": "MCO", "code": "MCO", "label": "哥伦比亚", "language": "es", "currency": "COP"},
            {"key": "MLA", "code": "MLA", "label": "阿根廷", "language": "es", "currency": "ARS"},
        ),
    },
    {
        "key": Marketplace.YANDEX.value,
        "label": "Yandex",
        "sites": (
            {"key": "global", "code": "global", "label": "俄罗斯", "language": "ru-RU", "currency": "RUB"},
        ),
    },
    {
        "key": Marketplace.OZON.value,
        "label": "Ozon",
        "sites": (
            {"key": "global", "code": "global", "label": "俄罗斯", "language": "ru-RU", "currency": "RUB"},
        ),
    },
)
PLATFORMS = tuple(option["key"] for option in MARKETPLACE_OPTIONS)


def marketplace_options() -> list[dict[str, object]]:
    """返回可直接下发给前端的平台注册表副本。"""

    return [{**option, "sites": [dict(site) for site in option["sites"]]} for option in MARKETPLACE_OPTIONS]


def marketplace_site(platform: str, site: str = "") -> dict[str, str]:
    """返回平台站点配置；未指定或未知站点时回退到该平台默认站点。"""

    platform_key = str(platform or "").strip().lower()
    site_key = str(site or "").strip().lower()
    option = next((item for item in MARKETPLACE_OPTIONS if item["key"] == platform_key), None)
    if not option:
        return {"key": "", "code": "", "label": "", "language": "", "currency": ""}
    sites = option["sites"]
    selected = next((item for item in sites if item["key"].lower() == site_key or item["code"].lower() == site_key), sites[0])
    return dict(selected)


def default_marketplace_site(platform: str) -> dict[str, str]:
    """返回一级平台的默认子站点。"""

    return marketplace_site(platform)


__all__ = [
    "MARKETPLACE_OPTIONS",
    "Marketplace",
    "PLATFORMS",
    "default_marketplace_site",
    "marketplace_options",
    "marketplace_site",
]

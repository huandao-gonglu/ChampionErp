from erp_web.marketplace_registry import default_marketplace_site, marketplace_options, marketplace_site


def test_marketplace_registry_exposes_parent_platforms_and_sites() -> None:
    options = marketplace_options()

    assert [item["key"] for item in options] == ["mercadolibre", "yandex", "ozon"]
    assert [site["code"] for site in options[0]["sites"]] == ["CBT", "MLM", "MLB", "MLC", "MCO", "MLA"]
    assert default_marketplace_site("mercadolibre")["code"] == "CBT"
    assert default_marketplace_site("yandex")["code"] == "global"
    assert default_marketplace_site("ozon")["currency"] == "RUB"


def test_marketplace_site_includes_language_and_currency_defaults() -> None:
    assert marketplace_site("mercadolibre", "MLM")["language"] == "es"
    assert marketplace_site("mercadolibre", "MLB") == {
        "key": "MLB",
        "code": "MLB",
        "label": "巴西",
        "language": "pt-BR",
        "currency": "BRL",
    }

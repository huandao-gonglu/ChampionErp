from erp_web.marketplace_registry import default_marketplace_site, marketplace_options, marketplace_site


def test_marketplace_registry_exposes_parent_platforms_and_sites() -> None:
    options = marketplace_options()

    assert [item["key"] for item in options] == ["mercadolibre", "yandex", "ozon"]
    assert [site["code"] for site in options[0]["sites"]] == ["CBT", "MLM", "MLB", "MLC", "MCO", "MLA"]
    assert default_marketplace_site("mercadolibre")["code"] == "CBT"
    assert default_marketplace_site("yandex")["code"] == "global"
    assert default_marketplace_site("ozon")["code"] == "global"


def test_marketplace_sites_carry_identity_only_without_currency() -> None:
    # 注册表只维护站点身份、标签与语言；发布币种唯一事实源是店铺授权配置，
    # 注册表不再携带 market_currency/listing_currency。
    assert marketplace_site("mercadolibre", "MLM")["language"] == "es"
    assert marketplace_site("mercadolibre", "MLB") == {
        "key": "MLB",
        "code": "MLB",
        "label": "巴西",
        "language": "pt-BR",
    }
    for option in marketplace_options():
        for item in option["sites"]:
            assert "listing_currency" not in item
            assert "market_currency" not in item

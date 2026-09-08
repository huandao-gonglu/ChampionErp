"""搜索语言来自平台接口，商品语言不能覆盖；错误整批不触发查询。"""

import pytest

from erp_web.marketplace_registry import marketplace_site
from erp_web.schemas.category_search_language import category_search_language, validate_category_keywords
from erp_web.schemas.ai_tools import AiToolExecutionError


@pytest.mark.parametrize("platform,site,language", [
    ("yandex", "global", "ru-RU"), ("ozon", "global", "ru-RU"),
    ("mercadolibre", "CBT", "en"), ("mercadolibre", "MLB", "pt-BR"),
    *[("mercadolibre", site, "es") for site in ("MLM", "MLC", "MCO", "MLA", "MLU")],
])
def test_market_search_language_is_fixed(platform, site, language):
    assert category_search_language(platform, site) == language
    assert category_search_language(platform, site.lower()) == language
    if site == "CBT":
        assert marketplace_site(platform, site)["language"] == "es"


@pytest.mark.parametrize("language,keywords", [
    ("ru-RU", ["балаклава", "防晒面罩"]), ("ru-RU", ["sun mask"]),
    ("en", ["маска"]), ("es", ["ventilador", "风扇"]),
    ("pt-BR", ["风扇 ventilador"]),
])
def test_wrong_script_is_rejected(language, keywords):
    with pytest.raises(AiToolExecutionError) as error:
        validate_category_keywords(keywords, language)
    assert error.value.code == "CATEGORY_SEARCH_LANGUAGE_MISMATCH"
    assert language in str(error.value)


def test_model_and_brand_can_accompany_native_product_name():
    validate_category_keywords(["маска Golovejoy XKZ42", "USB вентилятор"], "ru-RU")
    validate_category_keywords(["ventilador USB"], "pt-BR")


def test_unknown_market_cannot_silently_use_default_language():
    with pytest.raises(AiToolExecutionError, match="有效的目标站点"):
        category_search_language("mercadolibre", "UNKNOWN")

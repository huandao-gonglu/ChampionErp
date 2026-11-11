"""普通设置页可复用的业务配置视图；凭据及本机连接由原 owner 管理。"""
from erp_web.services.ai_model_config import normalize_ai_models, normalize_ai_use_case_bindings
from erp_web.stores.config_store import summarize_store_auth_states
from erp_web.services.listing_currency_service import store_identity_for_platform, store_listing_currency_from_auth, public_currency_configuration


def select(value, fields):
    return {key: value[key] for key in fields if key in value}


def model_settings(context, _):
    config = context.config.load_app_config()
    return {'models': [select(model, ('id', 'name', 'enabled', 'provider', 'model', 'connection_type',
            'capabilities', 'api_style')) for model in normalize_ai_models(config.get('ai_models'))],
            'bindings': normalize_ai_use_case_bindings(config.get('ai_use_case_bindings'))}


def research_settings(context, _):
    config = context.config.load_app_config()['product_research']
    provider_fields = ('id', 'name', 'source_type', 'platform', 'enabled', 'priority', 'supported_markets',
                       'supported_languages', 'supported_data_types', 'auth_required', 'rate_limit_per_minute', 'compliance_note')
    def provider(row):
        private = row.get('config_json') or {}
        return {**select(row, provider_fields), 'config_json': select(private, ('provider_strategy',)),
                'api_key_configured': bool(private.get('api_key'))}
    targets = []
    for target in config.get('target_markets', []):
        targets.append({**select(target, ('id', 'platform', 'site', 'display_name', 'enabled')),
                        'search_methods': [select(item, ('method_id', 'enabled')) for item in target.get('search_methods', [])]})
    return {**select(config, ('search_defaults', 'provider_runtime')), 'target_markets': targets,
            'search_providers': [provider(row) for row in config.get('search_providers', [])],
            'source_registry': [provider(row) for row in config.get('source_registry', [])]}


def store_settings(context, _):
    config = context.config.load_store_config()
    fields = ('shop_name', 'site_id', 'business_id', 'campaign_id', 'warehouse_id', 'category_id',
              'currency_id', 'fulfillment', 'fulfillment_model', 'listing_currency', 'listing_currencies')
    stores = []
    for platform in ('yandex', 'ozon', 'mercadolibre', 'wildberries'):
        section = config.get(platform, {})
        currency = public_currency_configuration(store_listing_currency_from_auth(
            platform, store_identity_for_platform(platform, section), section))
        currency.pop('currency_error_message', None)
        stores.append({'platform': platform, **select(section, fields), 'currency_configuration': currency})
    return {'stores': stores,
            'listing': select(config.get('listing', {}), ('stock', 'sku', 'currency_id', 'condition',
                             'listing_type_id', 'mercadolibre_logistic_type', 'mercadolibre_price', 'yandex_price', 'ozon_price')),
            'authorization': {key: select(value, ('platform', 'status', 'checked_at', 'error_code', 'shop_name', 'site_id', 'bound'))
                              for key, value in summarize_store_auth_states(config).items()}}


def collection_settings(context, _):
    config = context.config.load_app_config()
    return {'auto_ai_recognition': config.get('auto_ai_recognition', '0'),
            'alibaba_cookie_configured': bool(config.get('alibaba_cookie')),
            'api': {'timeout_seconds': config['1688_api']['timeout_seconds']}}


def pricing_defaults(context, _):
    defaults = context.config.load_app_config()['pricing_defaults']
    return {**{key: value for key, value in defaults.items() if key != 'exchange_rate_api_url'},
            'exchange_rate_api_configured': bool(defaults.get('exchange_rate_api_url'))}


__all__ = ['collection_settings', 'model_settings', 'pricing_defaults', 'research_settings', 'store_settings']

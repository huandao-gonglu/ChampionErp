"""真实账号请求验证共享配置权限、费率隔离及显式升级。"""
import base64
from copy import deepcopy
import json
from decimal import Decimal

import pytest

from erp_web.context import bind_context
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService
from erp_web.services.alibaba_credentials import application_fingerprint
from erp_web.international_shipping.models import Package
from erp_web.runtime_units.pricing_runtime import PricingSession
from erp_web.runtime_units.draft_pricing import _pricing_environment
from tests.test_accounts import accounts_http, register
from tests.test_tariff_store import YA, xlsx


@pytest.fixture(autouse=True)
def clean_enterprise_environment(monkeypatch):
    for prefix in ('ERP_ALIBABA_', 'ERP_ALIBABA_CROSSBORDER_'):
        for name in ('APP_KEY', 'APP_SECRET', 'REDIRECT_URI'):
            monkeypatch.delenv(prefix + name, raising=False)
    monkeypatch.delenv('ERP_CROSSBORDERBUS_CLIENT_SECRET', raising=False)


def settings_body(client, url):
    response = client.get(url + '/api/admin/settings')
    assert response.status_code == 200
    data = response.json()
    for key in ('ok', 'crossborderbus_secret_configured'):
        data.pop(key)
    for key in ('alibaba', 'alibaba_crossborder'):
        data[key].pop('secret_configured')
    return data


def post_settings(client, url, body):
    response = client.post(url + '/api/admin/settings', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def tariff_items(client, url):
    response = client.get(url + '/api/shipping/tariffs')
    assert response.status_code == 200, response.text
    return {item['platform']: item for item in response.json()['items']}


def import_body(client, url, tmp_path, *, scope='shared', fixed=78, preview=False):
    source = tmp_path / 'rates.xlsx'
    xlsx(source, rows=[{**YA, 'fixed': fixed}])
    item = tariff_items(client, url)['yandex']
    version = item['shared']['version'] if scope == 'shared' else item['version']
    return dict(platform='yandex', scope=scope, filename=source.name, preview=preview,
                expected_version=version, content_base64=base64.b64encode(source.read_bytes()).decode())


def import_rates(client, url, body):
    response = client.post(url + '/api/shipping/tariffs/import', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_rates_and_forbidden_categories_are_shared_and_user_cannot_overwrite(accounts_http):
    url, runtime, login = accounts_http
    admin = login()
    user = register(url)
    client = login('user@example.com')
    body = settings_body(admin, url)
    previous_api = body['exchange_rates']['exchange_rate_api_url']
    runtime.root.db.save_exchange_rates({'USD/CNY': 7, 'USD/MXN': 18}, '2026-11-01T00:00:00Z')
    runtime.root.db.set_runtime_secret('exchange_rates', 'source', None)
    body['exchange_rates']['exchange_rate_api_url'] = 'https://shared.example/rates'
    post_settings(admin, url, body)
    assert runtime.root.db.load_exchange_rates()['source'] == previous_api
    context = runtime.context_for(next(row for row in runtime.store.users() if row['id'] == user['id']))
    assert context.user_id == user['id']
    assert context.config.load_app_config()['pricing_defaults']['exchange_rate_api_url'] == 'https://shared.example/rates'
    assert client.get(url + '/api/admin/settings').status_code == 403
    assert client.post(url + '/api/admin/settings', json=body).status_code == 403
    assert 'pricing_defaults' not in client.get(url + '/api/state').json()['appConfig']
    assert client.post(url + '/api/save-settings', json={'appConfig': {
        'pricing_defaults': {'exchange_rate_api_url': 'https://user.example/rates'}}}).status_code == 403
    assert client.post(url + '/api/ai-config/save', json={
        'pricing_defaults': {'exchange_rate_api_url': 'https://user.example/rates'}}).status_code == 403
    assert client.post(url + '/api/system-settings/forbidden-categories/import', json={}).status_code == 403
    root = runtime.root.config
    root.save_forbidden_categories({'categories': [{'category_id': '123', 'name': '测试禁售'}]})
    assert client.get(url + '/api/system-settings/forbidden-categories').json()['categories'] == root.forbidden_categories()['categories']
    # 个人配置持久化不会复制共享名单或管理员的 API 地址。
    personal = context.config.load_app_config()
    context.config.save_app_config(personal)
    raw = json.loads(context.paths.app_config_path.read_text())
    assert raw['forbidden_categories']['categories'] == []
    assert raw['pricing_defaults']['exchange_rate_api_url'] != 'https://shared.example/rates'


def test_enterprise_rotation_invalidates_each_users_authorization_even_when_reverted(accounts_http):
    url, runtime, login = accounts_http
    admin = login()
    user = register(url)
    body = settings_body(admin, url)
    body['alibaba'] = dict(app_key='123', app_secret='secret-a', redirect_uri='https://erp.example/auth/alibaba/callback')
    body['crossborderbus_client_secret'] = 'bus-secret-a'
    public = post_settings(admin, url, body)
    assert 'secret-a' not in json.dumps(public)
    context = runtime.context_for(next(row for row in runtime.store.users() if row['id'] == user['id']))
    store = context.config
    application = store.alibaba_application()
    store.update_alibaba_credentials({'authorization': {
        'access_token': 'private-token', 'application_fingerprint': application_fingerprint(application)}})
    store.save_crossborderbus_credentials({**store.crossborderbus_credentials(), 'app_uid': 8, 'access_token': 'bus-token'})
    assert AlibabaAuthorizationService(store).status()['authorized']
    assert store.crossborderbus_credentials()['app_uid'] == 8
    late_bus_receipt = store.crossborderbus_credentials()
    for app_secret, bus_secret in [('secret-b', 'bus-secret-b'), ('secret-a', 'bus-secret-a')]:
        body = settings_body(admin, url)
        body['alibaba']['app_secret'] = app_secret
        body['crossborderbus_client_secret'] = bus_secret
        post_settings(admin, url, body)
        assert not AlibabaAuthorizationService(store).status()['authorized']
        assert 'app_uid' not in store.crossborderbus_credentials()
        with pytest.raises(ValueError, match='配置已变化'):
            store.save_crossborderbus_credentials(late_bus_receipt)
    assert store.alibaba_credentials()['authorization']['access_token'] == 'private-token'
    assert runtime.root.config.alibaba_credentials().get('authorization', {}) == {}


def test_managed_settings_compare_revision_and_never_echo_validation_secrets(accounts_http):
    url, _, login = accounts_http
    admin = login()
    first = settings_body(admin, url)
    second = deepcopy(first)
    first['exchange_rates']['exchange_rate_cache_ttl_seconds'] = 7
    post_settings(admin, url, first)
    assert admin.post(url + '/api/admin/settings', json=second).status_code == 409
    invalid = settings_body(admin, url)
    invalid['alibaba']['app_secret'] = ['sensitive-value']
    result = admin.post(url + '/api/admin/settings', json=invalid)
    assert result.status_code == 400
    assert 'sensitive-value' not in result.text


def test_tariff_publication_personal_override_and_confirmed_upgrade(accounts_http, tmp_path):
    url, runtime, login = accounts_http
    admin = login()
    user = register(url)
    client = login('user@example.com')
    initial = tariff_items(client, url)
    assert initial['ozon']['version'] and initial['ozon']['source'] == 'shared'
    original = import_body(admin, url, tmp_path)
    preview = import_rates(admin, url, {**original, 'preview': True})
    assert preview['preview'] and not tariff_items(client, url)['yandex']['version']
    import_rates(admin, url, original)
    first = tariff_items(client, url)['yandex']
    assert first['source'] == 'shared' and first['version']
    denied = import_body(client, url, tmp_path, fixed=100)
    assert client.post(url + '/api/shipping/tariffs/import', json=denied).status_code == 403
    personal = import_body(client, url, tmp_path, scope='personal', fixed=101)
    import_rates(client, url, personal)
    custom = tariff_items(client, url)['yandex']
    assert custom['source'] == 'personal' and not custom['update_available']
    import_rates(admin, url, import_body(admin, url, tmp_path, fixed=110))
    pending = tariff_items(client, url)['yandex']
    assert pending['version'] == custom['version'] and pending['update_available']
    context = runtime.context_for(next(row for row in runtime.store.users() if row['id'] == user['id']))
    with bind_context(context):
        assert context.tariffs.load('yandex')['version'] == custom['version']
        assert context.yandex_logistics.routes()
        environment = _pricing_environment()
        quotes = PricingSession().shipping.module.quote('yandex', Package(100, 10, 10, 10),
            lambda *args: Decimal(100), cny_per_rub=Decimal('0.08'))
        assert quotes.candidates[0].evidence['tariff_version'] == custom['version']
    upgrade = dict(platform='yandex', shared_version=pending['shared']['version'], expected_version=custom['version'])
    # 确认弹窗打开后管理员再次发布，旧确认不能覆盖个人选择。
    import_rates(admin, url, import_body(admin, url, tmp_path, fixed=120))
    with bind_context(context):
        assert _pricing_environment() == environment
    assert client.post(url + '/api/shipping/tariffs/upgrade', json=upgrade).status_code == 409
    upgrade['shared_version'] = tariff_items(client, url)['yandex']['shared']['version']
    assert client.post(url + '/api/shipping/tariffs/upgrade', json=upgrade).status_code == 200
    followed = tariff_items(client, url)['yandex']
    assert followed['source'] == 'shared' and not followed['update_available']
    assert context.tariffs.load('yandex')['version'] == followed['version']
    with bind_context(context):
        assert _pricing_environment() != environment
    import_rates(admin, url, import_body(admin, url, tmp_path, fixed=130))
    assert tariff_items(client, url)['yandex']['version'] != followed['version']


def test_invalid_tariff_and_stale_import_preserve_active_version(accounts_http, tmp_path):
    url, _, login = accounts_http
    admin = login()
    original = import_body(admin, url, tmp_path)
    import_rates(admin, url, original)
    assert admin.post(url + '/api/shipping/tariffs/import', json=original).status_code == 409
    before = tariff_items(admin, url)
    invalid = import_body(admin, url, tmp_path)
    invalid['content_base64'] = base64.b64encode(b'invalid xlsx').decode()
    assert admin.post(url + '/api/shipping/tariffs/import', json=invalid).status_code == 400
    assert tariff_items(admin, url) == before


def test_saved_price_using_old_tariff_is_blocked_before_publication(monkeypatch):
    from erp_web.context import get_context
    from erp_web.runtime_units import publish_validation
    from erp_web.runtime_units.publish_validation_session import PublishValidationSession
    from erp_web.services.pricing_service import pricing_calculation_fingerprint

    monkeypatch.setattr(publish_validation, 'single_sku_publish_draft', lambda product, draft: draft)
    monkeypatch.setattr(publish_validation, 'store_listing_currency_ready', lambda state: True)
    monkeypatch.setattr(publish_validation, 'store_listing_currency_from_auth', lambda *args: {
        'listing_currency': 'CNY', 'currency_fingerprint': 'account-cny'})
    version = get_context().tariffs.load('ozon')['version']
    basis = {'listing_currency': 'CNY', 'currency_fingerprint': 'account-cny',
             'shipping_evidence': {'source': 'ogl_tariff', 'tariff_version': version}}
    selected = {'applied_price': {'amount': '100', 'currency': 'CNY'}, 'calculation_basis': basis,
                'calculation_fingerprint': pricing_calculation_fingerprint(basis)}
    draft = {'platform': 'ozon', 'site': 'global', 'listing_currency': 'CNY', 'selected_pricing': selected}
    assert publish_validation._selected_price_errors({}, draft, session=PublishValidationSession(sku_inputs_checked=True)) == []
    basis['shipping_evidence']['tariff_version'] = 'ozon-old'
    selected['calculation_fingerprint'] = pricing_calculation_fingerprint(basis)
    errors = publish_validation._selected_price_errors({}, draft, session=PublishValidationSession(sku_inputs_checked=True))
    assert errors[0]['code'] == 'PRICING_STALE'
    assert '运费表已更新' in errors[0]['message']


@pytest.mark.parametrize('kind', ['exchange_rate', 'exchange', 'pricing'])
def test_rate_test_aliases_always_use_the_managed_api(kind, monkeypatch):
    from erp_web.context import get_context
    from erp_web.facades import auth_config_facade

    configured = get_context().config.load_app_config()['pricing_defaults']
    calls = []
    def test_config(received_kind, config, value):
        calls.append(config)
        return {'ok': True}
    monkeypatch.setattr(auth_config_facade, 'test_api_config', test_config)
    result, status = auth_config_facade.test_api_config_payload({'kind': kind, 'config': {
        'exchange_rate_api_url': 'https://user-controlled.example/rates'}})
    assert status == 200 and result['ok']
    assert calls == [configured]

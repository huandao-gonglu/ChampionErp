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
    monkeypatch.delenv('ERP_GEMINI_CANVAS_URL', raising=False)


def settings_body(client, url):
    response = client.get(url + '/api/admin/settings')
    assert response.status_code == 200
    data = response.json()
    for key in ('ok', 'crossborderbus_secret_configured'):
        data.pop(key)
    for key in ('alibaba', 'alibaba_crossborder'):
        data[key].pop('secret_configured')
    data['onebound'].pop('secret_configured')
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


def tariff_rules(client, url, platform='yandex', scope='effective'):
    response = client.get(url + '/api/shipping/tariffs/rules', params={'platform': platform, 'scope': scope})
    assert response.status_code == 200, response.text
    return response.json()


def test_imported_rules_are_visible_with_personal_isolation_and_explicit_units(accounts_http, tmp_path):
    url, _, login = accounts_http
    admin = login()
    register(url)
    user = login('user@example.com')
    import_rates(admin, url, import_body(admin, url, tmp_path))
    default = tariff_rules(user, url)
    assert default['source'] == 'shared' and default['filename'] == 'rates.xlsx'
    rule = default['rules'][0]
    assert rule['currency'] == rule['price_currency'] == 'RUB'
    assert rule['max_g'] == '500' and rule['price_max'] == '1500'
    assert rule['max_edge_cm'] == '60' and rule['max_sum_cm'] == '90'
    assert rule['liquid'] is None
    assert '/tmp/' not in json.dumps(default) and '/var/' not in json.dumps(default)
    import_rates(user, url, import_body(user, url, tmp_path, scope='personal', fixed=101))
    assert tariff_rules(user, url)['rules'][0]['fixed'] == '101'
    assert tariff_rules(user, url)['source'] == 'personal'
    assert tariff_rules(user, url, scope='shared')['rules'][0]['fixed'] == '78'
    assert tariff_rules(admin, url)['rules'][0]['fixed'] == '78'
    ogl = tariff_rules(user, url, platform='ozon')
    assert len(ogl['rules']) == 6
    big = next(row for row in ogl['rules'] if row['group'] == 'Big')
    assert big['currency'] == big['price_currency'] == 'CNY'
    assert big['volume_divisor_g'] == '12' and big['step_g'] == '1'
    for query in ({'platform': 'mercadolibre'}, {'platform': 'yandex', 'scope': 'personal'},
                  {'platform': 'yandex', 'owner': 'other'}, {'platform': 'yandex', 'scope': '../private'}):
        assert user.get(url + '/api/shipping/tariffs/rules', params=query).status_code == 400


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


@pytest.mark.parametrize('provider', ['alibaba', 'alibaba_crossborder'])
def test_enterprise_rotation_invalidates_each_users_authorization_even_when_reverted(accounts_http, provider):
    url, runtime, login = accounts_http
    admin = login()
    user = register(url)
    body = settings_body(admin, url)
    body[provider] = dict(app_key='123', app_secret='secret-a', redirect_uri='https://erp.example/auth/alibaba/callback')
    body['onebound'] = dict(api_key='ob-key-a', api_secret='ob-secret-a', initial_grant=200)
    body['alibaba_api_application'] = provider
    body['crossborderbus_client_secret'] = 'bus-secret-a'
    public = post_settings(admin, url, body)
    assert 'secret-a' not in json.dumps(public)
    admin_store = runtime.root.config
    user_store = runtime.context_for(next(row for row in runtime.store.users() if row['id'] == user['id'])).config

    # 管理员持有企业应用的个人授权；普通用户持有万邦和跨境巴士授权。
    admin_app = admin_store.alibaba_application(provider)
    admin_store.update_alibaba_credentials({'authorization': {
        'access_token': 'admin-token', 'application_fingerprint': application_fingerprint(admin_app)}}, provider=provider)
    ob_app = user_store.alibaba_application('onebound')
    user_store.update_alibaba_credentials({'authorization': {
        'access_token': 'user-ob-token', 'credential_type': 'access_token', 'member_id': 'buyer-u',
        'expires_at': 4_000_000_000, 'application_fingerprint': application_fingerprint(ob_app)}}, provider='onebound')
    user_store.save_crossborderbus_credentials({**user_store.crossborderbus_credentials(), 'app_uid': 8, 'access_token': 'bus-token'})
    assert AlibabaAuthorizationService(admin_store).status(provider)['authorized']
    assert AlibabaAuthorizationService(user_store).status()['authorized']
    assert user_store.crossborderbus_credentials()['app_uid'] == 8

    # 仅修改万邦默认基础额度，不更换 Key/Secret，不应使已有万邦授权失效。
    body = settings_body(admin, url)
    body['onebound']['initial_grant'] = 350
    saved_grant = post_settings(admin, url, body)
    assert saved_grant['onebound']['initial_grant'] == 350
    assert AlibabaAuthorizationService(user_store).status()['authorized']

    late_bus_receipt = user_store.crossborderbus_credentials()
    for app_secret, ob_secret, bus_secret in [('secret-b', 'ob-secret-b', 'bus-secret-b'), ('secret-a', 'ob-secret-a', 'bus-secret-a')]:
        body = settings_body(admin, url)
        body[provider]['app_secret'] = app_secret
        body['onebound']['api_secret'] = ob_secret
        body['crossborderbus_client_secret'] = bus_secret
        post_settings(admin, url, body)
        assert not AlibabaAuthorizationService(admin_store).status(provider)['authorized']
        assert not AlibabaAuthorizationService(user_store).status()['authorized']
        assert 'app_uid' not in user_store.crossborderbus_credentials()
        with pytest.raises(ValueError, match='配置已变化'):
            user_store.save_crossborderbus_credentials(late_bus_receipt)
    assert admin_store.alibaba_credentials(provider)['authorization']['access_token'] == 'admin-token'
    assert user_store.alibaba_credentials('onebound')['authorization']['access_token'] == 'user-ob-token'


def test_admin_application_switch_is_shared_and_preserves_separate_buyer_grants(accounts_http):
    from erp_web.services.alibaba_credentials import buyer_scope
    from erp_web.services.alibaba_self_purchase_service import account_scope

    url, runtime, login = accounts_http
    admin = login()
    user = register(url)
    client = login('user@example.com')
    body = settings_body(admin, url)
    assert body['alibaba_api_application'] == 'alibaba'
    assert body['onebound']['initial_grant'] == 200
    for provider, key in [('alibaba', '1938727'), ('alibaba_crossborder', '4848173')]:
        body[provider] = dict(app_key=key, app_secret=provider + '-secret', redirect_uri='https://erp.example/auth/alibaba/callback')
    public = post_settings(admin, url, body)
    admin_store = runtime.root.config
    user_store = runtime.context_for(next(row for row in runtime.store.users() if row['id'] == user['id'])).config
    grants = {}
    scopes = {}
    for provider in ('alibaba', 'alibaba_crossborder'):
        body = settings_body(admin, url)
        body['alibaba_api_application'] = provider
        post_settings(admin, url, body)
        # 管理员切换方案只影响管理员自己；普通用户固定使用万邦。
        assert admin.get(url + '/api/alibaba/authorization').json()['application_type'] == provider
        assert client.get(url + '/api/alibaba/authorization').json()['application_type'] == 'onebound'
        application = admin_store.alibaba_application(provider)
        scopes[provider] = buyer_scope(application['app_key'], 'same-buyer')
        grants[provider] = {'access_token': provider + '-token', 'member_id': 'same-buyer',
            'application_fingerprint': application_fingerprint(application), 'account_scope': scopes[provider]}
        admin_store.update_alibaba_credentials({'authorization': grants[provider]}, provider=provider)
        assert account_scope(AlibabaAuthorizationService(admin_store).runtime_config()) == scopes[provider]
    assert scopes['alibaba'] != scopes['alibaba_crossborder']
    late_application = admin_store.alibaba_application('alibaba_crossborder')
    late_state = admin_store.alibaba_credentials('alibaba_crossborder')
    stale_settings = settings_body(admin, url)
    body = deepcopy(stale_settings)
    body['alibaba_api_application'] = 'alibaba'
    switched = post_settings(admin, url, body)
    assert switched['revision'] != stale_settings['revision']
    assert admin.post(url + '/api/admin/settings', json=stale_settings).status_code == 409
    with pytest.raises(ValueError, match='已变化'):
        admin_store.update_alibaba_credentials({'authorization': {'access_token': 'late-token'}}, expected=late_state, application=late_application)
    assert admin_store.alibaba_credentials('alibaba')['authorization'] == grants['alibaba']
    assert user_store.alibaba_application('onebound') == admin_store.alibaba_application('onebound')
    assert admin_store.alibaba_application('alibaba')['credential_generation']
    assert AlibabaAuthorizationService(admin_store).status()['authorized']
    # 旧客户端省略选择与自用配置时，保留管理员的现有选择和原密钥。
    body = settings_body(admin, url)
    body.pop('alibaba_api_application')
    body.pop('alibaba')
    assert post_settings(admin, url, body)['alibaba_api_application'] == 'alibaba'
    body = settings_body(admin, url)
    body['alibaba_api_application'] = 'alibaba_crossborder'
    final = post_settings(admin, url, body)
    assert admin_store.alibaba_credentials('alibaba_crossborder')['authorization'] == grants['alibaba_crossborder']
    assert AlibabaAuthorizationService(admin_store).status()['authorized']
    assert not user_store.alibaba_credentials('onebound').get('authorization')
    assert public['alibaba'] == final['alibaba']
    assert public['alibaba_crossborder'] == final['alibaba_crossborder']
    for value in (public, switched, final):
        assert not any(secret in json.dumps(value) for secret in ('alibaba-secret', 'alibaba_crossborder-secret', 'late-token'))
    body = settings_body(admin, url)
    body['alibaba_api_application'] = 'unknown'
    assert admin.post(url + '/api/admin/settings', json=body).status_code == 400
    assert client.post(url + '/api/admin/settings', json=body).status_code == 403


def test_managed_settings_compare_revision_and_never_echo_validation_secrets(accounts_http):
    url, _, login = accounts_http
    admin = login()
    first = settings_body(admin, url)
    second = deepcopy(first)
    first['exchange_rates']['exchange_rate_cache_ttl_seconds'] = 7
    post_settings(admin, url, first)
    assert admin.post(url + '/api/admin/settings', json=second).status_code == 409
    invalid = settings_body(admin, url)
    invalid['alibaba_crossborder']['app_secret'] = ['sensitive-value']
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


def test_admin_gemini_canvas_url_is_shared_validated_and_resettable(accounts_http):
    from erp_shared.browser_ai import DEFAULT_GEMINI_CANVAS_URL

    url, runtime, login = accounts_http
    admin = login()
    user = register(url)
    client = login('user@example.com')

    body = settings_body(admin, url)
    assert body['gemini_canvas_url'] == DEFAULT_GEMINI_CANVAS_URL

    # 非法或非 Gemini 域名地址被拒绝。
    for bad_url in (
        'https://evil.example/share/abc',
        'http://gemini.google.com/share/abc',
        'https://gemini.google.com/app',
        'https://gemini.google.com/share/',
    ):
        invalid = deepcopy(body)
        invalid['gemini_canvas_url'] = bad_url
        assert admin.post(url + '/api/admin/settings', json=invalid).status_code == 400

    # 管理员更新后，全员配置与公开 AI 配置立即生效，且不落盘到个人 app_config.json。
    updated_url = 'https://gemini.google.com/share/team-canvas-v2'
    body['gemini_canvas_url'] = updated_url
    saved = post_settings(admin, url, body)
    assert saved['gemini_canvas_url'] == updated_url
    assert client.post(url + '/api/admin/settings', json=body).status_code == 403

    user_ctx = runtime.context_for(next(row for row in runtime.store.users() if row['id'] == user['id']))
    user_canvas = next(m for m in user_ctx.config.load_app_config()['ai_models'] if m.get('canvas_managed'))
    assert user_canvas['canvas_url'] == updated_url

    public_ai = client.get(url + '/api/ai-config').json()['config']
    public_canvas = next(m for m in public_ai['ai_models'] if m.get('canvas_managed'))
    assert public_canvas['canvas_url'] == updated_url

    personal = user_ctx.config.load_app_config()
    user_ctx.config.save_app_config(personal)
    raw = json.loads(user_ctx.paths.app_config_path.read_text())
    assert all('canvas_url' not in m for m in raw['ai_models'])

    # 省略字段时保留当前管理员配置，传空字符串恢复默认地址。
    keep_body = settings_body(admin, url)
    keep_body.pop('gemini_canvas_url')
    assert post_settings(admin, url, keep_body)['gemini_canvas_url'] == updated_url

    reset_body = settings_body(admin, url)
    reset_body['gemini_canvas_url'] = ''
    assert post_settings(admin, url, reset_body)['gemini_canvas_url'] == DEFAULT_GEMINI_CANVAS_URL

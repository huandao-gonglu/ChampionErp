"""1688 官方 OAuth；应用由部署管理，买家授权只在主库保存。"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request
from pydantic import ValidationError

from erp_web.schemas.alibaba_authorization import AlibabaAuthorizationStatus, AuthorizationAttempt, OneBoundAuthorizationRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.alibaba_credentials import application_fingerprint, buyer_scope, onebound_scope
from erp_web.services.alibaba_self_purchase_client import AlibabaSelfPurchaseClient, ADDRESSES
from erp_web.services.alibaba_purchase_address import saved_addresses
from erp_web.services.external_request_context import credential_fingerprint, request_context
from erp_web.services.external_request_manager import managed_urlopen

_AUTH_LOCK = threading.RLock()


def _configured(application):
    if application.get('application_type') == 'onebound':
        return bool(application.get('api_key') and application.get('api_secret'))
    return bool(re.fullmatch(r'[0-9]+', application.get('app_key', '')) and application.get('app_secret'))


def _redirect(application):
    uri = application.get('redirect_uri', '')
    parsed = urlsplit(uri)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1'}))):
        raise ValueError('1688 授权回调尚未配置，请联系管理员')
    return uri


def _authorization(state, application):
    auth = state.get('authorization') or {}
    if auth.get('application_fingerprint') != application_fingerprint(application):
        return {}
    # 企业 Key 默认买家不代表当前用户获得了个人授权。
    if application.get('application_type') == 'onebound' and not auth.get('access_token'):
        return {}
    return auth


class AlibabaAuthorizationService:
    def __init__(self, config_store, *, owner_id=''):
        self.store = config_store
        self.owner_id = owner_id

    def status(self) -> AlibabaAuthorizationStatus:
        application = self.store.alibaba_application()
        state = self.store.alibaba_credentials()
        auth = _authorization(state, application)
        can_authorize = False
        if application['application_type'] != 'onebound':
            try:
                _redirect(application)
                can_authorize = _configured(application)
            except ValueError:
                pass
        expires = auth.get('expires_at', 0)
        pending = state.get('pending') or {}
        attempt: AuthorizationAttempt = 'idle'
        if pending and pending.get('application_fingerprint') == application_fingerprint(application):
            if pending.get('failed'):
                attempt = 'failed'
            elif pending.get('expires_at', 0) <= time.time():
                attempt = 'expired'
            else:
                attempt = 'waiting'
        return {'ok': True, 'application_type': application['application_type'], 'application_configured': _configured(application), 'can_authorize': can_authorize,
                'authorized': bool(auth.get('access_token') and _configured(application)),
                'member_id': auth.get('member_id', ''), 'account_name': auth.get('account_name') or auth.get('member_id', ''),
                'attempt': attempt, 'expires_at': expires,
                'refreshable': bool(auth.get('refresh_token')),
                'needs_reauthorization': bool(state.get('authorization') and not auth)
                    or bool(expires and expires <= time.time() and not auth.get('refresh_token'))}

    def start(self):
        with _AUTH_LOCK:
            application = self.store.alibaba_application()
            if application['application_type'] == 'onebound':
                raise ValueError('万邦个人 1688 在线授权尚未接入，请联系管理员')
            if not _configured(application):
                raise ValueError('1688 授权服务暂未配置，请联系管理员')
            redirect = _redirect(application)
            state = "crossborder." + secrets.token_urlsafe(32)
            owner = self.owner_id
            if owner:
                state = f"account.{owner}.{state}"
            snapshot = self.store.alibaba_credentials()
            self.store.update_alibaba_credentials({'pending': {
                'state_hash': hashlib.sha256(state.encode()).hexdigest(),
                'expires_at': time.time() + 600, 'application_fingerprint': application_fingerprint(application),
                'redirect_uri': redirect}}, expected=snapshot, application=application)
            params = {'client_id': application['app_key'], 'site': 'china', 'redirect_uri': redirect, 'state': state}
            signing = ''.join(key + params[key] for key in sorted(params))
            params['_aop_signature'] = hmac.new(application['app_secret'].encode(), signing.encode(), hashlib.sha1).hexdigest().upper()
            return {'ok': True, 'url': 'https://auth.1688.com/auth/authorize.htm?' + urlencode(params)}

    def callback(self, code, state):
        with _AUTH_LOCK:
            return self._complete(self.store.alibaba_application(), code, state)

    def complete_from_url(self, callback_url: str):
        """只解析管理员粘贴的 URL，授权码仍由当前账号的服务端会话交换。"""
        with _AUTH_LOCK:
            application = self.store.alibaba_application()
            base, separator, query = callback_url.strip().partition('?')
            if (not separator or base != _redirect(application) or '#' in callback_url
                    or len(callback_url) > 16384):
                raise ValueError('请粘贴与本次授权回调地址一致的完整 URL')
            try:
                params = parse_qs(query, keep_blank_values=True, max_num_fields=32, errors='strict')
            except ValueError:
                raise ValueError('授权回调 URL 的参数无效') from None
            for key in ('code', 'state'):
                values = params.get(key, [])
                if len(values) != 1 or not values[0].strip():
                    raise ValueError('完整回调地址必须包含唯一的 code 和 state')
            return self._complete(application, params['code'][0], params['state'][0])

    def _complete(self, application, code, state):
        snapshot = self.store.alibaba_credentials()
        pending = snapshot.get('pending') or {}
        if (not state or len(code) > 4096 or len(state) > 256
                or not secrets.compare_digest(pending.get('state_hash', ''), hashlib.sha256(state.encode()).hexdigest())
                or pending.get('expires_at', 0) <= time.time()
                or pending.get('application_fingerprint') != application_fingerprint(application)
                or pending.get('redirect_uri') != application.get('redirect_uri')):
            raise ValueError('1688 授权请求已失效或不匹配，请重新连接账号')
        # 授权码只交换一次；超时不重放，原授权仍保留。
        # 已按时收到回调，给正在进行的 Token 交换保留完成时间。
        processing = {**pending, 'state_hash': '', 'expires_at': max(pending['expires_at'], time.time() + 60)}
        consumed = self.store.update_alibaba_credentials(
            {'pending': processing}, expected=snapshot, application=application)
        try:
            if not code:
                raise ValueError('1688 未返回授权码，请重新授权')
            payload = self._token(application, {'grant_type': 'authorization_code', 'code': code})
            auth = self._parse_token(application, payload)
        except (ValueError, ExternalRequestBlocked):
            self.store.update_alibaba_credentials(
                {'pending': {**processing, 'failed': True}}, expected=consumed,
                application=application)
            raise
        self.store.update_alibaba_credentials(
            {'authorization': auth, 'pending': {}}, expected=consumed, application=application)
        return self.status()

    def runtime_config(self):
        with _AUTH_LOCK:
            application = self.store.alibaba_application()
            snapshot = self.store.alibaba_credentials()
            auth = _authorization(snapshot, application)
            if application['application_type'] == 'onebound':
                if not (_configured(application) and auth.get('access_token')):
                    raise ValueError('当前方案的 1688 账号尚未连接，请联系管理员')
                return {**application, 'access_token': auth['access_token'], 'account_scope': auth['account_scope'],
                        'timeout_seconds': self.store.load_app_config().get('1688_api', {}).get('timeout_seconds', '20')}
            if auth.get('expires_at') and auth['expires_at'] <= time.time() + 300:
                if not auth.get('refresh_token'):
                    raise ValueError('1688 账号授权已到期，请重新连接账号')
                payload = self._token(application, {'grant_type': 'refresh_token', 'refresh_token': auth['refresh_token']})
                refreshed = self._parse_token(application, payload, previous=auth)
                self.store.update_alibaba_credentials({'authorization': refreshed}, expected=snapshot, application=application)
                auth = refreshed
            timeout = self.store.load_app_config().get('1688_api', {}).get('timeout_seconds', '20')
            return {'timeout_seconds': timeout, **application, 'access_token': auth.get('access_token', ''),
                    'member_id': auth.get('member_id', ''), 'account_scope': auth.get('account_scope', '')}

    def bind_onebound(self, body):
        try:
            request = OneBoundAuthorizationRequest.model_validate(body)
        except ValidationError:
            raise ValueError('请填写有效的万邦 1688 授权 Session（最多 4096 字符）') from None
        application = self.store.alibaba_application()
        if application['application_type'] != 'onebound' or not _configured(application):
            raise ValueError('请先由管理员选择并配置万邦方案')
        snapshot = self.store.alibaba_credentials()
        config = {**application, 'access_token': request.session}
        # 绑定只读核验，不创建订单，不发起支付；不冒充已识别的买家身份。
        saved_addresses(AlibabaSelfPurchaseClient(config).call(ADDRESSES, {}))
        auth = {'access_token': request.session, 'account_name': '万邦采购账号',
                'account_scope': onebound_scope(application['api_key'], request.session),
                'application_fingerprint': application_fingerprint(application)}
        self.store.update_alibaba_credentials({'authorization': auth, 'pending': {}}, expected=snapshot, application=application)
        return self.status()

    @staticmethod
    def _parse_token(application, payload, previous=None):
        previous = previous or {}
        token = payload.get('access_token')
        member = str(payload.get('memberId') or payload.get('member_id') or previous.get('member_id') or '')
        if not isinstance(token, str) or not token or not member:
            raise ValueError('1688 未返回完整的账号授权，请重新连接账号')
        if previous.get('member_id') and member != previous['member_id']:
            raise ValueError('1688 刷新回执的账号不一致，已停止使用该回执')
        try:
            expires = int(payload['expires_in'])
        except (KeyError, ValueError, TypeError):
            raise ValueError('1688 未返回有效授权期限，请重新连接账号') from None
        if expires <= 0:
            raise ValueError('1688 授权已失效，请重新连接账号')
        refresh = payload.get('refresh_token') or previous.get('refresh_token', '')
        if not isinstance(refresh, str):
            raise ValueError('1688 返回的刷新凭据无效，请重新连接账号')
        return {'access_token': token, 'refresh_token': refresh, 'expires_at': time.time() + expires,
                'refresh_token_timeout': payload.get('refresh_token_timeout', previous.get('refresh_token_timeout', '')),
                'account_name': str(payload.get('resource_owner') or previous.get('account_name') or member),
                'member_id': member, 'app_key': application['app_key'],
                'account_scope': previous.get('account_scope') or buyer_scope(application['app_key'], member),
                'application_fingerprint': application_fingerprint(application)}

    @staticmethod
    def _token(application, values):
        url = 'https://gw.open.1688.com/openapi/http/1/system.oauth2/getToken/' + application['app_key']
        params = {**values, 'client_id': application['app_key'], 'client_secret': application['app_secret'],
                  'redirect_uri': application['redirect_uri'], 'need_refresh_token': 'true'}
        request = Request(url, data=urlencode(params).encode(), method='POST', headers={
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'Accept': 'application/json'})
        context = request_context(url, data=request.data, method='POST', timeout=20, platform='gw.open.1688.com',
                                  account_id='app:' + application['app_key'], source='1688账号授权', semantics='write')
        context = replace(context, interface='system.oauth2.getToken',
                          credential_id=credential_fingerprint(values.get('refresh_token') or values.get('code')))
        try:
            with managed_urlopen(request, timeout=20, request_context=context, follow_redirects=False) as response:
                payload = json.loads(response.read())
        except HTTPError as exc:
            exc.close()
            raise ValueError('1688 授权失败，请检查应用授权配置并重新连接账号') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ValueError('1688 授权未完成，请重新连接账号；原授权已保留') from None
        if not isinstance(payload, dict) or payload.get('error') or payload.get('error_code') or payload.get('errorCode'):
            raise ValueError('1688 拒绝了本次授权，请重新连接账号并检查应用权限')
        return payload


def alibaba_runtime_config(config_store):
    return AlibabaAuthorizationService(config_store).runtime_config()

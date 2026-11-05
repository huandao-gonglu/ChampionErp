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

from erp_web.schemas.alibaba_authorization import AlibabaAuthorizationStatus
from erp_web.services.alibaba_credentials import application_fingerprint, buyer_scope
from erp_web.services.external_request_context import credential_fingerprint, request_context
from erp_web.services.external_request_manager import managed_urlopen

_AUTH_LOCK = threading.RLock()


def _configured(application):
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
    return auth


class AlibabaAuthorizationService:
    def __init__(self, config_store, *, crossborder=False):
        self.store = config_store
        self.crossborder = crossborder

    def status(self) -> AlibabaAuthorizationStatus:
        application = self.store.alibaba_application(crossborder=self.crossborder)
        state = self.store.alibaba_credentials(crossborder=self.crossborder)
        auth = _authorization(state, application)
        try:
            _redirect(application)
            can_authorize = _configured(application)
        except ValueError:
            can_authorize = False
        expires = auth.get('expires_at', 0)
        return {'ok': True, 'application_configured': _configured(application), 'can_authorize': can_authorize,
                'authorized': bool(auth.get('access_token') and _configured(application)),
                'member_id': auth.get('member_id', ''), 'expires_at': expires,
                'refreshable': bool(auth.get('refresh_token')),
                'needs_reauthorization': bool(state.get('authorization') and not auth)
                    or bool(expires and expires <= time.time() and not auth.get('refresh_token'))}

    def start(self):
        with _AUTH_LOCK:
            application = self.store.alibaba_application(crossborder=self.crossborder)
            if not _configured(application):
                raise ValueError('1688 授权服务暂未配置，请联系管理员')
            redirect = _redirect(application)
            state = secrets.token_urlsafe(32)
            if self.crossborder:
                state = "crossborder." + state
            snapshot = self.store.alibaba_credentials(crossborder=self.crossborder)
            self.store.update_alibaba_credentials({'pending': {
                'state_hash': hashlib.sha256(state.encode()).hexdigest(),
                'expires_at': time.time() + 600, 'application_fingerprint': application_fingerprint(application),
                'redirect_uri': redirect}}, expected=snapshot, application=application, crossborder=self.crossborder)
            params = {'client_id': application['app_key'], 'site': 'china', 'redirect_uri': redirect, 'state': state}
            signing = ''.join(key + params[key] for key in sorted(params))
            params['_aop_signature'] = hmac.new(application['app_secret'].encode(), signing.encode(), hashlib.sha1).hexdigest().upper()
            return {'ok': True, 'url': 'https://auth.1688.com/auth/authorize.htm?' + urlencode(params)}

    def complete(self, callback_url):
        with _AUTH_LOCK:
            application = self.store.alibaba_application(crossborder=self.crossborder)
            redirect = _redirect(application)
            parsed = urlsplit(callback_url)
            if parsed._replace(query='', fragment='').geturl() != redirect or parsed.fragment:
                raise ValueError('请粘贴本次 1688 授权完成后的完整回调地址')
            query = parse_qs(parsed.query, keep_blank_values=True)
            if any(len(query.get(key, [])) != 1 or not query[key][0] for key in ('state', 'code')):
                raise ValueError('1688 授权回调缺少有效的 code 或 state，请重新授权')
            return self._complete(application, query['code'][0], query['state'][0])

    def callback(self, code, state):
        with _AUTH_LOCK:
            return self._complete(self.store.alibaba_application(crossborder=self.crossborder), code, state)

    def _complete(self, application, code, state):
        snapshot = self.store.alibaba_credentials(crossborder=self.crossborder)
        pending = snapshot.get('pending') or {}
        if (not code or not state or len(code) > 4096 or len(state) > 256
                or not secrets.compare_digest(pending.get('state_hash', ''), hashlib.sha256(state.encode()).hexdigest())
                or pending.get('expires_at', 0) <= time.time()
                or pending.get('application_fingerprint') != application_fingerprint(application)
                or pending.get('redirect_uri') != application.get('redirect_uri')):
            raise ValueError('1688 授权请求已失效或不匹配，请重新连接账号')
        # 授权码只交换一次；超时不重放，原授权仍保留。
        consumed = self.store.update_alibaba_credentials({'pending': {}}, expected=snapshot, application=application, crossborder=self.crossborder)
        payload = self._token(application, {'grant_type': 'authorization_code', 'code': code})
        auth = self._parse_token(application, payload)
        self.store.update_alibaba_credentials({'authorization': auth}, expected=consumed, application=application, crossborder=self.crossborder)
        return self.status()

    def runtime_config(self):
        with _AUTH_LOCK:
            application = self.store.alibaba_application(crossborder=self.crossborder)
            snapshot = self.store.alibaba_credentials(crossborder=self.crossborder)
            auth = _authorization(snapshot, application)
            if auth.get('expires_at') and auth['expires_at'] <= time.time() + 300:
                if not auth.get('refresh_token'):
                    raise ValueError('1688 账号授权已到期，请重新连接账号')
                payload = self._token(application, {'grant_type': 'refresh_token', 'refresh_token': auth['refresh_token']})
                refreshed = self._parse_token(application, payload, previous=auth)
                self.store.update_alibaba_credentials({'authorization': refreshed}, expected=snapshot, application=application, crossborder=self.crossborder)
                auth = refreshed
            options = {} if self.crossborder else self.store.load_app_config().get('1688_api', {})
            return {**options, **application, 'access_token': auth.get('access_token', ''),
                    'member_id': auth.get('member_id', ''), 'account_scope': auth.get('account_scope', '')}

    @staticmethod
    def _parse_token(application, payload, previous=None):
        previous = previous or {}
        token = payload.get('access_token')
        member = str(payload.get('memberId') or previous.get('member_id') or '')
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

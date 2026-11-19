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

from erp_web.schemas.alibaba_authorization import AlibabaAuthorizationStatus, AuthorizationAttempt, OneBoundAuthorizationRequest, OneBoundUserBinding
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.alibaba_credentials import application_fingerprint, buyer_scope, legacy_account_scope, onebound_scope
from erp_web.services.alibaba_api_client import OneBoundAlibabaProvider, alibaba_provider
from erp_web.services.external_request_context import credential_fingerprint, request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.services.onebound_api_transport import list_onebound_shop_bindings, onebound_directory_credential

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


def _callback_provider(state):
    # state 的方案标记只负责定位，后续仍逐项验证当前用户该方案的待处理记录。
    parts = state.rsplit('.', 2)
    if len(parts) >= 2 and parts[-2] in {'alibaba', 'alibaba_crossborder'}:
        return parts[-2]
    return None


class AlibabaAuthorizationService:
    def __init__(self, config_store, *, owner_id=''):
        self.store = config_store
        self.owner_id = owner_id

    def status(self, provider=None) -> AlibabaAuthorizationStatus:
        application = self.store.alibaba_application(provider)
        state = self.store.alibaba_credentials(application['application_type'])
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
        binding_pending = auth.get('directory_verified') is False
        return {'ok': True, 'application_type': application['application_type'],
                'active': application['application_type'] == self.store.alibaba_api_application(), 'application_configured': _configured(application), 'can_authorize': can_authorize,
                'authorized': bool(auth.get('access_token') and _configured(application) and not binding_pending),
                **({'binding_pending': True} if binding_pending else {}),
                'member_id': auth.get('member_id', ''), 'account_name': auth.get('account_name') or auth.get('member_id', ''),
                'attempt': attempt, 'expires_at': expires,
                'refreshable': bool(auth.get('refresh_token')),
                'needs_reauthorization': bool(state.get('authorization') and not auth)
                    or bool(expires and expires <= time.time() and not auth.get('refresh_token'))}

    def start(self, provider=None):
        with _AUTH_LOCK:
            application = self.store.alibaba_application(provider)
            if application['application_type'] == 'onebound':
                raise ValueError('万邦个人 1688 在线授权尚未接入，请联系管理员')
            if not _configured(application):
                raise ValueError('1688 授权服务暂未配置，请联系管理员')
            redirect = _redirect(application)
            provider = application['application_type']
            state = provider + "." + secrets.token_urlsafe(32)
            owner = self.owner_id
            if owner:
                state = f"account.{owner}.{state}"
            snapshot = self.store.alibaba_credentials(application['application_type'])
            self.store.update_alibaba_credentials({'pending': {
                'state_hash': hashlib.sha256(state.encode()).hexdigest(),
                'expires_at': time.time() + 600, 'application_fingerprint': application_fingerprint(application),
                'redirect_uri': redirect}}, expected=snapshot, application=application, provider=provider)
            params = {'client_id': application['app_key'], 'site': 'china', 'redirect_uri': redirect, 'state': state}
            signing = ''.join(key + params[key] for key in sorted(params))
            params['_aop_signature'] = hmac.new(application['app_secret'].encode(), signing.encode(), hashlib.sha1).hexdigest().upper()
            return {'ok': True, 'url': 'https://auth.1688.com/auth/authorize.htm?' + urlencode(params)}

    def callback(self, code, state):
        with _AUTH_LOCK:
            provider = _callback_provider(state)
            return self._complete(self.store.alibaba_application(provider), code, state, provider)

    def complete_from_url(self, callback_url: str, provider=None):
        """只解析管理员粘贴的 URL，授权码仍由当前账号的服务端会话交换。"""
        with _AUTH_LOCK:
            base, separator, query = callback_url.strip().partition('?')
            if not separator or '#' in callback_url or len(callback_url) > 16384:
                raise ValueError('请粘贴与本次授权回调地址一致的完整 URL')
            try:
                params = parse_qs(query, keep_blank_values=True, max_num_fields=32, errors='strict')
            except ValueError:
                raise ValueError('授权回调 URL 的参数无效') from None
            for key in ('code', 'state'):
                values = params.get(key, [])
                if len(values) != 1 or not values[0].strip():
                    raise ValueError('完整回调地址必须包含唯一的 code 和 state')
            state = params['state'][0]
            provider = provider or _callback_provider(state)
            application = self.store.alibaba_application(provider)
            if base != _redirect(application):
                raise ValueError('请粘贴与本次授权回调地址一致的完整 URL')
            return self._complete(application, params['code'][0], state, provider)

    def _complete(self, application, code, state, provider):
        snapshot = self.store.alibaba_credentials(application['application_type'])
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
            {'pending': processing}, expected=snapshot, application=application, provider=provider)
        try:
            if not code:
                raise ValueError('1688 未返回授权码，请重新授权')
            payload = self._token(application, {'grant_type': 'authorization_code', 'code': code})
            auth = self._parse_token(application, payload)
        except (ValueError, ExternalRequestBlocked):
            self.store.update_alibaba_credentials(
                {'pending': {**processing, 'failed': True}}, expected=consumed,
                application=application, provider=provider)
            raise
        self.store.update_alibaba_credentials(
            {'authorization': auth, 'pending': {}}, expected=consumed, application=application, provider=provider)
        return self.status(provider)

    def runtime_config(self):
        with _AUTH_LOCK:
            application = self.store.alibaba_application()
            snapshot = self.store.alibaba_credentials(application['application_type'])
            auth = _authorization(snapshot, application)
            if application['application_type'] == 'onebound':
                if not (_configured(application) and auth.get('access_token')):
                    raise ValueError('当前方案的 1688 账号尚未连接，请联系管理员')
                if auth.get('directory_verified') is False:
                    raise ValueError('当前万邦买家身份待核验，请联系管理员')
                return {**application, 'access_token': auth['access_token'], 'account_scope': auth['account_scope'],
                        'member_id': auth.get('member_id', ''),
                        'credential_type': auth.get('credential_type', 'session'),
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

    @staticmethod
    def _verify_self_use_token(application, token):
        # 该买家接口要求应用签名和个人 Token；仅校验回执形状，空地址簿也代表调用成功。
        alibaba_provider({**application, 'access_token': token}).list_addresses()

    def bind_self_use_token(self, token):
        token = token.strip() if isinstance(token, str) else ''
        if not token or len(token) > 4096 or any(c.isspace() for c in token) or '://' in token:
            raise ValueError('请粘贴企业自用应用「授权配置」中的已授权 Token')
        application = self.store.alibaba_application('alibaba')
        if not _configured(application):
            raise ValueError('请先配置 1688 企业自用应用')
        snapshot = self.store.alibaba_credentials('alibaba')
        self._verify_self_use_token(application, token)
        previous = _authorization(snapshot, application)
        # 平台的永久 Token 未附带账号资料；仅对同一应用的同一凭据保留已核实的账号身份。
        auth = {'access_token': token, 'app_key': application['app_key'],
                'application_fingerprint': application_fingerprint(application),
                'account_scope': legacy_account_scope({**application, 'access_token': token})}
        if previous.get('access_token') == token:
            auth.update({k: previous[k] for k in ('account_scope', 'member_id', 'account_name') if previous.get(k)})
        self.store.update_alibaba_credentials({'authorization': auth, 'pending': {}}, expected=snapshot,
                                             application=application, provider='alibaba')
        return self.status('alibaba')

    def list_onebound_shops(self, *, nick, page):
        if len(nick) > 256 or not re.fullmatch(r'[1-9][0-9]{0,8}', page):
            raise ValueError('请提供有效的店铺昵称和页码')
        application = self.store.alibaba_application('onebound')
        if not _configured(application):
            raise ValueError('请先由管理员配置万邦企业凭据')
        return list_onebound_shop_bindings(application, nick=nick, page=int(page))

    def onebound_binding(self) -> OneBoundUserBinding:
        application = self.store.alibaba_application('onebound')
        snapshot = self.store.alibaba_credentials('onebound')
        saved = snapshot.get('authorization') or {}
        auth = _authorization(snapshot, application)
        if not saved.get('access_token'):
            state = 'unbound'
        elif not auth or not _configured(application):
            state = 'expired'
        elif auth.get('directory_verified') is False:
            state = 'pending'
        else:
            state = 'connected'
        return {'binding_id': saved.get('directory_shop_id', ''), 'account_name': saved.get('account_name', ''),
                'state': state}

    def bind_onebound_directory(self, *, binding_id: str, page: int) -> OneBoundUserBinding:
        application = self.store.alibaba_application('onebound')
        if not _configured(application):
            raise ValueError('请先由管理员配置万邦企业凭据')
        snapshot = self.store.alibaba_credentials('onebound')
        nick, session = onebound_directory_credential(application, binding_id=binding_id, page=page)
        provider = OneBoundAlibabaProvider({**application, 'access_token': session, 'credential_type': 'session'}, 20)
        buyer_id, login = provider.get_buyer_profile()
        verified = bool(buyer_id and login == nick)
        auth = {'access_token': session, 'credential_type': 'session', 'account_name': nick,
                'directory_shop_id': binding_id, 'directory_verified': verified,
                'member_id': buyer_id if verified else '',
                'account_scope': onebound_scope(application['api_key'], session, 'session'),
                'application_fingerprint': application_fingerprint(application)}
        self.store.update_alibaba_credentials({'authorization': auth, 'pending': {}}, expected=snapshot,
                                             application=application, provider='onebound')
        return self.onebound_binding()

    def bind_onebound(self, body):
        try:
            request = OneBoundAuthorizationRequest.model_validate(body)
        except ValidationError:
            raise ValueError('请提供一项有效的万邦买家凭据（最多 4096 字符）') from None
        application = self.store.alibaba_application('onebound')
        if not _configured(application):
            raise ValueError('请先由管理员配置万邦企业凭据')
        snapshot = self.store.alibaba_credentials('onebound')
        credential_type = 'access_token' if request.access_token else 'session'
        credential = request.access_token or request.session
        config = {**application, 'access_token': credential, 'credential_type': credential_type}
        # 只检查当前凭据能否读取；采购归属沿用凭据范围，不按网关返回的默认买家归并。
        alibaba_provider(config).list_addresses()
        auth = {'access_token': credential, 'credential_type': credential_type, 'account_name': '万邦采购账号',
                'account_scope': onebound_scope(application['api_key'], credential, credential_type),
                'application_fingerprint': application_fingerprint(application)}
        self.store.update_alibaba_credentials({'authorization': auth, 'pending': {}}, expected=snapshot, application=application, provider='onebound')
        return self.status('onebound')

    def _parse_token(self, application, payload, previous=None):
        previous = previous or {}
        token = payload.get('access_token')
        member = str(payload.get('memberId') or payload.get('member_id') or previous.get('member_id') or '')
        if not isinstance(token, str) or not token or not member:
            raise ValueError('1688 未返回完整的账号授权，请重新连接账号')
        if previous.get('member_id') and member != previous['member_id']:
            raise ValueError('1688 刷新回执的账号不一致，已停止使用该回执')
        raw_expires = payload.get('expires_in')
        if isinstance(raw_expires, bool) or not isinstance(raw_expires, (str, int)):
            raise ValueError('1688 未返回有效授权期限，请重新连接账号')
        try:
            expires = int(raw_expires)
        except ValueError:
            raise ValueError('1688 未返回有效授权期限，请重新连接账号') from None
        if expires <= 0:
            if application.get('application_type') != 'alibaba':
                raise ValueError(f'1688 返回的授权有效期为 {expires} 秒，请重新连接账号')
            try:
                self._verify_self_use_token(application, token)
            except (ValueError, ExternalRequestBlocked) as exc:
                raise ValueError(f'1688 返回的授权有效期为 {expires} 秒，凭据验证失败：{exc}。'
                                 '可使用企业自用应用「授权配置」中的已授权 Token 连接。') from None
        refresh = payload.get('refresh_token') or previous.get('refresh_token', '')
        if not isinstance(refresh, str):
            raise ValueError('1688 返回的刷新凭据无效，请重新连接账号')
        return {'access_token': token, 'refresh_token': refresh, 'expires_at': time.time() + expires if expires > 0 else 0,
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

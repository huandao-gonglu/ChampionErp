"""1688 买家授权，企业应用与用户凭据独立保存。"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request

from erp_web.context import peek_context
from erp_web.schemas.accounts import AccountError
from erp_web.schemas.alibaba_authorization import AlibabaAuthorizationStatus, AuthorizationAttempt
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.onebound_quota import DEFAULT_INITIAL_GRANT, UserOneBoundQuotaSummary
from erp_web.services import onebound_quota_service
from erp_web.stores.onebound_quota_store import QuotaBalanceConflictError
from erp_web.services.alibaba_api_client import OneBoundAlibabaProvider, alibaba_provider, onebound_trade_ready
from erp_web.services.alibaba_credentials import application_fingerprint, buyer_scope, legacy_account_scope, onebound_buyer_scope
from erp_web.services.external_request_context import credential_fingerprint, request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.services.onebound_api_transport import exchange_onebound_buyer_code, fetch_onebound_buyer_token

_AUTH_LOCK = threading.RLock()
_ONEBOUND_AUTHORIZE = 'https://o0b.cn/9vXze'
_ONEBOUND_CALLBACK = 'https://dc.dev.1buyo.com/api/Accredit1688/callback_1688'


class _OneBoundTokenSync:
    """绑定当前账号存储的万邦轮换 Token 同步器，支持快照深拷贝和相等比较。"""

    __slots__ = ('_store',)

    def __init__(self, store):
        self._store = store

    def __call__(self, failed_token: str) -> str:
        return AlibabaAuthorizationService(self._store).sync_onebound_token(failed_token=failed_token)

    def __deepcopy__(self, memo):
        return self

    def __eq__(self, other):
        return isinstance(other, _OneBoundTokenSync) and self._store is other._store

    def __repr__(self):
        return '<OneBoundTokenSync>'


def _configured(application):
    if application.get('application_type') == 'onebound':
        return bool(application.get('api_key') and application.get('api_secret'))
    return bool(re.fullmatch(r'[0-9]+', application.get('app_key', '')) and application.get('app_secret'))


def _redirect(application):
    if application['application_type'] == 'onebound':
        return _ONEBOUND_CALLBACK
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

    def _enterprise_workspace(self) -> bool:
        return self.store.shared_config is self.store

    def business_provider(self) -> str:
        return self.store.alibaba_api_application() if self._enterprise_workspace() else 'onebound'

    def _require_provider(self, provider=None) -> str:
        """个人空间固定仅允许使用万邦；企业自用与跨境应用仅限管理员本人。"""
        resolved = provider or self.business_provider()
        if resolved != 'onebound' and not self._enterprise_workspace():
            raise PermissionError('企业自用与跨境应用仅限管理员使用')
        return resolved

    def status(self, provider=None) -> AlibabaAuthorizationStatus:
        resolved = self._require_provider(provider)
        application = self.store.alibaba_application(resolved)
        state = self.store.alibaba_credentials(resolved)
        auth = _authorization(state, application)
        configured = _configured(application)
        can_authorize = configured
        try:
            _redirect(application)
        except ValueError:
            can_authorize = False
        expires = auth.get('expires_at', 0)
        now = time.time()
        expired = bool(expires and expires <= now)
        pending = state.get('pending') or {}
        attempt: AuthorizationAttempt = 'idle'
        if pending and pending.get('application_fingerprint') == application_fingerprint(application):
            if pending.get('failed'):
                attempt = 'failed'
            elif pending.get('expires_at', 0) <= now:
                attempt = 'expired'
            else:
                attempt = 'waiting'
        unverified_directory = auth.get('directory_verified') is False
        return {'ok': True, 'application_type': application['application_type'],
                'active': application['application_type'] == self.business_provider(),
                'application_configured': configured, 'can_authorize': can_authorize,
                'authorized': bool(auth.get('access_token') and configured and not unverified_directory
                                   and not (application['application_type'] == 'onebound' and expired)),
                'member_id': auth.get('member_id', ''), 'account_name': auth.get('account_name') or auth.get('member_id', ''),
                'attempt': attempt, 'expires_at': expires,
                'refreshable': bool(auth.get('refresh_token')),
                'needs_reauthorization': unverified_directory or bool(state.get('authorization') and not auth)
                    or (expired and not bool(auth.get('refresh_token')))}

    def start(self, provider=None):
        with _AUTH_LOCK:
            resolved = self._require_provider(provider)
            application = self.store.alibaba_application(resolved)
            if not _configured(application):
                raise ValueError('1688 授权服务暂未配置，请联系管理员')
            redirect = _redirect(application)
            provider = application['application_type']
            pending = {'expires_at': time.time() + 600, 'application_fingerprint': application_fingerprint(application),
                       'redirect_uri': redirect}
            if provider == 'onebound':
                # 万邦使用企业 Key 作为 state。本地尝试编号用于区分同一用户先后发起的请求。
                state = application['api_key']
                pending['attempt_id'] = secrets.token_urlsafe(32)
            else:
                state = provider + "." + secrets.token_urlsafe(32)
                if self.owner_id:
                    state = f"account.{self.owner_id}.{state}"
            pending['state_hash'] = hashlib.sha256(state.encode()).hexdigest()
            snapshot = self.store.alibaba_credentials(provider)
            self.store.update_alibaba_credentials({'pending': pending}, expected=snapshot, application=application, provider=provider)
            if provider == 'onebound':
                return {'ok': True, 'url': _ONEBOUND_AUTHORIZE + '?' + urlencode({'state': state})}
            params = {'client_id': application['app_key'], 'site': 'china', 'redirect_uri': redirect, 'state': state}
            signing = ''.join(key + params[key] for key in sorted(params))
            params['_aop_signature'] = hmac.new(application['app_secret'].encode(), signing.encode(), hashlib.sha1).hexdigest().upper()
            return {'ok': True, 'url': 'https://auth.1688.com/auth/authorize.htm?' + urlencode(params)}

    def callback(self, code, state):
        with _AUTH_LOCK:
            resolved = self._require_provider(_callback_provider(state))
            application = self.store.alibaba_application(resolved)
            if application['application_type'] == 'onebound':
                raise ValueError('请在 ERP 的万邦授权卡粘贴完整回调地址')
            return self._complete(application, code, state)

    def complete_from_url(self, callback_url: str, provider=None):
        """解析粘贴的地址，通过当前账号的待完成记录兑换授权码。"""
        if provider is not None:
            self._require_provider(provider)
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
        code, state = params['code'][0], params['state'][0]
        resolved = self._require_provider(provider or _callback_provider(state))
        application = self.store.alibaba_application(resolved)
        if base != _redirect(application):
            raise ValueError('请粘贴与本次授权回调地址一致的完整 URL')
        if application['application_type'] == 'onebound':
            return self._complete(application, code, state)
        with _AUTH_LOCK:
            return self._complete(application, code, state)

    def _complete(self, application, code, state):
        provider = application['application_type']
        snapshot = self.store.alibaba_credentials(provider)
        pending = snapshot.get('pending') or {}
        if (not state or len(code) > 4096 or len(state) > 256
                or not secrets.compare_digest(pending.get('state_hash', ''), hashlib.sha256(state.encode()).hexdigest())
                or pending.get('expires_at', 0) <= time.time()
                or pending.get('application_fingerprint') != application_fingerprint(application)
                or pending.get('redirect_uri') != _redirect(application)):
            raise ValueError('1688 授权请求已失效或不匹配，请重新连接账号')
        # 授权码只交换一次；超时不重放，原授权仍保留。
        # 已按时收到回调，给正在进行的 Token 交换保留完成时间。
        processing = {**pending, 'state_hash': '', 'expires_at': max(pending['expires_at'], time.time() + 60)}
        consumed = self.store.update_alibaba_credentials(
            {'pending': processing}, expected=snapshot, application=application, provider=provider)
        try:
            if not code:
                raise ValueError('1688 未返回授权码，请重新授权')
            if provider == 'onebound':
                auth = self._exchange_onebound(application, code, _authorization(snapshot, application))
            else:
                payload = self._token(application, {'grant_type': 'authorization_code', 'code': code})
                auth = self._parse_token(application, payload)
        except (ValueError, ExternalRequestBlocked):
            self.store.update_alibaba_credentials(
                {'pending': {**processing, 'failed': True}}, expected=consumed,
                application=application, provider=provider)
            raise
        self.store.update_alibaba_credentials(
            {'authorization': auth, 'pending': {}}, expected=consumed, application=application, provider=provider)
        if provider == 'onebound' and auth.get('member_id'):
            app_ctx = peek_context()
            if app_ctx is not None:
                onebound_quota_service.ensure_buyer_grant(
                    app_ctx.onebound_quota,
                    application['api_key'],
                    auth['member_id'],
                    initial_grant=application.get('initial_grant', DEFAULT_INITIAL_GRANT),
                )
        return self.status(provider)

    @staticmethod
    def _exchange_onebound(application, code, previous):
        token, nick, expires_at = exchange_onebound_buyer_code(application, code)
        client = OneBoundAlibabaProvider({**application, 'access_token': token, 'credential_type': 'access_token'}, 20)
        member_id, login = client.get_buyer_profile()
        if not member_id or login != nick:
            raise ValueError('万邦授权回执与 1688 买家身份不一致，请重新登录授权')
        same_buyer = previous.get('member_id') == member_id or (
            not previous.get('member_id') and previous.get('credential_type') == 'access_token'
            and previous.get('access_token') == token)
        scope = previous.get('account_scope') if same_buyer else ''
        return {'access_token': token, 'credential_type': 'access_token', 'member_id': member_id,
                'account_name': login, 'expires_at': expires_at,
                'account_scope': scope or onebound_buyer_scope(application['api_key'], member_id),
                'application_fingerprint': application_fingerprint(application)}

    def sync_onebound_token(self, *, failed_token: str) -> str:
        """同步万邦后台自动轮换后的 1688 买家 Token，并再次核验买家身份。"""
        with _AUTH_LOCK:
            application = self.store.alibaba_application('onebound')
            snapshot = self.store.alibaba_credentials('onebound')
            auth = _authorization(snapshot, application)
            if not (_configured(application) and auth.get('access_token')):
                raise ValueError('当前方案的 1688 账号尚未连接，请到万邦授权卡连接账号')
            if auth.get('credential_type') != 'access_token' or not auth.get('member_id'):
                raise ValueError('万邦买家授权已失效，请重新连接账号')
            if auth.get('expires_at') and auth['expires_at'] <= time.time():
                raise ValueError('万邦买家授权已到期，请重新连接账号')
            if auth['access_token'] != failed_token:
                return auth['access_token']
            member_id = str(auth['member_id']).strip()
            account_name = str(auth.get('account_name') or '').strip()

        token, nick, expires_at = fetch_onebound_buyer_token(application, member_id=member_id, account_name=account_name)
        if token == failed_token:
            raise ValueError('万邦买家授权已失效，请重新连接账号')
        client = OneBoundAlibabaProvider({**application, 'access_token': token, 'credential_type': 'access_token'}, 20)
        verified_member_id, verified_login = client.get_buyer_profile()
        if verified_member_id != member_id or (nick and verified_login != nick):
            raise ValueError('万邦授权回执与 1688 买家身份不一致，请重新连接账号')
        refreshed = {**auth, 'access_token': token, 'account_name': verified_login or account_name, 'expires_at': expires_at}
        try:
            self.store.update_alibaba_credentials(
                {'authorization': refreshed}, expected=snapshot, application=application, provider='onebound',
            )
            return token
        except ValueError:
            latest_app = self.store.alibaba_application('onebound')
            latest_auth = _authorization(self.store.alibaba_credentials('onebound'), latest_app)
            if (latest_app == application and latest_auth.get('credential_type') == 'access_token'
                    and latest_auth.get('member_id') == member_id
                    and latest_auth.get('access_token') and latest_auth['access_token'] != failed_token
                    and not (latest_auth.get('expires_at') and latest_auth['expires_at'] <= time.time())):
                return latest_auth['access_token']
            raise

    def runtime_config(self, provider=None):
        with _AUTH_LOCK:
            resolved = self._require_provider(provider) if provider else self.business_provider()
            application = self.store.alibaba_application(resolved)
            snapshot = self.store.alibaba_credentials(resolved)
            auth = _authorization(snapshot, application)
            if application['application_type'] == 'onebound':
                if not (_configured(application) and auth.get('access_token')):
                    raise ValueError('当前方案的 1688 账号尚未连接，请到万邦授权卡连接账号')
                if auth.get('directory_verified') is False:
                    raise ValueError('当前万邦买家身份未通过核验，请重新连接账号')
                if auth.get('expires_at') and auth['expires_at'] <= time.time():
                    raise ValueError('万邦买家授权已到期，请重新连接账号')
                credential_type = auth.get('credential_type', 'session')
                member_id = auth.get('member_id', '')
                runtime = {**application, 'access_token': auth['access_token'], 'account_scope': auth['account_scope'],
                           'member_id': member_id,
                           'credential_type': credential_type,
                           'directory_verified': auth.get('directory_verified') is True,
                           'timeout_seconds': self.store.load_app_config().get('1688_api', {}).get('timeout_seconds', '20')}
                if credential_type == 'access_token' and member_id:
                    runtime['_onebound_token_sync'] = _OneBoundTokenSync(self.store)
                return runtime
            if auth.get('expires_at') and auth['expires_at'] <= time.time() + 300:
                if not auth.get('refresh_token'):
                    raise ValueError('1688 账号授权已到期，请重新连接账号')
                payload = self._token(application, {'grant_type': 'refresh_token', 'refresh_token': auth['refresh_token']})
                refreshed = self._parse_token(application, payload, previous=auth)
                self.store.update_alibaba_credentials({'authorization': refreshed}, expected=snapshot,
                                                      application=application, provider=resolved)
                auth = refreshed
            timeout = self.store.load_app_config().get('1688_api', {}).get('timeout_seconds', '20')
            return {'timeout_seconds': timeout, **application, 'access_token': auth.get('access_token', ''),
                    'member_id': auth.get('member_id', ''), 'account_scope': auth.get('account_scope', '')}

    @staticmethod
    def _verify_self_use_token(application, token):
        # 该买家接口要求应用签名和个人 Token；仅校验回执形状，空地址簿也代表调用成功。
        alibaba_provider({**application, 'access_token': token}).list_addresses()

    def bind_self_use_token(self, token):
        self._require_provider('alibaba')
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


    def restore_onebound_key(self, quota_store, *, clear_auth_block=None) -> bool:
        if not self._enterprise_workspace():
            raise PermissionError('仅管理员可恢复万邦企业 Key')
        application = self.store.alibaba_application('onebound')
        if not _configured(application):
            raise ValueError('万邦企业 API 尚未配置')
        return onebound_quota_service.restore_key(
            quota_store,
            application['api_key'],
            clear_auth_block=clear_auth_block,
        )


def alibaba_runtime_config(config_store):
    return AlibabaAuthorizationService(config_store).runtime_config()


def alibaba_onebound_runtime_config(config_store):
    """强制按万邦企业 Key + 当前账号万邦买家授权解析运行时配置（供独立 1688 直购使用）。"""
    return AlibabaAuthorizationService(config_store).runtime_config('onebound')


def onebound_direct_purchase_status(config_store) -> dict:
    """只读检查当前账号的万邦 1688 直购可用状态与买家身份，不触碰 ERP 主业务方案。"""
    service = AlibabaAuthorizationService(config_store)
    status = service.status('onebound')
    application = config_store.alibaba_application('onebound')
    auth = _authorization(config_store.alibaba_credentials('onebound'), application)
    configured = bool(status['application_configured'])
    authorized = bool(status['authorized'])
    candidate = {
        **auth,
        'credential_type': auth.get('credential_type', 'session'),
        'directory_verified': auth.get('directory_verified') is True,
    }
    session_ready = bool(authorized and onebound_trade_ready(candidate))
    if not configured:
        blocked_reason = '万邦企业 API 尚未配置，请联系管理员在平台授权中配置企业 Key'
    elif not authorized:
        blocked_reason = '请先在「平台授权 → 万邦 1688」完成个人 1688 买家自助授权'
    elif not session_ready:
        blocked_reason = '当前万邦授权缺少已核验的买家身份，请到「平台授权 → 万邦 1688」重新完成自助授权'
    else:
        blocked_reason = ''
    return {
        'configured': configured,
        'authorized': authorized,
        'session_ready': session_ready,
        'member_id': str(status['member_id'] or ''),
        'account_name': str(status['account_name'] or ''),
        'account_scope': str(auth.get('account_scope') or ''),
        'blocked_reason': blocked_reason,
    }


def onebound_quota_view(context):
    service = AlibabaAuthorizationService(context.config, owner_id=context.user_id)
    status = service.status('onebound')
    application = context.config.alibaba_application('onebound')
    return onebound_quota_service.build_quota_view(
        context.onebound_quota,
        is_admin=service._enterprise_workspace(),
        active_provider=service.business_provider(),
        application=application,
        authorized=bool(status['authorized']),
        member_id=status['member_id'],
        account_name=status['account_name'],
    )


def _read_user_onebound_state(runtime, user: dict) -> dict:
    """只读读取指定账号的万邦授权记录，绝不给从未登录的新账号创建业务库。"""
    workspace = str(user.get('workspace') or '')
    if workspace == '.' and user.get('role') == 'admin':
        return runtime.root.config.alibaba_credentials('onebound')
    with runtime._lock:
        existing = runtime._contexts.get(user['id'])
    if existing is not None:
        return existing.config.alibaba_credentials('onebound')
    if workspace != f"users/{user['id']}":
        return {}
    users_root = (runtime.root.paths.app_dir / 'users').resolve()
    directory = (runtime.root.paths.app_dir / workspace).resolve()
    if not directory.is_relative_to(users_root):
        return {}
    db_path = directory / 'erp.sqlite3'
    if not db_path.is_file():
        return {}
    try:
        conn = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT secret_json FROM runtime_secrets WHERE namespace='credentials:alibaba' AND secret_path='onebound'"
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return {}
        data = json.loads(row[0])
        return data if isinstance(data, dict) else {}
    except (sqlite3.Error, ValueError, OSError):
        return {}


def _valid_onebound_binding(state: dict, application: dict, *, now: float) -> tuple[str, str]:
    auth = _authorization(state, application)
    if not auth or not auth.get('access_token') or auth.get('directory_verified') is False:
        return '', ''
    expires = auth.get('expires_at', 0)
    if expires and expires <= now:
        return '', ''
    member_id = str(auth.get('member_id') or '').strip()
    if not member_id:
        return '', ''
    account_name = str(auth.get('account_name') or member_id).strip()
    return member_id, account_name


def admin_users_onebound_quotas(runtime, users: list[dict]) -> dict[str, UserOneBoundQuotaSummary]:
    """为管理员用户列表附带各账号绑定的万邦 1688 买家与当前余额。"""
    application = runtime.root.config.alibaba_application('onebound')
    configured = _configured(application)
    if not configured:
        return {
            u['id']: {
                'configured': False,
                'authorized': False,
                'member_id': '',
                'account_name': '',
                'balance': None,
                'shared_user_count': 0,
            }
            for u in users
        }
    now = time.time()
    all_users = runtime.store.users()
    bindings: dict[str, tuple[str, str]] = {}
    member_counts: dict[str, int] = {}
    for row in all_users:
        member_id, account_name = _valid_onebound_binding(_read_user_onebound_state(runtime, row), application, now=now)
        bindings[row['id']] = (member_id, account_name)
        if member_id:
            member_counts[member_id] = member_counts.get(member_id, 0) + 1
    key_fp = onebound_quota_service.key_fingerprint(application['api_key'])
    initial_grant = application.get('initial_grant', DEFAULT_INITIAL_GRANT)
    store = runtime.root.onebound_quota
    balance_cache: dict[str, int] = {}
    result: dict[str, UserOneBoundQuotaSummary] = {}
    for row in users:
        member_id, account_name = bindings.get(row['id'], ('', ''))
        if not member_id:
            result[row['id']] = {
                'configured': True,
                'authorized': False,
                'member_id': '',
                'account_name': '',
                'balance': None,
                'shared_user_count': 0,
            }
            continue
        if member_id not in balance_cache:
            balance_cache[member_id] = store.read_buyer_summary(
                key_fp, member_id, account_name=account_name, initial_grant=initial_grant,
            )['balance']
        result[row['id']] = {
            'configured': True,
            'authorized': True,
            'member_id': member_id,
            'account_name': account_name,
            'balance': balance_cache[member_id],
            'shared_user_count': member_counts.get(member_id, 1),
        }
    return result


def admin_set_user_onebound_balance(
    runtime,
    user_id: str,
    target_balance: int,
    *,
    expected_balance: int | None = None,
    note: str = '',
) -> dict:
    """管理员按目标余额调整指定用户所授权 1688 买家的万邦额度。"""
    user = next((row for row in runtime.store.users() if row['id'] == user_id), None)
    if user is None:
        raise AccountError('账号不存在。', status_code=404, error_code='USER_NOT_FOUND')
    application = runtime.root.config.alibaba_application('onebound')
    if not _configured(application):
        raise AccountError('万邦企业 API 尚未配置，暂无法调整额度。', status_code=400, error_code='ONEBOUND_NOT_CONFIGURED')
    now = time.time()
    member_id, account_name = _valid_onebound_binding(_read_user_onebound_state(runtime, user), application, now=now)
    if not member_id:
        raise AccountError('该账号尚未完成有效的万邦 1688 授权，暂无法调整额度。', status_code=400, error_code='ONEBOUND_NOT_AUTHORIZED')
    shared_count = sum(
        1 for row in runtime.store.users()
        if _valid_onebound_binding(_read_user_onebound_state(runtime, row), application, now=now)[0] == member_id
    )
    try:
        outcome = onebound_quota_service.set_buyer_target_balance(
            runtime.root.onebound_quota,
            application['api_key'],
            member_id,
            target_balance,
            expected_balance=expected_balance,
            note=note,
            initial_grant=application.get('initial_grant', DEFAULT_INITIAL_GRANT),
            now=now,
        )
    except QuotaBalanceConflictError as exc:
        raise AccountError(str(exc), status_code=409, error_code='QUOTA_BALANCE_CHANGED') from None
    except ValueError as exc:
        raise AccountError(str(exc), status_code=400, error_code='INVALID_QUOTA_TARGET') from None
    return {
        'ok': True,
        'user_id': user_id,
        'member_id': member_id,
        'account_name': account_name,
        'shared_user_count': shared_count,
        **outcome,
    }

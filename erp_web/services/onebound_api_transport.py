"""万邦 1688 固定网关，负责授权换码和显式声明的业务请求。"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from erp_web.context import peek_context
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.onebound_quota import DEFAULT_INITIAL_GRANT
from erp_web.services import onebound_quota_service
from erp_web.services.external_request_context import credential_fingerprint, request_context
from erp_web.services.external_request_manager import managed_urlopen


class OneBoundApiError(ValueError):
    """固定错误文案，不透传可能带凭据的网关响应。"""


class OneBoundApiRejected(OneBoundApiError):
    """网关明确拒绝调用，业务可释放本次尚未执行的占位。"""


class OneBoundApiUnauthorized(OneBoundApiRejected):
    """买家凭据已失效或未获当前接口授权，网关明确拒绝本次调用。"""


_REJECTED_MESSAGES = {
    '4003': '万邦请求参数无效（4003），请核对商品或订单参数',
    '4004': '万邦接口参数或请求格式错误（4004），请核对接入配置',
    '4005': '万邦 API Key 或签名无效（4005），请管理员核对企业配置',
    '4006': '万邦接口已停用（4006），请联系管理员检查万邦控制台接口状态',
    '4007': '万邦账户已停用（4007），请联系管理员核对账户状态',
    '4008': '万邦接口请求频率超限（4008），请稍后重试',
    '4010': '万邦请求被安全策略拒绝（4010），请核对接入配置',
    '4012': '万邦尚未开通该接口权限（4012），请联系管理员在万邦控制台添加该 API',
    '4013': '万邦今日调用次数已超限（4013），将于次日 00:00（北京时间）自动恢复',
    '4014': '万邦接口权限或套餐状态异常（4014），请联系管理员核对配置',
    '4015': '万邦账号或接口状态受限（4015），请联系管理员核对配置',
    '4016': '万邦账户余额不足（4016），请联系管理员充值后在设置中恢复使用',
}


def _initial_grant(config) -> int:
    try:
        raw = config.get('initial_grant', DEFAULT_INITIAL_GRANT)
        if isinstance(raw, bool):
            return DEFAULT_INITIAL_GRANT
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INITIAL_GRANT


def request_onebound(config, api, values, *, namespace, semantics, timeout):
    token = config.get('access_token')
    if not token:
        raise OneBoundApiRejected('请先连接当前方案的 1688 采购账号')
    # 买家凭据取自当前授权，通过网关外层传递。_o_args 只包含业务参数。
    arguments = dict(values)
    arguments.pop('access_token', None)
    params = {'key': config['api_key'], 'secret': config['api_secret'],
              'method': namespace + '/' + api,
              'cache': 'no', 'result_type': 'json', 'lang': 'zh-CN'}
    if config.get('credential_type') == 'session':
        params['session'] = token
    else:
        params['access_token'] = token
    if arguments or api != 'alibaba.trade.getBuyerOrderList':
        params['_o_args'] = json.dumps(arguments, ensure_ascii=False, separators=(',', ':'))
    payload = _request_gateway(config, '/1688/custom', params, interface=namespace + '/' + api,
                               credential=token, semantics=semantics, timeout=timeout,
                               member_id=str(config.get('member_id') or '').strip())
    _validate_gateway_result(payload)
    result = payload.get('response')
    if not isinstance(result, (dict, list)):
        raise OneBoundApiError('万邦未返回有效的 1688 接口回执')
    return normalize_onebound_response(api, result)


def _request_gateway(config, path, params, *, interface, credential, semantics, timeout, member_id=''):
    url = 'https://api-gw.onebound.cn' + path + '?' + urlencode(params)
    request = Request(url, method='GET', headers={'Accept': 'application/json'})
    # 万邦示例用 GET 执行写入，必须显式声明写语义，禁止自动重试。
    context = request_context(url, method='GET', timeout=timeout, platform='onebound',
                              account_id='key:' + credential_fingerprint(config['api_key']),
                              source='万邦1688接口', semantics=semantics)
    app_ctx = peek_context()
    quota_store = app_ctx.onebound_quota if app_ctx is not None else None
    before_send = context.before_send
    if quota_store is not None:
        existing_before_send = before_send
        quota_check = onebound_quota_service.admission_hook(
            quota_store, config['api_key'], member_id, interface,
            initial_grant=_initial_grant(config),
        )

        def combined_before_send(req_ctx):
            if existing_before_send is not None:
                existing_before_send(req_ctx)
            quota_check(req_ctx)

        before_send = combined_before_send
    context = replace(context, interface=interface, credential_id=credential_fingerprint(credential),
                      before_send=before_send)
    try:
        with managed_urlopen(request, timeout=timeout, request_context=context, follow_redirects=False) as response:
            payload = json.loads(response.read())
    except ExternalRequestBlocked as exc:
        if not exc.details.get('local_rejection') and exc.code.startswith('ONEBOUND_'):
            if quota_store is not None:
                if exc.code == 'ONEBOUND_ACCOUNT_DISABLED':
                    onebound_quota_service.disable_key(
                        quota_store, config['api_key'], code=exc.code, message=str(exc),
                    )
                elif exc.code == 'ONEBOUND_DAILY_RATE_LIMITED':
                    onebound_quota_service.mark_daily_exhausted(
                        quota_store, config['api_key'], resume_at=exc.details.get('resume_at'),
                    )
            raise OneBoundApiRejected(str(exc)) from None
        raise
    except HTTPError as exc:
        exc.close()
        raise OneBoundApiError('万邦接口请求未完成，请核对配置并查询原请求结果') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise OneBoundApiError('万邦未返回有效响应，请查询原请求，勿重复下单或支付') from None
    if not isinstance(payload, dict):
        raise OneBoundApiError('万邦接口响应格式无效')
    if quota_store is not None and payload.get('api_info'):
        onebound_quota_service.observe_api_info(
            quota_store, config['api_key'], payload.get('api_info'),
            request_id=context.operation_id,
        )
    return payload


def _validate_gateway_result(payload):
    code = str(payload.get('error_code') or '')
    reason = str(payload.get('reason') or '')
    if payload.get('error') or code not in {'', '0', '0000'}:
        if code == '2000' and 'request need user authorized' in reason.lower():
            raise OneBoundApiUnauthorized('万邦买家授权已失效，请重新连接账号或稍后重试')
        if code == '4009':
            raise OneBoundApiError('万邦网关维护中（4009），请稍后查询原请求结果，勿重复下单或支付')
        # 网络、超时、服务端错误不能证明底层写入未发生。
        if code in _REJECTED_MESSAGES:
            raise OneBoundApiRejected(_REJECTED_MESSAGES[code])
        raise OneBoundApiError('万邦未确认接口结果，请查询原请求，勿重复下单或支付')


def _parse_onebound_expiry(raw) -> float:
    try:
        expiry = datetime.fromisoformat(raw)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone(timedelta(hours=8)))
        expires_at = expiry.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        raise OneBoundApiError('万邦未返回有效的授权期限，请重新授权') from None
    if expires_at <= time.time():
        raise OneBoundApiError('万邦返回的买家授权已到期，请重新授权')
    return expires_at


def exchange_onebound_buyer_code(config, code: str) -> tuple[str, str, float]:
    params = {'key': config['api_key'], 'secret': config['api_secret'], 'code': code,
              'cache': 'no', 'result_type': 'json', 'lang': 'zh-CN'}
    try:
        payload = _request_gateway(config, '/1688global/buyer_token_create/', params,
                                   interface='1688global/buyer_token_create', credential=code,
                                   semantics='write', timeout=20)
        _validate_gateway_result(payload)
    except OneBoundApiRejected:
        raise OneBoundApiRejected('万邦拒绝换取授权，请核对换码接口权限，并重新登录授权') from None
    except OneBoundApiError:
        raise OneBoundApiError('万邦换取授权未完成，请重新登录授权') from None
    item = payload.get('items')
    if not isinstance(item, dict):
        raise OneBoundApiError('万邦未返回唯一的买家授权，请重新授权')
    token, nick = item.get('session'), item.get('nick')
    if (not isinstance(token, str) or not 0 < len(token) <= 4096 or any(char.isspace() for char in token)
            or not isinstance(nick, str) or not nick.strip()):
        raise OneBoundApiError('万邦未返回完整的买家授权，请重新授权')
    return token, nick.strip(), _parse_onebound_expiry(item.get('exp_time'))


_MAX_SHOP_PAGES = 20


def _shop_field(row, name):
    value = row.get(name)
    return str(value).strip() if type(value) in {str, int} else ''


def _onebound_shop_page(config, *, nick: str = '', page: int = 1, member_id: str = '') -> tuple[list[dict], int]:
    params = {'key': config['api_key'], 'secret': config['api_secret'], 'nick': nick, 'page': page,
              'cache': 'no', 'result_type': 'json', 'lang': 'zh-CN'}
    payload = _request_gateway(config, '/sys/shop_bind_list/', params, interface='sys/shop_bind_list',
                               credential=config['api_secret'], semantics='read', timeout=20,
                               member_id=member_id)
    if str(payload.get('error_code') or '') == '2000':
        return [], 0
    _validate_gateway_result(payload)
    result = payload.get('items')
    if not isinstance(result, dict) or 'item' not in result or 'pagecount' not in result:
        raise OneBoundApiError('万邦未返回有效的授权店铺分页')
    raw_pages = result['pagecount']
    raw_total = result.get('total_results')
    if isinstance(raw_pages, bool) or isinstance(raw_total, bool):
        raise OneBoundApiError('万邦未返回有效的授权店铺分页')
    try:
        pages = int(raw_pages)
        total = int(raw_total) if raw_total not in (None, '') else None
    except (TypeError, ValueError):
        raise OneBoundApiError('万邦未返回有效的授权店铺分页') from None
    if pages < 0 or (total is not None and total < 0):
        raise OneBoundApiError('万邦未返回有效的授权店铺分页')
    rows = result['item']
    if rows is None:
        if pages == 0 and (total is None or total == 0):
            return [], 0
        raise OneBoundApiError('万邦未返回有效的授权店铺分页')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise OneBoundApiError('万邦未返回有效的授权店铺分页')
    return rows, pages


def _matching_buyer_rows(config, *, member_id: str) -> list[dict]:
    matches = []
    page = 1
    while page <= _MAX_SHOP_PAGES:
        rows, pages = _onebound_shop_page(config, nick='', page=page, member_id=member_id)
        for row in rows:
            if (_shop_field(row, 'api_type').lower() in {'1688', '1688global', 'alibaba'}
                    and _shop_field(row, 'status') == '1'
                    and _shop_field(row, 'memo') == member_id):
                matches.append(row)
        if not rows or page >= pages:
            break
        page += 1
    return matches


def fetch_onebound_buyer_token(config, *, member_id: str, account_name: str = '') -> tuple[str, str, float]:
    """按已核验的 1688 会员 ID 读取万邦后台轮换后的最新买家 Token。"""
    member_id = str(member_id or '').strip()
    if not member_id:
        raise OneBoundApiUnauthorized('当前万邦买家身份缺失，请重新连接账号')
    app_ctx = peek_context()
    if app_ctx is not None and not onebound_quota_service.check_shop_bind_throttle(
        app_ctx.onebound_quota, config['api_key'], member_id
    ):
        raise OneBoundApiUnauthorized('万邦买家授权同步过于频繁，请稍后重试或重新连接账号')
    # 万邦 /sys/shop_bind_list/ 按中文 nick 过滤实测会返回空分页（pagecount=0, item=null）；
    # 直接按空 nick 拉取分页并在本地按已核验 member_id（memo）匹配，避免误查空和浪费额度。
    matches = _matching_buyer_rows(config, member_id=member_id)
    nick = str(account_name or '').strip()
    if len(matches) > 1 and nick:
        nick_matches = [row for row in matches if _shop_field(row, 'nick') == nick]
        if nick_matches:
            matches = nick_matches
    if len(matches) != 1:
        raise OneBoundApiUnauthorized('未找到唯一有效的万邦买家授权，请重新连接账号')
    row = matches[0]
    token = row.get('session')
    matched_nick = _shop_field(row, 'nick')
    if not isinstance(token, str) or not 0 < len(token) <= 4096 or any(char.isspace() for char in token):
        raise OneBoundApiUnauthorized('万邦未返回完整的买家授权，请重新连接账号')
    return token, matched_nick, _parse_onebound_expiry(row.get('exp_time'))


def normalize_onebound_response(api, value):
    """万邦实测将官方 result 展开；按接口恢复业务层共用的官方形状。"""
    if isinstance(value, dict):
        error = value.get('error')
        if error or value.get('errorCode') or value.get('error_code'):
            code = error.get('code') if isinstance(error, dict) else ''
            message = error.get('message') if isinstance(error, dict) else ''
            return {'success': False, 'errorCode': code or value.get('errorCode') or value.get('error_code') or 'ONEBOUND_BUSINESS_ERROR',
                    'errorMessage': message or value.get('errorMessage') or value.get('error_message')}
        value = {k: v for k, v in value.items() if k != 'error'}
        if 'success' in value:
            if api == 'alibaba.trade.pay.protocolPay.preparePay' and 'paySuccess' in value and 'result' not in value:
                # 支付字段与 success 同层时，恢复共用支付回执所需的结果包装。
                success = value.pop('success')
                return {'success': success, 'result': value}
            return value
        # 保留订单列表的分页包装，交由统一客户端校验 totalRecord 和订单字段。
        if api == 'alibaba.trade.getBuyerOrderList' and ('totalRecord' in value or 'result' in value):
            return value
    # getBuyerOrderList 的数组被网关转换为数字键对象；拒绝缺号或混杂字段。
    if api in {'alibaba.trade.getBuyerOrderList', 'alibaba.trade.getLogisticsInfos.buyerView'}:
        if isinstance(value, dict) and all(str(k).isdigit() for k in value):
            keys = sorted(value, key=int)
            if [int(k) for k in keys] != list(range(len(keys))):
                raise OneBoundApiError('万邦列表回执不完整，不能判断原订单结果')
            value = [value[k] for k in keys]
    if api == 'alibaba.createOrder.preview':
        if not isinstance(value, dict) or not isinstance(value.get('orderPreviewResuslt'), list):
            raise OneBoundApiError('万邦未返回有效订单预览')
        return {'success': True, **value}
    if api == 'alibaba.trade.getLogisticsTraceInfo.buyerView':
        if not isinstance(value, dict) or not isinstance(value.get('logisticsTrace'), list):
            raise OneBoundApiError('万邦未返回有效的物流轨迹')
        return {'success': True, **value}
    shapes = {
        'alibaba.trade.receiveAddress.get': 'receiveAddressItems',
        'alibaba.trade.pay.protocolPay.isopen': 'paymentAgreements',
        'alibaba.account.basic': 'memberId',
        'alibaba.trade.get.buyerView': 'baseInfo',
        'product.search.queryProductDetail': 'offerId',
    }
    if api in shapes and (not isinstance(value, dict) or shapes[api] not in value):
        # 部分接口保留原官方结果包装。
        if isinstance(value, dict) and isinstance(value.get('result'), dict):
            return value
        raise OneBoundApiError('万邦接口回执缺少必要业务字段')
    if api == 'product.search.queryProductDetail':
        return {'result': {'success': True, 'result': value}}
    return {'success': True, 'result': value}

"""万邦 1688 custom 固定网关；只转发显式声明的业务请求。"""
from dataclasses import replace
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from erp_web.services.external_request_context import credential_fingerprint, request_context
from erp_web.services.external_request_manager import managed_urlopen


class OneBoundApiError(ValueError):
    """固定错误文案，不透传可能带凭据的网关响应。"""


class OneBoundApiRejected(OneBoundApiError):
    """网关明确拒绝调用，业务可释放本次尚未执行的占位。"""


def request_onebound(config, api, values, *, namespace, semantics, timeout):
    # 万邦文档使用支付宝支付链接接口，参数名与官方组合收银台不同。
    if api == 'alibaba.trade.grouppay.url.get':
        api, values = 'alibaba.alipay.url.get', {'orderIdList': values['orderIds']}
    token = config.get('access_token')
    if not token:
        raise OneBoundApiRejected('请先连接当前方案的 1688 采购账号')
    # 买家凭据只取自当前授权。历史 Session 仍按原协议发送。
    arguments = dict(values)
    arguments.pop('access_token', None)
    legacy_session = config.get('credential_type') == 'session'
    if not legacy_session:
        arguments['access_token'] = token
    params = {'key': config['api_key'], 'secret': config['api_secret'],
              'method': namespace + '/' + api,
              '_o_args': json.dumps(arguments, ensure_ascii=False, separators=(',', ':')),
              'cache': 'no', 'result_type': 'json', 'lang': 'zh-CN'}
    if legacy_session:
        params['session'] = token
    url = 'https://api-gw.onebound.cn/1688/custom?' + urlencode(params)
    request = Request(url, method='GET', headers={'Accept': 'application/json'})
    # 万邦示例用 GET 执行写入，必须显式声明写语义，禁止自动重试。
    context = request_context(url, method='GET', timeout=timeout, platform='onebound',
                              account_id='key:' + credential_fingerprint(config['api_key']),
                              source='万邦1688接口', semantics=semantics)
    context = replace(context, interface=namespace + '/' + api,
                      credential_id=credential_fingerprint(token))
    try:
        with managed_urlopen(request, timeout=timeout, request_context=context, follow_redirects=False) as response:
            payload = json.loads(response.read())
    except HTTPError as exc:
        exc.close()
        raise OneBoundApiError('万邦接口请求未完成，请核对配置并查询原请求结果') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise OneBoundApiError('万邦未返回有效响应，请查询原请求，勿重复下单或支付') from None
    if not isinstance(payload, dict):
        raise OneBoundApiError('万邦接口响应格式无效')
    code = str(payload.get('error_code') or '')
    if payload.get('error') or code not in {'', '0', '0000'}:
        # 网络、超时、服务端错误不能证明底层写入未发生。
        if code in {'4003', '4004', '4005', '4006', '4007', '4008', '4010', '4012', '4013', '4014', '4015', '4016'}:
            raise OneBoundApiRejected('万邦拒绝调用，请核对接入配置、买家授权、接口权限及可用额度')
        raise OneBoundApiError('万邦未确认接口结果，请查询原请求，勿重复下单或支付')
    result = payload.get('response')
    if not isinstance(result, (dict, list)):
        raise OneBoundApiError('万邦未返回有效的 1688 接口回执')
    return normalize_onebound_response(api, result)


def normalize_onebound_response(api, value):
    """万邦实测将官方 result 展开；按接口恢复业务层共用的官方形状。"""
    if isinstance(value, dict):
        error = value.get('error')
        if error or value.get('errorCode') or value.get('error_code'):
            code = error.get('code') if isinstance(error, dict) else ''
            return {'success': False, 'errorCode': code or value.get('errorCode') or value.get('error_code') or 'ONEBOUND_BUSINESS_ERROR'}
        value = {k: v for k, v in value.items() if k != 'error'}
        if 'success' in value:
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

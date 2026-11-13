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
    params = {'key': config['api_key'], 'secret': config['api_secret'],
              'session': config['access_token'], 'method': namespace + '/' + api,
              '_o_args': json.dumps(values, ensure_ascii=False, separators=(',', ':')),
              'cache': 'no', 'result_type': 'json', 'lang': 'zh-CN'}
    url = 'https://api-gw.onebound.cn/1688/custom?' + urlencode(params)
    request = Request(url, method='GET', headers={'Accept': 'application/json'})
    # 万邦示例用 GET 执行写入，必须显式声明写语义，禁止自动重试。
    context = request_context(url, method='GET', timeout=timeout, platform='onebound',
                              account_id='key:' + credential_fingerprint(config['api_key']),
                              source='万邦1688接口', semantics=semantics)
    context = replace(context, interface=namespace + '/' + api,
                      credential_id=credential_fingerprint(config['access_token']))
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
            raise OneBoundApiRejected('万邦拒绝调用，请检查 Key、Secret、Session、接口权限及可用额度')
        raise OneBoundApiError('万邦未确认接口结果，请查询原请求，勿重复下单或支付')
    result = payload.get('response')
    if not isinstance(result, dict):
        raise OneBoundApiError('万邦未返回有效的 1688 接口回执')
    return result

"""1688 免密支付官方回执解析；不猜测失败原因，不切换支付渠道。"""
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from erp_web.services.alibaba_api_client import AlibabaApiError


def payment_label(channel):
    if channel not in {'alipay', 'shegou'}:
        raise AlibabaApiError('不支持的免密支付渠道')
    return '支付宝' if channel == 'alipay' else '先采后付'


def is_true(value):
    return value is True or value == 'true'


def payment_result(payload):
    """官方模型带业务结果包装；同时接受当前网关已展开的 result。"""
    if not isinstance(payload, dict) or not is_true(payload.get('success')):
        raise AlibabaApiError('1688 未返回有效支付回执，请查询原订单')
    result = payload.get('result')
    if isinstance(result, dict) and 'success' in result:
        if not is_true(result['success']):
            raise AlibabaApiError('1688 未确认支付结果，请查询原订单')
        result = result.get('result')
    if not isinstance(result, dict):
        raise AlibabaApiError('1688 未返回有效支付回执，请查询原订单')
    return result


def payment_agreement(payload, channel):
    label = payment_label(channel)
    rows = payment_result(payload).get('paymentAgreements')
    matches = [row for row in rows if isinstance(row, dict) and row.get('payChannel') == channel.upper()] if isinstance(rows, list) else []
    if len(matches) != 1:
        raise AlibabaApiError(f'1688 未返回唯一的{label}签约状态')
    row = matches[0]
    signed, bound = is_true(row.get('signedStatus')), is_true(row.get('bindingStatus'))
    url = row.get('signUrl') or ''
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or not any(parsed.hostname == host or (parsed.hostname or '').endswith('.' + host) for host in ('1688.com', 'alipay.com'))):
            url = ''
    except (ValueError, TypeError):
        url = ''
    return dict(channel=channel, ready=signed and bound, signed=signed, bound=bound, sign_url=url,
                message=f'{label}免密支付已开通' if signed and bound else f'请先在 1688 完成{label}免密签约及绑定，再刷新状态')


def check_payment_amount(payload, expected_fen):
    # buyerView.totalAmount 单位元；preparePay.payAmount 单位分。
    try:
        value = Decimal(str(payload['result']['baseInfo']['totalAmount'])) * 100
    except (KeyError, TypeError, InvalidOperation):
        raise AlibabaApiError('订单未返回有效应付金额，不能发起支付') from None
    if not value.is_finite() or value != expected_fen:
        raise AlibabaApiError('1688 订单应付金额与已确认预览不同，请在 1688 核对后处理')


def payment_receipt(payload, channel):
    label = payment_label(channel)
    result = payment_result(payload)
    if is_true(result.get('paySuccess')) and str(result.get('payChannel') or '').lower() == channel:
        return 'succeeded', f'{label}支付确认成功' + ('，账期以 1688 为准' if channel == 'shegou' else '')
    # 官方明确说明 paySuccess=false 也可能实际扣款成功，不能据此重试。
    return 'unknown', f'1688 尚未确认{label}支付结果，请查询原订单，勿重复支付'

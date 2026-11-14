"""后台业务同步策略的单一目录、默认值与编辑约束；不执行调度。"""
from copy import deepcopy
import math
from typing import TypedDict


class SyncPolicyField(TypedDict):
    key: str
    label: str
    default: float
    minimum: float
    maximum: float
    step: float
    unit: str


class SyncPolicyItem(TypedDict):
    key: str
    title: str
    description: str
    effect: str
    editable: bool
    fields: list[SyncPolicyField]
    values: dict[str, int | float]


class SyncPolicyCatalog(TypedDict):
    items: list[SyncPolicyItem]


def field(key, label, default, minimum=1, maximum=86400, *, step=1, unit='秒'):
    return dict(key=key, label=label, default=default, minimum=minimum, maximum=maximum, step=step, unit=unit)


def confirmation(first, interval, checks, lifetime, minimum, per_target):
    return [field('first_delay', '首次查询等待', first, 0), field('interval', '后续查询间隔', interval, 30),
            field('max_checks', '最多自动查询轮数', checks, 1, 1000, unit='轮'),
            field('lifetime', '自动确认期限', lifetime, 60, 604800),
            field('min_requests', '最低请求预算', minimum, 1, 100000, unit='次'),
            field('requests_per_target', '每个商品请求预算', per_target, 0, 10000, unit='次'),
            field('round_timeout', '单轮查询期限', 120, 10, 600),
            field('cooldown', '手动与自动查询最小间隔', 30, 1, 3600)]


# 目录同时用于验证、页面字段和真实执行，避免维护互相脱节的常量副本。
POLICIES = {
    'confirmation_yandex_content': dict(title='Yandex 发布与内容确认', description='Yandex 商品发布与在线内容修改受理后的只读确认。',
        effect='新确认计划生效；已有计划保留用量和截止时间', fields=confirmation(60, 1800, 24, 86400, 120, 120)),
    'confirmation_yandex_price': dict(title='Yandex 价格确认', description='Yandex 在线价格修改后的只读确认。',
        effect='新确认计划生效；已有计划保留用量和截止时间', fields=confirmation(30, 60, 10, 900, 40, 0)),
    'confirmation_default': dict(title='其他发布与在线修改确认', description='Ozon、Mercado 及其余需要异步确认的发布或在线修改。同步成功不额外查询。',
        effect='新确认计划生效；已有计划保留用量和截止时间', fields=confirmation(30, 60, 10, 900, 80, 40)),
    'order_callback': dict(title='订单回调失败重试', description='平台通知读取失败后的指数退避。主动同步失败不重试；平台限流仍遵守平台冷却时间。',
        effect='下一次失败计算重试时间时生效', fields=[field('max_attempts', '最多执行次数', 8, 1, 100, unit='次'),
            field('base_delay', '退避基数', 5, 1, 3600), field('max_delay', '最长退避', 1800, 1, 86400)]),
    'yandex_submission': dict(title='Yandex 发布分步执行', description='已批准发布的商品资料、上架条件、价格和库存分步推进；结果未知不自动重放。',
        effect='下一次步骤等待或可重试错误时生效', fields=[field('interval', '步骤间隔', 2, .5, 30, step=.5),
            field('max_delay', '最长退避', 30, .5, 300, step=.5), field('max_retries', '最多额外重试次数', 8, 1, 100, unit='次')]),
    'wechat_delivery': dict(title='微信消息投递重试', description='已授权消息尚未交付时持续重试，直到交付或解绑；保留原投递编号，不重新执行已完成的业务操作。',
        effect='下一次投递等待生效', fields=[field('retry_delay', '投递重试间隔', 1, 1, 300)]),
    'newapi_account': dict(title='New API 账号状态同步', description='改密和账号启停通知失败后按此间隔重试，直到同步完成。由管理员统一维护。',
        effect='下一轮账号扫描生效', shared=True, fields=[field('interval', '同步间隔', 30, 5, 3600)]),
}


def policy_values(overrides: dict, key: str) -> dict:
    return {**{item['key']: item['default'] for item in POLICIES[key]['fields']}, **overrides.get(key, {})}


def validate_overrides(value):
    if not isinstance(value, dict):
        raise ValueError('同步策略必须是对象')
    for key, values in value.items():
        if key not in POLICIES or not isinstance(values, dict):
            raise ValueError(f'未知或不可直接保存的同步策略：{key}')
        fields = {item['key']: item for item in POLICIES[key]['fields']}
        for name, number in values.items():
            item = fields.get(name)
            if (item is None or type(number) not in (int, float) or not math.isfinite(number)
                    or not item['minimum'] <= number <= item['maximum']
                    or (number / item['step']) % 1 != 0):
                raise ValueError(f'同步策略字段无效：{key}.{name}')
    return deepcopy(value)


def validate_combined(overrides):
    validate_overrides(overrides)
    for key in overrides:
        values = policy_values(overrides, key)
        if key.startswith('confirmation_') and (values['lifetime'] <= values['first_delay'] or values['lifetime'] < values['round_timeout']):
            raise ValueError('自动确认期限必须大于首次等待，且不能小于单轮查询期限')
        if key == 'order_callback' and values['max_delay'] < values['base_delay']:
            raise ValueError('最长退避不能小于退避基数')
        if key == 'yandex_submission' and values['max_delay'] < values['interval']:
            raise ValueError('最长退避不能小于步骤间隔')


__all__ = ['SyncPolicyField', 'SyncPolicyItem', 'SyncPolicyCatalog', 'POLICIES', 'policy_values', 'validate_overrides', 'validate_combined']

"""授权页的请求中断投影与明确恢复；不自动重放业务写入。"""
from __future__ import annotations

import hashlib
import json
import time

from erp_web.schemas.external_requests import is_definite_request_rejection


def block_id(block):
    # 身份包含状态版本；旧页面不能解除其后新产生的故障。
    value = {key: block[key] for key in ('platform', 'account_id', 'scope', 'scope_key', 'failure', 'created')}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def recovery_mode(block):
    failure = block['failure']
    if failure['code'] == 'EXTERNAL_TRANSIENT_FAILURE':
        return 'waiting' if failure.get('probe_id') and failure.get('probe_until', 0) > time.time() else 'probe'
    if failure.get('resume_at') is not None:
        return 'waiting'
    if is_definite_request_rejection(block['scope'], failure['code']):
        return 'confirm_request'
    if block['scope'] in {'request', 'operation'} or failure['code'] == 'EXTERNAL_WRITE_OUTCOME_UNKNOWN':
        return 'verify_result'
    return 'confirm'


def block_groups(blocks):
    """只归并有接口证据的同类明确拒绝；不合并实际放行范围。"""
    groups = {}
    for block in blocks:
        key = block_id(block)
        if recovery_mode(block) == 'confirm_request' and block.get('request_interface'):
            key = json.dumps([block['platform'], block['account_id'], block['request_interface'], block['failure']], sort_keys=True)
        groups.setdefault(key, []).append(block)
    return list(groups.values())


def group_id(blocks):
    identities = sorted(block_id(block) for block in blocks)
    if len(identities) == 1:
        return identities[0]
    # 组身份绑定成员快照；新记录出现后，旧确认框不能连带恢复它。
    return hashlib.sha256(json.dumps(identities).encode()).hexdigest()


def group_view(blocks):
    members = sorted(blocks, key=lambda block: block['created'])
    first = members[0]
    return {
        'id': group_id(members), 'platform': first['platform'],
        'account': first['account_id'] if not first['account_id'].startswith('credential:') else '当前凭据',
        'scope': first['scope'],
        'interface': first['scope_key'] if first['scope'] == 'interface' else first.get('request_interface') or '',
        'code': first['failure']['code'], 'message': first['failure']['message'],
        'http_status': first['failure'].get('status', 0),
        'created_at': first['created'], 'last_created_at': members[-1]['created'],
        'count': len(members), 'blocked_count': sum(block['blocked_count'] for block in members),
        'resume_at': max(first['failure'].get('resume_at') or 0, first['failure'].get('probe_until') or 0),
        'recovery_mode': recovery_mode(first),
        'occurrences': [{'id': block_id(block), 'created_at': block['created'], 'blocked_count': block['blocked_count']} for block in members],
    }


class ExternalRequestControlService:
    def __init__(self, store):
        self.store = store

    def status(self, *, offset=0, operation_ids=()):
        now = time.time()
        blocks = [b for b in self.store.blocks() if b['failure']['code'] == 'EXTERNAL_TRANSIENT_FAILURE'
                  or b['failure'].get('resume_at') is None or b['failure']['resume_at'] > now]
        history, total = self.store.interruptions(offset=offset)
        return {
            'ok': True, 'server_time': now,
            'blocks': [group_view(group) for group in block_groups(blocks)],
            'history': history, 'total': total,
            'notices': self.store.rejection_notices(operation_ids),
        }

    def recover(self, identity, *, reason=''):
        group = next((group for group in block_groups(self.store.blocks()) if group_id(group) == identity), None)
        if not group:
            raise ValueError('中断状态已变化，请刷新后重试')
        block = group[0]
        mode = recovery_mode(block)
        if mode == 'probe':
            self.store.request_probe(block['platform'], block['account_id'], {block['scope_key']}, expected_failure=block['failure'])
            message = '已允许下一次只读请求检查恢复情况；可返回原功能重试，后台任务也会按计划继续。'
        elif mode in {'confirm', 'confirm_request'}:
            self.store.recover_confirmed(group, reason=reason)
            message = f'已解除所选 {len(group)} 条请求的限制并逐条记录恢复原因。历史失败记录保留；请返回原功能重试。' if mode == 'confirm_request' else '已记录恢复原因。请返回原功能重试。'
        else:
            raise ValueError('请等待当前恢复检查或平台冷却结束；写入结果未知时请先核对业务回执')
        return {'ok': True, 'message': message}

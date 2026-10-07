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
            'blocks': [{
                'id': block_id(b), 'platform': b['platform'],
                'account': b['account_id'] if not b['account_id'].startswith('credential:') else '当前凭据',
                'scope': b['scope'], 'interface': b['scope_key'] if b['scope'] == 'interface' else '',
                'code': b['failure']['code'], 'message': b['failure']['message'],
                'http_status': b['failure'].get('status', 0),
                'created_at': b['created'], 'blocked_count': b['blocked_count'],
                'resume_at': max(b['failure'].get('resume_at') or 0, b['failure'].get('probe_until') or 0),
                'recovery_mode': recovery_mode(b),
            } for b in blocks],
            'history': history, 'total': total,
            'notices': self.store.rejection_notices(operation_ids),
        }

    def recover(self, identity, *, reason=''):
        block = next((b for b in self.store.blocks() if block_id(b) == identity), None)
        if not block:
            raise ValueError('中断状态已变化，请刷新后重试')
        mode = recovery_mode(block)
        if mode == 'probe':
            self.store.request_probe(block['platform'], block['account_id'], {block['scope_key']}, expected_failure=block['failure'])
            message = '已允许下一次只读请求检查恢复情况；可返回原功能重试，后台任务也会按计划继续。'
        elif mode in {'confirm', 'confirm_request'}:
            self.store.recover_confirmed([block], reason=reason)
            message = '已解除所选请求的限制并记录恢复原因。历史失败记录保留；请返回原功能重试。' if mode == 'confirm_request' else '已记录恢复原因。请返回原功能重试。'
        else:
            raise ValueError('请等待当前恢复检查或平台冷却结束；写入结果未知时请先核对业务回执')
        return {'ok': True, 'message': message}

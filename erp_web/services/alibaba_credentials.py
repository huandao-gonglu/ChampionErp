"""1688 应用与买家授权的纯身份规则。"""
import hashlib
import json


def application_fingerprint(application):
    identity = [application.get('app_key'), application.get('app_secret')]
    if application.get('application_type') == 'onebound':
        identity = ['onebound', application.get('api_key'), application.get('api_secret')]
    if application.get('credential_generation'):
        identity.append(application['credential_generation'])
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def legacy_account_scope(config):
    return hashlib.sha256(json.dumps([config.get('app_key'), config.get('access_token')]).encode()).hexdigest()


def buyer_scope(app_key, member_id):
    return hashlib.sha256(json.dumps(['1688-buyer', app_key, member_id]).encode()).hexdigest()


def onebound_scope(api_key, credential, credential_type):
    """未取得稳定买家身份时，按企业 Key 和个人凭据隔离采购。"""
    # 旧 Session 的摘要已用于持久化采购记录。新类型单独编码，避免同值凭据认领旧订单。
    namespace = '1688-onebound' if credential_type == 'session' else '1688-onebound-access-token'
    return hashlib.sha256(json.dumps([namespace, api_key, credential]).encode()).hexdigest()


def onebound_buyer_scope(api_key, member_id):
    return hashlib.sha256(json.dumps(['1688-onebound-buyer', api_key, member_id]).encode()).hexdigest()

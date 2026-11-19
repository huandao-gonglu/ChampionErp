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


def onebound_buyer_scope(api_key, member_id):
    return hashlib.sha256(json.dumps(['1688-onebound-buyer', api_key, member_id]).encode()).hexdigest()

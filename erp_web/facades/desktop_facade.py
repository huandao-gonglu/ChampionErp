"""桌面连接 HTTP 边界；关联由进程内连接服务管理。"""
from erp_web.context import get_context
from erp_web.services.desktop_connection_service import current_desktop_connection


def connect(body):
    return get_context().desktop_connections.connect()


def poll(body):
    return get_context().desktop_connections.poll(current_desktop_connection())


def complete(body):
    if not isinstance(body.get('ok'), bool):
        raise ValueError('本机回执 ok 必须是布尔值。')
    return get_context().desktop_connections.complete(current_desktop_connection(), body)


def upload_asset(body):
    return get_context().desktop_connections.accept_asset(current_desktop_connection(), body['id'], body['asset'])


def disconnect(body):
    get_context().desktop_connections.disconnect(current_desktop_connection())
    return {'ok': True}

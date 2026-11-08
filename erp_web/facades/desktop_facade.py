"""桌面连接 HTTP 边界；关联由进程内连接服务管理。"""
from erp_web.context import get_context
from erp_web.services.desktop_connection_service import current_desktop_connection
from erp_web.services.account_request import current_session
from erp_web.schemas.api import DesktopInstallers
from erp_web.services.desktop_version_policy import available_installers


def connect(body):
    session = current_session()
    return get_context().desktop_connections.connect(session_hash=session.token_hash if session else '')


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


def create_handoff(body):
    return get_context().desktop_handoffs.create(current_session().token_hash, body['target'], body.get('state', {}))


def handoff_status(body):
    return get_context().desktop_handoffs.status(body['request_id'], current_session().token_hash)


def resolve_handoff(body):
    connection = get_context().desktop_connections.validate_connection(
        current_desktop_connection(), session_hash=current_session().token_hash)
    return get_context().desktop_handoffs.resolve(body['request_id'], connection)


def acknowledge_handoff(body):
    connection = get_context().desktop_connections.validate_connection(
        current_desktop_connection(), session_hash=current_session().token_hash)
    return get_context().desktop_handoffs.acknowledge(body['request_id'], connection)


def desktop_installers(body) -> DesktopInstallers:
    return available_installers()

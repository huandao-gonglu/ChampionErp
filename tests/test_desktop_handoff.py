"""交接只恢复同账号页面，确认来自实际领取请求的桌面连接。"""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Thread

import pytest
import requests

from erp_web.http_handler import Handler
from erp_web.schemas.requests import RequestValidationError
from erp_web.server import ERPHTTPServer
from erp_web.services.desktop_handoff_service import DesktopHandoffs
from tests.account_test_utils import authenticated_headers


def test_handoff_checks_source_session_expiry_and_atomic_desktop_claim(monkeypatch):
    valid = {'web'}
    service = DesktopHandoffs(session_validator=lambda session: session in valid)
    request = service.create('web', '/?tab=drafts', {'workflow': {'draft': {'draftId': 'draft-a'}}})['request_id']
    with pytest.raises(RequestValidationError):
        service.status(request, 'other-web-session')

    def claim(connection):
        try:
            return connection, service.resolve(request, connection)
        except RequestValidationError:
            return connection, None

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(claim, ['desktop-a', 'desktop-b']))
    accepted = [connection for connection, result in results if result]
    assert len(accepted) == 1
    assert not service.status(request, 'web')['opened']
    with pytest.raises(RequestValidationError):
        service.acknowledge(request, 'unrelated-desktop')
    service.acknowledge(request, accepted[0])
    service.acknowledge(request, accepted[0])
    assert service.status(request, 'web')['opened']
    assert service.resolve(request, accepted[0])['state'] == {}
    valid.clear()
    with pytest.raises(RequestValidationError) as expired:
        service.status(request, 'web')
    assert expired.value.status_code == 410
    valid.add('web')
    request = service.create('web', '/aiWork', {})['request_id']
    monkeypatch.setattr('erp_web.services.desktop_handoff_service.time.monotonic', lambda: float('inf'))
    with pytest.raises(RequestValidationError):
        service.resolve(request, 'desktop-a')


@pytest.mark.parametrize('target', ['https://other.test/', '//other.test/', '/api/state', '/login', '/\\other.test', '/?tab=collect\n'])
def test_handoff_rejects_external_urls_and_non_business_targets(target):
    with pytest.raises(ValueError):
        DesktopHandoffs().create('web', target, {})


def test_http_handoff_restores_state_and_requires_current_desktop_and_same_account(monkeypatch, tmp_path):
    from erp_web.services import desktop_version_policy
    server = ERPHTTPServer(('127.0.0.1', 0), Handler)
    web_headers = authenticated_headers(server)
    runtime = server.account_runtime
    admin = runtime.store.admin()
    desktop_token, desktop_session = runtime.store.create_session(admin['id'], admin['password_hash'])
    desktop_headers = {'Cookie': f'champion_session={desktop_token}', 'X-CSRF-Token': desktop_session.csrf_token}
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'

    def post(suffix, body, headers=web_headers):
        return requests.post(base + '/api/' + suffix, json=body, headers=headers, timeout=5)

    try:
        payload = {'target': '/?tab=collect', 'state': {'workflow': {'collect': {'mode': 'browser', 'productUrl': 'https://example.test/item'}}}}
        result = post('desktop-handoff/create', payload)
        assert result.status_code == 200, result.text
        request_id = result.json()['request_id']
        body = {'request_id': request_id}
        assert post('desktop-handoff/resolve', body).status_code == 409
        assert post('desktop-handoff/status', body, desktop_headers).status_code == 403
        connection = post('desktop/connect', {}, desktop_headers).json()['connection_id']
        headers = {**desktop_headers, 'X-Champion-Desktop': connection}
        received = post('desktop-handoff/resolve', body, headers)
        assert received.status_code == 200, received.text
        assert received.json()['state'] == payload['state']
        assert not post('desktop-handoff/status', body).json()['opened']
        other = runtime.store.create_user('other@example.test', '其他用户', admin['password_hash'])
        other_token, session = runtime.store.create_session(other['id'], other['password_hash'])
        other_headers = {'Cookie': f'champion_session={other_token}', 'X-CSRF-Token': session.csrf_token}
        other_connection = post('desktop/connect', {}, other_headers).json()['connection_id']
        assert post('desktop-handoff/resolve', body, {**other_headers, 'X-Champion-Desktop': other_connection}).status_code == 410
        assert post('desktop-handoff/acknowledge', body, headers).status_code == 200
        assert post('desktop-handoff/status', body).json()['opened']
        assert post('desktop-handoff/create', {**payload, 'state': []}).status_code == 400

        monkeypatch.setattr(desktop_version_policy, 'RELEASES', tmp_path)
        directory = tmp_path / 'darwin-arm64'
        directory.mkdir()
        (directory / 'latest.json').write_text(json.dumps({'version': '1.2.36', 'platform': 'darwin', 'arch': 'arm64', 'installer': {'file': 'installer.dmg'}}))
        installers = post('desktop-handoff/installers', {}).json()['items']
        assert installers == [{'label': 'macOS Apple 芯片', 'version': '1.2.36', 'url': '/desktop-updates/darwin-arm64/1.2.36/installer.dmg'}]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        runtime.close()

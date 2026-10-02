"""真实薄路由的配置回读、默认项及请求契约回归。"""
from urllib.parse import urlsplit

import pytest

from erp_web.context import get_context
from erp_web.http_route_units import image_hosting_routes
from tests.image_hosting_test_utils import profile


class _Handler:
    def __init__(self, path, body=None):
        self.path, self.body = path, body
        self.response = None

    def read_body(self):
        return self.body

    def send_json(self, result, status=200):
        self.response = result, status


def post(action, body):
    handler = _Handler(f"/api/image-hosting/{action}", body)
    assert image_hosting_routes.handle_post(handler, urlsplit(handler.path))
    return handler.response


def test_config_routes_do_not_send_network_and_never_return_credentials(monkeypatch):
    from erp_web.services import s3_image_storage
    monkeypatch.setattr(s3_image_storage, "safe_image_urlopen", lambda *a, **kw: pytest.fail("配置操作不得发送请求"))
    response, status = post("save", {"profile": {key: value for key, value in profile().items() if key != "id"}})
    assert status == 200 and response["ok"]
    identity = response["id"]
    assert identity and identity != "images-main"
    assert "test-access-key" not in str(response) and "test-secret-key" not in str(response)
    assert post("default", {"id": identity})[0]["image_hosting"]["default_profile_id"] == identity
    assert post("delete", {"id": identity})[1] == 400
    assert post("default", {"id": ""})[1] == 200
    assert post("delete", {"id": identity})[0]["image_hosting"]["profiles"] == []
    handler = _Handler("/api/image-hosting")
    assert image_hosting_routes.handle_get(handler, urlsplit(handler.path))
    assert handler.response == ({"ok": True, "image_hosting": {"default_profile_id": "", "profiles": []}}, 200)
    assert "test-secret-key" not in str(get_context().db.load_runtime_secrets("app_config"))


@pytest.mark.parametrize("action,body", [("save", {}), ("save", {"profile": []}), ("test", {"profile": "secret"}), ("delete", {}), ("default", {"id": 12})])
def test_invalid_request_is_rejected_before_facade(action, body):
    with pytest.raises(ValueError):
        post(action, body)


def test_unsaved_default_and_unknown_routes_are_rejected():
    assert post("default", {})[1] == 400
    assert post("default", {"id": "missing"})[1] == 400
    handler = _Handler("/api/not-image-hosting", {})
    assert not image_hosting_routes.handle_post(handler, urlsplit(handler.path))
    assert not image_hosting_routes.handle_get(handler, urlsplit(handler.path))

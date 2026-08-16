from __future__ import annotations

import io
import json
import logging
import urllib.parse
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from erp_web import http_routes
from erp_web.http_request import MAX_JSON_BODY_BYTES, safe_json_body
from erp_web.http_route_units import get_routes, image_routes
from erp_web.http_route_units.common import UserInputError
from erp_web.http_route_units.static_routes import (
    handle_ml_callback,
    serve_file,
    serve_frontend_asset,
)
from erp_web.schemas.requests import (
    REQUEST_CONTRACTS,
    RequestValidationError,
    validate_request_payload,
)


def _headers(**values: str) -> Message:
    headers = Message()
    for name, value in values.items():
        headers[name.replace("_", "-")] = value
    return headers


class BodyHandler:
    def __init__(
        self,
        raw: bytes = b"",
        *,
        path: str = "/api/save-product",
        headers: Message | None = None,
    ) -> None:
        self.path = path
        self.headers = headers or _headers(
            Host="127.0.0.1:5050",
            Content_Length=str(len(raw)),
            **({"Content_Type": "application/json"} if raw else {}),
        )
        self.rfile = io.BytesIO(raw)


class RouterHandler(BodyHandler):
    def __init__(self, path: str, headers: Message) -> None:
        super().__init__(path=path, headers=headers)
        self.sent: list[tuple[dict[str, Any], int]] = []
        self.statuses: list[int] = []

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self.sent.append((payload, status))

    def send_response(self, status: int) -> None:
        self.statuses.append(status)

    def end_headers(self) -> None:
        pass


def test_empty_post_body_keeps_desktop_client_compatibility() -> None:
    handler = BodyHandler(headers=_headers(Host="127.0.0.1:5050"))

    assert safe_json_body(handler) == {}


def test_non_json_post_body_is_rejected_with_415() -> None:
    handler = BodyHandler(
        b'{"product": {}}',
        headers=_headers(
            Host="127.0.0.1:5050",
            Content_Length="15",
            Content_Type="text/plain",
        ),
    )

    with pytest.raises(RequestValidationError) as raised:
        safe_json_body(handler)

    assert raised.value.status_code == 415
    assert raised.value.error_code == "UNSUPPORTED_CONTENT_TYPE"


@pytest.mark.parametrize(
    ("headers", "error_code"),
    [
        (
            _headers(
                Host="127.0.0.1:5050",
                Origin="https://attacker.example",
            ),
            "UNTRUSTED_ORIGIN",
        ),
        (
            _headers(Host="attacker.example"),
            "UNTRUSTED_HOST",
        ),
        (
            _headers(
                Host="localhost:5050",
                Sec_Fetch_Site="cross-site",
            ),
            "CROSS_SITE_REQUEST",
        ),
    ],
)
def test_browser_cross_site_metadata_is_rejected(
    headers: Message,
    error_code: str,
) -> None:
    with pytest.raises(RequestValidationError) as raised:
        safe_json_body(BodyHandler(headers=headers))

    assert raised.value.status_code == 403
    assert raised.value.error_code == error_code


def test_no_origin_script_and_loopback_origin_remain_supported() -> None:
    without_origin = BodyHandler(headers=_headers(Host="localhost:5050"))
    loopback_origin = BodyHandler(
        headers=_headers(
            Host="127.0.0.1:5050",
            Origin="http://localhost:5173",
        )
    )

    assert safe_json_body(without_origin) == {}
    assert safe_json_body(loopback_origin) == {}


def test_oversized_body_is_rejected_before_reading() -> None:
    handler = BodyHandler(
        headers=_headers(
            Host="127.0.0.1:5050",
            Content_Length=str(MAX_JSON_BODY_BYTES + 1),
            Content_Type="application/json",
        )
    )

    with pytest.raises(RequestValidationError) as raised:
        safe_json_body(handler)

    assert raised.value.status_code == 413
    assert raised.value.error_code == "REQUEST_BODY_TOO_LARGE"
    assert handler.rfile.tell() == 0


@pytest.mark.parametrize(
    "headers",
    [
        _headers(
            Host="127.0.0.1:5050",
            Content_Length="not-a-number",
        ),
        _headers(
            Host="127.0.0.1:5050",
            Content_Length="-1",
        ),
        _headers(
            Host="127.0.0.1:5050",
            Transfer_Encoding="chunked",
        ),
    ],
)
def test_ambiguous_body_length_is_a_stable_400(headers: Message) -> None:
    with pytest.raises(RequestValidationError) as raised:
        safe_json_body(BodyHandler(headers=headers))

    assert raised.value.status_code == 400


def test_endpoint_contract_normalizes_integer_and_boolean_fields() -> None:
    category = validate_request_payload(
        {"query": "phone", "limit": "25"},
        endpoint="/api/category-search?debug=1",
    )
    publish = validate_request_payload(
        {"product_id": "p-1", "confirm": "false"},
        endpoint="/api/mercadolibre/confirm-real-publish",
    )

    assert category["limit"] == 25
    assert publish["confirm"] is False


def test_category_match_contract_requires_subject_platform_and_site() -> None:
    valid = validate_request_payload(
        {
            "draft_id": "draft-1",
            "platform": "mercadolibre",
            "site": "MLM",
        },
        endpoint="/api/v1/category-match",
    )

    assert valid["draft_id"] == "draft-1"
    with pytest.raises(RequestValidationError) as raised:
        validate_request_payload(
            {"draft_id": "draft-1", "platform": "mercadolibre"},
            endpoint="/api/v1/category-match",
        )
    assert raised.value.error_code == "MISSING_REQUIRED_FIELD"


@pytest.mark.parametrize(
    ("endpoint", "payload", "error_code"),
    [
        (
            "/api/category-search",
            {"query": "phone", "limit": {"bad": True}},
            "INVALID_FIELD_TYPE",
        ),
        (
            "/api/image-pool/action",
            {"product_id": "p-1", "action": "destroy-everything"},
            "INVALID_FIELD_VALUE",
        ),
        (
            "/api/delete-products",
            {"product_ids": "p-1"},
            "INVALID_FIELD_TYPE",
        ),
    ],
)
def test_endpoint_contract_rejects_invalid_field_shapes(
    endpoint: str,
    payload: dict[str, Any],
    error_code: str,
) -> None:
    with pytest.raises(RequestValidationError) as raised:
        validate_request_payload(payload, endpoint=endpoint)

    assert raised.value.status_code == 400
    assert raised.value.error_code == error_code


def test_every_post_route_has_an_explicit_contract() -> None:
    routes = http_routes.POST_API_ROUTES | image_routes.IMAGE_POST_PATHS

    assert routes == REQUEST_CONTRACTS.keys()


def test_top_level_router_preserves_request_error_status() -> None:
    raw = json.dumps({"product": {}}).encode("utf-8")
    handler = BodyHandler(
        raw,
        headers=_headers(
            Host="127.0.0.1:5050",
            Content_Length=str(len(raw)),
            Content_Type="text/plain",
        ),
    )
    sent: list[tuple[dict[str, Any], int]] = []
    handler.read_body = lambda: safe_json_body(handler)  # type: ignore[attr-defined]
    handler.send_json = lambda payload, status=200: sent.append(  # type: ignore[attr-defined]
        (payload, status)
    )
    handler.send_response = lambda status: None  # type: ignore[attr-defined]
    handler.end_headers = lambda: None  # type: ignore[attr-defined]

    http_routes.handle_post(handler)  # type: ignore[arg-type]

    assert sent == [
        (
            {
                "ok": False,
                "error": "POST 请求体必须使用 application/json",
                "error_code": "UNSUPPORTED_CONTENT_TYPE",
            },
            415,
        )
    ]


def test_post_security_rejection_log_omits_query_string(caplog) -> None:
    handler = RouterHandler(
        "/api/save-product?access_token=post-query-secret",
        _headers(
            Host="127.0.0.1:5050",
            Origin="https://attacker.example",
        ),
    )

    with caplog.at_level(
        logging.WARNING,
        logger="erp_web.http_routes",
    ):
        http_routes.handle_post(handler)

    assert handler.sent[0][1] == 403
    assert "/api/save-product" in caplog.text
    assert "post-query-secret" not in caplog.text


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (UserInputError("输入不合法"), 400),
        (RuntimeError("处理失败"), 500),
    ],
)
def test_post_route_failure_log_omits_query_string(
    failure: Exception,
    expected_status: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    def fail_request(*_args: Any) -> bool:
        raise failure

    monkeypatch.setitem(
        http_routes.POST_ROUTE_UNITS_BY_PATH,
        "/api/save-product",
        SimpleNamespace(handle_post=fail_request),
    )
    handler = RouterHandler(
        "/api/save-product?token=route-query-secret",
        _headers(Host="127.0.0.1:5050"),
    )

    with caplog.at_level(
        logging.WARNING,
        logger="erp_web.http_routes",
    ):
        http_routes.handle_post(handler)

    assert handler.sent[0][1] == expected_status
    assert "/api/save-product" in caplog.text
    assert "route-query-secret" not in caplog.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/state",
        "/file?path=%2Ftmp%2Fsecret.json",
        "/auth/mercadolibre/callback?code=oauth-secret-code",
        "/unknown",
    ],
)
def test_every_get_is_rejected_before_route_dispatch(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(
        get_routes,
        "handle_get",
        lambda _handler, parsed: dispatched.append(parsed.path) or True,
    )
    handler = RouterHandler(
        path,
        _headers(
            Host="127.0.0.1:5050",
            Origin="https://attacker.example",
        ),
    )

    http_routes.handle_get(handler)  # type: ignore[arg-type]

    assert dispatched == []
    assert handler.sent == [
        (
            {
                "ok": False,
                "error": "不允许浏览器跨站请求",
                "error_code": "UNTRUSTED_ORIGIN",
            },
            403,
        )
    ]


def test_oauth_callback_security_rejection_has_no_exchange_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchanged: list[str] = []
    monkeypatch.setitem(
        get_routes.GET_HANDLERS,
        "/auth/mercadolibre/callback",
        lambda _handler, _parsed: exchanged.append("called"),
    )
    handler = RouterHandler(
        "/auth/mercadolibre/callback?code=oauth-secret-code",
        _headers(
            Host="127.0.0.1:5050",
            Sec_Fetch_Site="cross-site",
        ),
    )

    http_routes.handle_get(handler)  # type: ignore[arg-type]

    assert exchanged == []
    assert handler.sent[0][1] == 403
    assert handler.sent[0][0]["error_code"] == "CROSS_SITE_REQUEST"


class StaticHandler:
    def __init__(self) -> None:
        self.statuses: list[int] = []
        self.headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

    def send_response(self, status: int) -> None:
        self.statuses.append(status)

    def send_header(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def end_headers(self) -> None:
        pass


def test_oauth_callback_never_forwards_credentials_from_query_string() -> None:
    handler = StaticHandler()
    exchanged: list[dict[str, Any]] = []

    handle_ml_callback(
        handler,
        urllib.parse.urlparse(
            "/auth/mercadolibre/callback?"
            + urllib.parse.urlencode(
                {
                    "code": "authorization-code",
                    "app_secret": "query-secret",
                    "client_secret": "query-client-secret",
                    "code_verifier": "query-verifier",
                }
            )
        ),
        exchange_code=lambda body: exchanged.append(body) or {},
        mask_secret=lambda value: "masked" if value else "",
    )

    assert exchanged == [{"code_or_url": "authorization-code"}]
    assert b"query-secret" not in handler.wfile.getvalue()


def test_static_asset_rejects_prefix_sibling_traversal(tmp_path: Path) -> None:
    asset_root = tmp_path / "dist"
    sibling = tmp_path / "dist-private"
    asset_root.mkdir()
    sibling.mkdir()
    (sibling / "secret.js").write_text("secret", encoding="utf-8")
    handler = StaticHandler()

    serve_frontend_asset(
        handler,
        urllib.parse.urlparse("/../dist-private/secret.js"),
        asset_root,
    )

    assert handler.statuses == [404]
    assert handler.wfile.getvalue() == b""


def test_file_route_requires_regular_file_inside_allowed_root(
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "images"
    screenshot_root = tmp_path / "cache" / "collect_debug"
    sibling = tmp_path / "images-private"
    media_root.mkdir()
    screenshot_root.mkdir(parents=True)
    sibling.mkdir()
    allowed = media_root / "image.png"
    screenshot = screenshot_root / "capture.webp"
    secret = sibling / "secret.png"
    config_json = media_root / "config.json"
    debug_text = screenshot_root / "page.txt"
    database = media_root / "erp.db"
    allowed.write_bytes(b"allowed")
    screenshot.write_bytes(b"screenshot")
    secret.write_bytes(b"secret")
    config_json.write_text('{"secret": true}', encoding="utf-8")
    debug_text.write_text("private debug page", encoding="utf-8")
    database.write_bytes(b"sqlite")

    allowed_handler = StaticHandler()
    serve_file(
        allowed_handler,
        urllib.parse.urlparse(
            "/file?" + urllib.parse.urlencode({"path": str(allowed)})
        ),
        [media_root, screenshot_root],
    )
    screenshot_handler = StaticHandler()
    serve_file(
        screenshot_handler,
        urllib.parse.urlparse(
            "/file?" + urllib.parse.urlencode({"path": str(screenshot)})
        ),
        [media_root, screenshot_root],
    )
    denied_handler = StaticHandler()
    serve_file(
        denied_handler,
        urllib.parse.urlparse(
            "/file?" + urllib.parse.urlencode({"path": str(secret)})
        ),
        [media_root, screenshot_root],
    )
    directory_handler = StaticHandler()
    serve_file(
        directory_handler,
        urllib.parse.urlparse(
            "/file?" + urllib.parse.urlencode({"path": str(media_root)})
        ),
        [media_root, screenshot_root],
    )
    non_image_handlers = []
    for path in (config_json, debug_text, database):
        handler = StaticHandler()
        serve_file(
            handler,
            urllib.parse.urlparse(
                "/file?" + urllib.parse.urlencode({"path": str(path)})
            ),
            [media_root, screenshot_root],
        )
        non_image_handlers.append(handler)

    assert allowed_handler.statuses == [200]
    assert allowed_handler.wfile.getvalue() == b"allowed"
    assert screenshot_handler.statuses == [200]
    assert screenshot_handler.wfile.getvalue() == b"screenshot"
    assert denied_handler.statuses == [404]
    assert directory_handler.statuses == [404]
    assert [handler.statuses for handler in non_image_handlers] == [
        [404],
        [404],
        [404],
    ]


def test_file_http_route_only_exposes_image_and_collect_screenshot_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images_dir = tmp_path / "data" / "images"
    collect_debug_dir = tmp_path / "data" / "cache" / "collect_debug"
    logs_dir = tmp_path / "data" / "logs"
    images_dir.mkdir(parents=True)
    collect_debug_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    image = images_dir / "product.jpg"
    screenshot = collect_debug_dir / "capture.png"
    secret_log = logs_dir / "backend.txt"
    image.write_bytes(b"image")
    screenshot.write_bytes(b"screenshot")
    secret_log.write_text("token=secret", encoding="utf-8")
    monkeypatch.setattr(
        get_routes,
        "get_context",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(
                images_dir=images_dir,
                collect_debug_dir=collect_debug_dir,
            )
        ),
    )

    handlers = []
    for path in (image, screenshot, secret_log):
        handler = StaticHandler()
        get_routes.handle_file(
            handler,
            urllib.parse.urlparse(
                "/file?" + urllib.parse.urlencode({"path": str(path)})
            ),
        )
        handlers.append(handler)

    assert [handler.statuses for handler in handlers] == [[200], [200], [404]]


def test_ai_config_get_has_no_filesystem_template_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_writes: list[Path] = []
    monkeypatch.setattr(
        get_routes,
        "get_context",
        lambda: SimpleNamespace(paths=SimpleNamespace(app_dir=tmp_path)),
    )
    monkeypatch.setattr(get_routes, "load_app_config", lambda: {"ai": {}})
    monkeypatch.setattr(
        get_routes.config_service,
        "public_ai_config",
        lambda app_dir, config: {"app_dir": str(app_dir), "config": config},
    )
    monkeypatch.setattr(
        get_routes.config_service,
        "write_env_template",
        lambda app_dir: template_writes.append(app_dir),
    )
    handler = StaticHandler()
    sent: list[tuple[dict[str, Any], int]] = []
    handler.send_json = lambda payload, status=200: sent.append((payload, status))  # type: ignore[attr-defined]

    get_routes.handle_ai_config(handler, urllib.parse.urlparse("/api/ai-config"))

    assert template_writes == []
    assert sent == [
        (
            {
                "ok": True,
                "config": {"app_dir": str(tmp_path), "config": {"ai": {}}},
            },
            200,
        )
    ]


def test_publish_bus_status_get_is_a_pure_status_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    terminal_job = {
        "job_id": "job-1",
        "status": "completed",
        "platforms": {"mercadolibre": {"status": "completed"}},
    }
    bus = SimpleNamespace(
        get_public_status=lambda job_id: calls.append(job_id) or terminal_job
    )
    monkeypatch.setattr(get_routes, "get_publishing_bus", lambda: bus)
    handler = StaticHandler()
    sent: list[tuple[dict[str, Any], int]] = []
    handler.send_json = lambda payload, status=200: sent.append((payload, status))  # type: ignore[attr-defined]

    get_routes.handle_publish_bus_status(
        handler,
        urllib.parse.urlparse("/api/publish-bus/status?job_id=job-1"),
    )

    assert calls == ["job-1"]
    assert sent == [({"ok": True, "job": terminal_job}, 200)]


def test_publish_bus_jobs_get_lists_filtered_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    result = {
        "items": [{"job_id": "job-2", "status": "failed"}],
        "next_cursor": "job-2",
    }
    bus = SimpleNamespace(
        list_jobs=lambda **kwargs: calls.append(kwargs) or result,
    )
    monkeypatch.setattr(get_routes, "get_publishing_bus", lambda: bus)
    handler = StaticHandler()
    sent: list[tuple[dict[str, Any], int]] = []
    handler.send_json = lambda payload, status=200: sent.append((payload, status))  # type: ignore[attr-defined]

    get_routes.handle_publish_bus_jobs(
        handler,
        urllib.parse.urlparse(
            "/api/publish-bus/jobs?limit=25&cursor=job-3&status=failed"
            "&platform=ozon&product_id=product-1"
        ),
    )

    assert calls == [
        {
            "limit": 25,
            "cursor": "job-3",
            "status": "failed",
            "platform": "ozon",
            "product_id": "product-1",
        }
    ]
    assert sent == [({"ok": True, **result}, 200)]

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from erp_web.http_route_units import global_agent_routes


class _Handler:
    def __init__(
        self,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.body = body
        self.headers = headers or {}
        self.read_count = 0
        self.response: tuple[dict[str, Any], int] | None = None

    def read_body(self) -> dict[str, Any]:
        self.read_count += 1
        return self.body

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self.response = (payload, status)


@pytest.mark.parametrize(
    ("path", "facade_name"),
    [
        ("/api/global-task-state", "get_global_task_payload"),
        ("/api/global-task-input", "submit_global_task_input_payload"),
        ("/api/global-task-approve", "approve_global_task_payload"),
        ("/api/global-task-reject", "reject_global_task_payload"),
        ("/api/global-task-cancel", "cancel_global_task_payload"),
        ("/api/global-task-refresh", "refresh_global_task_payload"),
    ],
)
def test_each_global_task_route_validates_with_its_endpoint_and_dispatches_to_facade(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    facade_name: str,
) -> None:
    body = {"request": path}
    validated = {"validated": path}
    validations: list[tuple[dict[str, Any], str]] = []
    facade_calls: list[dict[str, Any]] = []
    approval_required = path in {
        "/api/global-task-approve",
        "/api/global-task-reject",
    }

    def validate_request_payload(
        payload: dict[str, Any],
        *,
        endpoint: str,
    ) -> dict[str, Any]:
        validations.append((payload, endpoint))
        return validated

    def facade_call(
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        facade_calls.append({"payload": payload, **kwargs})
        return {"ok": True, "path": path}, 200

    monkeypatch.setattr(
        global_agent_routes,
        "validate_request_payload",
        validate_request_payload,
    )
    monkeypatch.setattr(
        global_agent_routes.global_task_facade,
        facade_name,
        facade_call,
    )
    handler = _Handler(
        path,
        body,
        headers={global_agent_routes.APPROVAL_TOKEN_HEADER: "token-123"}
        if approval_required
        else None,
    )

    assert global_agent_routes.handle_post(handler, SimpleNamespace(path=path)) is True
    assert handler.read_count == 1
    assert validations == [(body, path)]
    if approval_required:
        # 审批入口必须把受信请求头里的审批凭据传给 facade。
        assert facade_calls == [
            {"payload": validated, "approval_token": "token-123"}
        ]
    else:
        assert facade_calls == [{"payload": validated}]
    assert handler.response == ({"ok": True, "path": path}, 200)


def test_global_task_route_contract_has_no_task_creation_or_publish_confirm_route() -> None:
    # 任务创建只通过 global.chat 的 global_task_start 类型化参数进入；
    # HTTP 不再暴露创建入口，也不再有发布专用确认入口。
    assert "/api/global-task-start" not in global_agent_routes.HANDLED_PATHS
    assert (
        "/api/global-task-publish-confirm"
        not in global_agent_routes.HANDLED_PATHS
    )


def test_global_agent_route_contract_has_exactly_six_explicit_post_handlers() -> None:
    assert global_agent_routes.HANDLED_PATHS == frozenset(
        {
            "/api/global-task-state",
            "/api/global-task-input",
            "/api/global-task-approve",
            "/api/global-task-reject",
            "/api/global-task-cancel",
            "/api/global-task-refresh",
        }
    )
    assert set(global_agent_routes.POST_HANDLERS) == set(
        global_agent_routes.HANDLED_PATHS
    )

    handler = _Handler("/api/not-global-agent", {})
    assert global_agent_routes.handle_post(
        handler,
        SimpleNamespace(path=handler.path),
    ) is False
    assert handler.read_count == 0
    assert handler.response is None

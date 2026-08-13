from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from erp_web.http_route_units import global_agent_routes


class _Handler:
    def __init__(self, path: str, body: dict[str, Any]) -> None:
        self.path = path
        self.body = body
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
        ("/api/global-task-start", "start_global_task_payload"),
        ("/api/global-task-state", "get_global_task_payload"),
        ("/api/global-task-input", "submit_global_task_input_payload"),
        (
            "/api/global-task-publish-confirm",
            "confirm_global_task_publish_payload",
        ),
        ("/api/global-task-cancel", "cancel_global_task_payload"),
    ],
)
def test_each_global_agent_route_validates_with_its_endpoint_and_dispatches_to_facade(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    facade_name: str,
) -> None:
    body = {"request": path}
    validated = {"validated": path}
    validations: list[tuple[dict[str, Any], str]] = []
    facade_calls: list[dict[str, Any]] = []

    def validate_request_payload(
        payload: dict[str, Any],
        *,
        endpoint: str,
    ) -> dict[str, Any]:
        validations.append((payload, endpoint))
        return validated

    def facade_call(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        facade_calls.append(payload)
        return {"ok": True, "path": path}, 202

    monkeypatch.setattr(
        global_agent_routes,
        "validate_request_payload",
        validate_request_payload,
    )
    monkeypatch.setattr(
        global_agent_routes.global_agent_facade,
        facade_name,
        facade_call,
    )
    handler = _Handler(path, body)

    assert global_agent_routes.handle_post(handler, SimpleNamespace(path=path)) is True
    assert handler.read_count == 1
    assert validations == [(body, path)]
    assert facade_calls == [validated]
    assert handler.response == ({"ok": True, "path": path}, 202)


def test_global_agent_route_contract_has_exactly_five_explicit_post_handlers() -> None:
    assert global_agent_routes.HANDLED_PATHS == frozenset(
        {
            "/api/global-task-start",
            "/api/global-task-state",
            "/api/global-task-input",
            "/api/global-task-publish-confirm",
            "/api/global-task-cancel",
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

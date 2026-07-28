from __future__ import annotations

import json
import os

import pytest
import requests

from erp_web.schemas.api import API_SCHEMA_VERSION

EXPECTED_STATE_FIELDS = {
    "appConfig",
    "generatedImages",
    "imagePool",
    "mercadolibreAuthChecklist",
    "ok",
    "outputDir",
    "platformOptions",
    "product",
    "schemaVersion",
    "sourceImages",
    "storeAuthSummary",
    "storeConfig",
}


def _json(response: requests.Response) -> dict:
    payload = response.json()
    assert isinstance(payload, dict), response.text
    return payload


def _require_safe_write_target() -> None:
    if (
        os.environ.get("ERP_TEST_BASE_URL", "").strip()
        and os.environ.get("ERP_ACCEPTANCE_ALLOW_EXTERNAL_WRITES") != "1"
    ):
        pytest.fail(
            "拒绝向 ERP_TEST_BASE_URL 外部实例写验收数据；"
            "请使用默认隔离服务器，或对专用测试实例显式设置 "
            "ERP_ACCEPTANCE_ALLOW_EXTERNAL_WRITES=1"
        )


# 验收：请求体不是合法 JSON 对象时，HTTP 边界必须稳定返回 400，而不是泄漏 500。
@pytest.mark.parametrize(
    ("raw_body", "content_type"),
    [
        ("{", "application/json"),
        ("[]", "application/json"),
    ],
)
def test_write_endpoint_rejects_invalid_json_objects(
    backend_server: str,
    raw_body: str,
    content_type: str,
) -> None:
    _require_safe_write_target()
    response = requests.post(
        f"{backend_server}/api/save-product",
        data=raw_body.encode("utf-8"),
        headers={"Content-Type": content_type},
        timeout=10,
    )
    payload = _json(response)
    assert response.status_code == 400
    assert payload["ok"] is False
    assert str(payload.get("error") or "").strip()


# 验收：真实 /api/state 必须带版本、统一脱敏，且不再承担领域索引/日志聚合。
def test_real_state_endpoint_is_small_versioned_and_redacted(
    backend_server: str,
) -> None:
    _require_safe_write_target()
    secrets = (
        "acceptance-cookie-secret",
        "acceptance-ai-key-secret",
        "acceptance-store-token-secret",
    )
    saved = requests.post(
        f"{backend_server}/api/save-settings",
        json={
            "appConfig": {
                "auto_ai_recognition": "acceptance-http-enabled",
                "alibaba_cookie": secrets[0],
                "ai_models": [
                    {
                        "id": "acceptance-http-model",
                        "provider": "OpenAI-Compatible",
                        "model": "acceptance-model",
                        "api_key": secrets[1],
                    }
                ],
            },
            "storeConfig": {
                "mercadolibre": {
                    "site_id": "MLM",
                    "access_token": secrets[2],
                }
            },
        },
        timeout=10,
    )
    assert saved.status_code == 200, saved.text
    saved_payload = _json(saved)
    assert (
        saved_payload["appConfig"]["auto_ai_recognition"]
        == "acceptance-http-enabled"
    )
    saved_model = next(
        model
        for model in saved_payload["appConfig"]["ai_models"]
        if model.get("id") == "acceptance-http-model"
    )
    assert saved_model["api_key"]
    assert saved_model["api_key"] != secrets[1]
    saved_store = saved_payload["storeConfig"]["mercadolibre"]
    assert saved_store["site_id"] == "MLM"
    assert saved_store["access_token"]
    assert saved_store["access_token"] != secrets[2]

    response = requests.get(f"{backend_server}/api/state", timeout=10)
    state = _json(response)
    serialized = json.dumps(state, ensure_ascii=False)
    assert response.status_code == 200
    assert state["schemaVersion"] == API_SCHEMA_VERSION
    assert state["ok"] is True
    assert all(secret not in serialized for secret in secrets)
    assert state["appConfig"]["auto_ai_recognition"] == "acceptance-http-enabled"
    assert set(state) == EXPECTED_STATE_FIELDS


# 验收：设置接口必须拒绝 mass-assignment，未知顶层键不能进入持久化状态。
def test_real_settings_endpoint_ignores_unknown_top_level_fields(
    backend_server: str,
) -> None:
    _require_safe_write_target()
    unknown_key = "acceptance_unknown_admin_override"
    known_value = "acceptance-whitelist-roundtrip"
    saved = requests.post(
        f"{backend_server}/api/save-settings",
        json={
            "appConfig": {
                unknown_key: {"enabled": True},
                "auto_ai_recognition": known_value,
            }
        },
        timeout=10,
    )
    payload = _json(saved)
    assert saved.status_code == 200
    assert unknown_key not in payload["appConfig"]
    assert payload["appConfig"]["auto_ai_recognition"] == known_value

    state = _json(requests.get(f"{backend_server}/api/state", timeout=10))
    assert unknown_key not in state["appConfig"]
    assert state["appConfig"]["auto_ai_recognition"] == known_value

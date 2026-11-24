"""本机固定动作与管道/HTTP 共享契约；不依赖 ERP 上下文。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict
from pydantic import BaseModel, ConfigDict, Field

DesktopOperation = Literal[
    'browser.status', 'collect.read', 'collect.verify',
    'browser.ai.open', 'browser.ai.chat', 'canvas.check', 'canvas.edit',
    'canvas.accounts', 'canvas.account.login', 'canvas.account.logout', 'canvas.account.toggle',
    'cli.run', 'cli.status', 'config.read', 'config.write', 'auth.open',
]
LOCAL_MODEL_FIELDS = frozenset({
    'command', 'profile', 'sandbox', 'browser_mode', 'browser_profile',
    'browser_port', 'browser_url', 'browser_proxy',
})


class DesktopCall(TypedDict):
    id: str
    operation: DesktopOperation
    params: dict[str, Any]
    timeout: float


class DesktopReply(TypedDict):
    id: str
    ok: bool
    result: NotRequired[dict[str, Any]]
    error: NotRequired[str]
    error_code: NotRequired[str]
    outcome_unknown: NotRequired[bool]


class ActionParams(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CollectReadParams(ActionParams):
    tab_url: str = ''
    platform_hint: str = ''
    product_url: str = ''
    browser_tab_id: str = ''
    # 读取已发布协议中的 false 占位，独立快照操作已下线。
    save_only: Literal[False] = False
    open_url: bool = False


class VerificationParams(ActionParams):
    browser_tab_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)


class ModelParams(ActionParams):
    model: dict[str, Any]
    prompt: str = ''
    force_login: bool = False
    probe_image: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1, le=7200)


class CanvasParams(ModelParams):
    images: list[dict[str, Any]]
    mode: str = 'edit'
    count: int = 1
    combine_references: bool = False


class CanvasAccountLoginParams(ActionParams):
    model: dict[str, Any]
    profile: str = ''
    force_login: bool = False
    check_only: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1, le=7200)


class CanvasAccountLogoutParams(ActionParams):
    profile: str = Field(min_length=1, max_length=80)


class CanvasAccountToggleParams(ActionParams):
    profile: str = Field(min_length=1, max_length=80)
    enabled: bool


class ConfigParams(ActionParams):
    models: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AuthParams(ActionParams):
    url: str
    browser: str = 'default'


ACTION_PARAMS: dict[str, type[ActionParams]] = {
    'browser.status': ActionParams,
    'collect.read': CollectReadParams, 'collect.verify': VerificationParams,
    'browser.ai.open': ModelParams, 'browser.ai.chat': ModelParams, 'canvas.check': ModelParams,
    'canvas.edit': CanvasParams,
    'canvas.accounts': ActionParams,
    'canvas.account.login': CanvasAccountLoginParams,
    'canvas.account.logout': CanvasAccountLogoutParams,
    'canvas.account.toggle': CanvasAccountToggleParams,
    'cli.run': ModelParams, 'cli.status': ModelParams,
    'config.read': ActionParams, 'config.write': ConfigParams, 'auth.open': AuthParams,
}


def validate_action(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    schema = ACTION_PARAMS.get(operation)
    if schema is None:
        raise ValueError('不支持的本机操作')
    return schema.model_validate(params).model_dump()


@dataclass(frozen=True)
class LocalImageEditRequest:
    app_dir: Path | str
    model: dict[str, Any]
    prompt: str
    images: list[dict[str, Any]]
    mode: str = 'edit'
    count: int = 1
    timeout_seconds: int = 180
    combine_references: bool = False


def local_configuration_fingerprint(model):
    import hashlib
    import json
    value = {key: model[key] for key in sorted(LOCAL_MODEL_FIELDS) if key in model}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

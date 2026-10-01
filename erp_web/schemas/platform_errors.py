"""平台业务错误的公共契约，不依赖网络适配器。"""
from __future__ import annotations

from typing import Any

class PublishAdapterError(RuntimeError):
    """类型化发布错误：PublishingBus 依据 ``retryable`` 决定是否重试。

    平台 HTTP 边界负责把远端失败转换成本类型；未被分类的异常默认视为
    不可重试，避免确定性 4xx 被总线重复发送。``message`` 必须脱敏，
    不允许携带凭据。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "").strip() or "PUBLISH_FAILED"
        self.retryable = bool(retryable)
        self.details = dict(details or {})

    def to_error_map(self) -> dict[str, Any]:
        error_map = {
            "summary": str(self),
            "error_code": self.code,
            "retryable": self.retryable,
            "field_errors": (
                self.details.get("field_errors")
                if isinstance(self.details.get("field_errors"), dict)
                else {}
            ),
            "raw": str(self),
        }
        next_action = str(self.details.get("next_action") or "").strip()
        if next_action:
            error_map["next_action"] = next_action
        return error_map


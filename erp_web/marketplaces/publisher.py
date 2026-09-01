from __future__ import annotations

"""平台发布适配器契约。

通用发布编排只依赖此协议；具体平台负责类目解析、草稿校验、payload 构造、
真实发布和错误映射。未注册适配器即表示该平台没有发布能力，不能返回假成功。
"""

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycle
    from erp_web.runtime_units.publish_context import PreparedPublishContext


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


class PlatformPublisher(Protocol):
    """一个已真实接入发布链路的平台。"""

    platform: str

    def prepare_product(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        ...

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        ...

    def required_attributes_missing(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> list[str]:
        ...

    def validate_draft(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def build_payload(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        ...

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        ...

    def map_publish_error(self, error: Exception) -> dict[str, Any]:
        ...

    def publish(
        self,
        product: dict[str, Any],
        platform: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        ...


__all__ = ["PlatformPublisher", "PublishAdapterError"]

from __future__ import annotations

"""平台发布适配器契约。

通用发布编排只依赖此协议；具体平台负责类目解析、草稿校验、payload 构造、
真实发布和错误映射。未注册适配器即表示该平台没有发布能力，不能返回假成功。
"""

from typing import Any, Protocol


class PlatformPublisher(Protocol):
    """一个已真实接入发布链路的平台。"""

    platform: str

    def prepare_product(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        ...

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        ...

    def required_attributes_missing(self, product: dict[str, Any], config: dict[str, Any]) -> list[str]:
        ...

    def validate_draft(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        ...

    def build_payload(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
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


__all__ = ["PlatformPublisher"]

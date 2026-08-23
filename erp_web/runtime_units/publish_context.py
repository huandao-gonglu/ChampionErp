# -*- coding: utf-8 -*-
"""发布预检/编译的类型化临时上下文（类目 Schema 分离计划 Phase 2）。

每次预检/编译运行只加载一次类目定义：

```text
加载草稿目标
    ↓
Catalog.attribute_definitions(platform, site, category_id)
    ↓
PreparedPublishContext
    ├── adapter.validate_draft(context)
    └── adapter.build_payload(context)
```

发布队列持久化的是已批准 payload 与 digest，不持久化 CategoryDefinition；
队列 worker 发布冻结 payload 时不再重新加载规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from erp_web.schemas.category_definition import CategoryDefinition

from .category_definition_support import definition_to_legacy_attribute


CategoryRecordLoader = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class PreparedPublishContext:
    """一次发布评估的临时上下文；定义只加载一次并在预检/编译间共享。"""

    product: dict[str, Any]
    draft: dict[str, Any]
    target: dict[str, Any]
    category_definition: CategoryDefinition | None = None
    platform: str = ""
    #: 定义加载失败的可重试原因（用于预检提示，不阻断身份类检查）。
    definition_error: str = ""

    @property
    def category_id(self) -> str:
        return str(self.draft.get("category_id") or "").strip()

    def with_product(self, product: dict[str, Any]) -> "PreparedPublishContext":
        """复用同一次加载的定义，切换到更新后的商品/草稿视图。"""

        from .publish_helpers import _draft_for_platform

        draft = _draft_for_platform(product, self.platform)
        targets = (
            draft.get("target_sites")
            if isinstance(draft.get("target_sites"), list)
            else []
        )
        target = targets[0] if targets and isinstance(targets[0], dict) else {}
        return PreparedPublishContext(
            product=product,
            draft=draft,
            target=target,
            category_definition=self.category_definition,
            platform=self.platform,
            definition_error=self.definition_error,
        )

    @property
    def category_record(self) -> dict[str, Any] | None:
        """内部过渡视图：类型化定义 → legacy record shape（不含 raw/values）。"""

        definition = self.category_definition
        if definition is None:
            return None
        return {
            "platform": definition.platform,
            "site": definition.site,
            "category_id": definition.category_id,
            "description_category_id": definition.description_category_id,
            "category_path": definition.category_path,
            "path_original": [
                segment.strip()
                for segment in definition.category_path.split("/")
                if segment.strip()
            ],
            "source": f"{definition.platform}_live",
            "attributes": {
                "required": [
                    {
                        **definition_to_legacy_attribute(attribute),
                        "complex_id": attribute.platform_binding.complex_id,
                    }
                    for attribute in definition.required
                ],
                "optional": [
                    {
                        **definition_to_legacy_attribute(attribute),
                        "complex_id": attribute.platform_binding.complex_id,
                    }
                    for attribute in definition.optional
                ],
            },
        }


def prepare_publish_context(
    product: dict[str, Any],
    platform: str,
    *,
    timeout_seconds: float | None = None,
) -> PreparedPublishContext:
    """构造发布上下文；类目定义经 Catalog 当次加载一次。"""

    from .publish_helpers import _draft_for_platform

    platform = str(platform or "").strip().lower()
    draft = _draft_for_platform(product, platform)
    targets = (
        draft.get("target_sites") if isinstance(draft.get("target_sites"), list) else []
    )
    target = targets[0] if targets and isinstance(targets[0], dict) else {}
    category_id = str(draft.get("category_id") or "").strip()
    definition: CategoryDefinition | None = None
    definition_error = ""
    if category_id:
        from .category_catalog import get_category_catalog
        from .category_providers import category_provider_for

        if category_provider_for(platform) is not None:
            try:
                definition = get_category_catalog().attribute_definitions(
                    platform,
                    category_id,
                    site=str(draft.get("site") or target.get("site") or ""),
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - 预检必须可解释地继续
                definition_error = str(exc)
    return PreparedPublishContext(
        product=product,
        draft=draft,
        target=target,
        category_definition=definition,
        platform=platform,
        definition_error=definition_error,
    )


__all__ = [
    "CategoryRecordLoader",
    "PreparedPublishContext",
    "prepare_publish_context",
]

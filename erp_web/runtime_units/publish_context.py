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

from .publish_validation_session import PublishValidationSession
from .category_definition_support import definition_to_legacy_record


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
    #: 仅在本次评估中共享，键为类目和授权凭据；不进入持久化与日志。
    category_currency_cache: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict, repr=False, compare=False,
    )

    validation_session: PublishValidationSession | None = field(default=None, repr=False, compare=False)
    #: 当次评估按 CategoryDefinition 身份缓存派生视图，避免多 SKU 重复投影。
    _definition_cache: dict[str, tuple[CategoryDefinition, Any]] = field(
        default_factory=dict, repr=False, compare=False,
    )

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
            category_currency_cache=self.category_currency_cache,
            validation_session=self.validation_session,
            _definition_cache=self._definition_cache,
        )

    @property
    def category_record(self) -> dict[str, Any] | None:
        """内部过渡视图：类型化定义 → legacy record shape（不含 raw/values）。"""

        definition = self.category_definition
        if definition is None:
            return None
        cached = self._definition_cache.get("category_record")
        if cached is not None and cached[0] is definition:
            return cached[1]
        record = definition_to_legacy_record(definition)
        self._definition_cache["category_record"] = (definition, record)
        return record

    @property
    def attribute_definitions_dicts(self) -> list[dict[str, Any]]:
        """缓存当次类目定义的属性字典列表，避免多 SKU 投影逐条 model_dump。"""

        definition = self.category_definition
        if definition is None:
            return []
        cached = self._definition_cache.get("attribute_definitions_dicts")
        if cached is not None and cached[0] is definition:
            return cached[1]
        dumped = [
            attr.model_dump()
            for attr in (*definition.required, *definition.optional)
        ]
        self._definition_cache["attribute_definitions_dicts"] = (definition, dumped)
        return dumped

    @property
    def variant_attribute_dicts(self) -> dict[str, dict[str, Any]]:
        """缓存当次类目定义的变体属性字典索引，供逐 SKU 过滤旧缺失提示复用。"""

        definition = self.category_definition
        if definition is None:
            return {}
        cached = self._definition_cache.get("variant_attribute_dicts")
        if cached is not None and cached[0] is definition:
            return cached[1]
        variants = {
            attr["id"]: attr
            for attr in self.attribute_definitions_dicts
            if attr.get("variation_role") == "variant"
        }
        self._definition_cache["variant_attribute_dicts"] = (definition, variants)
        return variants


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
            catalog = get_category_catalog()
            site = str(draft.get("site") or target.get("site") or "")
            try:
                definition = catalog.attribute_definitions(
                    platform,
                    category_id,
                    site=site,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - 预检必须可解释地继续
                definition_error = str(exc)
            if platform == "yandex":
                normalized_cid = (
                    str(int(category_id))
                    if category_id.isascii() and category_id.isdigit()
                    else category_id
                )
                if normalized_cid not in catalog.cached_category_ancestor_map(platform, site=site):
                    try:
                        catalog.category_detail(
                            platform,
                            category_id,
                            site=site,
                            timeout_seconds=timeout_seconds,
                        )
                    except Exception:  # noqa: BLE001 - 类目树不可用时仍保留精确 ID 拦截
                        pass
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

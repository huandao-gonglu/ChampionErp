"""单次发布校验的共享读取；不跨请求复用、不持久化。"""
from dataclasses import dataclass, field
from functools import cached_property
import json
from typing import Any

from erp_web.context import get_context
from erp_web.services.image_delivery_service import ImageInspectionCache


@dataclass
class PublishValidationSession:
    # 组入口已检查逐 SKU 的成本、尺寸和库存，叶子只检查报价身份与平台规则。
    sku_inputs_checked: bool = False
    images: ImageInspectionCache = field(default_factory=ImageInspectionCache)
    logistics_errors: dict[str, str] = field(default_factory=dict)
    _yandex_ancestors_by_token: dict[str, dict[str, tuple[str, ...]]] = field(
        default_factory=dict, repr=False, compare=False,
    )

    @cached_property
    def store_config(self) -> dict[str, Any]:
        return get_context().config.load_store_config()

    @cached_property
    def forbidden_categories(self) -> dict[str, str]:
        settings = get_context().config.forbidden_categories()
        return {item["category_id"]: item["name"] for item in settings["categories"]}

    def yandex_ancestor_ids(
        self,
        category_id: str,
        *,
        api_token: str = "",
        category_record: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        cid = str(category_id or "").strip()
        if not cid:
            return ()
        if cid.isascii() and cid.isdigit():
            cid = str(int(cid))
        token = str(api_token or "").strip()
        if token not in self._yandex_ancestors_by_token:
            self._yandex_ancestors_by_token[token] = (
                get_context().category_catalog.cached_category_ancestor_map(
                    "yandex",
                    api_token=token,
                )
            )
        ancestor_map = self._yandex_ancestors_by_token[token]
        if cid in ancestor_map:
            return ancestor_map[cid]
        if isinstance(category_record, dict):
            raw = category_record.get("ancestor_ids")
            if isinstance(raw, (list, tuple)):
                return tuple(str(item).strip() for item in raw if str(item).strip())
        return ()

    @cached_property
    def tariff_versions(self) -> dict[str, str]:
        return get_context().tariffs.versions()

    def yandex_logistics_error(self, plan: dict[str, Any]) -> str:
        key = json.dumps(plan, sort_keys=True, ensure_ascii=False)
        if key not in self.logistics_errors:
            try:
                get_context().yandex_logistics.validate(plan)
            except ValueError as exc:
                self.logistics_errors[key] = str(exc)
            else:
                self.logistics_errors[key] = ""
        return self.logistics_errors[key]

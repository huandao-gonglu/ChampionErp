# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from erp_web import marketplaces as marketplace_api
from erp_web.context import AppContext, get_context
from erp_web.marketplace_registry import CAP_PUBLISH, platform_has_capability, platform_label
from erp_web.marketplaces.publisher import PlatformPublisher, PublishAdapterError
from erp_web.marketplaces.publishing import poll_mercadolibre_publish_status
from erp_web.product_model import default_draft
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.stores.product_store import normalize_product_fields

from .publish_helpers import (
    _required_attribute_summary,
    build_mercadolibre_publish_payload,
    precheck_item,
    validate_mercadolibre_publish_payload,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .publish_context import PreparedPublishContext
from .publish_mercadolibre import (
    ensure_mercadolibre_pictures_uploaded,
    map_mercadolibre_publish_error,
)
from .publish_ozon import (
    build_ozon_publish_payload,
    map_ozon_publish_error,
    ozon_category_pair,
    ozon_required_attributes_missing,
    poll_ozon_import_status,
    publish_ozon_payload,
    validate_ozon_publish_payload,
)
from .publish_validation import validate_mercadolibre_draft
from .publish_validation import validate_ozon_draft
from .publish_validation import validate_yandex_draft
from .publish_yandex import (
    build_yandex_publish_payload,
    map_yandex_publish_error,
    poll_yandex_publish_status,
    publish_yandex_payload,
    validate_yandex_publish_payload,
    yandex_required_attributes_missing,
)

logger = logging.getLogger(__name__)


def _flag_definition_unavailable(
    context: "PreparedPublishContext",
    precheck: dict[str, Any],
) -> dict[str, Any]:
    """草稿已选类目但当次定义加载失败时，预检必须显式报错而非静默放行。"""

    if context.category_id and context.category_definition is None:
        errors = list(precheck.get("errors") or [])
        errors.append(
            precheck_item(
                "CATEGORY_ATTRIBUTES_UNAVAILABLE",
                "category_id",
                "类目属性定义暂时不可用，无法完成发布校验",
                "error",
                context.definition_error
                or "稍后重试；若持续失败请检查平台授权与类目接口",
            )
        )
        return {**precheck, "ok": False, "errors": errors}
    return precheck


class MercadoLibrePublishingAdapter:
    """Mercado Libre 的完整发布适配器；也是当前唯一可入队的平台。"""

    platform = "mercadolibre"

    def prepare_product(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        # User Products 只接受 Mercado picture ID。统一发布队列必须在编译
        # payload 前完成上传，不能依赖已删除的特殊“真实发布”入口。
        token = str((config.get(self.platform) or {}).get("access_token") or "")
        uploaded = ensure_mercadolibre_pictures_uploaded(product, token)
        if not uploaded.get("ok"):
            errors = [
                str(item.get("message") or "")
                for item in uploaded.get("errors", [])
                if isinstance(item, dict) and str(item.get("message") or "")
            ]
            raise RuntimeError("；".join(errors) or "Mercado Libre 图片上传失败")
        return normalize_product_fields(uploaded.get("product"))

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        # 类目身份只来自平台草稿；不再回落到商品级规则副本。
        product = normalize_product_fields(product)
        drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
        draft = drafts.get(self.platform) if isinstance(drafts.get(self.platform), dict) else {}
        category_id = str(
            draft.get("category_id")
            or config.get(self.platform, {}).get("category_id")
            or ""
        ).strip()
        if category_id:
            drafts = product.setdefault("drafts", {})
            draft = drafts.setdefault(
                self.platform,
                default_draft(self.platform),
            )
            draft["category_id"] = category_id
        return product

    def required_attributes_missing(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> list[str]:
        return list(
            _required_attribute_summary(
                context.product, self.platform, context.category_record
            ).get("missing")
            or []
        )

    def validate_draft(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        result = validate_mercadolibre_draft(
            context.product,
            config,
            context.category_record,
            category_definition=context.category_definition,
        )
        return _flag_definition_unavailable(context, result)

    def build_payload(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return build_mercadolibre_publish_payload(
            context.product,
            config,
            category_definition=context.category_definition,
        )

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        return validate_mercadolibre_publish_payload(payload, config)

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        token = str((config.get(self.platform) or {}).get("access_token") or "")
        return marketplace_api.publish_mercadolibre(payload, token)

    def map_publish_error(self, error: Exception) -> dict[str, Any]:
        if isinstance(error, PublishAdapterError):
            return error.to_error_map()
        return map_mercadolibre_publish_error(marketplace_api.parse_mercadolibre_error(error))

    def poll_publish_status(
        self,
        result: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        store = (
            config.get(self.platform)
            if isinstance(config.get(self.platform), dict)
            else {}
        )
        token = str(store.get("access_token") or "")
        try:
            return poll_mercadolibre_publish_status(
                result,
                token,
                max_confirmation_polls=max(
                    1,
                    int(store.get("publish_confirmation_max_polls") or 300),
                ),
            )
        except PublishAdapterError as exc:
            # 此处只执行 task GET；读失败不能证明此前 PUT 失败。重试耗尽后
            # PublishingBus 必须进入 outcome_unknown 并保留活动锁。
            raise PublishAdapterError(
                exc.code,
                str(exc),
                retryable=exc.retryable,
                details={**exc.details, "outcome_unknown": True},
            ) from exc
        except Exception as exc:
            raise PublishAdapterError(
                "MERCADOLIBRE_CONFIRMATION_FAILED",
                str(exc) or "Mercado Libre 异步任务确认失败",
                retryable=False,
                details={"outcome_unknown": True},
            ) from exc

    def publish_poll_interval_seconds(self, config: dict[str, Any]) -> float:
        store = (
            config.get(self.platform)
            if isinstance(config.get(self.platform), dict)
            else {}
        )
        return max(
            0.2,
            float(store.get("publish_poll_interval_seconds") or 1.0),
        )

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        from .runtime_api import publish_product

        return publish_product(product, platform, config)


class OzonPublishingAdapter:
    """通过 Ozon Seller API 创建或更新商品，并确认异步导入终态。"""

    platform = "ozon"

    def prepare_product(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return get_context().image_delivery.prepare_product(product, self.platform)

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        product = normalize_product_fields(product)
        type_id, _ = ozon_category_pair(product)
        if type_id:
            drafts = product.setdefault("drafts", {})
            draft = drafts.setdefault(self.platform, default_draft(self.platform))
            draft["category_id"] = type_id
        return product

    def required_attributes_missing(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> list[str]:
        return ozon_required_attributes_missing(
            context.product, context.category_record
        )

    def validate_draft(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        result = validate_ozon_draft(
            context.product, config, context.category_record
        )
        return _flag_definition_unavailable(context, result)

    def build_payload(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return build_ozon_publish_payload(
            context.product, config, context.category_record
        )

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        return validate_ozon_publish_payload(payload, config)

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        store = config.get(self.platform) if isinstance(config.get(self.platform), dict) else {}
        return publish_ozon_payload(
            payload,
            str(store.get("client_id") or "").strip(),
            str(store.get("api_key") or "").strip(),
            timeout_seconds=float(store.get("publish_timeout_seconds") or 30),
            poll_interval_seconds=float(store.get("publish_poll_interval_seconds") or 0.5),
        )

    def map_publish_error(self, error: Exception) -> dict[str, Any]:
        return map_ozon_publish_error(error)

    def poll_publish_status(
        self,
        result: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """继续确认已经提交的 Ozon task_id，不重新创建导入任务。"""

        pending = (
            result.get("result")
            if isinstance(result.get("result"), dict)
            else result
        )
        task_id = pending.get("task_id")
        if task_id in (None, "", 0):
            raise RuntimeError("Ozon 待确认发布结果缺少 task_id")
        store = config.get(self.platform) if isinstance(config.get(self.platform), dict) else {}
        try:
            polled = poll_ozon_import_status(
                task_id,
                str(store.get("client_id") or "").strip(),
                str(store.get("api_key") or "").strip(),
            )
        except RuntimeError as exc:
            if not str(exc).startswith("Ozon 商品导入失败："):
                raise
            mapped = map_ozon_publish_error(exc)
            return {
                "ok": False,
                "status": "real_publish_failed",
                "error": mapped["summary"],
                "error_code": mapped["error_code"],
                "error_map": mapped,
            }
        if polled["status"] == "pending_confirmation":
            return {
                "ok": True,
                "status": "publish_pending_confirmation",
                "result": polled,
            }
        return {
            "ok": True,
            "status": "real_publish_success",
            "result": polled,
        }

    def publish_poll_interval_seconds(self, config: dict[str, Any]) -> float:
        store = config.get(self.platform) if isinstance(config.get(self.platform), dict) else {}
        return max(0.05, float(store.get("publish_poll_interval_seconds") or 0.5))

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        from .runtime_api import publish_product

        return publish_product(product, platform, config)


class YandexPublishingAdapter:
    """Yandex Market Seller API 的创建/编辑发布适配器。

    发布状态机按“目录商品 → 上架条件 → 价格 → 库存 → 只读回读”执行；
    每次调用只推进一个确定性 mutation，checkpoint 由 PublishingBus 持久化。
    """

    platform = "yandex"

    def prepare_product(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return get_context().image_delivery.prepare_product(product, self.platform)

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        # 类目身份只来自平台草稿；不再回落到商品级规则副本。
        product = normalize_product_fields(product)
        drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
        draft = drafts.get(self.platform) if isinstance(drafts.get(self.platform), dict) else {}
        category_id = str(draft.get("category_id") or "").strip()
        if category_id:
            drafts = product.setdefault("drafts", {})
            target_draft = drafts.setdefault(
                self.platform,
                default_draft(self.platform),
            )
            target_draft["category_id"] = category_id
        return product

    def required_attributes_missing(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> list[str]:
        return yandex_required_attributes_missing(
            context.product, context.category_record
        )

    def validate_draft(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        result = validate_yandex_draft(
            context.product, config, context.category_record
        )
        return _flag_definition_unavailable(context, result)

    def build_payload(
        self,
        context: "PreparedPublishContext",
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return build_yandex_publish_payload(
            context.product, config, context.category_record
        )

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        return validate_yandex_publish_payload(payload, config)

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return publish_yandex_payload(payload, config)

    def map_publish_error(self, error: Exception) -> dict[str, Any]:
        return map_yandex_publish_error(error)

    def poll_publish_status(
        self,
        result: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """根据已持久化 checkpoint 推进下一个 mutation 或只读确认。"""

        return poll_yandex_publish_status(result, config)

    def publish_poll_interval_seconds(self, config: dict[str, Any]) -> float:
        store = config.get(self.platform) if isinstance(config.get(self.platform), dict) else {}
        return min(30.0, max(0.5, float(store.get("publish_poll_interval_seconds") or 2.0)))

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        from .runtime_api import publish_product

        return publish_product(product, platform, config)


_PUBLISHERS: dict[str, PlatformPublisher] = {
    MercadoLibrePublishingAdapter.platform: MercadoLibrePublishingAdapter(),
    OzonPublishingAdapter.platform: OzonPublishingAdapter(),
    YandexPublishingAdapter.platform: YandexPublishingAdapter(),
}


def publishing_adapter_for(platform: str) -> PlatformPublisher | None:
    key = str(platform or "").strip().lower()
    if not platform_has_capability(key, CAP_PUBLISH):
        return None
    return _PUBLISHERS.get(key)


def require_publishing_adapter(platform: str) -> PlatformPublisher:
    adapter = publishing_adapter_for(platform)
    if adapter is None:
        raise RuntimeError(f"{platform_label(platform)}发布未接入")
    return adapter


def unsupported_publish_response(platform: str) -> dict[str, Any]:
    key = str(platform or "").strip().lower()
    return {
        "ok": False,
        "supported": False,
        "platform": key,
        "status": "unsupported",
        "error": f"{platform_label(key)}发布未接入",
    }


def build_publishing_bus(context: AppContext) -> PublishingBus:
    """为一个 AppContext 构造发布总线；测试上下文与生产上下文互不串状态。"""

    from .publish_bus import persist_publish_bus_terminal_results

    return PublishingBus(
        context.db,
        adapters=dict(_PUBLISHERS),
        config_provider=context.config.load_store_config,
        terminal_callback=lambda state: (
            persist_publish_bus_terminal_results(
                state,
                context=context,
            )
        ),
        auto_resume_pending=False,
    )


def get_publishing_bus() -> PublishingBus:
    return get_context().publishing_bus


def resume_pending_publish_jobs() -> None:
    """Explicitly recover publish jobs left queued/running by a previous run."""
    try:
        get_publishing_bus().recover_pending_jobs()
    except Exception:
        logger.exception("Failed to resume pending publish jobs")


__all__ = [
    "MercadoLibrePublishingAdapter",
    "OzonPublishingAdapter",
    "YandexPublishingAdapter",
    "build_publishing_bus",
    "get_publishing_bus",
    "publishing_adapter_for",
    "require_publishing_adapter",
    "resume_pending_publish_jobs",
    "unsupported_publish_response",
]

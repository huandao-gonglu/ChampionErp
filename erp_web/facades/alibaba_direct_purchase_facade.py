"""1688 万邦个人直购门面装配；强制使用万邦企业 Key 与独立直购存储。"""
from __future__ import annotations

from erp_web.context import get_context
from erp_web.services.alibaba_authorization_service import (
    alibaba_onebound_runtime_config,
    onebound_direct_purchase_status,
)
from erp_web.services.alibaba_direct_purchase_service import AlibabaDirectPurchaseService
from erp_web.services.onebound_quota_service import credit_paid_order


def create_service(context, *, client_factory=None) -> AlibabaDirectPurchaseService:
    kwargs = {}
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    return AlibabaDirectPurchaseService(
        context.alibaba_direct_purchases,
        config_provider=lambda: alibaba_onebound_runtime_config(context.config),
        status_provider=lambda: onebound_direct_purchase_status(context.config),
        read_cache=context.alibaba_read_cache,
        quota_credit=lambda config, order_number, total_fen: credit_paid_order(
            context.onebound_quota, config, order_number, total_fen,
        ),
        **kwargs,
    )


def state(*, context=None) -> dict:
    return create_service(context or get_context()).state()


def addresses(*, refresh: bool = False, context=None) -> dict:
    return create_service(context or get_context()).addresses(refresh=refresh)


def command(action: str, body: dict, *, context=None) -> dict:
    service = create_service(context or get_context())
    return {
        "resolve": service.resolve,
        "parse-address": service.parse_address,
        "preview": service.preview,
        "create": service.create,
        "reconcile": service.reconcile,
        "cashier": service.cashier,
        "payment-status": service.payment_status,
        "pay": service.pay,
    }[action](body)


__all__ = ["addresses", "command", "create_service", "state"]

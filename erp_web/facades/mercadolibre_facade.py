from __future__ import annotations

"""Mercado Libre webhook 的领域入口。"""

from erp_web.runtime_units.mercadolibre_orders import (
    record_mercadolibre_order_notification,
)

__all__ = ["record_mercadolibre_order_notification"]

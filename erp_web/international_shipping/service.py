"""国际物流唯一报价入口；一个实例固定本轮规则和仓库渠道。"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from .models import Package, PriceForShipping, QuoteResult
from .platform_api import PlatformClient, quote_mercadolibre
from .tables import quote_table
from .ogl import quote_ogl
from .tariff_store import load_active



class ShippingModule:
    def __init__(self, rules_dir: Path, credentials: dict[str, dict], *, opener, snapshot_loader=None):
        self.opener = opener
        self.rules_dir = rules_dir
        self.credentials = credentials
        self.snapshot_loader = snapshot_loader or (lambda platform, version=None: load_active(rules_dir, platform, version))
        self.snapshots: dict[str, dict] = {}
        self.errors: dict[str, str] = {}
        self.ozon_discovery_ms = 0.0

    def quote(
        self, platform: str, package: Package, price_for_shipping: PriceForShipping,
        *, cny_per_rub: Decimal | None = None, cny_per_usd: Decimal | None = None,
        binding: dict | None = None, category_id: str = '',
        listing_type_id: str = 'gold_pro', free_shipping: bool = True,
        tariff_version: str | None = None,
        route_ids: set[str] | None = None,
    ) -> QuoteResult:
        if platform == 'mercadolibre':
            return quote_mercadolibre(
                PlatformClient(platform, self.credentials.get(platform, {}), opener=self.opener), package,
                binding or {}, price_for_shipping, cny_per_usd=cny_per_usd, category_id=category_id,
                listing_type_id=listing_type_id, free_shipping=free_shipping,
            )
        if platform not in ('ozon', 'yandex'):
            raise ValueError('当前平台没有国际物流自动报价')
        if platform in self.errors:
            raise ValueError(self.errors[platform])
        if platform not in self.snapshots:
            try:
                self.snapshots[platform] = self.snapshot_loader(platform, tariff_version)
            except (ValueError, FileNotFoundError) as exc:
                self.errors[platform] = str(exc)
                raise
        elif tariff_version and self.snapshots[platform]['version'] != tariff_version:
            raise ValueError('同一次核价不能混用同一平台的不同费率版本')
        if platform == 'ozon':
            return quote_ogl(self.snapshots[platform], package, price_for_shipping, cny_per_rub=cny_per_rub)
        return quote_table(self.snapshots[platform], package, price_for_shipping, cny_per_rub=cny_per_rub, route_ids=route_ids)

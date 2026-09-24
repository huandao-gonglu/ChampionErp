"""国际物流唯一报价入口；一个实例固定本轮规则和仓库渠道。"""
from __future__ import annotations

from decimal import Decimal
import logging
from pathlib import Path
from time import perf_counter

from .models import Package, PriceForShipping, QuoteResult
from .platform_api import PlatformClient, discover_ozon, quote_mercadolibre
from .tables import quote_table
from .tariff_store import load_active

logger = logging.getLogger("erp.pricing")


class ShippingModule:
    def __init__(self, rules_dir: Path, credentials: dict[str, dict]):
        self.rules_dir = rules_dir
        self.credentials = credentials
        self.snapshots: dict[str, dict] = {}
        self.methods: list[dict] | None = None
        self.errors: dict[str, str] = {}
        self.ozon_discovery_ms = 0.0

    def quote(
        self, platform: str, package: Package, price_for_shipping: PriceForShipping,
        *, cny_per_rub: Decimal | None = None, cny_per_usd: Decimal | None = None,
        binding: dict | None = None, category_id: str = '',
        listing_type_id: str = 'gold_pro', free_shipping: bool = True,
        tariff_version: str | None = None,
    ) -> QuoteResult:
        if platform == 'mercadolibre':
            return quote_mercadolibre(
                PlatformClient(platform, self.credentials.get(platform, {})), package,
                binding or {}, price_for_shipping, cny_per_usd=cny_per_usd, category_id=category_id,
                listing_type_id=listing_type_id, free_shipping=free_shipping,
            )
        if platform not in ('ozon', 'yandex'):
            raise ValueError('当前平台没有国际物流自动报价')
        if platform in self.errors:
            raise ValueError(self.errors[platform])
        if platform not in self.snapshots:
            try:
                self.snapshots[platform] = load_active(self.rules_dir, platform, tariff_version)
            except (ValueError, FileNotFoundError) as exc:
                self.errors[platform] = str(exc)
                raise
        elif tariff_version and self.snapshots[platform]['version'] != tariff_version:
            raise ValueError('同一次核价不能混用同一平台的不同费率版本')
        if platform == 'ozon' and self.methods is None:
            started = perf_counter()
            logger.info("Ozon 公共渠道查询开始：读取 rFBS 仓库和配送渠道，本批 SKU 共用")
            try:
                self.methods = discover_ozon(PlatformClient(platform, self.credentials.get(platform, {})))
            except ValueError as exc:
                # 本轮公共查询失败后直接复用错误，下轮新建实例才重试。
                self.errors[platform] = str(exc)
                raise
            finally:
                self.ozon_discovery_ms = (perf_counter() - started) * 1000
                logger.info("Ozon 公共渠道查询%s，耗时 %.2f 秒，可用渠道 %d", "失败" if platform in self.errors else "完成", self.ozon_discovery_ms / 1000, len(self.methods or []))
        return quote_table(self.snapshots[platform], package, price_for_shipping, cny_per_rub=cny_per_rub, methods=self.methods)

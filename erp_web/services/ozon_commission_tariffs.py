"""从 Ozon 当前官方说明发现并读取中国／香港卖家的新品 rFBS 佣金表。"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import re
from urllib.parse import urlsplit

from erp_web.international_shipping.tariff_store import Workbook
from erp_web.services.commission_sources import read_official_content


ARTICLE_URL = "https://global-help.ozon.com/zh/commissions/ozon-fees/commissions"
DOCUMENT_URL = ("https://global-help.ozon.com/document-manager-api/global-help/api/v3/document/public/by-path"
                "?path=/commissions/ozon-fees/commissions&lang=zh")
_BAND = re.compile(r"^RFBS\s*->\s*([\d.]+)\s*(?:-\s*([\d.]+)|(\+))\s*->")


def _links(node):
    if isinstance(node, dict):
        href = node.get("href")
        if isinstance(href, str):
            yield href
        for value in node.values():
            yield from _links(value)
    elif isinstance(node, list):
        for value in node:
            yield from _links(value)


def _name(value):
    return " ".join(str(value or "").split()).casefold()


class OzonCommissionTariffs:
    """每次核价重新读取当前公告；同一批 SKU 共用资料，不回退到旧版本。"""

    def __init__(self):
        self.loaded = False
        self.error = ""
        self.rows = {}

    def _load(self):
        if self.error:
            raise ValueError(self.error)
        if self.loaded:
            return
        try:
            body = json.loads(read_official_content(DOCUMENT_URL))
            document = body["document"]
            if document.get("status") != "active" or document.get("url") != "/commissions/ozon-fees/commissions":
                raise ValueError("Ozon 当前官方佣金说明无效")
            content = document["contentJson"]
            content = json.loads(content) if isinstance(content, str) else content
            urls = {url for url in _links(content) if urlsplit(url).scheme == "https"
                    and urlsplit(url).netloc == "cdn.ozone.ru"
                    and re.search(r"/Tarifs_CN_[^/]+\.xlsx$", urlsplit(url).path)}
            if len(urls) != 1:
                raise ValueError("Ozon 当前官方说明未提供唯一的中国／香港标准佣金表")
            self.file_url = urls.pop()
            workbook = Workbook(content=read_official_content(self.file_url))
            sheets = [sheet for sheet in workbook.sheets if sheet["name"] == "Full ChinaHK"]
            if len(sheets) != 1:
                raise ValueError("Ozon 官方佣金表缺少 Full ChinaHK 明细")
            rows = sheets[0]["rows"]
            value = lambda row, column: row.get(column, {}).get("value", "")
            first_row = " ".join(cell["value"] for cell in rows[1].values())
            start = re.search(r"Starts from\s+(\d{2}/\d{2}/\d{4})", first_row)
            if not start:
                raise ValueError("Ozon 官方佣金表缺少生效日期")
            effective = datetime.strptime(start[1], "%d/%m/%Y").date()
            if effective > datetime.now(timezone.utc).date():
                raise ValueError("Ozon 官方佣金表尚未生效")
            headers = rows[2]
            if value(headers, "A") != "DescriptiveType" or value(headers, "D") != "DescriptiveCategory3" or value(headers, "J") != "Brand":
                raise ValueError("Ozon 官方佣金表的类型、类目或品牌列已变化")
            bands = []
            for column, cell in headers.items():
                match = _BAND.match(cell["value"])
                if match:
                    bands.append((column, Decimal(match[1]), Decimal(match[2]) if match[2] else None))
            bands.sort(key=lambda band: band[1])
            if not bands or bands[0][1] != 0 or bands[-1][2] is not None:
                raise ValueError("Ozon 官方 rFBS 价格档不完整")
            for left, right in zip(bands, bands[1:]):
                if left[2] is None or left[2] + Decimal("0.01") != right[1]:
                    raise ValueError("Ozon 官方 rFBS 价格档存在缺口或重叠")
            for number, row in rows.items():
                if number < 3 or not value(row, "A"):
                    continue
                key = (_name(value(row, "A")), _name(value(row, "D")))
                rates = [Decimal(value(row, column)) for column, _, _ in bands]
                if not value(row, "J") or not all(rate.is_finite() and 0 <= rate < 1 for rate in rates):
                    raise ValueError("Ozon 官方佣金表包含无效费率或品牌规则")
                self.rows.setdefault(key, []).append({"row": number, "brand": value(row, "J"), "rates": rates})
            if not self.rows:
                raise ValueError("Ozon 官方佣金表没有商品类型明细")
            self.bands = bands
            self.metadata = {"source": "official_tariff", "endpoint": ARTICLE_URL, "file_url": self.file_url,
                             "file_sha256": workbook.sha, "effective_from": effective.isoformat(),
                             "document_updated_at": document.get("updated", ""), "scope": "ChinaHK/rFBS"}
            self.loaded = True
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            self.error = str(exc) if isinstance(exc, ValueError) else "Ozon 官方佣金资料格式无效"
            raise ValueError(self.error) from None

    def quote(self, category: dict, brand_attribute, rub_price: Decimal):
        self._load()
        path = category.get("path_original") or []
        if len(path) < 2:
            raise ValueError("Ozon 官方商品类型缺少所属类目，无法匹配佣金")
        key = (_name(category.get("name_original")), _name(path[-2]))
        rows = self.rows.get(key, [])
        if not rows:
            raise ValueError("Ozon 当前官方佣金表未包含所选商品类型与类目的精确组合")
        rub_price = rub_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        matches = [index for index, (_, lower, upper) in enumerate(self.bands)
                   if lower <= rub_price and (upper is None or rub_price <= upper)]
        if len(matches) != 1:
            raise ValueError("Ozon 官方佣金表未覆盖当前卢布售价")
        index = matches[0]
        # 有品牌特例时必须使用真正选中的字典名称；不凭标题、SKU 或默认品牌推测。
        brands = brand_attribute.get("values") if isinstance(brand_attribute, dict) else None
        brand = (brands[0].get("value") if isinstance(brands, list) and len(brands) == 1
                 and isinstance(brands[0], dict) and brands[0].get("dictionary_value_id") else "")
        if len({row["rates"][index] for row in rows}) > 1 and not brand:
            raise ValueError("Ozon 此商品类型存在品牌专属佣金，请先选择品牌属性")
        selected = [row for row in rows if brand and _name(row["brand"]) == _name(brand)]
        if not selected:
            selected = [row for row in rows if _name(row["brand"]) == "all"]
        if not selected or len({row["rates"][index] for row in selected}) != 1:
            raise ValueError("Ozon 官方品牌佣金规则不唯一，无法确认费率")
        column, lower, upper = self.bands[index]
        return selected[0]["rates"][index], {**self.metadata,
            "type_id": str(category["type_id"]), "description_category_id": str(category["description_category_id"]),
            "type_name": category["name_original"], "description_category_name": path[-2],
            "brand_attribute": brand_attribute, "brand_rule": selected[0]["brand"],
            "source_rows": [row["row"] for row in selected], "source_column": column,
            "rub_price": str(rub_price), "price_band_rub": {"min": str(lower), "max": str(upper) if upper is not None else None},
            "limitation": "中国／香港卖家 rFBS 基础销售佣金；按本次汇率试算，不含地区及推广系数，最终按下单日费率与汇率结算",
        }


__all__ = ["OzonCommissionTariffs"]

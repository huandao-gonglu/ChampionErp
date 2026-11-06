"""商品页面的结构化事实提取，不下载文件、不访问业务库。"""
from __future__ import annotations
import json
import html as html_module
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any
from erp_shared import html as html_extract_service
from erp_shared.html import normalize_space
from erp_shared.text import normalize_list

def extract_text_pattern(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.M)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return ""

def infer_list_from_text(value: str) -> list[str]:
    cleaned = re.sub(r"[，;；/]+", "、", value)
    return [item.strip() for item in cleaned.split("、") if item.strip()]

def _clean_attribute_key(value: Any, limit: int = 80) -> str:
    text = normalize_space(html_module.unescape(str(value or "")))
    text = re.sub(r"[\s：:]+$", "", text).strip()
    return text[:limit]

def _clean_attribute_value(value: Any, limit: int = 600) -> str:
    if isinstance(value, list):
        value = "、".join(str(item or "").strip() for item in value if str(item or "").strip())
    text = normalize_space(html_module.unescape(str(value or "")))
    return text[:limit]

def _add_attribute(attrs: dict[str, str], key: Any, value: Any, *, overwrite: bool = False) -> None:
    clean_key = _clean_attribute_key(key)
    clean_value = _clean_attribute_value(value)
    if not clean_key or not clean_value:
        return
    if len(clean_key) > 80 or clean_key.lower() in {"url", "href", "src", "class", "style"}:
        return
    if overwrite or clean_key not in attrs:
        attrs[clean_key] = clean_value

def _extract_balanced_json_after(text: str, marker: str) -> str:
    marker_index = text.find(marker)
    if marker_index < 0:
        return ""
    start = text.find("{", marker_index + len(marker))
    if start < 0:
        return ""
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""

def _json_object_after(text: str, marker: str) -> dict[str, Any]:
    raw = _extract_balanced_json_after(text, marker)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

class _AttributeTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._table_depth = 0
        self._cell_tag = ""
        self._cell_parts: list[str] = []
        self.cells: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
        if self._table_depth and tag in {"th", "td"}:
            self._cell_tag = tag
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._table_depth and tag in {"th", "td"} and self._cell_tag == tag:
            value = normalize_space("".join(self._cell_parts))
            if value:
                self.cells.append(value)
            self._cell_tag = ""
            self._cell_parts = []
        if tag == "table" and self._table_depth:
            self._table_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._cell_tag:
            self._cell_parts.append(data)

def _section_after_id(html: str, element_id: str, limit: int = 220_000) -> str:
    start = html.find(f'id="{element_id}"')
    if start < 0:
        start = html.find(f"id='{element_id}'")
    if start < 0:
        return ""
    candidates = [
        html.find(f'<div id="{next_id}"', start + 1)
        for next_id in ["productWarning", "sizeChart", "productPackInfo", "description", "sameProduct"]
    ]
    end_candidates = [index for index in candidates if index > start]
    end = min(end_candidates) if end_candidates else min(len(html), start + limit)
    return html[start:end]

def extract_1688_attribute_table(html: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    sections = [_section_after_id(html, "productAttributes")]
    if not sections[0]:
        heading_index = html.find("商品属性")
        if heading_index >= 0:
            end_candidates = [html.find(marker, heading_index + 4) for marker in ["包装信息", "商品详情", "商品评价"]]
            end_positions = [index for index in end_candidates if index > heading_index]
            end = min(end_positions) if end_positions else min(len(html), heading_index + 120_000)
            sections = [html[heading_index:end]]
    for section in [item for item in sections if item]:
        parser = _AttributeTableParser()
        try:
            parser.feed(section)
        except Exception:
            continue
        cells = parser.cells
        for index in range(0, len(cells) - 1, 2):
            _add_attribute(attrs, cells[index], cells[index + 1], overwrite=True)
    return attrs

def _iter_cpv_rows(container: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ["decisionCpv", "normalCpv", "featureAttributes", "attributes"]:
        value = container.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows

def extract_1688_context_data(html: str) -> dict[str, Any]:
    data_json = _json_object_after(html, '"dataJson":')
    cpv = _json_object_after(html, '"CpvEnhance":')
    piece_weight_scale = _json_object_after(html, '"pieceWeightScale":')
    final_price = _json_object_after(html, '"finalPriceModel":')

    attrs: dict[str, str] = {}
    for item in _iter_cpv_rows(cpv):
        _add_attribute(attrs, item.get("name") or item.get("key"), item.get("value") or item.get("values") or item.get("decisionValues"))
    sell_info = (
        ((final_price.get("tradeModel") or {}) if isinstance(final_price.get("tradeModel"), dict) else {}).get("offerIDatacenterSellInfo")
        if isinstance(final_price, dict)
        else None
    )
    if isinstance(sell_info, dict):
        for key, value in sell_info.items():
            if key != "sellPointModel" and isinstance(value, (str, int, float, bool)):
                _add_attribute(attrs, key, value)

    temp_model = data_json.get("tempModel") if isinstance(data_json.get("tempModel"), dict) else {}
    gallery = _json_object_after(html, '"gallery":')
    gallery_fields = gallery.get("fields") if isinstance(gallery.get("fields"), dict) else {}
    title = _clean_attribute_value(gallery_fields.get("subject") or temp_model.get("offerTitle") or "")

    sku_model = data_json.get("skuModel") if isinstance(data_json.get("skuModel"), dict) else {}
    sku_props = sku_model.get("skuProps") if isinstance(sku_model.get("skuProps"), list) else []
    prop_names = [str(item.get("prop") or "").strip() for item in sku_props if isinstance(item, dict)]
    sku_image_lookup: dict[str, str] = {}
    sku_prop_values: dict[str, list[str]] = {}
    for prop in sku_props:
        if not isinstance(prop, dict):
            continue
        prop_name = str(prop.get("prop") or "").strip()
        values = prop.get("value") if isinstance(prop.get("value"), list) else []
        for value in values:
            if not isinstance(value, dict):
                continue
            name = _clean_attribute_value(value.get("name"), 180)
            if not name:
                continue
            sku_prop_values.setdefault(prop_name, []).append(name)
            image_url = _clean_attribute_value(value.get("imageUrl"), 500)
            if image_url:
                sku_image_lookup[name] = image_url

    sku_info_map = sku_model.get("skuInfoMap") if isinstance(sku_model.get("skuInfoMap"), dict) else {}
    if not sku_info_map:
        sku_info_map = sku_model.get("skuInfoMapOriginal") if isinstance(sku_model.get("skuInfoMapOriginal"), dict) else {}
    skus: list[dict[str, str]] = []
    for index, (spec_key, item) in enumerate(sku_info_map.items()):
        if not isinstance(item, dict):
            continue
        spec_text = html_module.unescape(str(item.get("specAttrs") or spec_key or "")).strip()
        parts = [_clean_attribute_value(part, 180) for part in spec_text.split(">") if _clean_attribute_value(part, 180)]
        image = _clean_attribute_value(item.get("imageUrl") or item.get("picUrl") or "", 500)
        if not image:
            for part in parts:
                image = sku_image_lookup.get(part, "")
                if image:
                    break
        skus.append(
            {
                "id": _clean_attribute_value(item.get("skuId") or item.get("id"), 80),
                "spec_id": _clean_attribute_value(item.get("specId"), 200),
                "offer_id": str((data_json.get("offerBaseInfo") or {}).get("offerId") or temp_model.get("offerId") or ""),
                "name": " / ".join(parts) or _clean_attribute_value(spec_key, 180) or f"SKU {index + 1}",
                "options": {name: value for name, value in zip(prop_names, parts) if name},
                "price": _clean_attribute_value(item.get("discountPrice") or item.get("price"), 80),
                "stock": str(next((item[key] for key in ("canBookCount", "stock", "inventory") if item.get(key) is not None), "")),
                "image": image,
                "sale_price": _clean_attribute_value(item.get("price"), 80),
            }
        )

    pack_rows = piece_weight_scale.get("pieceWeightScaleInfo") if isinstance(piece_weight_scale.get("pieceWeightScaleInfo"), list) else []
    pack_by_id = {str(row.get("skuId")): row for row in pack_rows if isinstance(row, dict)}
    for sku in skus:
        row = pack_by_id.get(sku["id"], {})
        try:
            package_weight = f"{float(row["weight"]) / 1000:g}" if row.get("weight") is not None else ""
        except (ValueError, TypeError):
            package_weight = ""
        sku["package_dimensions"] = {
            **{f"{key}_cm": str(row.get(key) if row.get(key) is not None else "") for key in ("length", "width", "height")},
            "weight_kg": package_weight,
        }
    weight_kg = ""
    dimensions_text = ""
    for item in pack_rows:
        if not isinstance(item, dict):
            continue
        raw_weight = item.get("weight")
        try:
            weight_value = float(raw_weight)
        except (TypeError, ValueError):
            weight_value = 0.0
        if weight_value > 0 and not weight_kg:
            weight_kg = f"{weight_value / 1000:g}"
        dims = []
        for key in ["length", "width", "height"]:
            try:
                number = float(item.get(key) or 0)
            except (TypeError, ValueError):
                number = 0.0
            if number > 0:
                dims.append(f"{number:g}")
        if len(dims) == 3 and not dimensions_text:
            dimensions_text = " x ".join(dims) + " cm"
        if weight_kg and dimensions_text:
            break

    prices = []
    for item in skus:
        for key in ["price", "sale_price"]:
            try:
                prices.append(float(str(item.get(key) or "").replace(",", "")))
            except ValueError:
                pass
    price = f"{min(prices):g}" if prices else ""
    main_image_urls = normalize_list(gallery_fields.get("mainImage") or gallery_fields.get("offerImgList"))
    image_urls = list(main_image_urls)
    image_urls.extend(sku_image_lookup.values())
    description = _json_object_after(html, '"description":')
    description_fields = description.get("fields") if isinstance(description.get("fields"), dict) else {}

    return {
        "title": title,
        "attributes": attrs,
        "sku_props": sku_prop_values,
        "skus": skus,
        "price": price,
        "currency": "CNY" if price else "",
        "weight_kg": weight_kg,
        "dimensions_text": dimensions_text,
        "image_urls": list(dict.fromkeys([url for url in image_urls if url])),
        "main_image_urls": main_image_urls,
        "detail_url": str(description_fields.get("detailUrl") or "").strip(),
        "offer_id": _clean_attribute_value((data_json.get("offerBaseInfo") or {}).get("offerId") if isinstance(data_json.get("offerBaseInfo"), dict) else temp_model.get("offerId"), 80),
    }

def extract_1688_attributes(text: str, html: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    context_attrs = extract_1688_context_data(html).get("attributes")
    if isinstance(context_attrs, dict):
        attrs.update({str(key): str(value) for key, value in context_attrs.items() if str(key).strip() and str(value).strip()})
    attrs.update(extract_1688_attribute_table(html))
    if attrs:
        # 结构化商品属性已就绪，不混入页面底部的价格说明、时间和店铺文案。
        return attrs
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        match = re.match(r"^([A-Za-z0-9_\-\u4e00-\u9fff]{1,24})\s*[:：]\s*(.+)$", line)
        if not match:
            continue
        key = normalize_space(match.group(1))
        value = normalize_space(match.group(2))
        if not key or not value or len(value) > 600:
            continue
        attrs.setdefault(key, value)

    targeted = {
        "品牌": [r"品牌[:：]\s*([^\n]+)", r"厂牌[:：]\s*([^\n]+)"],
        "货号": [r"货号[:：]\s*([^\n]+)", r"产品货号[:：]\s*([^\n]+)", r"款号[:：]\s*([^\n]+)"],
        "SKU": [r"SKU[:：]\s*([^\n]+)", r"sku[:：]\s*([^\n]+)"],
        "型号": [r"型号[:：]\s*([^\n]+)", r"规格型号[:：]\s*([^\n]+)"],
        "材质": [r"材质[:：]\s*([^\n]+)", r"材料[:：]\s*([^\n]+)", r"面料[:：]\s*([^\n]+)"],
        "规格": [r"规格[:：]\s*([^\n]+)", r"尺寸[:：]\s*([^\n]+)", r"产品尺寸[:：]\s*([^\n]+)"],
        "重量": [r"重量[:：]\s*([^\n]+)", r"净重[:：]\s*([^\n]+)", r"毛重[:：]\s*([^\n]+)"],
        "包装清单": [r"包装清单[:：]\s*([^\n]+)", r"包装内容[:：]\s*([^\n]+)", r"包装说明[:：]\s*([^\n]+)"],
    }
    for key, patterns in targeted.items():
        if attrs.get(key):
            continue
        value = extract_text_pattern(text, patterns)
        if value:
            attrs[key] = value

    if not attrs:
        matches = re.findall(r"([A-Za-z0-9_\-\u4e00-\u9fff]{2,24})\s*[:：]\s*([^\n]{1,160})", html)
        for key, value in matches[:40]:
            key = normalize_space(key)
            value = normalize_space(value)
            if key and value and key not in attrs:
                attrs[key] = value
    return attrs

def extract_1688_sku(page_url: str, text: str, html: str, attrs: dict[str, str]) -> str:
    candidates = [
        attrs.get("SKU", ""),
        attrs.get("货号", ""),
        attrs.get("型号", ""),
        attrs.get("款号", ""),
        attrs.get("产品编号", ""),
        attrs.get("商品编号", ""),
    ]
    match = re.search(r"/offer/(\d+)\.html", page_url)
    if match:
        candidates.append(match.group(1))
    for pattern in [
        r'"offerId"\s*[:=]\s*"?(\d+)"?',
        r'"itemId"\s*[:=]\s*"?(\d+)"?',
        r'"productId"\s*[:=]\s*"?(\d+)"?',
        r'货号[:：]\s*([A-Za-z0-9\-_]+)',
        r'SKU[:：]\s*([A-Za-z0-9\-_]+)',
    ]:
        found = re.search(pattern, f"{text}\n{html}", flags=re.I)
        if found:
            candidates.append(found.group(1))
    for value in candidates:
        value = normalize_space(str(value))
        if value:
            return value[:80]
    return ""

def _weight_text_to_kg(value: Any) -> str:
    text = _clean_attribute_value(value, 80).lower()
    if not text:
        return ""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|千克|公斤|g|克)?", text)
    if not match:
        return ""
    try:
        number = float(match.group(1).replace(",", "."))
    except ValueError:
        return ""
    unit = match.group(2) or ""
    if unit in {"g", "克"}:
        return f"{number / 1000:g}"
    if unit in {"kg", "千克", "公斤"}:
        return f"{number:g}"
    return ""

def collect_product_image_urls(html: str, page_url: str, snapshot_image_urls: list[Any] | None = None, limit: int = 20) -> list[str]:
    """结构化主图优先，避免 SKU、缩略图和推荐图挤占主图名额。"""

    candidates: list[Any] = []
    if "1688.com" in (urllib.parse.urlparse(page_url).hostname or ""):
        candidates.extend(extract_1688_context_data(html).get("main_image_urls", []))
    try:
        candidates.extend(html_extract_service.extract_product_image_urls(html, page_url, limit=max(limit * 2, 20)))
    except Exception:
        pass
    candidates.extend(snapshot_image_urls or [])

    clean: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        raw = str(item or "").strip()
        if not raw:
            continue
        try:
            url = html_extract_service.normalize_image_url(raw, page_url)
        except Exception:
            url = raw.replace("\\/", "/").strip()
        lowered = url.lower()
        parsed_path = urllib.parse.urlparse(url).path.lower()
        if not url or url in seen:
            continue
        if parsed_path.endswith(".svg") or any(skip in lowered for skip in ["sprite", "logo", "avatar", "icon"]):
            continue
        seen.add(url)
        clean.append(url)
        if len(clean) >= limit:
            break
    return clean

class _DetailImageParser(HTMLParser):
    """只读取详情内容中的图片，不执行接口返回的 JavaScript。"""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = dict(attrs)
        raw = values.get("data-src") or values.get("src") or ""
        url = html_extract_service.normalize_image_url(raw, self.page_url)
        if urllib.parse.urlparse(url).scheme in {"https", "http"} and url not in self.urls:
            self.urls.append(url)

def extract_1688_detail_images(payload: str, page_url: str) -> list[str]:
    """解析 offer_details JSON 或详情 HTML，保留全部图片及原始顺序。"""
    content = payload.strip()
    if not content.startswith("<"):
        start = content.find("{")
        try:
            data, _ = json.JSONDecoder().raw_decode(content[start:])
        except (ValueError, TypeError) as exc:
            raise ValueError("1688 详情接口未返回有效图片内容") from exc
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            raise ValueError("1688 详情接口缺少 content")
        content = data["content"]
    parser = _DetailImageParser(page_url)
    parser.feed(content)
    return parser.urls


def validated_1688_detail_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {'https', 'http'} or parsed.hostname not in {'itemcdn.tmall.com', 'desc.alicdn.com'} or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError('1688 详情接口地址不属于支持的官方域名')
    return parsed._replace(scheme='https').geturl()

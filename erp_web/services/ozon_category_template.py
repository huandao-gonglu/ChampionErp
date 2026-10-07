"""读取官方 XLSX 元数据，仅替换商品数据区，保留模板字典、校验和隐藏页。"""
from __future__ import annotations

import base64
import binascii
import io
import json
import posixpath
import re
import zipfile
from decimal import Decimal
from xml.etree import ElementTree as ET

from erp_web.schemas.ozon_template_export import OzonExportTemplate

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"m": MAIN}
MAX_FILE_BYTES = 3 * 1024 * 1024


def decode_template(value: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("模板文件编码无效，请重新选择 XLSX 文件") from exc
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("模板文件不能超过 3 MB")
    return data


def _xml(data: bytes) -> ET.Element:
    if b"<!DOCTYPE" in data.upper():
        raise ValueError("模板 XML 格式不受支持")
    return ET.fromstring(data)


class OzonCategoryTemplate:
    """首版仅映射已核对的圣诞装饰品契约，其他类目显式拒绝。"""

    def __init__(self, data: bytes):
        self.data = data
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if (len(entries) > 2000 or sum(e.file_size for e in entries) > 48 * 1024 * 1024
                        or len({e.filename for e in entries}) != len(entries)):
                    raise ValueError("模板文件过大或包含重复文件")
                self.parts = {e.filename: archive.read(e) for e in entries}
            if any("vbaproject" in name.lower() for name in self.parts):
                raise ValueError("请使用无宏的官方 XLSX 类目模板")
            strings = _xml(self.parts.get("xl/sharedStrings.xml", f'<sst xmlns="{MAIN}"/>'.encode()))
            self.strings = ["".join(t.text or "" for t in item.findall(".//m:t", NS)) for item in strings]
            workbook = _xml(self.parts["xl/workbook.xml"])
            relationships = _xml(self.parts["xl/_rels/workbook.xml.rels"])
            targets = {r.get("Id"): posixpath.normpath(posixpath.join("xl", r.get("Target", "")))
                       if not r.get("Target", "").startswith("/") else r.get("Target", "").lstrip("/")
                       for r in relationships}
            sheets = {s.get("name"): targets[s.get(f"{{{REL}}}id")] for s in workbook.find("m:sheets", NS)}
            configs = self._cells(sheets["configs"])
            config = {configs.get(f"A{i}", ""): configs.get(f"B{i}", "") for i in range(1, 30)}
            chunks = self._cells(sheets["info"])
            raw_info = "".join(v for _, v in sorted(chunks.items(), key=lambda pair: _column_number(pair[0])))
            self.info = json.loads(base64.b64decode(raw_info, validate=True))
            self.category_id = config["DESCRIPTION_CATEGORY_ID"]
            self.currency = config["CURRENCY"]
            self.first_row = int(config["PRODUCTS_FIRST_DATA_ROW_INDEX"])
            self.title_row = int(config["PRODUCTS_TITLE_ROW_INDEX"])
            self.path = sheets[self.info["additional_column_by_name"]["list_name"]]
            cells = self._cells(self.path)
            self.columns = {re.sub(r"\*\s*$", "", value).strip(): re.sub(r"\d+$", "", ref)
                            for ref, value in cells.items() if re.search(r"\d+$", ref).group() == str(self.title_row)}
            self.required = [re.sub(r"\*\s*$", "", value).strip() for ref, value in cells.items()
                             if re.search(r"\d+$", ref).group() == str(self.title_row) and value.endswith("*")]
            self.styles = {re.sub(r"\d+$", "", c.get("r", "")): c.get("s")
                           for c in _xml(self.parts[self.path]).findall(f'm:sheetData/m:row[@r="{self.first_row}"]/m:c', NS)}
            offer_col = self.info["additional_column_by_name"]["offer_id"]
            if any(ref.startswith(self.columns[offer_col]) and re.fullmatch(self.columns[offer_col]+r"\d+", ref)
                   and int(re.search(r"\d+$", ref).group()) >= self.first_row and value for ref, value in cells.items()):
                raise ValueError("请上传空白类目模板，避免覆盖已有商品")
            self._validate_contract()
        except (KeyError, IndexError, TypeError, AttributeError, RuntimeError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
            if isinstance(exc, ValueError) and not isinstance(exc, (binascii.Error, ET.ParseError, json.JSONDecodeError)):
                raise
            raise ValueError("无法读取 Ozon 官方类目模板，请重新下载空白 XLSX 模板") from exc

    def _cells(self, path: str) -> dict[str, str]:
        result = {}
        for cell in _xml(self.parts[path]).findall("m:sheetData/m:row/m:c", NS):
            value = cell.findtext("m:v", default="", namespaces=NS)
            if cell.get("t") == "s":
                value = self.strings[int(value)]
            elif cell.get("t") == "inlineStr":
                value = "".join(t.text or "" for t in cell.findall("m:is//m:t", NS))
            result[cell.get("r", "")] = value
        return result

    def _validate_contract(self) -> None:
        if self.category_id != "43429543" or self.info.get("name") != "圣诞装饰品":
            raise ValueError("当前支持圣诞装饰品类目，请使用该类目的官方模板")
        if self.currency != "CNY":
            raise ValueError("当前支持 CNY 模板，请下载人民币模板；不会自动换汇")
        if self.first_row != 5 or self.title_row != 2:
            raise ValueError("模板表头版本不受支持，请重新下载当前类目模板")
        metadata_required = {str(key) for key, value in self.info["attributes"].items() if value.get("IsRequired")}
        if metadata_required != {"85", "9048", "8229"}:
            raise ValueError("模板必填属性已变化，需先更新字段映射")
        names = self.info["additional_column_by_name"]
        self.field_columns = {}
        for key, name in {"offer_id": names["offer_id"], "title": names["name"], "price": names["price"],
                          "barcode": names["barcode"], "weight_g": names["weight"], "width_mm": names["width"],
                          "height_mm": names["height"], "length_mm": names["depth"], "main_image": names["picture"],
                          "images": names["pictures"], "brand": self.info["attributes"]["85"]["Name"],
                          "model": self.info["attributes"]["9048"]["Name"], "type": names["desc_type"],
                          "review_promo": names["review_promo"]}.items():
            self.field_columns[key] = self.columns[name]
        known_required = {"offer_id", "price", "weight_g", "width_mm", "height_mm", "length_mm", "main_image", "brand", "model", "type"}
        required_columns = {self.columns[name] for name in self.required}
        self.required_fields = [key for key, column in self.field_columns.items() if column in required_columns]
        if set(self.required_fields) != known_required or len(self.required) != len(known_required):
            raise ValueError("模板必填字段已变化，需先更新字段映射")
        types = self.info["attributes"]["8229"]["LookupData"]["Values"]
        if not any(str(v.get("ID")) == "95421" and v.get("Value") == "圣诞装饰品" for v in types.values()):
            raise ValueError("模板商品类型与圣诞装饰品不匹配")
        self.promotion_no = names["promotion_no"]
        self.optional_columns = {key: self.columns[name] for key, name in {"variant": "颜色名称", "size_cm": "长度，厘米"}.items() if name in self.columns}

    def describe(self) -> OzonExportTemplate:
        return {"category": self.info["name"], "category_id": self.category_id, "currency": self.currency,
                "required_fields": self.required}

    def fill(self, rows: list[dict[str, str]]) -> bytes:
        source = self.parts[self.path].decode("utf-8")
        match = re.search(r"<sheetData\b[^>]*>(.*?)</sheetData>", source, re.S)
        if match is None:
            raise ValueError("模板数据区格式不受支持")
        headers = [m.group() for m in re.finditer(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', match[1], re.S)
                   if int(m[1]) < self.first_row]
        if len(headers) != self.first_row - 1:
            raise ValueError("模板说明行不完整")
        data_rows = []
        numeric = {"price", "weight_g", "width_mm", "height_mm", "length_mm", "size_cm"}
        for index, fields in enumerate(rows, self.first_row):
            row = ET.Element("row", {"r": str(index)})
            columns = {**self.field_columns, **self.optional_columns}
            values = {**fields, "type": "圣诞装饰品", "review_promo": self.promotion_no}
            columns["sequence"] = "A"
            values["sequence"] = str(index - self.first_row + 1)
            for key, column in sorted(columns.items(), key=lambda pair: _column_number(pair[1])):
                value = values.get(key, "")
                if not value:
                    continue
                attrs = {"r": f"{column}{index}"}
                if self.styles.get(column):
                    attrs["s"] = self.styles[column]
                cell = ET.SubElement(row, "c", attrs)
                if key in numeric or key == "sequence":
                    ET.SubElement(cell, "v").text = str(Decimal(value))
                else:
                    cell.set("t", "inlineStr")
                    ET.SubElement(ET.SubElement(cell, "is"), "t", {"xml:space": "preserve"}).text = value
            data_rows.append(ET.tostring(row, encoding="unicode"))
        replacement = "<sheetData>" + "".join(headers + data_rows) + "</sheetData>"
        changed = source[:match.start()] + replacement + source[match.end():]
        last_row = self.first_row + len(rows) - 1
        changed = re.sub(r'(<dimension\b[^>]*ref="[A-Z]+\d+:[A-Z]+)\d+("[^>]*>)', lambda m: m[1]+str(last_row)+m[2], changed)
        changed = re.sub(r'(<autoFilter\b[^>]*ref="[A-Z]+\d+:[A-Z]+)\d+("[^>]*>)', lambda m: m[1]+str(last_row)+m[2], changed)
        workbook = self.parts["xl/workbook.xml"].decode("utf-8")
        workbook = re.sub(r'(<definedName\b(?=[^>]*localSheetId="0")(?=[^>]*name="_xlnm._FilterDatabase")[^>]*>)([^<]*)(</definedName>)',
                          lambda m: m[1]+re.sub(r'(\$[A-Z]+\$)\d+$', lambda v: v[1]+str(last_row), m[2])+m[3], workbook)
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.data)) as original, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in original.infolist():
                content = changed.encode("utf-8") if entry.filename == self.path else workbook.encode("utf-8") if entry.filename == "xl/workbook.xml" else self.parts[entry.filename]
                archive.writestr(entry, content)
        return output.getvalue()


def _column_number(reference: str) -> int:
    result = 0
    for letter in re.sub(r"\d+$", "", reference):
        result = result * 26 + ord(letter) - ord("A") + 1
    return result


__all__ = ["OzonCategoryTemplate", "decode_template"]

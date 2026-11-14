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
    """从工作簿的类目元数据、表头和类型字典解析导出字段。"""

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
            headers = {ref.removesuffix(str(self.title_row)): value.strip() for ref, value in cells.items()
                       if re.fullmatch(rf"[A-Z]+{self.title_row}", ref) and value.strip()}
            self.headers = {col: title.rstrip("*").strip() for col, title in headers.items()}
            self.columns = {name: col for col, name in self.headers.items()}
            required = {col for col, title in headers.items() if title.endswith("*")}
            self.styles = {re.sub(r"\d+$", "", c.get("r", "")): c.get("s")
                           for c in _xml(self.parts[self.path]).findall(f'm:sheetData/m:row[@r="{self.first_row}"]/m:c', NS)}
            offer_col = self.info["additional_column_by_name"]["offer_id"]
            if any(ref.startswith(self.columns[offer_col]) and re.fullmatch(self.columns[offer_col]+r"\d+", ref)
                   and int(re.search(r"\d+$", ref).group()) >= self.first_row and value for ref, value in cells.items()):
                raise ValueError("请上传空白类目模板，避免覆盖已有商品")
            self._read_fields(required, cells)
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

    def _read_fields(self, required: set[str], cells: dict[str, str]) -> None:
        self.category = self.info["name"].strip()
        if not self.category or not self.category_id.strip():
            raise ValueError("模板缺少类目信息，请重新下载官方类目模板")
        if self.currency != "CNY":
            raise ValueError("当前支持 CNY 模板，请下载人民币模板；不会自动换汇")
        if not 0 < self.title_row < self.first_row:
            raise ValueError("模板未标明有效的表头和商品起始行")

        names = self.info["additional_column_by_name"]
        attributes = self.info["attributes"]
        brand = attributes.get("85", {})
        self.no_brand = names.get("no_brand", "").strip()
        self.brand_values = {str(v["Value"]) for v in brand.get("LookupData", {}).get("Values", {}).values()}
        self.brand_dictionary_complete = bool(self.brand_values) and not brand.get("IsOversized", False)
        self.promotion_no = names.get("promotion_no", "")
        common = {"offer_id": "offer_id", "title": "name", "price": "price", "barcode": "barcode",
                  "weight_g": "weight", "width_mm": "width", "height_mm": "height", "length_mm": "depth",
                  "main_image": "picture", "images": "pictures", "type": "desc_type", "review_promo": "review_promo"}
        labels = {key: names.get(source) for key, source in common.items()}
        labels.update(brand=brand.get("Name"), model=attributes.get("9048", {}).get("Name"))
        self.field_columns = {key: self.columns[label] for key, label in labels.items() if label in self.columns}
        if "review_promo" in self.field_columns and not self.promotion_no:
            raise ValueError("模板缺少关闭加速评价收集的选项")
        # 同时读取星号和隐藏属性。部分模板只在元数据里声明必填。
        for attr_id, attr in attributes.items():
            if attr.get("IsRequired"):
                column = self.columns.get(attr.get("LongName")) or self.columns.get(attr.get("Name"))
                if attr_id == "8229":
                    column = self.field_columns.get("type", column)
                if not column:
                    raise ValueError(f"模板中的必填属性“{attr.get('Name') or attr_id}”缺少对应表头")
                required.add(column)

        self.type_options = list(dict.fromkeys(
            v["Value"] for v in attributes.get("8229", {}).get("LookupData", {}).get("Values", {}).values()
            if v.get("Value")))
        type_col = self.field_columns.get("type")
        default = cells.get(f"{type_col}{self.first_row}", "").strip()
        self.product_type = default if default and (not self.type_options or default in self.type_options) else ""
        if not self.product_type and len(self.type_options) == 1:
            self.product_type = self.type_options[0]

        known = {col: key for key, col in self.field_columns.items()}
        self.required_fields = {known.get(col, col): name for col, name in self.headers.items() if col in required}
        self.extra_fields = {col: self.headers[col] for col in self.headers if col in required and col not in known}
        optional = {"variant": "颜色名称", "size_cm": "长度，厘米"}
        self.optional_columns = {key: self.columns[name] for key, name in optional.items()
                                 if name in self.columns and self.columns[name] not in required}
        # 多种类型可以逐 SKU 选择；单一类型或官方预填值直接用于预览。
        if type_col and (len(self.type_options) > 1 or not self.product_type):
            self.extra_fields["type"] = self.headers[type_col]
        self.extra_numeric = {}
        for attr in attributes.values():
            column = self.columns.get(attr.get("LongName")) or self.columns.get(attr.get("Name"))
            if column in self.extra_fields and attr.get("Type") in {"Integer", "Decimal"} and not attr.get("IsCollection"):
                self.extra_numeric[column] = attr["Type"]

    def describe(self) -> OzonExportTemplate:
        return {"category": self.category, "category_id": self.category_id, "currency": self.currency,
                "required_fields": list(self.required_fields.values()), "extra_fields": self.extra_fields,
                "type_options": self.type_options if "type" in self.extra_fields else []}

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
        columns = {**self.optional_columns, **self.field_columns}
        columns.update({col: col for col in self.extra_fields if col != "type"})
        if "№" in self.columns:
            columns["sequence"] = self.columns["№"]
        ordered_columns = sorted(columns.items(), key=lambda pair: _column_number(pair[1]))
        for index, fields in enumerate(rows, self.first_row):
            row = ET.Element("row", {"r": str(index)})
            values = {**fields, "review_promo": self.promotion_no, "sequence": str(index - self.first_row + 1)}
            for key, column in ordered_columns:
                value = values.get(key, "")
                if not value:
                    continue
                attrs = {"r": f"{column}{index}"}
                if self.styles.get(column):
                    attrs["s"] = self.styles[column]
                cell = ET.SubElement(row, "c", attrs)
                if key in numeric or key in self.extra_numeric or key == "sequence":
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

"""读取禁售类目 XLSX；只根据表内类目 ID 匹配，不推测名称或父子关系。"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
import posixpath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from erp_web.schemas.forbidden_categories import (
    ForbiddenCategoryConfiguration, ImportForbiddenCategoriesRequest,
)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _xml(data: bytes) -> ET.Element:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("禁售类目文件含不支持的 XML 声明")
    return ET.fromstring(data)


def _category_id(value: str, row: str) -> str:
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0 or number >= 10**19 or number != number.to_integral_value():
            raise ValueError
        return str(int(number))
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError(f"第 {row} 行的类目 ID 必须为正整数") from exc


def parse_categories(data: bytes) -> list[dict[str, str]]:
    """校验完整文件后返回去重名单；任何无效数据行均拒绝整次导入。"""
    if len(data) > 3 * 1024 * 1024:
        raise ValueError("禁售类目文件不能超过 3 MiB")
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if (len(entries) > 2000 or sum(e.file_size for e in entries) > 32 * 1024 * 1024
                    or len({e.filename for e in entries}) != len(entries)):
                raise ValueError("禁售类目文件过大或包含重复文件")
            if any("vbaproject" in e.filename.lower() for e in entries):
                raise ValueError("请使用不含宏的 XLSX 文件")
            strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = ["".join(t.text or "" for t in item.findall(".//m:t", NS))
                           for item in _xml(archive.read("xl/sharedStrings.xml"))]
            workbook = _xml(archive.read("xl/workbook.xml"))
            relations = {r.get("Id"): r for r in _xml(archive.read("xl/_rels/workbook.xml.rels"))}
            matches = []
            for sheet in workbook.findall("m:sheets/m:sheet", NS):
                relation = relations[sheet.get(f"{{{REL}}}id")]
                if relation.get("TargetMode") == "External":
                    raise ValueError("不支持引用外部工作表")
                target = relation.get("Target", "")
                path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
                rows = _xml(archive.read(path)).findall("m:sheetData/m:row", NS)
                parsed = []
                for row in rows:
                    cells = {}
                    for cell in row.findall("m:c", NS):
                        value = cell.findtext("m:v", default="", namespaces=NS)
                        if cell.get("t") == "s":
                            index = int(value) if value.isascii() and value.isdecimal() else -1
                            if not 0 <= index < len(strings):
                                raise ValueError("禁售类目文件的文本索引无效，请重新导出 XLSX 文件")
                            value = strings[index]
                        elif cell.get("t") == "inlineStr":
                            value = "".join(t.text or "" for t in cell.findall("m:is//m:t", NS))
                        ref = cell.get("r", "")
                        column = ref.rstrip("0123456789")
                        if cell.find("m:f", NS) is not None:
                            value = "__formula__"
                        cells[column] = value.strip()
                    if any(cells.values()):
                        parsed.append((row.get("r", "?"), cells))
                if not parsed:
                    continue
                header = {value.lower(): column for column, value in parsed[0][1].items()}
                if "category" not in header or "name" not in header:
                    continue
                categories: dict[str, dict[str, str]] = {}
                for index, (row, cells) in enumerate(parsed[1:]):
                    raw_id, name = cells.get(header["category"], ""), cells.get(header["name"], "")
                    if index == 0 and raw_id.lower() == "int64" and name.lower() == "string":
                        continue
                    if not raw_id and not name:
                        continue
                    category_id = _category_id(raw_id, row)
                    if not name or name == "__formula__" or len(name) > 1000:
                        raise ValueError(f"第 {row} 行的类目名称为空、过长或使用了公式")
                    if category_id in categories and categories[category_id]["name"] != name:
                        raise ValueError(f"第 {row} 行的类目 ID {category_id} 对应多个名称")
                    categories[category_id] = {"category_id": category_id, "name": name}
                    if len(categories) > 20000:
                        raise ValueError("禁售类目最多支持 20000 条")
                matches.append(list(categories.values()))
            if len(matches) != 1:
                raise ValueError("文件必须恰有一个包含 category、name 列的工作表")
            if not matches[0]:
                raise ValueError("文件中没有有效禁售类目")
            return matches[0]
    except (BadZipFile, KeyError, IndexError, TypeError, ET.ParseError, RuntimeError, OSError, NotImplementedError) as exc:
        raise ValueError("无法读取禁售类目文件，请选择有效的 XLSX 文件") from exc


def import_configuration(body: dict) -> dict:
    request = ImportForbiddenCategoriesRequest.model_validate(body)
    if not request.filename.lower().endswith(".xlsx") or "/" in request.filename or "\\" in request.filename:
        raise ValueError("请选择 XLSX 禁售类目文件")
    try:
        data = base64.b64decode(request.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("文件编码无效，请重新选择文件") from exc
    return ForbiddenCategoryConfiguration(
        filename=request.filename, categories=parse_categories(data),
        imported_at=datetime.now(timezone.utc).isoformat(),
    ).model_dump()


def forbidden_category_issue(platform: str, category_id: str, categories: dict[str, str]) -> dict | None:
    if platform != "yandex":
        return None
    category_id = str(category_id or "").strip()
    if category_id.isascii() and category_id.isdigit():
        category_id = str(int(category_id))
    if category_id not in categories:
        return None
    return {"code": "CATEGORY_FORBIDDEN", "field": "category_id", "severity": "error",
            "message": f"Yandex 类目 {category_id}（{categories[category_id]}）在禁售名单中，不能通过预检",
            "next_action": "停止发布此类商品；如名单有误，请联系管理员核实禁售类目"}


__all__ = ["parse_categories", "import_configuration", "forbidden_category_issue"]

# -*- coding: utf-8 -*-
"""草稿箱 ZIP 导出与导入服务。

导出的 ZIP 包包含 ``manifest.json`` 与 ``assets/<asset_key>`` 原始二进制图片：
- 不导出采集箱状态与采集日志（导入后宿主商品固定 ``in_library=False``，不进入接收方采集箱）；
- 按草稿与 SKU 实际引用裁剪图片池，避免携带多余采集废图；
- 清空所有 UPC/条码字段（保留 ``master_sku`` 与草稿 ``sku``）、发布运行态及图片平台/S3 托管交付态；
- 完整保留文案、类目、属性、核价、运费、物流方案与 SKU 配置，由目标账号的发布预检统一核对。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4
import zipfile

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]

from erp_web.db import new_draft_id
from erp_web.product_model import (
    PLATFORMS,
    normalize_platform_draft,
    validate_product_root_fields,
)
from erp_web.product_model.source_facts import validate_draft_facts
from erp_web.schemas.image_hosting import DELIVERY_FIELDS
from erp_web.schemas.product import PRODUCT_SCHEMA_VERSION
from erp_web.schemas.requests import RequestValidationError
from erp_web.services import export_archive_store, image_service
from erp_web.services.workspace_files import owned_path
from erp_web.stores.product_store import (
    ProductStore,
    RetiredCategorySchemaFieldError,
    normalize_product_fields,
    reject_retired_draft_schema_fields,
    reject_retired_product_category_fields,
)

DRAFT_EXPORT_FORMAT = "champion-erp.drafts"
DRAFT_EXPORT_VERSION = 1
SUPPORTED_DRAFT_EXPORT_VERSIONS = frozenset({DRAFT_EXPORT_VERSION})
ZIP_CONTENT_TYPES = frozenset({
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
})

MAX_IMPORT_DRAFTS = 200
MAX_ZIP_ENTRIES = 2000
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_SINGLE_ASSET_BYTES = 15 * 1024 * 1024
MAX_TOTAL_EXPORT_ASSET_BYTES = 128 * 1024 * 1024
MAX_TOTAL_IMPORT_ASSET_BYTES = 128 * 1024 * 1024

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_ASSET_KEY_PATTERN = re.compile(r"^asset_[0-9a-f]{16}\.(?:jpg|jpeg|png|webp)$")

_STRIPPED_PRODUCT_KEYS = (
    "drafts",
    "publish_preview",
    "workflow_statuses",
    "copy_results",
    "product_file_path",
    "collect_logs",
    "collect_diagnostics",
)

_STRIPPED_SOURCE_KEYS = (
    "collect_logs",
    "collect_diagnostics",
)

_STRIPPED_IMAGE_KEYS = (
    "platform_picture_id",
    "mercadolibre_picture_id",
    "upload_status",
    "upload_error",
    "uploaded_at",
    "platform_uploads",
    "delivery_error",
    "translate_job_id",
)

_DELIVERY_META_KEYS = tuple(key for key in DELIVERY_FIELDS if key != "url")

_DRAFT_SERVER_PUBLISH_DEFAULTS: dict[str, Any] = {
    "status": "",
    "publish_status": "",
    "category_precheck": {},
    "last_precheck": {},
    "last_precheck_target": {},
    "last_publish_task": {},
}

_STRIPPED_DRAFT_KEYS = (
    "draft_id",
    "product_id",
    "source_product_id",
    "created_at",
    "updated_at",
    "copy_operation_key",
    "publication",
    "publish_logs",
    "upc",
    "barcode",
)

_BARCODE_ATTRIBUTE_KEYS = frozenset({"GTIN", "EAN", "UPC", "BARCODE"})


@lru_cache(maxsize=1)
def _current_app_version() -> str:
    package_json = Path(__file__).resolve().parents[2] / "front" / "package.json"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("version") or "").strip() if isinstance(data, dict) else ""


def _is_exact_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_remote_url(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _local_candidate_path(raw: str, app_dir: Path) -> Path | None:
    text = str(raw or "").strip()
    if not text or _is_remote_url(text) or text.startswith("data:"):
        return None
    if text.startswith("/file?"):
        query = parse_qs(urlparse(text).query)
        extracted = (query.get("path") or [""])[0]
        text = unquote(extracted).strip()
        if not text:
            return None
    try:
        candidate = owned_path(text, app_dir=app_dir)
    except RequestValidationError:
        return None
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _strip_barcode_attributes(attributes: Any) -> dict[str, Any]:
    if not isinstance(attributes, dict):
        return {}
    return {
        key: deepcopy(value)
        for key, value in attributes.items()
        if str(key or "").strip().upper() not in _BARCODE_ATTRIBUTE_KEYS
    }


def _collect_referenced_asset_ids(
    product: dict[str, Any],
    drafts: list[dict[str, Any]],
) -> set[str]:
    referenced: set[str] = set()
    for sku in product.get("sku_items") or []:
        if isinstance(sku, dict):
            asset_id = str(sku.get("image_asset_id") or "").strip()
            if asset_id:
                referenced.add(asset_id)

    for draft in drafts:
        for ref in draft.get("images") or []:
            if isinstance(ref, dict):
                for key in ("asset_id", "source_asset_id"):
                    asset_id = str(ref.get(key) or "").strip()
                    if asset_id:
                        referenced.add(asset_id)
            elif isinstance(ref, str) and ref.strip():
                referenced.add(ref.strip())
        for row in draft.get("sku_items") or []:
            if not isinstance(row, dict):
                continue
            overrides = row.get("overrides") if isinstance(row.get("overrides"), dict) else {}
            asset_id = str(overrides.get("image_asset_id") or "").strip()
            if asset_id:
                referenced.add(asset_id)

    # 沿 source_asset_id / derived_from_id 追踪闭环，保留衍生图与原图的关系。
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    by_id = {
        str(item.get("id") or item.get("asset_id") or "").strip(): item
        for item in pool
        if isinstance(item, dict) and str(item.get("id") or item.get("asset_id") or "").strip()
    }
    queue = list(referenced)
    while queue:
        current = queue.pop()
        item = by_id.get(current)
        if not item:
            continue
        for parent_field in ("source_asset_id", "derived_from_id"):
            parent_id = str(item.get(parent_field) or "").strip()
            if parent_id and parent_id not in referenced:
                referenced.add(parent_id)
                queue.append(parent_id)
    return referenced


def _register_local_asset(
    file_path: Path,
    *,
    asset_manifest: dict[str, dict[str, str]],
    asset_blobs: dict[str, bytes],
    path_to_key: dict[str, str],
    total_bytes: list[int],
) -> str:
    resolved = str(file_path.resolve())
    if resolved in path_to_key:
        return path_to_key[resolved]
    try:
        raw = file_path.read_bytes()
    except OSError:
        return ""
    if not raw:
        return ""
    if len(raw) > MAX_SINGLE_ASSET_BYTES:
        raise RequestValidationError(
            f"图片 {file_path.name} 超过单张 {MAX_SINGLE_ASSET_BYTES // (1024 * 1024)} MB 导出限制。"
        )
    digest = hashlib.sha256(raw).hexdigest()[:16]
    suffix = file_path.suffix.lower() or ".png"
    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix not in image_service.IMAGE_SUFFIXES:
        suffix = ".png"
    key = f"asset_{digest}{suffix}"
    if key not in asset_manifest:
        if total_bytes[0] + len(raw) > MAX_TOTAL_EXPORT_ASSET_BYTES:
            raise RequestValidationError(
                f"本次导出图片总大小超过 {MAX_TOTAL_EXPORT_ASSET_BYTES // (1024 * 1024)} MB 限制，请分批导出。"
            )
        total_bytes[0] += len(raw)
        mime = mimetypes.guess_type(f"file{suffix}")[0] or "image/png"
        asset_manifest[key] = {
            "path": f"assets/{key}",
            "filename": file_path.name,
            "content_type": mime,
        }
        asset_blobs[key] = raw
    path_to_key[resolved] = key
    return key


def _prepare_host_product_for_export(
    product: dict[str, Any],
    drafts: list[dict[str, Any]],
    *,
    app_dir: Path,
    asset_manifest: dict[str, dict[str, str]],
    asset_blobs: dict[str, bytes],
    path_to_key: dict[str, str],
    total_bytes: list[int],
) -> dict[str, Any]:
    exported = deepcopy(product)
    for key in _STRIPPED_PRODUCT_KEYS:
        exported.pop(key, None)
    exported["schema_version"] = PRODUCT_SCHEMA_VERSION
    exported["in_library"] = False
    exported["upc"] = ""

    sku_items = exported.get("sku_items") if isinstance(exported.get("sku_items"), list) else []
    for sku in sku_items:
        if not isinstance(sku, dict):
            continue
        sku["barcode"] = ""
        sku.pop("upc", None)
        snapshot = sku.get("source_snapshot")
        if isinstance(snapshot, dict):
            snapshot.pop("barcode", None)
            snapshot.pop("upc", None)

    referenced_ids = _collect_referenced_asset_ids(exported, drafts)
    source = exported.get("source") if isinstance(exported.get("source"), dict) else {}
    for key in _STRIPPED_SOURCE_KEYS:
        source.pop(key, None)
    # 不导出采集原始 SKU 清单中的条码。
    if isinstance(source.get("skus"), list):
        for raw_sku in source["skus"]:
            if isinstance(raw_sku, dict):
                raw_sku.pop("barcode", None)
                raw_sku.pop("upc", None)

    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    ref_to_asset_key: dict[str, str] = {}
    cleaned_pool: list[dict[str, Any]] = []

    for raw_item in pool:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("id") or raw_item.get("asset_id") or "").strip()
        if referenced_ids and item_id and item_id not in referenced_ids:
            continue
        item = deepcopy(raw_item)
        had_delivery = any(item.get(field) for field in _DELIVERY_META_KEYS)
        for key in (*_STRIPPED_IMAGE_KEYS, *_DELIVERY_META_KEYS):
            item.pop(key, None)

        local_file: Path | None = None
        for ref_field in ("path", "local_path", "preview_url", "url"):
            local_file = _local_candidate_path(str(item.get(ref_field) or ""), app_dir)
            if local_file is not None:
                break

        asset_key = ""
        if local_file is not None:
            asset_key = _register_local_asset(
                local_file,
                asset_manifest=asset_manifest,
                asset_blobs=asset_blobs,
                path_to_key=path_to_key,
                total_bytes=total_bytes,
            )

        if asset_key:
            for ref_field in ("path", "local_path", "preview_url", "url"):
                ref_val = str(item.get(ref_field) or "").strip()
                if ref_val and not _is_remote_url(ref_val):
                    ref_to_asset_key[ref_val] = asset_key
            item["export_asset_key"] = asset_key
            item["path"] = ""
            item["local_path"] = ""
            raw_meta = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            source_remote = str(raw_meta.get("source_url") or "").strip()
            if had_delivery:
                item["url"] = source_remote if _is_remote_url(source_remote) else ""
            elif not _is_remote_url(str(item.get("url") or "")):
                item["url"] = ""
            item["preview_url"] = item.get("url") or ""
        else:
            item["path"] = ""
            item["local_path"] = ""
            raw_meta = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            remote = ""
            if had_delivery:
                candidate = str(raw_meta.get("source_url") or "").strip()
                if _is_remote_url(candidate):
                    remote = candidate
            if not remote:
                for ref_field in ("url", "preview_url"):
                    candidate = str(item.get(ref_field) or "").strip()
                    if _is_remote_url(candidate):
                        remote = candidate
                        break
            item["url"] = remote
            item["preview_url"] = remote
        cleaned_pool.append(item)

    source["image_pool"] = cleaned_pool
    # source.images 只保留归属当前裁剪后图片池的引用，避免携带多余采集图片。
    cleaned_images: list[str] = []
    for item in cleaned_pool:
        key = str(item.get("export_asset_key") or "").strip()
        if key:
            cleaned_images.append(f"asset://{key}")
        elif _is_remote_url(str(item.get("url") or "")):
            cleaned_images.append(str(item["url"]))
    source["images"] = cleaned_images
    exported["source"] = source
    return exported


def _prepare_draft_for_export(draft: dict[str, Any]) -> dict[str, Any]:
    exported = deepcopy(draft)
    for key in _STRIPPED_DRAFT_KEYS:
        exported.pop(key, None)
    for key, default in _DRAFT_SERVER_PUBLISH_DEFAULTS.items():
        exported[key] = deepcopy(default)
    exported["validation_errors"] = []
    exported["attributes"] = _strip_barcode_attributes(exported.get("attributes"))

    cleaned_targets: list[dict[str, Any]] = []
    for raw_target in exported.get("target_sites") or []:
        if not isinstance(raw_target, dict):
            continue
        target = deepcopy(raw_target)
        for key, default in _DRAFT_SERVER_PUBLISH_DEFAULTS.items():
            target[key] = deepcopy(default)
        target["validation_errors"] = []
        target["attributes"] = _strip_barcode_attributes(target.get("attributes"))
        if isinstance(target.get("sites_to_sell"), list):
            cleaned_ops: list[dict[str, Any]] = []
            for raw_op in target["sites_to_sell"]:
                if not isinstance(raw_op, dict):
                    continue
                op = deepcopy(raw_op)
                op.pop("status", None)
                cleaned_ops.append(op)
            target["sites_to_sell"] = cleaned_ops
        cleaned_targets.append(target)
    exported["target_sites"] = cleaned_targets

    cleaned_skus: list[dict[str, Any]] = []
    for raw_row in exported.get("sku_items") or []:
        if not isinstance(raw_row, dict):
            continue
        row = deepcopy(raw_row)
        row["publications"] = {}
        row.pop("barcode", None)
        row.pop("upc", None)
        overrides = row.get("overrides")
        if isinstance(overrides, dict):
            overrides.pop("barcode", None)
            overrides.pop("upc", None)
        attrs_by_target = row.get("attributes_by_target")
        if isinstance(attrs_by_target, dict):
            row["attributes_by_target"] = {
                tkey: _strip_barcode_attributes(tvals)
                for tkey, tvals in attrs_by_target.items()
                if isinstance(tvals, dict)
            }
        cleaned_skus.append(row)
    exported["sku_items"] = cleaned_skus
    return exported


def _draft_display_title(draft: dict[str, Any], product: dict[str, Any]) -> str:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    return str(
        draft.get("title")
        or draft.get("global_title")
        or source.get("title")
        or product.get("name")
        or ""
    ).strip()


def _sanitize_filename_title(title: str, max_len: int = 80) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", str(title or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned


def _build_export_filename(
    first_title: str,
    total_drafts: int,
    stamp: str,
) -> str:
    title = _sanitize_filename_title(first_title)
    if not title:
        title = f"drafts-{stamp}"
    if total_drafts == 1:
        return f"{title}.zip"
    return f"{title}等{total_drafts}份草稿.zip"


def export_drafts_archive(
    products_store: ProductStore,
    app_dir: Path,
    draft_ids: list[Any],
) -> tuple[bytes, str, int, list[str]]:
    """导出勾选草稿及其最小宿主上下文和本地图片资产为 ZIP 字节流。"""
    ids = list(dict.fromkeys(str(did or "").strip() for did in (draft_ids or []) if str(did or "").strip()))
    if not ids:
        raise RequestValidationError("请先勾选要导出的草稿。")

    missing_ids: list[str] = []
    grouped: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    first_title = ""

    for did in ids:
        draft = products_store.draft_record(did)
        if not draft:
            missing_ids.append(did)
            continue
        product_id = str(draft.get("product_id") or draft.get("source_product_id") or "").strip()
        product = products_store.find_indexed_product(product_id) if product_id else {}
        if not product:
            missing_ids.append(did)
            continue
        if not first_title:
            first_title = _draft_display_title(draft, product)
        if product_id not in grouped:
            grouped[product_id] = (product, [])
        grouped[product_id][1].append(draft)

    total_drafts = sum(len(entry[1]) for entry in grouped.values())
    if total_drafts == 0:
        raise RequestValidationError("未找到可导出的草稿，请刷新草稿箱后重试。", status_code=404)

    asset_manifest: dict[str, dict[str, str]] = {}
    asset_blobs: dict[str, bytes] = {}
    path_to_key: dict[str, str] = {}
    total_bytes = [0]
    exported_items: list[dict[str, Any]] = []

    for product, group_drafts in grouped.values():
        exported_product = _prepare_host_product_for_export(
            product,
            group_drafts,
            app_dir=app_dir,
            asset_manifest=asset_manifest,
            asset_blobs=asset_blobs,
            path_to_key=path_to_key,
            total_bytes=total_bytes,
        )
        exported_drafts = [_prepare_draft_for_export(draft) for draft in group_drafts]
        exported_items.append({
            "product": exported_product,
            "drafts": exported_drafts,
        })

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    filename = _build_export_filename(first_title, total_drafts, stamp)
    manifest = {
        "format": DRAFT_EXPORT_FORMAT,
        "version": DRAFT_EXPORT_VERSION,
        "product_schema_version": PRODUCT_SCHEMA_VERSION,
        "app_version": _current_app_version(),
        "exported_at": now.isoformat(timespec="seconds"),
        "items": exported_items,
        "assets": asset_manifest,
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for key, raw in asset_blobs.items():
            # 图片本身已压缩，直接存储更快且避免重复压缩开销。
            zf.writestr(f"assets/{key}", raw, compress_type=zipfile.ZIP_STORED)

    return buffer.getvalue(), filename, total_drafts, missing_ids


def export_drafts_to_file(
    products_store: ProductStore,
    app_dir: Path,
    draft_ids: list[Any],
) -> dict[str, Any]:
    """将草稿 ZIP 直接写入临时导出文件并签发下载票据，供浏览器原生流式下载。"""
    zip_bytes, filename, total_drafts, missing_ids = export_drafts_archive(
        products_store,
        app_dir,
        draft_ids,
    )
    token, target_path = export_archive_store.allocate_export_target(app_dir)
    try:
        target_path.write_bytes(zip_bytes)
        entry = export_archive_store.finalize_export_archive(
            app_dir,
            token,
            target_path,
            filename=filename,
            content_type="application/zip",
        )
    except Exception:
        try:
            target_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return {
        "ok": True,
        "count": total_drafts,
        "missingIds": missing_ids,
        "filename": entry.filename,
        "sizeBytes": entry.size_bytes,
        "downloadUrl": entry.download_url,
        "message": f"已打包 {total_drafts} 份草稿，开始下载。",
    }


def _read_zip_member_bounded(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    with zf.open(info, "r") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RequestValidationError(
                    f"{label} 解压大小超过限制（最大 {max_bytes // (1024 * 1024)} MB）。"
                )
            chunks.append(chunk)
    return b"".join(chunks)


def _validate_zip_entry_paths(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise RequestValidationError(f"ZIP 包内文件数过多（最多 {MAX_ZIP_ENTRIES} 个）。")
    entries: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        raw_name = info.filename
        if not raw_name or raw_name.endswith("/"):
            continue
        if "\\" in raw_name or raw_name.startswith("/") or ":" in raw_name:
            raise RequestValidationError(f"ZIP 包包含不安全的文件路径：{raw_name}")
        # 拒绝 POSIX 符号链接条目。
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise RequestValidationError(f"ZIP 包不允许包含符号链接：{raw_name}")
        posix = PurePosixPath(raw_name)
        if any(part in {"", ".", ".."} for part in posix.parts):
            raise RequestValidationError(f"ZIP 包包含不安全的文件路径：{raw_name}")
        normalized = posix.as_posix()
        if normalized in entries:
            raise RequestValidationError(f"ZIP 包包含重复文件路径：{normalized}")
        entries[normalized] = info
    return entries


def _validate_image_bytes(raw: bytes, suffix: str, asset_key: str) -> None:
    if not raw:
        raise RequestValidationError(f"图片资产内容为空：{asset_key}")
    if suffix not in image_service.IMAGE_SUFFIXES:
        raise RequestValidationError(f"图片资产格式不受支持：{asset_key}")
    if Image is not None:
        try:
            with Image.open(io.BytesIO(raw)) as img:
                img.verify()
        except Exception:
            raise RequestValidationError(f"图片资产内容损坏或不是合法图片：{asset_key}") from None


def _validate_manifest_envelope(manifest: Any) -> tuple[list[Any], dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise RequestValidationError("草稿导入包中的 manifest.json 格式无效。")
    fmt = str(manifest.get("format") or "").strip()
    if fmt != DRAFT_EXPORT_FORMAT:
        raise RequestValidationError("不支持的导入文件格式，请选择从草稿箱导出的 ZIP 文件。")
    version = manifest.get("version")
    if not _is_exact_int(version):
        raise RequestValidationError("导入包缺少有效的格式版本号（version）。")
    if version > DRAFT_EXPORT_VERSION:
        raise RequestValidationError(
            f"导入包格式版本（v{version}）高于当前系统支持的版本（v{DRAFT_EXPORT_VERSION}），请先升级系统后再导入。"
        )
    if version not in SUPPORTED_DRAFT_EXPORT_VERSIONS:
        raise RequestValidationError(
            f"导入包格式版本（v{version}）不受支持，当前仅支持 v{DRAFT_EXPORT_VERSION}，请重新导出。"
        )
    schema_version = manifest.get("product_schema_version")
    if not _is_exact_int(schema_version):
        raise RequestValidationError("导入包缺少有效的商品数据结构版本（product_schema_version）。")
    if schema_version > PRODUCT_SCHEMA_VERSION:
        raise RequestValidationError(
            f"商品数据结构版本（v{schema_version}）高于当前系统支持的版本（v{PRODUCT_SCHEMA_VERSION}），请先升级系统后再导入。"
        )
    if schema_version != PRODUCT_SCHEMA_VERSION:
        raise RequestValidationError(
            f"商品数据结构版本（v{schema_version}）与当前系统版本（v{PRODUCT_SCHEMA_VERSION}）不兼容，请重新导出。"
        )
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise RequestValidationError("导入包中缺少有效的草稿数据（items）。")
    assets = manifest.get("assets")
    if assets is not None and not isinstance(assets, dict):
        raise RequestValidationError("导入包中的图片资产索引（assets）格式无效。")
    return items, (assets if isinstance(assets, dict) else {})


def _validate_asset_reference(
    asset_key: str,
    asset_manifest: dict[str, Any],
    zip_entries: dict[str, zipfile.ZipInfo],
    zf: zipfile.ZipFile,
    *,
    decoded_assets: dict[str, tuple[bytes, str, str]],
    total_asset_bytes: list[int],
) -> None:
    if asset_key in decoded_assets:
        return
    if not _ASSET_KEY_PATTERN.match(asset_key):
        raise RequestValidationError(f"图片资产标识格式无效：{asset_key}")
    meta = asset_manifest.get(asset_key)
    if not isinstance(meta, dict):
        raise RequestValidationError(f"引用的图片资产索引缺失：{asset_key}")
    expected_path = f"assets/{asset_key}"
    declared_path = str(meta.get("path") or expected_path).strip()
    if declared_path != expected_path:
        raise RequestValidationError(f"图片资产路径与索引不一致：{asset_key}")
    info = zip_entries.get(expected_path)
    if info is None:
        raise RequestValidationError(f"ZIP 包中缺少引用的图片文件：{expected_path}")
    raw = _read_zip_member_bounded(
        zf,
        info,
        max_bytes=MAX_SINGLE_ASSET_BYTES,
        label=f"图片 {asset_key}",
    )
    if total_asset_bytes[0] + len(raw) > MAX_TOTAL_IMPORT_ASSET_BYTES:
        raise RequestValidationError(
            f"导入包中的图片总大小超出限制（最大 {MAX_TOTAL_IMPORT_ASSET_BYTES // (1024 * 1024)} MB）。"
        )
    suffix = Path(asset_key).suffix.lower() or ".png"
    _validate_image_bytes(raw, suffix, asset_key)
    total_asset_bytes[0] += len(raw)
    filename = str(meta.get("filename") or asset_key).strip() or asset_key
    decoded_assets[asset_key] = (raw, suffix, filename)


def _prevalidate_import_item(
    raw_item: Any,
    *,
    asset_manifest: dict[str, Any],
    zip_entries: dict[str, zipfile.ZipInfo],
    zf: zipfile.ZipFile,
    decoded_assets: dict[str, tuple[bytes, str, str]],
    total_asset_bytes: list[int],
) -> int:
    if not isinstance(raw_item, dict):
        raise RequestValidationError("草稿分组项格式无效，必须是对象。")
    raw_product = raw_item.get("product")
    raw_drafts = raw_item.get("drafts")
    if not isinstance(raw_product, dict):
        raise RequestValidationError("缺少有效的宿主商品信息（product）。")
    if not isinstance(raw_drafts, list) or not raw_drafts:
        raise RequestValidationError("缺少有效的草稿列表（drafts）。")

    try:
        reject_retired_product_category_fields(raw_product)
    except RetiredCategorySchemaFieldError as exc:
        raise RequestValidationError(str(exc)) from None

    item_schema_version = raw_product.get("schema_version")
    if not _is_exact_int(item_schema_version):
        raise RequestValidationError("缺少有效的商品数据结构版本（schema_version）。")
    if item_schema_version != PRODUCT_SCHEMA_VERSION:
        raise RequestValidationError(
            f"商品数据结构版本（v{item_schema_version}）与当前系统版本（v{PRODUCT_SCHEMA_VERSION}）不兼容。"
        )

    source = raw_product.get("source") if isinstance(raw_product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    for raw_img in pool:
        if not isinstance(raw_img, dict):
            continue
        asset_key = str(raw_img.get("export_asset_key") or "").strip()
        if asset_key:
            _validate_asset_reference(
                asset_key,
                asset_manifest,
                zip_entries,
                zf,
                decoded_assets=decoded_assets,
                total_asset_bytes=total_asset_bytes,
            )

    for ref in source.get("images") or []:
        text = str(ref or "").strip()
        if text.startswith("asset://"):
            _validate_asset_reference(
                text[len("asset://"):].strip(),
                asset_manifest,
                zip_entries,
                zf,
                decoded_assets=decoded_assets,
                total_asset_bytes=total_asset_bytes,
            )

    dry_product = deepcopy(raw_product)
    for key in _STRIPPED_PRODUCT_KEYS:
        dry_product.pop(key, None)
    dry_product["in_library"] = False
    validate_product_root_fields(dry_product)
    normalized_product = normalize_product_fields(dry_product)

    for d_idx, raw_draft in enumerate(raw_drafts, start=1):
        if not isinstance(raw_draft, dict):
            raise RequestValidationError(f"第 {d_idx} 份草稿格式无效，必须是对象。")
        try:
            reject_retired_draft_schema_fields(raw_draft)
        except RetiredCategorySchemaFieldError as exc:
            raise RequestValidationError(f"第 {d_idx} 份草稿校验失败：{exc}") from None
        platform = str(raw_draft.get("platform") or "").strip().lower()
        if platform not in PLATFORMS:
            raise RequestValidationError(f"第 {d_idx} 份草稿平台不受支持：{platform or '空'}")
        dry_draft = _prepare_draft_for_export(raw_draft)
        dry_draft["draft_id"] = "draft_dry_check"
        dry_draft["product_id"] = "prod_dry_check"
        dry_draft["source_product_id"] = "prod_dry_check"
        validate_draft_facts(dry_draft)
        normalize_platform_draft(dry_draft, platform, normalized_product)

    return len(raw_drafts)


def _write_imported_asset(
    asset_key: str,
    decoded_assets: dict[str, tuple[bytes, str, str]],
    *,
    app_dir: Path,
    product_id: str,
    restored_cache: dict[str, tuple[str, str, int, int]],
    reuse_cached: bool = True,
) -> tuple[str, str, int, int] | None:
    if reuse_cached and asset_key in restored_cache:
        return restored_cache[asset_key]
    entry = decoded_assets.get(asset_key)
    if entry is None:
        return None
    raw, suffix, original_name = entry

    target_dir = owned_path(
        image_service.images_root(app_dir) / image_service.safe_segment(product_id, "imported"),
        app_dir=app_dir,
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = owned_path(target_dir / image_service.safe_filename(original_name, suffix), app_dir=app_dir)
    while dest.exists():
        dest = owned_path(target_dir / image_service.safe_filename(original_name, suffix), app_dir=app_dir)

    pending_path = ""
    try:
        with NamedTemporaryFile(dir=target_dir, delete=False) as pending:
            pending.write(raw)
            pending_path = pending.name
        os.replace(pending_path, dest)
    except Exception:
        if pending_path:
            try:
                os.unlink(pending_path)
            except OSError:
                pass
        raise

    width, height = image_service.image_dimensions(dest)
    rel_path = image_service.relative_to_app(dest, app_dir)
    preview = image_service.file_url(dest)
    result = (rel_path, preview, width, height)
    restored_cache.setdefault(asset_key, result)
    return result


def _prepare_host_product_for_import(
    raw_product: dict[str, Any],
    *,
    target_id: str,
    app_dir: Path,
    decoded_assets: dict[str, tuple[bytes, str, str]],
) -> dict[str, Any]:
    item = deepcopy(raw_product)
    for key in _STRIPPED_PRODUCT_KEYS:
        item.pop(key, None)
    item["product_id"] = target_id
    item["in_library"] = False
    item["upc"] = ""
    item.pop("updated_at", None)
    item.pop("created_at", None)

    for sku in item.get("sku_items") or []:
        if isinstance(sku, dict):
            sku["barcode"] = ""
            sku.pop("upc", None)

    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    for key in _STRIPPED_SOURCE_KEYS:
        source.pop(key, None)

    restored_cache: dict[str, tuple[str, str, int, int]] = {}
    restored_pool_assets: dict[tuple[str, str], tuple[str, str, int, int]] = {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    restored_pool: list[dict[str, Any]] = []

    for raw_img in pool:
        if not isinstance(raw_img, dict):
            continue
        img = deepcopy(raw_img)
        for key in (*_STRIPPED_IMAGE_KEYS, *_DELIVERY_META_KEYS):
            img.pop(key, None)
        asset_key = str(img.pop("export_asset_key", "") or "").strip()
        if asset_key and asset_key in decoded_assets:
            pool_asset_id = str(img.get("id") or img.get("asset_id") or "").strip()
            pool_cache_key = (pool_asset_id, asset_key) if pool_asset_id else None
            # 不同图片池资产即使二进制内容相同也必须落盘到独立路径，避免 normalize_image_pool 去重裁掉 SKU 图。
            if pool_cache_key and pool_cache_key in restored_pool_assets:
                restored = restored_pool_assets[pool_cache_key]
            else:
                restored = _write_imported_asset(
                    asset_key,
                    decoded_assets,
                    app_dir=app_dir,
                    product_id=target_id,
                    restored_cache=restored_cache,
                    reuse_cached=False,
                )
                if restored and pool_cache_key:
                    restored_pool_assets[pool_cache_key] = restored
            if restored:
                rel_path, preview, width, height = restored
                img["path"] = rel_path
                img["local_path"] = rel_path
                img["preview_url"] = preview
                if width and height:
                    img["width"] = width
                    img["height"] = height
                    img["width_px"] = width
                    img["height_px"] = height
                if not _is_remote_url(str(img.get("url") or "")):
                    img["url"] = preview
                restored_pool.append(img)
                continue

        # 未携带内嵌资产时仅允许保留安全的远端 HTTP(S) 链接，丢弃任何本机路径。
        img["path"] = ""
        img["local_path"] = ""
        remote = str(img.get("url") or "").strip()
        if not _is_remote_url(remote):
            remote = str(img.get("preview_url") or "").strip()
        if _is_remote_url(remote):
            img["url"] = remote
            img["preview_url"] = remote
            restored_pool.append(img)

    source["image_pool"] = restored_pool
    restored_images: list[str] = []
    for ref in source.get("images") or []:
        text = str(ref or "").strip()
        if text.startswith("asset://"):
            asset_key = text[len("asset://"):].strip()
            restored = _write_imported_asset(
                asset_key,
                decoded_assets,
                app_dir=app_dir,
                product_id=target_id,
                restored_cache=restored_cache,
            )
            if restored:
                restored_images.append(restored[0])
        elif _is_remote_url(text):
            restored_images.append(text)
    source["images"] = restored_images
    item["source"] = source
    return item


def import_drafts_archive(
    products_store: ProductStore,
    app_dir: Path,
    raw_zip: bytes,
) -> dict[str, Any]:
    """全量预校验后导入草稿 ZIP 包，宿主商品固定 ``in_library=False``。"""
    if not raw_zip:
        raise RequestValidationError("上传的草稿导入包不能为空。")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_zip), mode="r")
    except zipfile.BadZipFile:
        raise RequestValidationError("所选文件不是有效的 ZIP 压缩包。") from None

    with zf:
        zip_entries = _validate_zip_entry_paths(zf)
        manifest_info = zip_entries.get("manifest.json")
        if manifest_info is None:
            raise RequestValidationError("草稿导入包缺少 manifest.json，请确认是从草稿箱导出的 ZIP 文件。")
        manifest_bytes = _read_zip_member_bounded(
            zf,
            manifest_info,
            max_bytes=MAX_MANIFEST_BYTES,
            label="manifest.json",
        )
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RequestValidationError("草稿导入包中的 manifest.json 不是合法 UTF-8 JSON。") from None

        raw_items, asset_manifest = _validate_manifest_envelope(manifest)

        # 第一阶段：全量预校验，任何错误都不写入部分文件或数据库记录。
        decoded_assets: dict[str, tuple[bytes, str, str]] = {}
        total_asset_bytes = [0]
        total_drafts = 0
        for index, raw_item in enumerate(raw_items, start=1):
            try:
                total_drafts += _prevalidate_import_item(
                    raw_item,
                    asset_manifest=asset_manifest,
                    zip_entries=zip_entries,
                    zf=zf,
                    decoded_assets=decoded_assets,
                    total_asset_bytes=total_asset_bytes,
                )
            except RequestValidationError as exc:
                raise RequestValidationError(f"第 {index} 组草稿校验失败：{exc}") from None
            except ValueError as exc:
                raise RequestValidationError(f"第 {index} 组草稿资料无效：{exc}") from None
        if total_drafts > MAX_IMPORT_DRAFTS:
            raise RequestValidationError(f"单次最多导入 {MAX_IMPORT_DRAFTS} 份草稿。")

    # 第二阶段：全部校验通过后再统一写盘与入库。
    imported_draft_ids: list[str] = []
    for index, raw_item in enumerate(raw_items, start=1):
        target_product_id = f"prod_{uuid4().hex[:12]}"
        asset_dir = owned_path(
            image_service.images_root(app_dir) / image_service.safe_segment(target_product_id, "imported"),
            app_dir=app_dir,
        )
        try:
            prepared_product = _prepare_host_product_for_import(
                raw_item["product"],
                target_id=target_product_id,
                app_dir=app_dir,
                decoded_assets=decoded_assets,
            )
            saved_product = products_store.save_product(
                prepared_product,
                preserve_platform_facts=False,
            )
            saved_product_id = str(saved_product.get("product_id") or target_product_id)

            for raw_draft in raw_item["drafts"]:
                prepared_draft = _prepare_draft_for_export(raw_draft)
                platform = str(prepared_draft.get("platform") or "").strip().lower()
                prepared_draft["draft_id"] = new_draft_id()
                saved_draft = products_store.import_draft_record(
                    saved_product_id,
                    platform,
                    prepared_draft,
                )
                imported_draft_ids.append(str(saved_draft.get("draft_id") or prepared_draft["draft_id"]))
        except (RequestValidationError, ValueError) as exc:
            if asset_dir.exists():
                shutil.rmtree(asset_dir, ignore_errors=True)
            raise RequestValidationError(f"第 {index} 组草稿导入失败：{exc}") from None
        except Exception:
            if asset_dir.exists():
                shutil.rmtree(asset_dir, ignore_errors=True)
            raise

    return {
        "ok": True,
        "imported": len(imported_draft_ids),
        "importedDraftIds": imported_draft_ids,
        "draftsIndex": products_store.load_drafts_index(scope="all"),
        "message": f"已成功导入 {len(imported_draft_ids)} 份草稿，请核对核价与 SKU 编码并重新预检。",
    }


__all__ = [
    "DRAFT_EXPORT_FORMAT",
    "DRAFT_EXPORT_VERSION",
    "SUPPORTED_DRAFT_EXPORT_VERSIONS",
    "ZIP_CONTENT_TYPES",
    "export_drafts_archive",
    "export_drafts_to_file",
    "import_drafts_archive",
]

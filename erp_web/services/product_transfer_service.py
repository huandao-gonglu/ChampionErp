# -*- coding: utf-8 -*-
"""采集箱商品 JSON 导出与导入服务。

导出的 JSON 只包含采集箱商品事实、SKU 及通用图片资产，不包含草稿箱与平台上传态；
本地图片按当前账号空间内嵌为 base64 资产，导入时写入目标账号的 ``data/images`` 目录。
"""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import mimetypes
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from erp_web.schemas.requests import RequestValidationError
from erp_web.services import image_service
from erp_web.services.workspace_files import owned_path
from erp_web.stores.product_store import (
    ProductStore,
    RetiredCategorySchemaFieldError,
    reject_retired_product_category_fields,
)

EXPORT_FORMAT = "champion-erp.library-products"
EXPORT_VERSION = 1
MAX_IMPORT_PRODUCTS = 200
MAX_SINGLE_ASSET_BYTES = 5 * 1024 * 1024
MAX_TOTAL_EXPORT_ASSET_BYTES = 32 * 1024 * 1024
MAX_TOTAL_IMPORT_ASSET_BYTES = 32 * 1024 * 1024

_STRIPPED_PRODUCT_KEYS = (
    "drafts",
    "publish_preview",
    "workflow_statuses",
    "copy_results",
    "product_file_path",
)

_STRIPPED_IMAGE_KEYS = (
    "platform_picture_id",
    "mercadolibre_picture_id",
    "upload_status",
    "upload_error",
    "uploaded_at",
    "platform_uploads",
)


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


def _embed_local_file(
    file_path: Path,
    *,
    assets: dict[str, dict[str, str]],
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
    if not raw or len(raw) > MAX_SINGLE_ASSET_BYTES:
        return ""
    if total_bytes[0] + len(raw) > MAX_TOTAL_EXPORT_ASSET_BYTES:
        return ""
    digest = hashlib.sha256(raw).hexdigest()[:16]
    suffix = file_path.suffix.lower() or ".png"
    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix not in image_service.IMAGE_SUFFIXES:
        suffix = ".png"
    mime = mimetypes.guess_type(f"file{suffix}")[0] or "image/png"
    key = f"asset_{digest}{suffix}"
    if key not in assets:
        total_bytes[0] += len(raw)
        encoded = base64.b64encode(raw).decode("ascii")
        assets[key] = {
            "filename": file_path.name,
            "content_type": mime,
            "data_url": f"data:{mime};base64,{encoded}",
        }
    path_to_key[resolved] = key
    return key


def _prepare_product_for_export(
    product: dict[str, Any],
    *,
    app_dir: Path,
    assets: dict[str, dict[str, str]],
    path_to_key: dict[str, str],
    total_bytes: list[int],
) -> dict[str, Any]:
    exported = deepcopy(product)
    for key in _STRIPPED_PRODUCT_KEYS:
        exported.pop(key, None)

    source = exported.get("source") if isinstance(exported.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    ref_to_asset_key: dict[str, str] = {}
    cleaned_pool: list[dict[str, Any]] = []

    for raw_item in pool:
        if not isinstance(raw_item, dict):
            continue
        item = deepcopy(raw_item)
        for key in _STRIPPED_IMAGE_KEYS:
            item.pop(key, None)

        local_file: Path | None = None
        for ref_field in ("path", "local_path", "preview_url", "url"):
            local_file = _local_candidate_path(str(item.get(ref_field) or ""), app_dir)
            if local_file is not None:
                break

        asset_key = ""
        if local_file is not None:
            asset_key = _embed_local_file(
                local_file,
                assets=assets,
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
            if not _is_remote_url(str(item.get("url") or "")):
                item["url"] = ""
            item["preview_url"] = item.get("url") or ""
        else:
            item["path"] = ""
            item["local_path"] = ""
            remote = str(item.get("url") or "").strip()
            if not _is_remote_url(remote):
                remote = str(item.get("preview_url") or "").strip()
            if _is_remote_url(remote):
                item["url"] = remote
                item["preview_url"] = remote
            else:
                item["url"] = ""
                item["preview_url"] = ""
        cleaned_pool.append(item)

    source["image_pool"] = cleaned_pool

    raw_images = source.get("images") if isinstance(source.get("images"), list) else []
    cleaned_images: list[str] = []
    for ref in raw_images:
        text = str(ref or "").strip()
        if not text:
            continue
        if _is_remote_url(text):
            cleaned_images.append(text)
            continue
        mapped = ref_to_asset_key.get(text)
        if not mapped:
            local_file = _local_candidate_path(text, app_dir)
            if local_file is not None:
                mapped = _embed_local_file(
                    local_file,
                    assets=assets,
                    path_to_key=path_to_key,
                    total_bytes=total_bytes,
                )
        if mapped:
            cleaned_images.append(f"asset://{mapped}")
    source["images"] = cleaned_images
    exported["source"] = source

    sku_items = exported.get("sku_items") if isinstance(exported.get("sku_items"), list) else []
    for sku in sku_items:
        if not isinstance(sku, dict):
            continue
        for field in ("image", "image_url", "image_ref"):
            val = str(sku.get(field) or "").strip()
            if not val or _is_remote_url(val):
                continue
            mapped = ref_to_asset_key.get(val)
            if not mapped:
                local_file = _local_candidate_path(val, app_dir)
                if local_file is not None:
                    mapped = _embed_local_file(
                        local_file,
                        assets=assets,
                        path_to_key=path_to_key,
                        total_bytes=total_bytes,
                    )
            sku[field] = f"asset://{mapped}" if mapped else ""

    return exported


def _write_imported_asset(
    asset_key: str,
    assets: dict[str, Any],
    *,
    app_dir: Path,
    product_id: str,
    restored_cache: dict[str, tuple[str, str, int, int]],
    total_bytes: list[int],
) -> tuple[str, str, int, int] | None:
    if asset_key in restored_cache:
        return restored_cache[asset_key]
    entry = assets.get(asset_key)
    if not isinstance(entry, dict):
        return None
    raw, suffix = image_service.decode_upload(entry)
    if not raw or len(raw) > MAX_SINGLE_ASSET_BYTES:
        return None
    if total_bytes[0] + len(raw) > MAX_TOTAL_IMPORT_ASSET_BYTES:
        raise RequestValidationError("导入文件中的图片总大小超出限制（最大 32 MB）。")
    total_bytes[0] += len(raw)

    target_dir = owned_path(
        image_service.images_root(app_dir) / image_service.safe_segment(product_id, "imported"),
        app_dir=app_dir,
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = image_service.safe_filename(str(entry.get("filename") or asset_key), suffix)
    dest = owned_path(target_dir / filename, app_dir=app_dir)
    with NamedTemporaryFile(dir=target_dir, delete=False) as pending:
        pending.write(raw)
        pending_path = pending.name
    os.replace(pending_path, dest)

    width, height = image_service.image_dimensions(dest)
    rel_path = image_service.relative_to_app(dest, app_dir)
    preview = image_service.file_url(dest)
    result = (rel_path, preview, width, height)
    restored_cache[asset_key] = result
    return result


def _prepare_product_for_import(
    raw_product: dict[str, Any],
    *,
    products_store: ProductStore,
    app_dir: Path,
    assets: dict[str, Any],
    total_asset_bytes: list[int],
) -> dict[str, Any]:
    if not isinstance(raw_product, dict):
        raise RequestValidationError("导入商品项格式无效，必须是对象。")
    try:
        reject_retired_product_category_fields(raw_product)
    except RetiredCategorySchemaFieldError as exc:
        raise RequestValidationError(str(exc)) from None

    item = deepcopy(raw_product)
    for key in _STRIPPED_PRODUCT_KEYS:
        item.pop(key, None)

    incoming_id = str(item.get("product_id") or "").strip()
    if not incoming_id or products_store.find_indexed_product(incoming_id):
        target_id = f"prod_{uuid4().hex[:12]}"
    else:
        target_id = incoming_id
    item["product_id"] = target_id
    item.pop("updated_at", None)

    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    has_identity = bool(
        str(item.get("name") or "").strip()
        or str(source.get("title") or "").strip()
        or str(source.get("source_url") or "").strip()
        or (isinstance(item.get("sku_items"), list) and item.get("sku_items"))
    )
    if not has_identity:
        raise RequestValidationError("导入商品缺少标题、来源链接或 SKU 信息。")

    if not str(source.get("collect_status") or "").strip():
        source["collect_status"] = "success" if (source.get("title") or item.get("name")) else "pending"

    restored_cache: dict[str, tuple[str, str, int, int]] = {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    restored_pool: list[dict[str, Any]] = []
    for raw_img in pool:
        if not isinstance(raw_img, dict):
            continue
        img = deepcopy(raw_img)
        for key in _STRIPPED_IMAGE_KEYS:
            img.pop(key, None)
        asset_key = str(img.pop("export_asset_key", "") or "").strip()
        if asset_key and asset_key in assets:
            restored = _write_imported_asset(
                asset_key,
                assets,
                app_dir=app_dir,
                product_id=target_id,
                restored_cache=restored_cache,
                total_bytes=total_asset_bytes,
            )
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

        # 未携带内嵌资产时，仅允许保留安全的远端 HTTP(S) 链接，丢弃任何本机路径。
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

    raw_images = source.get("images") if isinstance(source.get("images"), list) else []
    restored_images: list[str] = []
    for ref in raw_images:
        text = str(ref or "").strip()
        if text.startswith("asset://"):
            asset_key = text[len("asset://"):].strip()
            restored = _write_imported_asset(
                asset_key,
                assets,
                app_dir=app_dir,
                product_id=target_id,
                restored_cache=restored_cache,
                total_bytes=total_asset_bytes,
            )
            if restored:
                restored_images.append(restored[0])
        elif _is_remote_url(text):
            restored_images.append(text)
    source["images"] = restored_images
    item["source"] = source

    sku_items = item.get("sku_items") if isinstance(item.get("sku_items"), list) else []
    for sku in sku_items:
        if not isinstance(sku, dict):
            continue
        for field in ("image", "image_url", "image_ref"):
            val = str(sku.get(field) or "").strip()
            if not val:
                continue
            if val.startswith("asset://"):
                asset_key = val[len("asset://"):].strip()
                restored = _write_imported_asset(
                    asset_key,
                    assets,
                    app_dir=app_dir,
                    product_id=target_id,
                    restored_cache=restored_cache,
                    total_bytes=total_asset_bytes,
                )
                sku[field] = restored[1] if restored else ""
            elif not _is_remote_url(val):
                sku[field] = ""

    return item


_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _product_display_title(product: dict[str, Any]) -> str:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    return str(source.get("title") or product.get("name") or "").strip()


def _sanitize_filename_title(title: str, max_len: int = 80) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", str(title or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned


def _build_export_filename(exported_products: list[dict[str, Any]], stamp: str) -> str:
    first = exported_products[0] if exported_products else {}
    title = _sanitize_filename_title(_product_display_title(first))
    if not title:
        fallback_id = _sanitize_filename_title(str(first.get("product_id") or ""))
        title = fallback_id if len(exported_products) == 1 and fallback_id else f"library-products-{stamp}"
    if len(exported_products) == 1:
        return f"{title}.json"
    return f"{title}等{len(exported_products)}个商品.json"


def export_products(
    products_store: ProductStore,
    app_dir: Path,
    product_ids: list[Any],
) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(pid or "").strip() for pid in (product_ids or []) if str(pid or "").strip()))
    if not ids:
        raise RequestValidationError("请先勾选要导出的商品。")

    assets: dict[str, dict[str, str]] = {}
    path_to_key: dict[str, str] = {}
    total_bytes = [0]
    exported_products: list[dict[str, Any]] = []
    missing_ids: list[str] = []

    for pid in ids:
        record = products_store.find_indexed_product(pid)
        if not record:
            missing_ids.append(pid)
            continue
        exported_products.append(
            _prepare_product_for_export(
                record,
                app_dir=app_dir,
                assets=assets,
                path_to_key=path_to_key,
                total_bytes=total_bytes,
            )
        )

    if not exported_products:
        raise RequestValidationError("未找到可导出的商品，请刷新采集箱后重试。", status_code=404)

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    filename = _build_export_filename(exported_products, stamp)
    export_payload = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": now.isoformat(timespec="seconds"),
        "products": exported_products,
        "assets": assets,
    }
    return {
        "ok": True,
        "count": len(exported_products),
        "missingIds": missing_ids,
        "filename": filename,
        "exportData": export_payload,
        "message": f"已导出 {len(exported_products)} 个商品。",
    }


def import_products(
    products_store: ProductStore,
    app_dir: Path,
    body: dict[str, Any],
) -> dict[str, Any]:
    raw_products = body.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise RequestValidationError("导入 JSON 中缺少有效的商品列表（products）。")
    if len(raw_products) > MAX_IMPORT_PRODUCTS:
        raise RequestValidationError(f"单次最多导入 {MAX_IMPORT_PRODUCTS} 个商品。")

    raw_assets = body.get("assets")
    assets: dict[str, Any] = raw_assets if isinstance(raw_assets, dict) else {}
    total_asset_bytes = [0]
    imported_ids: list[str] = []

    for index, raw_product in enumerate(raw_products, start=1):
        try:
            prepared = _prepare_product_for_import(
                raw_product,
                products_store=products_store,
                app_dir=app_dir,
                assets=assets,
                total_asset_bytes=total_asset_bytes,
            )
            saved = products_store.save_product(prepared, preserve_platform_facts=False)
            imported_ids.append(str(saved.get("product_id") or prepared["product_id"]))
        except RequestValidationError as exc:
            raise RequestValidationError(f"第 {index} 个商品导入失败：{exc}") from None
        except ValueError as exc:
            raise RequestValidationError(f"第 {index} 个商品资料无效：{exc}") from None

    return {
        "ok": True,
        "imported": len(imported_ids),
        "importedIds": imported_ids,
        "productsIndex": products_store.load_products_index(),
        "message": f"已成功导入 {len(imported_ids)} 个商品到采集箱。",
    }


__all__ = [
    "EXPORT_FORMAT",
    "EXPORT_VERSION",
    "export_products",
    "import_products",
]

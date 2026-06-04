from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


DEFAULT_DB_NAME = "erp.sqlite3"
REQUIRED_TABLES = (
    "store_auth",
    "products",
    "platform_drafts",
    "media_assets",
    "category_cache",
    "publish_logs",
)
PLATFORMS = ("mercadolibre", "wildberries", "ozon")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def db_path(app_dir: Path | str) -> Path:
    return Path(app_dir) / DEFAULT_DB_NAME


def connect(app_dir: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(app_dir))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database(app_dir: Path | str) -> Path:
    path = db_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(app_dir)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS store_auth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                shop_name TEXT NOT NULL DEFAULT '',
                access_token TEXT NOT NULL DEFAULT '',
                refresh_token TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                auth_status TEXT NOT NULL DEFAULT '',
                auth_payload_json TEXT NOT NULL DEFAULT '{}',
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(platform, shop_name)
            );

            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,
                source_platform TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                brand TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                collect_status TEXT NOT NULL DEFAULT '',
                purchase_price TEXT NOT NULL DEFAULT '',
                purchase_currency TEXT NOT NULL DEFAULT '',
                dimensions_json TEXT NOT NULL DEFAULT '{}',
                weight_kg TEXT NOT NULL DEFAULT '',
                source_json TEXT NOT NULL DEFAULT '{}',
                product_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                site TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'claimed',
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                category_id TEXT NOT NULL DEFAULT '',
                category_path TEXT NOT NULL DEFAULT '',
                attributes_json TEXT NOT NULL DEFAULT '{}',
                price_json TEXT NOT NULL DEFAULT '{}',
                draft_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(product_id) ON DELETE CASCADE,
                UNIQUE(product_id, platform, site)
            );

            CREATE TABLE IF NOT EXISTS media_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                local_path TEXT NOT NULL DEFAULT '',
                preview_url TEXT NOT NULL DEFAULT '',
                width INTEGER,
                height INTEGER,
                size_label TEXT NOT NULL DEFAULT '',
                asset_type TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT '',
                platforms_json TEXT NOT NULL DEFAULT '[]',
                is_main INTEGER NOT NULL DEFAULT 0,
                selected INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(product_id) ON DELETE CASCADE,
                UNIQUE(product_id, asset_id)
            );

            CREATE TABLE IF NOT EXISTS category_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                site TEXT NOT NULL DEFAULT '',
                category_id TEXT NOT NULL,
                name_original TEXT NOT NULL DEFAULT '',
                name_cn TEXT NOT NULL DEFAULT '',
                path_original_json TEXT NOT NULL DEFAULT '[]',
                path_cn_json TEXT NOT NULL DEFAULT '[]',
                keywords_json TEXT NOT NULL DEFAULT '[]',
                attributes_json TEXT NOT NULL DEFAULT '{}',
                raw_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                UNIQUE(platform, site, category_id)
            );

            CREATE TABLE IF NOT EXISTS publish_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                draft_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                request_payload_path TEXT NOT NULL DEFAULT '',
                response_body_path TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                field_errors_json TEXT NOT NULL DEFAULT '{}',
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_products_source_url ON products(source_url);
            CREATE INDEX IF NOT EXISTS idx_platform_drafts_product ON platform_drafts(product_id);
            CREATE INDEX IF NOT EXISTS idx_media_assets_product ON media_assets(product_id);
            CREATE INDEX IF NOT EXISTS idx_publish_logs_product ON publish_logs(product_id, platform);
            """
        )
        conn.commit()
    finally:
        conn.close()
    return path


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def product_identity(product: dict[str, Any]) -> str:
    product = _dict(product)
    source = _dict(product.get("source"))
    existing = str(product.get("product_id") or product.get("id") or source.get("product_id") or "").strip()
    if existing:
        return _slug(existing)
    seed = "|".join(
        [
            str(source.get("source_url") or product.get("source_url") or "").strip(),
            str(source.get("title") or product.get("name") or "").strip(),
            str(source.get("created_at") or product.get("created_at") or "").strip(),
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return digest or "product"


def _slug(value: str) -> str:
    chars = []
    for ch in str(value):
        chars.append(ch if ch.isalnum() or ch in "._-" else "_")
    slug = "".join(chars).strip("._-")
    return slug[:80] or "product"


def _source(product: dict[str, Any]) -> dict[str, Any]:
    source = _dict(product.get("source"))
    return source or {
        "source_url": product.get("source_url") or "",
        "source_platform": product.get("source_platform") or "",
        "title": product.get("name") or "",
        "price": product.get("detected_price") or "",
        "currency": product.get("detected_currency") or "",
        "bullets": product.get("selling_points") or [],
        "description": product.get("description") or "",
        "images": product.get("source_image_urls") or product.get("source_images") or [],
    }


def _image_pool(product: dict[str, Any]) -> list[dict[str, Any]]:
    source = _source(product)
    pool = [item for item in _list(source.get("image_pool")) if isinstance(item, dict)]
    if pool:
        return pool
    images = _list(source.get("images")) or _list(product.get("source_image_urls")) or _list(product.get("source_images"))
    return [
        {
            "id": f"source_{index + 1}",
            "url": str(url),
            "preview_url": str(url),
            "origin": str(source.get("source_platform") or product.get("source_platform") or "source"),
            "usage": "main" if index == 0 else "detail",
            "platforms": [],
            "is_main": index == 0,
            "selected": index == 0,
            "order": index,
        }
        for index, url in enumerate(images)
        if str(url).strip()
    ]


def upsert_product_model(app_dir: Path | str, product: dict[str, Any]) -> str:
    initialize_database(app_dir)
    now = utc_now()
    product = dict(_dict(product))
    source = _source(product)
    product_id = product_identity(product)
    product["product_id"] = product_id
    conn = connect(app_dir)
    try:
        conn.execute(
            """
            INSERT INTO products (
                product_id, source_platform, source_url, title, brand, model,
                collect_status, purchase_price, purchase_currency, dimensions_json,
                weight_kg, source_json, product_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                source_platform=excluded.source_platform,
                source_url=excluded.source_url,
                title=excluded.title,
                brand=excluded.brand,
                model=excluded.model,
                collect_status=excluded.collect_status,
                purchase_price=excluded.purchase_price,
                purchase_currency=excluded.purchase_currency,
                dimensions_json=excluded.dimensions_json,
                weight_kg=excluded.weight_kg,
                source_json=excluded.source_json,
                product_json=excluded.product_json,
                updated_at=excluded.updated_at
            """,
            (
                product_id,
                str(source.get("source_platform") or product.get("source_platform") or ""),
                str(source.get("source_url") or product.get("source_url") or ""),
                str(source.get("title") or product.get("name") or ""),
                str(source.get("brand") or product.get("brand") or ""),
                str(source.get("model") or product.get("model") or ""),
                str(source.get("collect_status") or product.get("collect_status") or ""),
                str(source.get("price") or product.get("detected_price") or ""),
                str(source.get("currency") or product.get("detected_currency") or ""),
                json_dumps(source.get("dimensions") or {}),
                str(source.get("weight_kg") or product.get("weight_kg") or ""),
                json_dumps(source),
                json_dumps(product),
                str(product.get("created_at") or source.get("created_at") or now),
                now,
            ),
        )
        _upsert_drafts(conn, product_id, product, now)
        _upsert_media(conn, product_id, product, now)
        conn.commit()
    finally:
        conn.close()
    return product_id


def _upsert_drafts(conn: sqlite3.Connection, product_id: str, product: dict[str, Any], now: str) -> None:
    drafts = _dict(product.get("drafts"))
    for platform, draft_raw in drafts.items():
        draft = _dict(draft_raw)
        if platform not in PLATFORMS or not draft:
            continue
        site = str(draft.get("site") or draft.get("site_id") or "").strip()
        price_json = {
            key: draft.get(key)
            for key in ("price", "sale_price", "currency", "net_profit", "pricing")
            if draft.get(key) not in (None, "")
        }
        conn.execute(
            """
            INSERT INTO platform_drafts (
                product_id, platform, site, status, title, description, category_id,
                category_path, attributes_json, price_json, draft_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, platform, site) DO UPDATE SET
                status=excluded.status,
                title=excluded.title,
                description=excluded.description,
                category_id=excluded.category_id,
                category_path=excluded.category_path,
                attributes_json=excluded.attributes_json,
                price_json=excluded.price_json,
                draft_json=excluded.draft_json,
                updated_at=excluded.updated_at
            """,
            (
                product_id,
                platform,
                site,
                str(draft.get("status") or draft.get("publish_status") or "claimed"),
                str(draft.get("title") or ""),
                str(draft.get("description") or ""),
                str(draft.get("category_id") or ""),
                str(draft.get("category_path") or ""),
                json_dumps(draft.get("attributes") or {}),
                json_dumps(price_json),
                json_dumps(draft),
                now,
                now,
            ),
        )


def _upsert_media(conn: sqlite3.Connection, product_id: str, product: dict[str, Any], now: str) -> None:
    for index, item in enumerate(_image_pool(product)):
        asset_id = str(item.get("id") or f"image_{index + 1}").strip() or f"image_{index + 1}"
        width = _int_or_none(item.get("width"))
        height = _int_or_none(item.get("height"))
        conn.execute(
            """
            INSERT INTO media_assets (
                product_id, asset_id, url, local_path, preview_url, width, height,
                size_label, asset_type, origin, platforms_json, is_main, selected,
                sort_order, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, asset_id) DO UPDATE SET
                url=excluded.url,
                local_path=excluded.local_path,
                preview_url=excluded.preview_url,
                width=excluded.width,
                height=excluded.height,
                size_label=excluded.size_label,
                asset_type=excluded.asset_type,
                origin=excluded.origin,
                platforms_json=excluded.platforms_json,
                is_main=excluded.is_main,
                selected=excluded.selected,
                sort_order=excluded.sort_order,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                product_id,
                asset_id,
                str(item.get("url") or ""),
                str(item.get("path") or item.get("local_path") or ""),
                str(item.get("preview_url") or item.get("url") or item.get("path") or ""),
                width,
                height,
                str(item.get("size_label") or (f"{width} x {height}" if width and height else "")),
                str(item.get("usage") or item.get("type") or ""),
                str(item.get("origin") or ""),
                json_dumps(_list(item.get("platforms"))),
                1 if item.get("is_main") else 0,
                1 if item.get("selected") else 0,
                int(item.get("order") if str(item.get("order") or "").isdigit() else index),
                json_dumps(item),
                now,
                now,
            ),
        )


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value).strip()))
    except Exception:
        return None


def load_product_model(app_dir: Path | str, product_id: str) -> dict[str, Any]:
    initialize_database(app_dir)
    conn = connect(app_dir)
    try:
        row = conn.execute("SELECT * FROM products WHERE product_id = ?", (product_id,)).fetchone()
        if not row:
            return {}
        product = json_loads(row["product_json"], {})
        if not isinstance(product, dict):
            product = {}
        product["product_id"] = row["product_id"]
        product["drafts"] = _load_drafts(conn, row["product_id"], _dict(product.get("drafts")))
        product.setdefault("source", {})
        if isinstance(product["source"], dict):
            product["source"]["image_pool"] = _load_media(conn, row["product_id"])
        return product
    finally:
        conn.close()


def _load_drafts(conn: sqlite3.Connection, product_id: str, existing: dict[str, Any]) -> dict[str, Any]:
    drafts = dict(existing)
    for row in conn.execute("SELECT * FROM platform_drafts WHERE product_id = ?", (product_id,)):
        draft = json_loads(row["draft_json"], {})
        if not isinstance(draft, dict):
            draft = {}
        draft.update(
            {
                "status": row["status"],
                "title": row["title"],
                "description": row["description"],
                "category_id": row["category_id"],
                "category_path": row["category_path"],
                "attributes": json_loads(row["attributes_json"], {}),
            }
        )
        drafts[row["platform"]] = draft
    return drafts


def _load_media(conn: sqlite3.Connection, product_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM media_assets WHERE product_id = ? ORDER BY sort_order ASC, id ASC",
        (product_id,),
    ).fetchall()
    items = []
    for row in rows:
        item = json_loads(row["raw_json"], {})
        if not isinstance(item, dict):
            item = {}
        item.update(
            {
                "id": row["asset_id"],
                "url": row["url"],
                "path": row["local_path"],
                "preview_url": row["preview_url"],
                "width": row["width"],
                "height": row["height"],
                "size_label": row["size_label"],
                "usage": row["asset_type"],
                "origin": row["origin"],
                "platforms": json_loads(row["platforms_json"], []),
                "is_main": bool(row["is_main"]),
                "selected": bool(row["selected"]),
                "order": row["sort_order"],
            }
        )
        items.append(item)
    return items


def list_product_records(app_dir: Path | str, limit: int = 500) -> list[dict[str, Any]]:
    initialize_database(app_dir)
    conn = connect(app_dir)
    try:
        rows = conn.execute(
            """
            SELECT p.product_id, p.title, p.source_platform, p.source_url, p.collect_status,
                   p.created_at, p.updated_at, p.product_json,
                   (
                     SELECT preview_url FROM media_assets m
                     WHERE m.product_id = p.product_id
                     ORDER BY m.is_main DESC, m.sort_order ASC, m.id ASC
                     LIMIT 1
                   ) AS main_image
            FROM products p
            ORDER BY p.updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit or 500)),),
        ).fetchall()
    finally:
        conn.close()
    return [_record_from_row(row) for row in rows]


def delete_product_model(app_dir: Path | str, product_id: str) -> bool:
    product_id = str(product_id or "").strip()
    if not product_id:
        return False
    initialize_database(app_dir)
    conn = connect(app_dir)
    try:
        cursor = conn.execute("DELETE FROM products WHERE product_id = ?", (product_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    product = json_loads(row["product_json"], {})
    drafts = _dict(product.get("drafts")) if isinstance(product, dict) else {}
    platforms = [
        platform
        for platform in PLATFORMS
        if isinstance(drafts.get(platform), dict)
        and (drafts[platform].get("enabled") or drafts[platform].get("title") or drafts[platform].get("category_id"))
    ]
    draft_statuses = {
        platform: str(drafts[platform].get("status") or "collected")
        for platform in PLATFORMS
        if isinstance(drafts.get(platform), dict)
    }
    ml_draft = _dict(drafts.get("mercadolibre"))
    return {
        "product_id": row["product_id"],
        "title": row["title"],
        "main_image": row["main_image"] or "",
        "source_platform": row["source_platform"],
        "source_url": row["source_url"],
        "collect_status": row["collect_status"],
        "workflow_status": draft_statuses.get("mercadolibre", "collected"),
        "draft_statuses": draft_statuses,
        "ai_copy_status": "done" if draft_statuses.get("mercadolibre") in {"copy_ready", "images_ready", "ready_to_publish", "published"} else "pending",
        "image_status": "done" if draft_statuses.get("mercadolibre") in {"images_ready", "ready_to_publish", "published"} else "pending",
        "category_status": "done" if ml_draft.get("category_id") else "pending",
        "attributes_status": "done" if isinstance(ml_draft.get("attributes"), dict) and ml_draft.get("attributes") else "pending",
        "pricing_status": "done" if ml_draft.get("price") or (_dict(ml_draft.get("pricing")).get("suggested_price")) else "pending",
        "precheck_status": (_dict(_dict(product.get("publish_preview")).get("mercadolibre")).get("ok", "pending") if isinstance(product, dict) else "pending"),
        "publish_status": ml_draft.get("publish_status") or "not_ready",
        "optimized": draft_statuses.get("mercadolibre") in {"copy_ready", "images_ready", "ready_to_publish", "published"},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "platforms": platforms,
        "product_file_path": f"sqlite://products/{row['product_id']}",
    }


def _category_identity(record: dict[str, Any]) -> str:
    for key in ("category_id", "subject_id", "type_id", "id"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _category_site(record: dict[str, Any], fallback: str = "") -> str:
    return str(record.get("site") or record.get("country") or fallback or "").strip()


def _category_attrs(record: dict[str, Any]) -> dict[str, Any]:
    attrs = _dict(record.get("attributes_cache") or record.get("attributes"))
    required = _list(attrs.get("required"))
    optional = _list(attrs.get("optional"))
    return {"required": required, "optional": optional}


def import_category_cache(app_dir: Path | str, cache: dict[str, Any]) -> int:
    initialize_database(app_dir)
    platform = str(cache.get("platform") or "").strip()
    if not platform:
        return 0
    site = str(cache.get("site") or "").strip()
    updated_at = str(cache.get("updated_at") or utc_now())
    records = [item for item in _list(cache.get("records")) if isinstance(item, dict)]
    conn = connect(app_dir)
    imported = 0
    try:
        for record in records:
            category_id = _category_identity(record)
            if not category_id:
                continue
            row_site = _category_site(record, site)
            attrs = _category_attrs(record)
            conn.execute(
                """
                INSERT INTO category_cache (
                    platform, site, category_id, name_original, name_cn,
                    path_original_json, path_cn_json, keywords_json,
                    attributes_json, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, site, category_id) DO UPDATE SET
                    name_original = excluded.name_original,
                    name_cn = excluded.name_cn,
                    path_original_json = excluded.path_original_json,
                    path_cn_json = excluded.path_cn_json,
                    keywords_json = excluded.keywords_json,
                    attributes_json = excluded.attributes_json,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    platform,
                    row_site,
                    category_id,
                    str(record.get("name_original") or record.get("name") or ""),
                    str(record.get("name_cn") or ""),
                    json_dumps(_list(record.get("path_original"))),
                    json_dumps(_list(record.get("path_cn"))),
                    json_dumps(_list(record.get("keywords"))),
                    json_dumps(attrs),
                    json_dumps(record),
                    updated_at,
                ),
            )
            imported += 1
        conn.commit()
    finally:
        conn.close()
    return imported


def category_cache_status(app_dir: Path | str, platform: str) -> dict[str, Any]:
    initialize_database(app_dir)
    conn = connect(app_dir)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS records, MAX(updated_at) AS updated_at
            FROM category_cache
            WHERE platform = ?
            """,
            (platform,),
        ).fetchone()
    finally:
        conn.close()
    return {
        "storage": "sqlite",
        "platform": platform,
        "records": int(row["records"] or 0) if row else 0,
        "updated_at": (row["updated_at"] if row else "") or "",
    }


def _category_record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    raw = json_loads(row["raw_json"], {})
    record = raw if isinstance(raw, dict) else {}
    record.update(
        {
            "platform": row["platform"],
            "site": row["site"],
            "category_id": row["category_id"],
            "name_original": row["name_original"],
            "name_cn": row["name_cn"],
            "path_original": json_loads(row["path_original_json"], []),
            "path_cn": json_loads(row["path_cn_json"], []),
            "keywords": json_loads(row["keywords_json"], []),
            "attributes_cache": json_loads(row["attributes_json"], {"required": [], "optional": []}),
            "updated_at": row["updated_at"],
        }
    )
    return record


def search_category_records(
    app_dir: Path | str,
    platform: str,
    query: str = "",
    site: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    initialize_database(app_dir)
    conn = connect(app_dir)
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM category_cache
            WHERE platform = ?
            ORDER BY name_cn ASC, name_original ASC
            """,
            (platform,),
        ).fetchall()
    finally:
        conn.close()
    query_text = str(query or "").strip().lower()
    site_text = str(site or "").strip().lower()
    results: list[dict[str, Any]] = []
    for row in rows:
        if site_text and str(row["site"] or "").lower() not in {"", site_text}:
            continue
        record = _category_record_from_row(row)
        haystack = " ".join(
            [
                str(record.get("category_id") or ""),
                str(record.get("subject_id") or ""),
                str(record.get("type_id") or ""),
                str(record.get("name_original") or ""),
                str(record.get("name_cn") or ""),
                " ".join(map(str, _list(record.get("path_original")))),
                " ".join(map(str, _list(record.get("path_cn")))),
                " ".join(map(str, _list(record.get("keywords")))),
            ]
        ).lower()
        if query_text and query_text not in haystack:
            continue
        results.append(record)
        if len(results) >= max(1, int(limit or 20)):
            break
    return results


def find_category_record(app_dir: Path | str, platform: str, category_id: str, site: str = "") -> dict[str, Any] | None:
    initialize_database(app_dir)
    category_id = str(category_id or "").strip()
    site_text = str(site or "").strip()
    conn = connect(app_dir)
    try:
        if site_text:
            row = conn.execute(
                """
                SELECT *
                FROM category_cache
                WHERE platform = ? AND category_id = ? AND site = ?
                LIMIT 1
                """,
                (platform, category_id, site_text),
            ).fetchone()
            if row:
                return _category_record_from_row(row)
        row = conn.execute(
            """
            SELECT *
            FROM category_cache
            WHERE platform = ? AND category_id = ?
            ORDER BY CASE WHEN site = '' THEN 1 ELSE 0 END, site ASC
            LIMIT 1
            """,
            (platform, category_id),
        ).fetchone()
    finally:
        conn.close()
    return _category_record_from_row(row) if row else None

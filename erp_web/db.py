from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from erp_web.marketplace_registry import PLATFORMS

DEFAULT_DB_NAME = "erp.sqlite3"
REQUIRED_TABLES = (
    "store_auth",
    "products",
    "platform_drafts",
    "media_assets",
    "publish_logs",
)


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
                draft_id TEXT PRIMARY KEY,
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
                FOREIGN KEY(product_id) REFERENCES products(product_id) ON DELETE CASCADE
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
        _migrate_platform_drafts_schema(conn)
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


def _platform_values(value: Any) -> list[str]:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        parsed = json_loads(value, [])
        if isinstance(parsed, list):
            raw_items = parsed
        else:
            raw_items = [
                part.strip()
                for part in value.replace("；", "\n").replace(";", "\n").replace(",", "\n").splitlines()
                if part.strip()
            ]
    else:
        raw_items = []
    platforms: list[str] = []
    for item in raw_items:
        platform = str(item or "").strip().lower()
        if platform in PLATFORMS and platform not in platforms:
            platforms.append(platform)
    return platforms


def _draft_platforms(draft: dict[str, Any], primary_platform: Any) -> list[str]:
    platforms: list[str] = []
    target_sites = draft.get("target_sites")
    if isinstance(target_sites, list):
        for target in target_sites:
            target = _dict(target)
            platform = str(target.get("platform") or "").strip().lower()
            if platform in PLATFORMS and platform not in platforms:
                platforms.append(platform)
    if not platforms:
        platforms = _platform_values(draft.get("platforms") or draft.get("platforms_json"))
    primary = str(primary_platform or "").strip().lower()
    if primary in PLATFORMS and primary not in platforms:
        platforms.insert(0, primary)
    return platforms


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


def _migrate_platform_drafts_schema(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(platform_drafts)").fetchall()
    if not columns:
        return
    has_draft_id = any(str(row["name"]) == "draft_id" for row in columns)
    draft_id_is_pk = any(str(row["name"]) == "draft_id" and int(row["pk"] or 0) == 1 for row in columns)
    if has_draft_id and draft_id_is_pk:
        return

    rows = conn.execute("SELECT * FROM platform_drafts").fetchall()
    conn.execute("ALTER TABLE platform_drafts RENAME TO platform_drafts_legacy")
    conn.execute(
        """
        CREATE TABLE platform_drafts (
            draft_id TEXT PRIMARY KEY,
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
            FOREIGN KEY(product_id) REFERENCES products(product_id) ON DELETE CASCADE
        )
        """
    )
    seen: set[str] = set()
    for row in rows:
        draft_json = json_loads(row["draft_json"], {})
        draft = draft_json if isinstance(draft_json, dict) else {}
        draft_id = str(draft.get("draft_id") or draft.get("draftId") or "").strip()
        if not draft_id:
            seed = "|".join([str(row["product_id"]), str(row["platform"]), str(row["site"]), str(row["created_at"])])
            draft_id = f"draft_{hashlib.sha1(seed.encode('utf-8', errors='ignore')).hexdigest()[:16]}"
        while draft_id in seen:
            draft_id = f"draft_{uuid.uuid4().hex[:16]}"
        seen.add(draft_id)
        draft["draft_id"] = draft_id
        conn.execute(
            """
            INSERT INTO platform_drafts (
                draft_id, product_id, platform, site, status, title, description,
                category_id, category_path, attributes_json, price_json, draft_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                row["product_id"],
                row["platform"],
                row["site"],
                row["status"],
                row["title"],
                row["description"],
                row["category_id"],
                row["category_path"],
                row["attributes_json"],
                row["price_json"],
                json_dumps(draft),
                row["created_at"],
                row["updated_at"],
            ),
        )
    conn.execute("DROP TABLE platform_drafts_legacy")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_platform_drafts_product ON platform_drafts(product_id)")


def draft_identity(product_id: str, platform: str, draft: dict[str, Any] | None = None) -> str:
    draft = _dict(draft)
    existing = str(draft.get("draft_id") or draft.get("draftId") or "").strip()
    if existing:
        return _slug(existing)
    prefix = _slug(f"{product_id}_{platform}")[:48]
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _draft_status(draft: dict[str, Any]) -> str:
    return str(draft.get("status") or draft.get("publish_status") or "claimed")


def _draft_should_persist(draft: dict[str, Any]) -> bool:
    if str(draft.get("draft_id") or draft.get("draftId") or "").strip():
        return True
    status = str(draft.get("status") or draft.get("publish_status") or "").strip().lower()
    if status in {"copy_ready", "images_ready", "ready_to_publish", "published", "failed", "not_ready"}:
        return True
    for key in ("title", "description", "category_id", "copy_generated_at"):
        if str(draft.get(key) or "").strip():
            return True
    for key in ("attributes", "validation_errors", "images"):
        value = draft.get(key)
        if isinstance(value, (dict, list)) and bool(value):
            return True
    return False


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
        if not _draft_should_persist(draft):
            continue
        draft_id = draft_identity(product_id, platform, draft)
        draft["draft_id"] = draft_id
        draft["platform"] = platform
        draft["platforms"] = _draft_platforms(draft, platform)
        site = str(draft.get("site") or draft.get("site_id") or "").strip()
        price_json = {
            key: draft.get(key)
            for key in ("price", "sale_price", "currency", "net_profit", "pricing")
            if draft.get(key) not in (None, "")
        }
        conn.execute(
            """
            INSERT INTO platform_drafts (
                draft_id, product_id, platform, site, status, title, description, category_id,
                category_path, attributes_json, price_json, draft_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(draft_id) DO UPDATE SET
                product_id=excluded.product_id,
                platform=excluded.platform,
                site=excluded.site,
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
                draft_id,
                product_id,
                platform,
                site,
                _draft_status(draft),
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
    pool = _image_pool(product)
    asset_ids = [
        str(item.get("id") or f"image_{index + 1}").strip() or f"image_{index + 1}"
        for index, item in enumerate(pool)
    ]
    if asset_ids:
        placeholders = ",".join("?" for _ in asset_ids)
        conn.execute(
            f"DELETE FROM media_assets WHERE product_id = ? AND asset_id NOT IN ({placeholders})",
            (product_id, *asset_ids),
        )
    else:
        conn.execute("DELETE FROM media_assets WHERE product_id = ?", (product_id,))
    for index, item in enumerate(pool):
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
    seen_platforms: set[str] = set()
    for row in conn.execute(
        """
        SELECT * FROM platform_drafts
        WHERE product_id = ?
        ORDER BY CASE WHEN status = 'published' THEN 1 ELSE 0 END ASC, updated_at DESC
        """,
        (product_id,),
    ):
        draft = json_loads(row["draft_json"], {})
        if not isinstance(draft, dict):
            draft = {}
        draft_id = row["draft_id"] if "draft_id" in row.keys() else str(draft.get("draft_id") or "")
        platform = str(row["platform"])
        draft.update(
            {
                "draft_id": draft_id,
                "platform": platform,
                "platforms": _draft_platforms(draft, platform),
                "status": row["status"],
                "title": row["title"],
                "description": row["description"],
                "category_id": row["category_id"],
                "category_path": row["category_path"],
                "attributes": json_loads(row["attributes_json"], {}),
            }
        )
        if platform not in seen_platforms:
            drafts[platform] = draft
            seen_platforms.add(platform)
    return drafts


def _draft_from_row(row: sqlite3.Row) -> dict[str, Any]:
    draft = json_loads(row["draft_json"], {})
    if not isinstance(draft, dict):
        draft = {}
    draft.update(
        {
            "draft_id": row["draft_id"],
            "product_id": row["product_id"],
            "source_product_id": draft.get("source_product_id") or row["product_id"],
            "platform": row["platform"],
            "platforms": _draft_platforms(draft, row["platform"]),
            "site": row["site"],
            "status": row["status"],
            "title": row["title"],
            "description": row["description"],
            "category_id": row["category_id"],
            "category_path": row["category_path"],
            "attributes": json_loads(row["attributes_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )
    return draft


def load_draft_model(app_dir: Path | str, draft_id: str) -> dict[str, Any]:
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return {}
    initialize_database(app_dir)
    conn = connect(app_dir)
    try:
        row = conn.execute("SELECT * FROM platform_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        return _draft_from_row(row) if row else {}
    finally:
        conn.close()


def load_product_for_draft(app_dir: Path | str, draft_id: str) -> dict[str, Any]:
    draft = load_draft_model(app_dir, draft_id)
    if not draft:
        return {}
    product = load_product_model(app_dir, str(draft.get("product_id") or ""))
    if not product:
        return {}
    platform = str(draft.get("platform") or "").strip()
    if platform in PLATFORMS:
        product.setdefault("drafts", {})
        if isinstance(product["drafts"], dict):
            product["drafts"][platform] = draft
        product["current_draft_id"] = draft.get("draft_id")
        product["current_draft_platform"] = platform
    return product


def delete_draft_model(app_dir: Path | str, draft_id: str) -> bool:
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return False
    initialize_database(app_dir)
    conn = connect(app_dir)
    try:
        row = conn.execute("SELECT product_id, platform FROM platform_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        cursor = conn.execute("DELETE FROM platform_drafts WHERE draft_id = ?", (draft_id,))
        if cursor.rowcount > 0 and row:
            product_row = conn.execute("SELECT product_json FROM products WHERE product_id = ?", (row["product_id"],)).fetchone()
            product = json_loads(product_row["product_json"], {}) if product_row else {}
            if isinstance(product, dict) and isinstance(product.get("drafts"), dict):
                product["drafts"].pop(str(row["platform"]), None)
                conn.execute(
                    "UPDATE products SET product_json = ?, updated_at = ? WHERE product_id = ?",
                    (json_dumps(product), utc_now(), row["product_id"]),
                )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def upsert_draft_model(app_dir: Path | str, product_id: str, platform: str, draft: dict[str, Any]) -> str:
    initialize_database(app_dir)
    now = utc_now()
    product_id = str(product_id or "").strip()
    platform = str(platform or "").strip().lower()
    draft = dict(_dict(draft))
    draft_platform = str(draft.get("platform") or "").strip().lower()
    if draft_platform in PLATFORMS:
        platform = draft_platform
    if not product_id or platform not in PLATFORMS:
        return ""
    draft_id = draft_identity(product_id, platform, draft)
    draft["draft_id"] = draft_id
    draft["platform"] = platform
    draft["platforms"] = _draft_platforms(draft, platform)
    site = str(draft.get("site") or draft.get("site_id") or "").strip()
    price_json = {
        key: draft.get(key)
        for key in ("price", "sale_price", "currency", "net_profit", "pricing")
        if draft.get(key) not in (None, "")
    }
    conn = connect(app_dir)
    try:
        conn.execute(
            """
            INSERT INTO platform_drafts (
                draft_id, product_id, platform, site, status, title, description,
                category_id, category_path, attributes_json, price_json, draft_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(draft_id) DO UPDATE SET
                product_id=excluded.product_id,
                platform=excluded.platform,
                site=excluded.site,
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
                draft_id,
                product_id,
                platform,
                site,
                _draft_status(draft),
                str(draft.get("title") or ""),
                str(draft.get("description") or ""),
                str(draft.get("category_id") or ""),
                str(draft.get("category_path") or ""),
                json_dumps(draft.get("attributes") or {}),
                json_dumps(price_json),
                json_dumps(draft),
                str(draft.get("created_at") or now),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return draft_id


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
        records: list[dict[str, Any]] = []
        for row in rows:
            product = json_loads(row["product_json"], {})
            existing_drafts = _dict(product.get("drafts")) if isinstance(product, dict) else {}
            records.append(_record_from_row(row, _load_drafts(conn, row["product_id"], existing_drafts)))
        return records
    finally:
        conn.close()


def list_draft_records(app_dir: Path | str, scope: str = "active", limit: int = 500) -> list[dict[str, Any]]:
    initialize_database(app_dir)
    scope = str(scope or "active").strip().lower()
    conn = connect(app_dir)
    try:
        rows = conn.execute(
            """
            SELECT d.*, p.title AS product_title, p.source_platform, p.source_url, p.product_json,
                   (
                     SELECT preview_url FROM media_assets m
                     WHERE m.product_id = d.product_id
                     ORDER BY m.is_main DESC, m.sort_order ASC, m.id ASC
                     LIMIT 1
                   ) AS main_image
            FROM platform_drafts d
            JOIN products p ON p.product_id = d.product_id
            ORDER BY d.created_at DESC, d.rowid DESC
            LIMIT ?
            """,
            (max(1, int(limit or 500)),),
        ).fetchall()
    finally:
        conn.close()
    records = [_draft_record_from_row(row) for row in rows]
    if scope == "published":
        return [item for item in records if str(item.get("status") or "").lower() == "published"]
    if scope == "all":
        return records
    return [item for item in records if str(item.get("status") or "").lower() != "published"]


def _draft_record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    product = json_loads(row["product_json"], {})
    draft = _draft_from_row(row)
    price_json = json_loads(row["price_json"], {})
    pricing = _dict(draft.get("pricing"))
    status = str(draft.get("status") or draft.get("publish_status") or row["status"] or "claimed")
    target_sites = draft.get("target_sites") if isinstance(draft.get("target_sites"), list) else [{"platform": row["platform"], "site": row["site"], "language": draft.get("language") or "", "currency": draft.get("currency") or ""}]
    return {
        "draft_id": row["draft_id"],
        "product_id": row["product_id"],
        "source_product_id": draft.get("source_product_id") or row["product_id"],
        "platform": row["platform"],
        "platforms": _draft_platforms({**draft, "target_sites": target_sites}, row["platform"]),
        "target_sites": target_sites,
        "site": row["site"],
        "language": str(draft.get("language") or ""),
        "status": status,
        "title": row["title"] or draft.get("title") or "",
        "product_title": row["product_title"] or _dict(product).get("name") or "",
        "main_image": row["main_image"] or "",
        "source_platform": row["source_platform"] or "",
        "source_url": row["source_url"] or "",
        "category_id": row["category_id"] or "",
        "category_path": row["category_path"] or "",
        "price": str(draft.get("price") or _dict(price_json).get("price") or pricing.get("suggested_price") or ""),
        "publish_status": str(draft.get("publish_status") or ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "product_file_path": f"sqlite://products/{row['product_id']}",
        "raw": draft,
    }


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


def _record_from_row(row: sqlite3.Row, loaded_drafts: dict[str, Any] | None = None) -> dict[str, Any]:
    product = json_loads(row["product_json"], {})
    drafts = loaded_drafts if isinstance(loaded_drafts, dict) else _dict(product.get("drafts")) if isinstance(product, dict) else {}
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

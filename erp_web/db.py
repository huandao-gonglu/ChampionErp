from __future__ import annotations

"""SQLite persistence layer.

``ErpDatabase`` owns the schema, the connection settings and every read/write
path.  The schema is versioned via ``PRAGMA user_version``; any mismatch drops
all known tables and rebuilds them (the project is unreleased — zero legacy
compatibility by decree).  The only seed path is the UPC pool: purchased UPC
codes in ``upc_pool.json`` are imported once when the table is empty.
"""

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from erp_web.marketplace_registry import PLATFORMS

DEFAULT_DB_NAME = "erp.sqlite3"
SCHEMA_VERSION = 4

REQUIRED_TABLES = (
    "store_auth",
    "products",
    "platform_drafts",
    "media_assets",
    "publish_logs",
    "upc_pool",
    "order_notifications",
    "publish_jobs",
    "research_runs",
    "research_candidates",
    "ai_sessions",
    "exchange_rates",
)

# Research run statuses that never change again (mirrors product_research_service).
_TERMINAL_RESEARCH_STATUSES = ("completed", "failed")

# Tables from earlier schema generations that must be dropped on rebuild.
_LEGACY_TABLES = (
    "category_cache",
    "draft_id_aliases",
    "platform_drafts_legacy",
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS store_auth (
    platform TEXT PRIMARY KEY,
    credentials_json TEXT NOT NULL DEFAULT '{}',
    auth_status TEXT NOT NULL DEFAULT '',
    auth_detail_json TEXT NOT NULL DEFAULT '{}',
    checked_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
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
    ts TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    product_id TEXT NOT NULL DEFAULT '',
    draft_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    artifacts_path TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS upc_pool (
    upc TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'free',
    product_id TEXT,
    assigned_at TEXT
);

CREATE TABLE IF NOT EXISTS order_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL DEFAULT '',
    resource TEXT NOT NULL DEFAULT '',
    order_id TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    received_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS publish_jobs (
    job_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL DEFAULT '',
    draft_id TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS research_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0,
    data_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_sessions (
    session_id TEXT PRIMARY KEY,
    day TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    last_seq INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    pair TEXT PRIMARY KEY,
    rate REAL NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_source_url ON products(source_url);
CREATE INDEX IF NOT EXISTS idx_platform_drafts_product ON platform_drafts(product_id);
CREATE INDEX IF NOT EXISTS idx_media_assets_product ON media_assets(product_id);
CREATE INDEX IF NOT EXISTS idx_publish_logs_product ON publish_logs(product_id, platform);
CREATE INDEX IF NOT EXISTS idx_upc_pool_status ON upc_pool(status);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status);
CREATE INDEX IF NOT EXISTS idx_research_runs_updated ON research_runs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_candidates_run ON research_candidates(run_id, rank);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_updated ON ai_sessions(updated_at DESC);
"""


# ---------------------------------------------------------------------------
# Pure helpers (no database access)
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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


def _slug(value: str) -> str:
    chars = []
    for ch in str(value):
        chars.append(ch if ch.isalnum() or ch in "._-" else "_")
    slug = "".join(chars).strip("._-")
    return slug[:80] or "product"


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


def new_draft_id() -> str:
    """草稿 ID 无语义：平台信息只存 platform_drafts.platform 列。"""
    return "d" + uuid.uuid4().hex[:12]


def draft_identity(draft: dict[str, Any] | None = None) -> str:
    draft = _dict(draft)
    existing = str(draft.get("draft_id") or draft.get("draftId") or "").strip()
    if existing:
        return _slug(existing)
    return new_draft_id()


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


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value).strip()))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ErpDatabase
# ---------------------------------------------------------------------------

class ErpDatabase:
    """Single owner of the SQLite store (schema, connections, all queries)."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._write_lock = threading.Lock()
        self._initialize_schema()

    # -- connection & schema ------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # timeout + busy_timeout + WAL: the ThreadingHTTPServer request threads
        # and the publishing bus worker pool write concurrently; without these
        # settings SQLite raises "database is locked" under load.
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            if version != SCHEMA_VERSION:
                # 零兼容：版本不匹配直接 drop 全部已知表重建。
                for table in REQUIRED_TABLES + _LEGACY_TABLES:
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.executescript(_SCHEMA_SQL)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            else:
                conn.executescript(_SCHEMA_SQL)
            conn.commit()
        self._maybe_seed_upc_pool()

    def _maybe_seed_upc_pool(self) -> None:
        """One-time import of purchased UPC codes from ``upc_pool.json``.

        The file holds paid-for UPC codes (a real asset).  When the table is
        empty and the file exists next to the database, import values and
        their used/free state once.  This is an import feature, not legacy
        data compatibility.
        """
        seed_path = self.db_path.parent / "upc_pool.json"
        if not seed_path.exists():
            return
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM upc_pool").fetchone()[0]
            if count:
                return
            try:
                pool = json.loads(seed_path.read_text(encoding="utf-8"))
            except Exception:
                return
            values = [str(item or "").strip() for item in _list(_dict(pool).get("values")) if str(item or "").strip()]
            used = {str(item or "").strip() for item in _list(_dict(pool).get("used")) if str(item or "").strip()}
            now = utc_now()
            for value in values:
                status = "used" if value in used else "free"
                conn.execute(
                    "INSERT OR IGNORE INTO upc_pool (upc, status, product_id, assigned_at) VALUES (?, ?, '', ?)",
                    (value, status, now if status == "used" else ""),
                )
            for value in sorted(used - set(values)):
                conn.execute(
                    "INSERT OR IGNORE INTO upc_pool (upc, status, product_id, assigned_at) VALUES (?, 'used', '', ?)",
                    (value, now),
                )
            conn.commit()

    # -- products / drafts / media ------------------------------------------

    def upsert_product_model(self, product: dict[str, Any]) -> str:
        now = utc_now()
        product = dict(_dict(product))
        source = _source(product)
        product_id = product_identity(product)
        product["product_id"] = product_id
        with self._connect() as conn:
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
            self._upsert_drafts(conn, product_id, product, now)
            self._upsert_media(conn, product_id, product, now)
            conn.commit()
        return product_id

    def _upsert_drafts(self, conn: sqlite3.Connection, product_id: str, product: dict[str, Any], now: str) -> None:
        drafts = _dict(product.get("drafts"))
        for platform, draft_raw in drafts.items():
            draft = _dict(draft_raw)
            if platform not in PLATFORMS or not draft:
                continue
            if not _draft_should_persist(draft):
                continue
            draft_id = draft_identity(draft)
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

    def _upsert_media(self, conn: sqlite3.Connection, product_id: str, product: dict[str, Any], now: str) -> None:
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

    def load_product_model(self, product_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM products WHERE product_id = ?", (product_id,)).fetchone()
            if not row:
                return {}
            product = json_loads(row["product_json"], {})
            if not isinstance(product, dict):
                product = {}
            product["product_id"] = row["product_id"]
            product["drafts"] = self._load_drafts(conn, row["product_id"], _dict(product.get("drafts")))
            product.setdefault("source", {})
            if isinstance(product["source"], dict):
                product["source"]["image_pool"] = self._load_media(conn, row["product_id"])
            return product

    def _load_drafts(self, conn: sqlite3.Connection, product_id: str, existing: dict[str, Any]) -> dict[str, Any]:
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

    @staticmethod
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

    def load_draft_model(self, draft_id: str) -> dict[str, Any]:
        draft_id = _slug(str(draft_id or "").strip()) if str(draft_id or "").strip() else ""
        if not draft_id:
            return {}
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM platform_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
            return self._draft_from_row(row) if row else {}

    def load_product_for_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.load_draft_model(draft_id)
        if not draft:
            return {}
        product = self.load_product_model(str(draft.get("product_id") or ""))
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

    def delete_draft_model(self, draft_id: str) -> bool:
        draft_id = str(draft_id or "").strip()
        if not draft_id:
            return False
        with self._connect() as conn:
            draft_id = _slug(draft_id)
            row = conn.execute(
                "SELECT product_id, platform FROM platform_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
            cursor = conn.execute("DELETE FROM platform_drafts WHERE draft_id = ?", (draft_id,))
            if cursor.rowcount > 0 and row:
                product_row = conn.execute(
                    "SELECT product_json FROM products WHERE product_id = ?", (row["product_id"],)
                ).fetchone()
                product = json_loads(product_row["product_json"], {}) if product_row else {}
                if isinstance(product, dict) and isinstance(product.get("drafts"), dict):
                    product["drafts"].pop(str(row["platform"]), None)
                    conn.execute(
                        "UPDATE products SET product_json = ?, updated_at = ? WHERE product_id = ?",
                        (json_dumps(product), utc_now(), row["product_id"]),
                    )
            conn.commit()
            return cursor.rowcount > 0

    def upsert_draft_model(self, product_id: str, platform: str, draft: dict[str, Any]) -> str:
        now = utc_now()
        product_id = str(product_id or "").strip()
        platform = str(platform or "").strip().lower()
        draft = dict(_dict(draft))
        draft_platform = str(draft.get("platform") or "").strip().lower()
        if draft_platform in PLATFORMS:
            platform = draft_platform
        if not product_id or platform not in PLATFORMS:
            return ""
        with self._connect() as conn:
            draft_id = draft_identity(draft)
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
        return draft_id

    def _load_media(self, conn: sqlite3.Connection, product_id: str) -> list[dict[str, Any]]:
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

    def list_product_records(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
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
                records.append(_record_from_row(row, self._load_drafts(conn, row["product_id"], existing_drafts)))
            return records

    def list_draft_records(self, scope: str = "active", limit: int = 500) -> list[dict[str, Any]]:
        scope = str(scope or "active").strip().lower()
        with self._connect() as conn:
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
        records = [self._draft_record_from_row(row) for row in rows]
        if scope == "published":
            return [item for item in records if str(item.get("status") or "").lower() == "published"]
        if scope == "all":
            return records
        return [item for item in records if str(item.get("status") or "").lower() != "published"]

    @classmethod
    def _draft_record_from_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        product = json_loads(row["product_json"], {})
        draft = cls._draft_from_row(row)
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

    def delete_product_model(self, product_id: str) -> bool:
        product_id = str(product_id or "").strip()
        if not product_id:
            return False
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM products WHERE product_id = ?", (product_id,))
            conn.commit()
            return cursor.rowcount > 0

    # -- store_auth ----------------------------------------------------------

    def get_store_auth(self, platform: str) -> dict[str, Any]:
        platform = str(platform or "").strip().lower()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM store_auth WHERE platform = ?", (platform,)).fetchone()
        if not row:
            return {"platform": platform, "credentials": {}, "auth_status": "", "auth_detail": {}, "checked_at": ""}
        return {
            "platform": platform,
            "credentials": _dict(json_loads(row["credentials_json"], {})),
            "auth_status": str(row["auth_status"] or ""),
            "auth_detail": _dict(json_loads(row["auth_detail_json"], {})),
            "checked_at": str(row["checked_at"] or ""),
        }

    def list_store_auth(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM store_auth").fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            platform = str(row["platform"] or "")
            result[platform] = {
                "platform": platform,
                "credentials": _dict(json_loads(row["credentials_json"], {})),
                "auth_status": str(row["auth_status"] or ""),
                "auth_detail": _dict(json_loads(row["auth_detail_json"], {})),
                "checked_at": str(row["checked_at"] or ""),
            }
        return result

    def update_store_auth(
        self,
        platform: str,
        credentials: dict[str, Any] | None = None,
        replace_credentials: bool = False,
        auth_status: str | None = None,
        auth_detail: dict[str, Any] | None = None,
        checked_at: str | None = None,
    ) -> None:
        """Persist credentials / auth state for one platform.

        ``credentials`` merge semantics: empty incoming values never clobber a
        stored secret (mirrors the old preserve_empty_sensitive file merge)
        unless ``replace_credentials`` is set, in which case the stored dict is
        replaced by the non-empty incoming fields (used after a successful
        OAuth code exchange to drop the one-shot code_verifier).
        """
        platform = str(platform or "").strip().lower()
        if not platform:
            return
        with self._write_lock:
            existing = self.get_store_auth(platform)
            stored_credentials = dict(existing["credentials"])
            if credentials is not None:
                incoming = {
                    str(key): value
                    for key, value in _dict(credentials).items()
                    if str(value if value is not None else "").strip()
                }
                if replace_credentials:
                    stored_credentials = incoming
                else:
                    stored_credentials.update(incoming)
            stored_detail = dict(existing["auth_detail"])
            if auth_detail is not None:
                for key, value in _dict(auth_detail).items():
                    stored_detail[str(key)] = "" if value is None else value
            status = existing["auth_status"] if auth_status is None else str(auth_status)
            checked = existing["checked_at"] if checked_at is None else str(checked_at)
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO store_auth (platform, credentials_json, auth_status, auth_detail_json, checked_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform) DO UPDATE SET
                        credentials_json=excluded.credentials_json,
                        auth_status=excluded.auth_status,
                        auth_detail_json=excluded.auth_detail_json,
                        checked_at=excluded.checked_at,
                        updated_at=excluded.updated_at
                    """,
                    (platform, json_dumps(stored_credentials), status, json_dumps(stored_detail), checked, utc_now()),
                )
                conn.commit()

    # -- publish_logs ----------------------------------------------------------

    def insert_publish_log(self, entry: dict[str, Any]) -> int:
        entry = _dict(entry)
        ts = str(entry.get("time") or entry.get("finished_at") or entry.get("checked_at") or "") or utc_now()
        artifacts_path = str(entry.get("response_body_path") or entry.get("request_payload_path") or "")
        message = str(entry.get("error_message") or entry.get("error") or entry.get("message") or "")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO publish_logs (ts, platform, product_id, draft_id, status, stage, message, artifacts_path, detail_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    str(entry.get("platform") or ""),
                    str(entry.get("product_id") or ""),
                    str(entry.get("draft_id") or ""),
                    str(entry.get("status") or ""),
                    str(entry.get("stage") or entry.get("test_type") or ""),
                    message,
                    artifacts_path,
                    json_dumps(entry),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def list_publish_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publish_logs ORDER BY id DESC LIMIT ?",
                (max(1, int(limit or 200)),),
            ).fetchall()
        entries: list[dict[str, Any]] = []
        for row in rows:
            entry = json_loads(row["detail_json"], {})
            if not isinstance(entry, dict):
                entry = {}
            entry.setdefault("platform", row["platform"])
            entry.setdefault("product_id", row["product_id"])
            entry.setdefault("draft_id", row["draft_id"])
            entry.setdefault("status", row["status"])
            entry.setdefault("time", row["ts"])
            entry["log_id"] = row["id"]
            entries.append(entry)
        return entries

    def publish_log_exists(self, job_id: str, platform: str) -> bool:
        job_id = str(job_id or "")
        platform = str(platform or "")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT detail_json FROM publish_logs WHERE platform = ? ORDER BY id DESC",
                (platform,),
            ).fetchall()
        for row in rows:
            entry = json_loads(row["detail_json"], {})
            if isinstance(entry, dict) and str(entry.get("job_id") or "") == job_id:
                return True
        return False

    # -- upc_pool ---------------------------------------------------------------

    def import_upcs(self, values: list[Any]) -> int:
        """INSERT OR IGNORE new UPC codes as free; returns how many were added."""
        added = 0
        with self._connect() as conn:
            for raw in values or []:
                value = str(raw or "").strip()
                if not value:
                    continue
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO upc_pool (upc, status, product_id, assigned_at) VALUES (?, 'free', '', '')",
                    (value,),
                )
                added += cursor.rowcount if cursor.rowcount > 0 else 0
            conn.commit()
        return added

    def assign_upc(self, product_id: str = "") -> str:
        """Atomically claim one free UPC (claim first, then use — the claim is
        persisted before the caller writes it into any draft)."""
        # Table never seeded yet? attempt the one-time import from file first
        # (no-op when the table already has rows or the file is absent).
        self._maybe_seed_upc_pool()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT upc FROM upc_pool WHERE status = 'free' ORDER BY upc LIMIT 1"
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return ""
            upc = str(row["upc"])
            cursor = conn.execute(
                "UPDATE upc_pool SET status = 'used', product_id = ?, assigned_at = ? WHERE upc = ? AND status = 'free'",
                (str(product_id or ""), utc_now(), upc),
            )
            if cursor.rowcount != 1:
                conn.execute("ROLLBACK")
                return ""
            conn.commit()
            return upc

    def upc_pool_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM upc_pool").fetchone()[0] or 0)
            free = int(conn.execute("SELECT COUNT(*) FROM upc_pool WHERE status = 'free'").fetchone()[0] or 0)
        return {"total": total, "free": free, "used": total - free}

    # -- order_notifications ------------------------------------------------------

    def insert_order_notification(self, notification: dict[str, Any]) -> int:
        notification = _dict(notification)
        received_at = str(notification.get("received_at") or "") or utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO order_notifications (topic, resource, order_id, raw_json, received_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(notification.get("topic") or ""),
                    str(notification.get("resource") or ""),
                    str(notification.get("order_id") or ""),
                    json_dumps(notification),
                    received_at,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def list_order_notifications(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM order_notifications ORDER BY id DESC LIMIT ?",
                (max(1, int(limit or 200)),),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = json_loads(row["raw_json"], {})
            if not isinstance(item, dict):
                item = {}
            item.setdefault("topic", row["topic"])
            item.setdefault("resource", row["resource"])
            item.setdefault("order_id", row["order_id"])
            item.setdefault("received_at", row["received_at"])
            items.append(item)
        return items

    # -- publish_jobs ------------------------------------------------------------

    def save_publish_job(self, state: dict[str, Any]) -> None:
        state = _dict(state)
        job_id = str(state.get("job_id") or "").strip()
        if not job_id:
            return
        product = _dict(state.get("product"))
        platforms = _dict(state.get("platforms"))
        platform_items = [item for item in platforms.values() if isinstance(item, dict)]
        stage = ""
        error = ""
        attempts = 0
        for item in platform_items:
            stage = str(item.get("stage") or stage)
            if not error:
                error = str(item.get("error") or "")
            attempts = max(attempts, int(item.get("attempts") or 0))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO publish_jobs (
                    job_id, product_id, draft_id, platform, status, stage, attempts,
                    error, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    product_id=excluded.product_id,
                    draft_id=excluded.draft_id,
                    platform=excluded.platform,
                    status=excluded.status,
                    stage=excluded.stage,
                    attempts=excluded.attempts,
                    error=excluded.error,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    str(product.get("product_id") or ""),
                    str(product.get("current_draft_id") or ""),
                    ",".join(sorted(str(key) for key in platforms)),
                    str(state.get("status") or ""),
                    stage,
                    attempts,
                    error,
                    json_dumps(state),
                    str(state.get("created_at") or "") or utc_now(),
                    str(state.get("updated_at") or "") or utc_now(),
                ),
            )
            conn.commit()

    def load_publish_job(self, job_id: str) -> dict[str, Any]:
        job_id = str(job_id or "").strip()
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM publish_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return {}
        state = json_loads(row["payload_json"], {})
        return state if isinstance(state, dict) else {}

    def list_pending_publish_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM publish_jobs WHERE status IN ('pending', 'queued', 'running', 'retrying') ORDER BY created_at ASC",
            ).fetchall()
        states: list[dict[str, Any]] = []
        for row in rows:
            state = json_loads(row["payload_json"], {})
            if isinstance(state, dict):
                states.append(state)
        return states

    # -- research_runs / research_candidates -----------------------------------

    def save_research_run(
        self,
        run_id: str,
        *,
        status: str,
        method: str,
        params: dict[str, Any],
        error: str = "",
        created_at: str = "",
        updated_at: str = "",
        items: list[dict[str, Any]] | None = None,
    ) -> None:
        """Upsert one research run; ``items`` (when given) replaces its candidates."""
        run_id = str(run_id or "").strip()
        if not run_id:
            return
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_runs (run_id, status, method, params_json, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    method=excluded.method,
                    params_json=excluded.params_json,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    str(status or ""),
                    str(method or ""),
                    json_dumps(params),
                    str(error or ""),
                    str(created_at or "") or now,
                    str(updated_at or "") or now,
                ),
            )
            if items is not None:
                conn.execute("DELETE FROM research_candidates WHERE run_id = ?", (run_id,))
                for index, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    rank = _int_or_none(item.get("rank"))
                    conn.execute(
                        "INSERT INTO research_candidates (run_id, rank, data_json) VALUES (?, ?, ?)",
                        (run_id, rank if rank is not None else index + 1, json_dumps(item)),
                    )
            conn.commit()

    def load_research_run(self, run_id: str) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id:
            return {}
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return {}
            item_rows = conn.execute(
                "SELECT data_json FROM research_candidates WHERE run_id = ? ORDER BY rank ASC, id ASC",
                (run_id,),
            ).fetchall()
        return {
            "run_id": run_id,
            "status": str(row["status"] or ""),
            "method": str(row["method"] or ""),
            "params": _dict(json_loads(row["params_json"], {})),
            "error": str(row["error"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "items": [item for item in (json_loads(item_row["data_json"], {}) for item_row in item_rows) if isinstance(item, dict)],
        }

    def list_research_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Newest-first run rows (without candidate payloads)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, status, method, error, created_at, updated_at FROM research_runs ORDER BY created_at DESC, run_id DESC LIMIT ?",
                (max(1, int(limit or 100)),),
            ).fetchall()
        return [
            {
                "run_id": str(row["run_id"] or ""),
                "status": str(row["status"] or ""),
                "method": str(row["method"] or ""),
                "error": str(row["error"] or ""),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        ]

    def mark_interrupted_research_runs_failed(self, error: str) -> int:
        """Flag every non-terminal run as failed (backend restart); keep candidates."""
        placeholders = ",".join("?" for _ in _TERMINAL_RESEARCH_STATUSES)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE research_runs SET status = 'failed', error = ?, updated_at = ? WHERE status NOT IN ({placeholders})",
                (str(error or ""), utc_now(), *_TERMINAL_RESEARCH_STATUSES),
            )
            conn.commit()
            return int(cursor.rowcount or 0)

    # -- ai_sessions ------------------------------------------------------------

    def upsert_ai_session(self, session_id: str, *, day: str, status: str, last_seq: int, updated_at: str) -> None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_sessions (session_id, day, status, last_seq, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    day=excluded.day,
                    status=excluded.status,
                    last_seq=excluded.last_seq,
                    updated_at=excluded.updated_at
                """,
                (session_id, str(day or ""), str(status or ""), int(last_seq or 0), str(updated_at or "") or utc_now()),
            )
            conn.commit()

    def get_ai_session(self, session_id: str) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return {}
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ai_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return {}
        return {
            "session_id": session_id,
            "day": str(row["day"] or ""),
            "status": str(row["status"] or ""),
            "last_seq": int(row["last_seq"] or 0),
            "updated_at": str(row["updated_at"] or ""),
        }

    def list_ai_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_sessions ORDER BY updated_at DESC, session_id DESC LIMIT ?",
                (max(1, int(limit or 50)),),
            ).fetchall()
        return [
            {
                "session_id": str(row["session_id"] or ""),
                "day": str(row["day"] or ""),
                "status": str(row["status"] or ""),
                "last_seq": int(row["last_seq"] or 0),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        ]

    # -- exchange_rates -----------------------------------------------------------

    def save_exchange_rates(self, rates: dict[str, float], fetched_at: str) -> None:
        """Replace the stored snapshot: one row per currency pair."""
        fetched = str(fetched_at or "") or utc_now()
        with self._connect() as conn:
            conn.execute("DELETE FROM exchange_rates")
            for pair, rate in _dict(rates).items():
                pair_key = str(pair or "").strip()
                try:
                    rate_value = float(rate)
                except (TypeError, ValueError):
                    continue
                if not pair_key or rate_value <= 0:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO exchange_rates (pair, rate, fetched_at) VALUES (?, ?, ?)",
                    (pair_key, rate_value, fetched),
                )
            conn.commit()

    def load_exchange_rates(self) -> dict[str, Any]:
        """Return ``{"rates": {pair: rate}, "fetched_at": str}`` (empty when unset)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM exchange_rates").fetchall()
        rates: dict[str, float] = {}
        fetched_at = ""
        for row in rows:
            rates[str(row["pair"])] = float(row["rate"] or 0)
            fetched_at = max(fetched_at, str(row["fetched_at"] or ""))
        return {"rates": rates, "fetched_at": fetched_at}


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

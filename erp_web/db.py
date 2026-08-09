from __future__ import annotations

"""SQLite persistence layer.

``ErpDatabase`` owns the schema, the connection settings and every read/write
path.  The schema is versioned via ``PRAGMA user_version``. Only an empty
database or the current complete schema is accepted; old, partial, unknown and
future schemas are rejected before any connection setting mutates the file.
The only seed path is the UPC pool: purchased UPC codes in ``upc_pool.json``
are imported once when the table is empty.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from erp_web.marketplace_registry import PLATFORMS
from erp_web.product_model.merge_model import (
    normalize_platform_draft,
    normalize_product_model,
    validate_platform_draft_root_fields,
    validate_product_root_fields,
)

DEFAULT_DB_NAME = "erp.sqlite3"
SCHEMA_VERSION = 5

REQUIRED_TABLES = (
    "store_auth",
    "runtime_secrets",
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

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS store_auth (
    platform TEXT PRIMARY KEY,
    credentials_json TEXT NOT NULL DEFAULT '{}',
    auth_status TEXT NOT NULL DEFAULT '',
    auth_detail_json TEXT NOT NULL DEFAULT '{}',
    checked_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runtime_secrets (
    namespace TEXT NOT NULL,
    secret_path TEXT NOT NULL,
    secret_json TEXT NOT NULL DEFAULT '""',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(namespace, secret_path)
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

_CURRENT_PLATFORM_DRAFT_COLUMNS = frozenset(
    {
        "draft_id",
        "product_id",
        "platform",
        "site",
        "status",
        "draft_json",
        "created_at",
        "updated_at",
    }
)


# ---------------------------------------------------------------------------
# Pure helpers (no database access)
# ---------------------------------------------------------------------------

def _execute_schema_statements(conn: sqlite3.Connection) -> None:
    """Execute schema DDL one statement at a time in the caller's transaction."""
    if not conn.in_transaction:
        raise RuntimeError("schema DDL 必须在显式事务内执行")
    for statement in _SCHEMA_SQL.split(";"):
        sql = statement.strip()
        if sql:
            conn.execute(sql)


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
    source = _source(product)
    existing = str(product.get("product_id") or "").strip()
    if existing:
        return _slug(existing)
    seed = "|".join(
        [
            str(source.get("source_url") or "").strip(),
            str(source.get("title") or "").strip(),
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


def _without_publish_logs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_publish_logs(item)
            for key, item in value.items()
            if key != "publish_logs"
        }
    if isinstance(value, list):
        return [_without_publish_logs(item) for item in value]
    return value


def _product_storage_payload(product: dict[str, Any]) -> dict[str, Any]:
    """Product JSON owns product fields; drafts are owned by platform_drafts."""
    payload = dict(_dict(_without_publish_logs(product)))
    payload.pop("drafts", None)
    return payload


_DRAFT_COLUMN_FIELDS = {
    "draft_id",
    "product_id",
    "platform",
    "site",
    "status",
    "created_at",
    "updated_at",
}


def _draft_storage_payload(draft: dict[str, Any]) -> dict[str, Any]:
    """Persist only fields not canonically represented by draft table columns."""
    return {
        key: value
        for key, value in _dict(_without_publish_logs(draft)).items()
        if key not in _DRAFT_COLUMN_FIELDS
    }


def _load_current_draft_json(value: Any) -> dict[str, Any]:
    try:
        draft = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "platform_drafts.draft_json 不是有效的当前 JSON"
        ) from exc
    if not isinstance(draft, dict):
        raise RuntimeError(
            "platform_drafts.draft_json 必须是 JSON object"
        )
    return draft


def _validate_product_write_shape(product: dict[str, Any]) -> None:
    validate_product_root_fields(product)
    raw_drafts = product.get("drafts")
    if not isinstance(raw_drafts, dict):
        return
    for draft in raw_drafts.values():
        if isinstance(draft, dict):
            validate_platform_draft_root_fields(draft)


def _draft_should_persist(draft: dict[str, Any]) -> bool:
    if str(draft.get("draft_id") or draft.get("draftId") or "").strip():
        return True
    status = str(draft.get("status") or draft.get("publish_status") or "").strip().lower()
    if status in {"copy_ready", "images_ready", "ready_to_publish", "published", "failed", "not_ready"}:
        return True
    for key in ("title", "description", "category_id", "copy_generated_at"):
        if str(draft.get(key) or "").strip():
            return True
    # 商品规范化会把商品级 attributes 映射到每个平台的默认草稿模板。
    # attributes 本身不能证明用户已经创建了平台草稿，否则保存一个带来源
    # 属性的商品就会为所有平台各落一条草稿记录。真实草稿应由 draft_id、
    # 文案、类目、图片或明确的工作流状态来标识。
    for key in ("validation_errors", "images"):
        value = draft.get(key)
        if isinstance(value, (dict, list)) and bool(value):
            return True
    return False


def _source(product: dict[str, Any]) -> dict[str, Any]:
    source = product.get("source")
    if not isinstance(source, dict):
        raise ValueError("产品缺少 canonical source object")
    return source


def _image_pool(product: dict[str, Any]) -> list[dict[str, Any]]:
    source = _source(product)
    pool = [item for item in _list(source.get("image_pool")) if isinstance(item, dict)]
    if pool:
        return pool
    images = _list(source.get("images"))
    return [
        {
            "id": f"source_{index + 1}",
            "url": str(url),
            "preview_url": str(url),
            "origin": str(source.get("source_platform") or "source"),
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

    def _restrict_database_file_permissions(self) -> None:
        """Keep the credential-bearing SQLite database and sidecars private."""
        if os.name == "nt":
            return
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            try:
                path.chmod(0o600)
            except FileNotFoundError:
                continue

    def _create_private_database_file(self) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(
            self.db_path,
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        os.close(descriptor)
        self._restrict_database_file_permissions()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # timeout + busy_timeout + WAL: the ThreadingHTTPServer request threads
        # and the publishing bus worker pool write concurrently; without these
        # settings SQLite raises "database is locked" under load.
        self._create_private_database_file()
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            self._restrict_database_file_permissions()
            yield conn
        finally:
            conn.close()
            self._restrict_database_file_permissions()

    def _inspect_schema_without_mutation(
        self,
    ) -> tuple[int, frozenset[str], frozenset[str]]:
        """Read an existing database through SQLite's read-only URI mode."""
        if not self.db_path.exists():
            return 0, frozenset(), frozenset()
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            version = int(
                conn.execute("PRAGMA user_version").fetchone()[0] or 0
            )
            tables = frozenset(
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                      AND name NOT LIKE 'sqlite_%'
                    """
                )
            )
            draft_columns = (
                frozenset(
                    str(row[1])
                    for row in conn.execute(
                        'PRAGMA table_info("platform_drafts")'
                    )
                )
                if "platform_drafts" in tables
                else frozenset()
            )
            return version, tables, draft_columns
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        (
            inspected_version,
            inspected_tables,
            inspected_draft_columns,
        ) = (
            self._inspect_schema_without_mutation()
        )
        is_empty_database = (
            inspected_version == 0 and not inspected_tables
        )
        is_current_database = (
            inspected_version == SCHEMA_VERSION
            and inspected_tables == frozenset(REQUIRED_TABLES)
            and inspected_draft_columns
            == _CURRENT_PLATFORM_DRAFT_COLUMNS
        )
        if not is_empty_database and not is_current_database:
            raise RuntimeError(
                "数据库 schema 版本 "
                f"{inspected_version} 不受支持（当前版本 {SCHEMA_VERSION}）；"
                "仅接受空库或当前完整 schema，未自动迁移或重建。"
            )
        with self._connect() as conn:
            if is_empty_database:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    _execute_schema_statements(conn)
                    conn.execute(
                        f"PRAGMA user_version = {SCHEMA_VERSION}"
                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        self._maybe_seed_upc_pool()

    def _maybe_seed_upc_pool(self) -> None:
        """One-time import of purchased UPC codes from ``upc_pool.json``.

        The file holds paid-for UPC codes (a real asset).  When the table is
        empty and the file exists next to the database, import values and
        their used/free state once. This is an explicit import feature, not
        persisted-schema compatibility.
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

    def _upsert_product_model_in_connection(
        self,
        conn: sqlite3.Connection,
        product: dict[str, Any],
    ) -> str:
        now = utc_now()
        product_input = dict(_dict(product))
        _validate_product_write_shape(product_input)
        product = normalize_product_model(product_input)
        source = _source(product)
        stored_product = _product_storage_payload(product)
        product_id = product_identity(product)
        product["product_id"] = product_id
        stored_product["product_id"] = product_id
        conn.execute(
            """
            INSERT INTO products (
                product_id, source_platform, source_url, title, brand, model,
                collect_status, purchase_price, purchase_currency,
                dimensions_json, weight_kg, source_json, product_json,
                created_at, updated_at
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
                str(source.get("source_platform") or ""),
                str(source.get("source_url") or ""),
                str(source.get("title") or product.get("name") or ""),
                str(source.get("brand") or product.get("brand") or ""),
                str(source.get("model") or product.get("model") or ""),
                str(
                    source.get("collect_status")
                    or product.get("collect_status")
                    or ""
                ),
                str(source.get("price") or ""),
                str(source.get("currency") or ""),
                json_dumps(source.get("dimensions") or {}),
                str(
                    source.get("weight_kg")
                    or product.get("weight_kg")
                    or ""
                ),
                json_dumps(_without_publish_logs(source)),
                json_dumps(stored_product),
                str(
                    product.get("created_at")
                    or source.get("created_at")
                    or now
                ),
                now,
            ),
        )
        self._upsert_drafts(conn, product_id, product, now)
        self._upsert_media(conn, product_id, product, now)
        return product_id

    def upsert_product_model(self, product: dict[str, Any]) -> str:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                product_id = self._upsert_product_model_in_connection(
                    conn,
                    product,
                )
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
            declared_product_id = str(
                draft.get("source_product_id")
                or draft.get("product_id")
                or ""
            ).strip()
            if declared_product_id and declared_product_id != product_id:
                raise ValueError(
                    f"草稿 {draft_id} 声明商品 {declared_product_id}，不能保存到商品 {product_id}。"
                )
            existing = conn.execute(
                "SELECT product_id, platform FROM platform_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            if existing and (
                str(existing["product_id"] or "") != product_id
                or str(existing["platform"] or "") != platform
            ):
                raise ValueError(
                    f"草稿 {draft_id} 已绑定商品 {existing['product_id']} / 平台 {existing['platform']}，禁止静默换绑。"
                )
            site = str(draft.get("site") or draft.get("site_id") or "").strip()
            conn.execute(
                """
                INSERT INTO platform_drafts (
                    draft_id, product_id, platform, site, status, draft_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    site=excluded.site,
                    status=excluded.status,
                    draft_json=excluded.draft_json,
                    updated_at=excluded.updated_at
                """,
                (
                    draft_id,
                    product_id,
                    platform,
                    site,
                    _draft_status(draft),
                    json_dumps(_draft_storage_payload(draft)),
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
            product["drafts"] = self._load_drafts(conn, row["product_id"])
            product.setdefault("source", {})
            if isinstance(product["source"], dict):
                product["source"]["image_pool"] = self._load_media(conn, row["product_id"])
            return product

    def _load_drafts(
        self,
        conn: sqlite3.Connection,
        product_id: str,
    ) -> dict[str, Any]:
        drafts: dict[str, Any] = {}
        seen_platforms: set[str] = set()
        for row in conn.execute(
            """
            SELECT * FROM platform_drafts
            WHERE product_id = ?
            ORDER BY CASE WHEN status = 'published' THEN 1 ELSE 0 END ASC, updated_at DESC
            """,
            (product_id,),
        ):
            draft = _load_current_draft_json(row["draft_json"])
            draft_id = row["draft_id"] if "draft_id" in row.keys() else str(draft.get("draft_id") or "")
            platform = str(row["platform"])
            draft.update(
                {
                    "draft_id": draft_id,
                    "product_id": row["product_id"],
                    "platform": platform,
                    "platforms": _draft_platforms(draft, platform),
                    "status": row["status"],
                    "site": row["site"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
            draft = normalize_platform_draft(
                draft,
                platform,
                {"product_id": row["product_id"]},
            )
            if platform not in seen_platforms:
                drafts[platform] = draft
                seen_platforms.add(platform)
        return drafts

    @staticmethod
    def _draft_from_row(row: sqlite3.Row) -> dict[str, Any]:
        draft = _load_current_draft_json(row["draft_json"])
        draft.update(
            {
                "draft_id": row["draft_id"],
                "product_id": row["product_id"],
                "source_product_id": draft.get("source_product_id") or row["product_id"],
                "platform": row["platform"],
                "platforms": _draft_platforms(draft, row["platform"]),
                "site": row["site"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return normalize_platform_draft(
            draft,
            str(row["platform"]),
            {"product_id": row["product_id"]},
        )

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
            cursor = conn.execute("DELETE FROM platform_drafts WHERE draft_id = ?", (draft_id,))
            conn.commit()
            return cursor.rowcount > 0

    def upsert_draft_model(self, product_id: str, platform: str, draft: dict[str, Any]) -> str:
        now = utc_now()
        product_id = str(product_id or "").strip()
        platform = str(platform or "").strip().lower()
        draft = dict(_dict(draft))
        validate_platform_draft_root_fields(draft)
        draft_platform = str(draft.get("platform") or "").strip().lower()
        if draft_platform in PLATFORMS:
            platform = draft_platform
        if not product_id or platform not in PLATFORMS:
            return ""
        draft = normalize_platform_draft(
            draft,
            platform,
            {"product_id": product_id},
        )
        with self._connect() as conn:
            draft_id = draft_identity(draft)
            draft["draft_id"] = draft_id
            draft["platform"] = platform
            draft["platforms"] = _draft_platforms(draft, platform)
            declared_product_id = str(
                draft.get("source_product_id")
                or draft.get("product_id")
                or ""
            ).strip()
            if declared_product_id and declared_product_id != product_id:
                raise ValueError(
                    f"草稿 {draft_id} 声明商品 {declared_product_id}，不能保存到商品 {product_id}。"
                )
            existing = conn.execute(
                "SELECT product_id, platform FROM platform_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            if existing and (
                str(existing["product_id"] or "") != product_id
                or str(existing["platform"] or "") != platform
            ):
                raise ValueError(
                    f"草稿 {draft_id} 已绑定商品 {existing['product_id']} / 平台 {existing['platform']}，禁止静默换绑。"
                )
            site = str(draft.get("site") or draft.get("site_id") or "").strip()
            conn.execute(
                """
                INSERT INTO platform_drafts (
                    draft_id, product_id, platform, site, status, draft_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    site=excluded.site,
                    status=excluded.status,
                    draft_json=excluded.draft_json,
                    updated_at=excluded.updated_at
                """,
                (
                    draft_id,
                    product_id,
                    platform,
                    site,
                    _draft_status(draft),
                    json_dumps(_draft_storage_payload(draft)),
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
                records.append(
                    _record_from_row(
                        row,
                        self._load_drafts(conn, row["product_id"]),
                    )
                )
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
        status = str(draft.get("status") or draft.get("publish_status") or row["status"] or "claimed")
        target_sites = draft.get("target_sites") if isinstance(draft.get("target_sites"), list) else [{"platform": row["platform"], "site": row["site"], "language": draft.get("language") or "", "market_currency": "", "listing_currency": ""}]
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
            "title": draft.get("title") or "",
            "product_title": row["product_title"] or _dict(product).get("name") or "",
            "main_image": row["main_image"] or "",
            "source_platform": row["source_platform"] or "",
            "source_url": row["source_url"] or "",
            "category_id": draft.get("category_id") or "",
            "category_path": draft.get("category_path") or "",
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

    def replace_store_auth_snapshot(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Restore the complete auth table after a paired file write fails."""
        rows = snapshot if isinstance(snapshot, dict) else {}
        now = utc_now()
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM store_auth")
                conn.executemany(
                    """
                    INSERT INTO store_auth (
                        platform, credentials_json, auth_status,
                        auth_detail_json, checked_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(platform),
                            json_dumps(
                                _dict(value).get("credentials")
                            ),
                            str(
                                _dict(value).get("auth_status")
                                or ""
                            ),
                            json_dumps(
                                _dict(value).get("auth_detail")
                            ),
                            str(
                                _dict(value).get("checked_at")
                                or ""
                            ),
                            now,
                        )
                        for platform, value in sorted(rows.items())
                        if str(platform).strip()
                    ],
                )
                conn.commit()

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

    def delete_store_auth(self, platform: str) -> bool:
        """Delete one platform's credentials and dynamic authorization state."""

        platform = str(platform or "").strip().lower()
        if not platform:
            return False
        with self._write_lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM store_auth WHERE platform = ?",
                    (platform,),
                )
                conn.commit()
        return cursor.rowcount > 0

    # -- runtime_secrets ----------------------------------------------------

    def load_runtime_secrets(self, namespace: str) -> dict[str, Any]:
        namespace = str(namespace or "").strip()
        if not namespace:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT secret_path, secret_json
                FROM runtime_secrets
                WHERE namespace = ?
                ORDER BY secret_path
                """,
                (namespace,),
            ).fetchall()
        return {
            str(row["secret_path"]): json_loads(row["secret_json"], "")
            for row in rows
        }

    def replace_runtime_secrets(
        self,
        namespace: str,
        secrets: dict[str, Any],
    ) -> None:
        """Atomically replace one namespace's path-addressed runtime secrets."""
        namespace = str(namespace or "").strip()
        if not namespace:
            return
        values = {
            str(path): value
            for path, value in _dict(secrets).items()
            if str(path).strip() and value not in (None, "")
        }
        now = utc_now()
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM runtime_secrets WHERE namespace = ?",
                    (namespace,),
                )
                conn.executemany(
                    """
                    INSERT INTO runtime_secrets (
                        namespace, secret_path, secret_json, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (namespace, path, json_dumps(value), now)
                        for path, value in sorted(values.items())
                    ],
                )
                conn.commit()

    # -- publish_logs ----------------------------------------------------------

    @staticmethod
    def _insert_publish_log_row(
        conn: sqlite3.Connection,
        entry: dict[str, Any],
    ) -> int:
        entry = _dict(entry)
        ts = str(entry.get("time") or entry.get("finished_at") or entry.get("checked_at") or "") or utc_now()
        artifacts_path = str(entry.get("response_body_path") or entry.get("request_payload_path") or "")
        message = str(entry.get("error_message") or entry.get("error") or entry.get("message") or "")
        cursor = conn.execute(
            """
            INSERT INTO publish_logs (
                ts, platform, product_id, draft_id, status,
                stage, message, artifacts_path, detail_json
            )
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
        return int(cursor.lastrowid or 0)

    def insert_publish_log(self, entry: dict[str, Any]) -> int:
        with self._write_lock:
            with self._connect() as conn:
                log_id = self._insert_publish_log_row(conn, entry)
                conn.commit()
        return log_id

    def insert_publish_log_once(
        self,
        entry: dict[str, Any],
    ) -> int:
        """Atomically insert one terminal event per job and platform.

        Older v5 databases keep ``job_id`` inside ``detail_json``. A
        ``BEGIN IMMEDIATE`` read-and-insert transaction provides the same
        cross-thread/process exclusion without destructively rebuilding the
        table merely to add an index.
        """
        entry = _dict(entry)
        job_id = str(entry.get("job_id") or "").strip()
        platform = str(entry.get("platform") or "").strip()
        if not job_id:
            return self.insert_publish_log(entry)
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT detail_json FROM publish_logs
                    WHERE platform = ?
                    ORDER BY id DESC
                    """,
                    (platform,),
                ).fetchall()
                if any(
                    str(
                        _dict(
                            json_loads(row["detail_json"], {})
                        ).get("job_id")
                        or ""
                    )
                    == job_id
                    for row in rows
                ):
                    conn.rollback()
                    return 0
                log_id = self._insert_publish_log_row(conn, entry)
                conn.commit()
                return log_id

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

    def assign_upc_to_product_model(
        self,
        product: dict[str, Any],
    ) -> tuple[str, str]:
        """Claim a purchased UPC and persist its product in one transaction."""
        self._maybe_seed_upc_pool()
        product_input = dict(_dict(product))
        _validate_product_write_shape(product_input)
        product = normalize_product_model(product_input)
        product_id = product_identity(product)
        product["product_id"] = product_id
        drafts = _dict(product.get("drafts"))
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT upc FROM upc_pool
                    WHERE status = 'free'
                    ORDER BY upc
                    LIMIT 1
                    """
                ).fetchone()
                if not row:
                    conn.rollback()
                    return "", product_id
                upc = str(row["upc"])
                product["upc"] = upc
                for draft in drafts.values():
                    if isinstance(draft, dict):
                        draft["upc"] = upc
                product["drafts"] = drafts
                self._upsert_product_model_in_connection(conn, product)
                cursor = conn.execute(
                    """
                    UPDATE upc_pool
                    SET status = 'used', product_id = ?, assigned_at = ?
                    WHERE upc = ? AND status = 'free'
                    """,
                    (product_id, utc_now(), upc),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return "", product_id
                conn.commit()
                return upc, product_id

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
        draft_id = str(
            product.get("current_draft_id") or product.get("draft_id") or ""
        )
        drafts = _dict(product.get("drafts"))
        draft_id = str(state.get("draft_id") or draft_id)
        if not draft_id:
            for item in platforms.values():
                if isinstance(item, dict) and item.get("draft_id"):
                    draft_id = str(item["draft_id"])
                    break
        if not draft_id:
            for platform in sorted(platforms):
                draft = _dict(drafts.get(platform))
                if draft.get("draft_id"):
                    draft_id = str(draft["draft_id"])
                    break
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
                    draft_id,
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
        """Return worker-resume and terminal-compensation candidates.

        ``completed`` rows remain candidates until their terminal callback has
        durably persisted product/log side effects. This closes the crash
        window between committing the completed job state and running that
        callback.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, payload_json
                FROM publish_jobs
                WHERE status IN (
                    'pending', 'queued', 'running', 'retrying', 'completed'
                )
                ORDER BY created_at ASC
                """,
            ).fetchall()
        states: list[dict[str, Any]] = []
        for row in rows:
            state = json_loads(row["payload_json"], {})
            if isinstance(state, dict):
                state.setdefault("status", str(row["status"] or ""))
                if (
                    str(row["status"] or "").lower() == "completed"
                    and state.get("terminal_results_persisted")
                ):
                    continue
                states.append(state)
        return states

    def list_publish_jobs(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        platform: str = "",
        product_id: str = "",
    ) -> tuple[list[dict[str, Any]], str]:
        resolved_limit = max(1, min(int(limit or 50), 100))
        clauses: list[str] = []
        values: list[Any] = []
        platform = str(platform or "").strip().lower()
        product_id = str(product_id or "").strip()

        if platform:
            clauses.append("instr(',' || lower(platform) || ',', ',' || ? || ',') > 0")
            values.append(platform)
        if product_id:
            clauses.append("product_id = ?")
            values.append(product_id)

        cursor = str(cursor or "").strip()
        with self._connect() as conn:
            if cursor:
                cursor_row = conn.execute(
                    "SELECT created_at, job_id FROM publish_jobs WHERE job_id = ?",
                    (cursor,),
                ).fetchone()
                if not cursor_row:
                    return [], ""
                clauses.append("(created_at < ? OR (created_at = ? AND job_id < ?))")
                values.extend(
                    [cursor_row["created_at"], cursor_row["created_at"], cursor_row["job_id"]]
                )

            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT job_id, product_id, draft_id, status, payload_json,
                       created_at, updated_at
                FROM publish_jobs
                {where}
                ORDER BY created_at DESC, job_id DESC
                LIMIT ?
                """,
                (*values, resolved_limit + 1),
            ).fetchall()

        has_more = len(rows) > resolved_limit
        selected_rows = rows[:resolved_limit]
        states: list[dict[str, Any]] = []
        for row in selected_rows:
            state = json_loads(row["payload_json"], {})
            if not isinstance(state, dict):
                continue
            state.setdefault("job_id", str(row["job_id"] or ""))
            state.setdefault("product_id", str(row["product_id"] or ""))
            state.setdefault("draft_id", str(row["draft_id"] or ""))
            state.setdefault("status", str(row["status"] or ""))
            state.setdefault("created_at", str(row["created_at"] or ""))
            state.setdefault("updated_at", str(row["updated_at"] or ""))
            states.append(state)
        next_cursor = str(selected_rows[-1]["job_id"] or "") if has_more and selected_rows else ""
        return states, next_cursor

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
        "pricing_status": "done" if any(
            isinstance(item, dict)
            and isinstance(item.get("applied_price"), dict)
            and str(item["applied_price"].get("amount") or "").strip()
            for item in _dict(_dict(ml_draft.get("pricing")).get("targets")).values()
        ) else "pending",
        "precheck_status": (_dict(_dict(product.get("publish_preview")).get("mercadolibre")).get("ok", "pending") if isinstance(product, dict) else "pending"),
        "publish_status": ml_draft.get("publish_status") or "not_ready",
        "optimized": draft_statuses.get("mercadolibre") in {"copy_ready", "images_ready", "ready_to_publish", "published"},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "platforms": platforms,
        "product_file_path": f"sqlite://products/{row['product_id']}",
    }

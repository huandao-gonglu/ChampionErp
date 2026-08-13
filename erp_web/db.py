from __future__ import annotations

"""SQLite 持久化边界。

``ErpDatabase`` 统一拥有 schema、连接设置和读写路径。数据库通过
``PRAGMA user_version`` 版本化；接受空库、当前完整 schema，以及有真实本地
数据证据的完整 v5 / v7 schema。迁移会在单个事务内升级到当前版本；其他旧版、
残缺、未知或未来格式在任何写入前拒绝。唯一 seed 路径是 UPC 池：表为空时，
从数据库旁的 ``upc_pool.json`` 一次性导入已购买的 UPC。
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
SCHEMA_VERSION = 8

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
    "global_tasks",
    "draft_query_snapshots",
)

# v5 是本功能落地前真实本地数据库的已持久化格式。只支持这一条有明确
# 数据证据的迁移，不恢复更早的历史兼容链。
_V5_REQUIRED_TABLES = frozenset(
    {
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
    }
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
    idempotency_key TEXT NOT NULL,
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
    parent_session_id TEXT DEFAULT NULL,
    day TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    last_seq INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(parent_session_id) REFERENCES ai_sessions(session_id)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    pair TEXT PRIMARY KEY,
    rate REAL NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS global_tasks (
    task_id TEXT PRIMARY KEY,
    ai_work_conversation_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    task_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS draft_query_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    ordered_draft_ids_json TEXT NOT NULL DEFAULT '[]',
    query_json TEXT NOT NULL DEFAULT '{}',
    aggregates_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_source_url ON products(source_url);
CREATE INDEX IF NOT EXISTS idx_platform_drafts_product ON platform_drafts(product_id);
CREATE INDEX IF NOT EXISTS idx_media_assets_product ON media_assets(product_id);
CREATE INDEX IF NOT EXISTS idx_publish_logs_product ON publish_logs(product_id, platform);
CREATE INDEX IF NOT EXISTS idx_upc_pool_status ON upc_pool(status);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_publish_jobs_idempotency_key
ON publish_jobs(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_research_runs_updated ON research_runs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_candidates_run ON research_candidates(run_id, rank);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_updated ON ai_sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_parent_updated
ON ai_sessions(parent_session_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_global_tasks_updated ON global_tasks(updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_global_tasks_one_active_conversation
ON global_tasks(ai_work_conversation_id)
WHERE ai_work_conversation_id != ''
  AND status NOT IN ('completed', 'failed', 'cancelled');
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

_CURRENT_PUBLISH_JOB_COLUMNS = frozenset(
    {
        "job_id",
        "idempotency_key",
        "product_id",
        "draft_id",
        "platform",
        "status",
        "stage",
        "attempts",
        "error",
        "payload_json",
        "created_at",
        "updated_at",
    }
)

_V5_PUBLISH_JOB_COLUMNS = _CURRENT_PUBLISH_JOB_COLUMNS - {"idempotency_key"}

_CURRENT_GLOBAL_TASK_COLUMNS = frozenset(
    {
        "task_id",
        "ai_work_conversation_id",
        "status",
        "task_json",
        "created_at",
        "updated_at",
    }
)

_CURRENT_DRAFT_QUERY_SNAPSHOT_COLUMNS = frozenset(
    {
        "snapshot_id",
        "ordered_draft_ids_json",
        "query_json",
        "aggregates_json",
        "created_at",
    }
)

_CURRENT_AI_SESSION_COLUMNS = frozenset(
    {
        "session_id",
        "parent_session_id",
        "day",
        "status",
        "last_seq",
        "updated_at",
    }
)

_V7_AI_SESSION_COLUMNS = _CURRENT_AI_SESSION_COLUMNS - {
    "parent_session_id"
}

_PUBLISH_JOB_IDEMPOTENCY_INDEX = "idx_publish_jobs_idempotency_key"
_GLOBAL_TASK_ACTIVE_CONVERSATION_INDEX = (
    "idx_global_tasks_one_active_conversation"
)
_AI_SESSION_PARENT_INDEX = "idx_ai_sessions_parent_updated"
_AI_WORK_JOURNAL_RELATIVE_DIR = Path("data") / "logs" / "ai_work"


# ---------------------------------------------------------------------------
# Pure helpers (no database access)
# ---------------------------------------------------------------------------

def _execute_schema_statements(conn: sqlite3.Connection) -> None:
    """在调用方事务中逐条执行 schema DDL。"""
    if not conn.in_transaction:
        raise RuntimeError("schema DDL 必须在显式事务内执行")
    for statement in _SCHEMA_SQL.split(";"):
        sql = statement.strip()
        if sql:
            conn.execute(sql)


def _has_required_unique_index(
    conn: sqlite3.Connection,
    *,
    table: str,
    name: str,
    columns: tuple[str, ...],
    partial: bool,
) -> bool:
    """验证关键唯一约束的列与 partial 属性，而不只相信索引名称。"""

    rows = conn.execute(f'PRAGMA index_list("{table}")').fetchall()
    matched = next(
        (
            row
            for row in rows
            if str(row[1]) == name
            and bool(row[2])
            and bool(row[4]) is partial
        ),
        None,
    )
    if matched is None:
        return False
    indexed_columns = tuple(
        str(row[2])
        for row in conn.execute(f'PRAGMA index_info("{name}")').fetchall()
    )
    return indexed_columns == columns


def _has_required_non_unique_index(
    conn: sqlite3.Connection,
    *,
    table: str,
    name: str,
    columns: tuple[str, ...],
) -> bool:
    """验证会话层级查询依赖的普通索引。"""

    rows = conn.execute(f'PRAGMA index_list("{table}")').fetchall()
    matched = next(
        (
            row
            for row in rows
            if str(row[1]) == name
            and not bool(row[2])
            and not bool(row[4])
        ),
        None,
    )
    if matched is None:
        return False
    indexed_columns = tuple(
        str(row[2])
        for row in conn.execute(f'PRAGMA index_info("{name}")').fetchall()
    )
    return indexed_columns == columns


def _has_valid_ai_session_parent_reference(
    conn: sqlite3.Connection,
) -> bool:
    """验证自引用外键定义及现存父子数据完整性。"""

    foreign_keys = conn.execute(
        'PRAGMA foreign_key_list("ai_sessions")'
    ).fetchall()
    has_expected_reference = any(
        str(row[2]) == "ai_sessions"
        and str(row[3]) == "parent_session_id"
        and str(row[4]) == "session_id"
        and str(row[6]).upper() == "SET NULL"
        for row in foreign_keys
    )
    if not has_expected_reference:
        return False
    invalid_hierarchy = conn.execute(
        """
        SELECT 1
        FROM ai_sessions AS child
        LEFT JOIN ai_sessions AS parent
          ON parent.session_id = child.parent_session_id
        WHERE child.parent_session_id IS NOT NULL
          AND (
              parent.session_id IS NULL
              OR child.session_id = child.parent_session_id
              OR parent.parent_session_id IS NOT NULL
          )
        LIMIT 1
        """
    ).fetchone()
    return invalid_hierarchy is None


def _legacy_publish_idempotency_facts(state: dict[str, Any]) -> dict[str, Any]:
    """从 v5 job 状态提取只用于既有任务恢复的稳定发布事实。"""

    product = _dict(state.get("product"))
    product_id = str(
        product.get("product_id") or state.get("product_id") or ""
    ).strip()
    raw_platforms = _dict(state.get("platforms"))
    platforms = sorted(str(key or "").strip().lower() for key in raw_platforms)
    platforms = [key for key in platforms if key]
    targets: dict[str, dict[str, str]] = {}
    for platform in platforms:
        item = _dict(raw_platforms.get(platform))
        targets[platform] = {
            "draft_id": str(item.get("draft_id") or "").strip(),
            "site": str(item.get("site") or "").strip(),
            "product_id": str(item.get("product_id") or product_id).strip(),
        }
    return {
        "product_id": product_id,
        "platforms": platforms,
        "targets": targets,
    }


def _add_ai_session_parent_column(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        ALTER TABLE ai_sessions
        ADD COLUMN parent_session_id TEXT DEFAULT NULL
            REFERENCES ai_sessions(session_id) ON DELETE SET NULL
        """
    )


def _backfill_ai_session_parent_links(
    conn: sqlite3.Connection,
    journal_root: Path,
) -> None:
    """一次性从稳定全局会话投影恢复历史父子关系。

    迁移只绑定父会话唯一且父子元数据均存在的链接；历史日志若把同一执行
    会话链接到多个父会话，则保持根会话，避免启动时任意选择一个 owner。
    """

    rows = conn.execute(
        "SELECT session_id, day FROM ai_sessions ORDER BY session_id"
    ).fetchall()
    known_sessions = {str(row["session_id"] or "") for row in rows}
    parents_by_child: dict[str, set[str]] = {}
    if "global_tasks" in {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }:
        task_rows = conn.execute(
            """
            SELECT ai_work_conversation_id, task_json
            FROM global_tasks
            WHERE ai_work_conversation_id != ''
            """
        ).fetchall()
        for task_row in task_rows:
            parent_id = str(
                task_row["ai_work_conversation_id"] or ""
            ).strip()
            task = json_loads(task_row["task_json"], {})
            raw_child_ids = (
                task.get("agent_execution_conversation_ids")
                if isinstance(task, dict)
                else []
            )
            if (
                parent_id not in known_sessions
                or not isinstance(raw_child_ids, list)
            ):
                continue
            for raw_child_id in raw_child_ids:
                child_id = str(raw_child_id or "").strip()
                if (
                    child_id
                    and child_id != parent_id
                    and child_id in known_sessions
                ):
                    parents_by_child.setdefault(child_id, set()).add(
                        parent_id
                    )
    for row in rows:
        parent_id = str(row["session_id"] or "").strip()
        day = str(row["day"] or "").strip()
        if not parent_id or not day:
            continue
        path = journal_root / day / f"{parent_id}.jsonl"
        try:
            source = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with source:
            is_global_parent: bool | None = None
            for raw_line in source:
                try:
                    event = json.loads(raw_line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                if is_global_parent is None:
                    metadata = event.get("rawEvent")
                    is_global_parent = bool(
                        isinstance(metadata, dict)
                        and metadata.get("use_case_id")
                        == "global.agent.chat"
                    )
                    if not is_global_parent:
                        break
                if (
                    event.get("type") != "CUSTOM"
                    or event.get("name")
                    != "global.agent_execution_link"
                ):
                    continue
                value = event.get("value")
                child_id = str(
                    value.get("conversation_id")
                    if isinstance(value, dict)
                    else ""
                ).strip()
                if (
                    not child_id
                    or child_id == parent_id
                    or child_id not in known_sessions
                ):
                    continue
                parents_by_child.setdefault(child_id, set()).add(parent_id)
    proposed_child_ids = set(parents_by_child)
    for child_id, parent_ids in parents_by_child.items():
        if len(parent_ids) != 1:
            continue
        parent_id = next(iter(parent_ids))
        if parent_id in proposed_child_ids:
            continue
        conn.execute(
            """
            UPDATE ai_sessions
            SET parent_session_id = ?
            WHERE session_id = ?
              AND parent_session_id IS NULL
            """,
            (parent_id, child_id),
        )


def _migrate_v5_to_v8(
    conn: sqlite3.Connection,
    journal_root: Path,
) -> None:
    """原子增加全局任务状态、发布幂等和 AI 会话层级。"""

    if not conn.in_transaction:
        raise RuntimeError("数据库迁移必须在显式事务内执行")
    conn.execute(
        "ALTER TABLE publish_jobs ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''"
    )
    _add_ai_session_parent_column(conn)
    rows = conn.execute(
        "SELECT job_id, product_id, payload_json FROM publish_jobs"
    ).fetchall()
    for row in rows:
        job_id = str(row["job_id"] or "").strip()
        state = json_loads(row["payload_json"], {})
        state = state if isinstance(state, dict) else {}
        state.setdefault("job_id", job_id)
        state.setdefault("product_id", str(row["product_id"] or ""))
        idempotency_key = f"migrated-publish-job:{job_id}"
        state["idempotency_key"] = idempotency_key
        state["idempotency_facts"] = _legacy_publish_idempotency_facts(state)
        conn.execute(
            """
            UPDATE publish_jobs
            SET idempotency_key = ?, payload_json = ?
            WHERE job_id = ?
            """,
            (idempotency_key, json_dumps(state), job_id),
        )
    _execute_schema_statements(conn)
    _backfill_ai_session_parent_links(conn, journal_root)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _migrate_v7_to_v8(
    conn: sqlite3.Connection,
    journal_root: Path,
) -> None:
    """原子增加 AI 会话层级并一次性回填历史 execution links。"""

    if not conn.in_transaction:
        raise RuntimeError("数据库迁移必须在显式事务内执行")
    _add_ai_session_parent_column(conn)
    _execute_schema_statements(conn)
    _backfill_ai_session_parent_links(conn, journal_root)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


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
    ) -> tuple[
        int,
        frozenset[str],
        frozenset[str],
        frozenset[str],
        frozenset[str],
        frozenset[str],
        frozenset[str],
        bool,
        bool,
        bool,
        bool,
    ]:
        """Read an existing database through SQLite's read-only URI mode."""
        if not self.db_path.exists():
            return (
                0,
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                False,
                False,
                False,
                False,
            )
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
            publish_job_columns = (
                frozenset(
                    str(row[1])
                    for row in conn.execute(
                        'PRAGMA table_info("publish_jobs")'
                    )
                )
                if "publish_jobs" in tables
                else frozenset()
            )
            global_task_columns = (
                frozenset(
                    str(row[1])
                    for row in conn.execute(
                        'PRAGMA table_info("global_tasks")'
                    )
                )
                if "global_tasks" in tables
                else frozenset()
            )
            snapshot_columns = (
                frozenset(
                    str(row[1])
                    for row in conn.execute(
                        'PRAGMA table_info("draft_query_snapshots")'
                    )
                )
                if "draft_query_snapshots" in tables
                else frozenset()
            )
            ai_session_columns = (
                frozenset(
                    str(row[1])
                    for row in conn.execute(
                        'PRAGMA table_info("ai_sessions")'
                    )
                )
                if "ai_sessions" in tables
                else frozenset()
            )
            publish_idempotency_index_valid = (
                _has_required_unique_index(
                    conn,
                    table="publish_jobs",
                    name=_PUBLISH_JOB_IDEMPOTENCY_INDEX,
                    columns=("idempotency_key",),
                    partial=False,
                )
                if "publish_jobs" in tables
                else False
            )
            active_conversation_index_valid = (
                _has_required_unique_index(
                    conn,
                    table="global_tasks",
                    name=_GLOBAL_TASK_ACTIVE_CONVERSATION_INDEX,
                    columns=("ai_work_conversation_id",),
                    partial=True,
                )
                if "global_tasks" in tables
                else False
            )
            ai_session_parent_index_valid = (
                _has_required_non_unique_index(
                    conn,
                    table="ai_sessions",
                    name=_AI_SESSION_PARENT_INDEX,
                    columns=("parent_session_id", "updated_at"),
                )
                if "parent_session_id" in ai_session_columns
                else False
            )
            ai_session_parent_reference_valid = (
                _has_valid_ai_session_parent_reference(conn)
                if "parent_session_id" in ai_session_columns
                else False
            )
            return (
                version,
                tables,
                draft_columns,
                publish_job_columns,
                global_task_columns,
                snapshot_columns,
                ai_session_columns,
                publish_idempotency_index_valid,
                active_conversation_index_valid,
                ai_session_parent_index_valid,
                ai_session_parent_reference_valid,
            )
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        (
            inspected_version,
            inspected_tables,
            inspected_draft_columns,
            inspected_publish_job_columns,
            inspected_global_task_columns,
            inspected_snapshot_columns,
            inspected_ai_session_columns,
            inspected_publish_idempotency_index_valid,
            inspected_active_conversation_index_valid,
            inspected_ai_session_parent_index_valid,
            inspected_ai_session_parent_reference_valid,
        ) = (
            self._inspect_schema_without_mutation()
        )
        is_empty_database = (
            inspected_version == 0 and not inspected_tables
        )
        is_migratable_v5_database = (
            inspected_version == 5
            and inspected_tables == _V5_REQUIRED_TABLES
            and inspected_draft_columns == _CURRENT_PLATFORM_DRAFT_COLUMNS
            and inspected_publish_job_columns == _V5_PUBLISH_JOB_COLUMNS
            and inspected_ai_session_columns == _V7_AI_SESSION_COLUMNS
            and not inspected_global_task_columns
            and not inspected_snapshot_columns
        )
        is_migratable_v7_database = (
            inspected_version == 7
            and inspected_tables == frozenset(REQUIRED_TABLES)
            and inspected_draft_columns
            == _CURRENT_PLATFORM_DRAFT_COLUMNS
            and inspected_publish_job_columns
            == _CURRENT_PUBLISH_JOB_COLUMNS
            and inspected_global_task_columns
            == _CURRENT_GLOBAL_TASK_COLUMNS
            and inspected_snapshot_columns
            == _CURRENT_DRAFT_QUERY_SNAPSHOT_COLUMNS
            and inspected_ai_session_columns == _V7_AI_SESSION_COLUMNS
            and inspected_publish_idempotency_index_valid
            and inspected_active_conversation_index_valid
            and not inspected_ai_session_parent_index_valid
            and not inspected_ai_session_parent_reference_valid
        )
        is_current_database = (
            inspected_version == SCHEMA_VERSION
            and inspected_tables == frozenset(REQUIRED_TABLES)
            and inspected_draft_columns
            == _CURRENT_PLATFORM_DRAFT_COLUMNS
            and inspected_publish_job_columns
            == _CURRENT_PUBLISH_JOB_COLUMNS
            and inspected_global_task_columns
            == _CURRENT_GLOBAL_TASK_COLUMNS
            and inspected_snapshot_columns
            == _CURRENT_DRAFT_QUERY_SNAPSHOT_COLUMNS
            and inspected_ai_session_columns
            == _CURRENT_AI_SESSION_COLUMNS
            and inspected_publish_idempotency_index_valid
            and inspected_active_conversation_index_valid
            and inspected_ai_session_parent_index_valid
            and inspected_ai_session_parent_reference_valid
        )
        if (
            not is_empty_database
            and not is_migratable_v5_database
            and not is_migratable_v7_database
            and not is_current_database
        ):
            raise RuntimeError(
                "数据库 schema 版本 "
                f"{inspected_version} 不受支持（当前版本 {SCHEMA_VERSION}）；"
                "仅接受空库或当前完整 schema；完整 v5 / v7 本地库会执行原子迁移，"
                "未对未知/不完整格式自动迁移或重建。"
            )
        with self._connect() as conn:
            if (
                is_empty_database
                or is_migratable_v5_database
                or is_migratable_v7_database
            ):
                conn.execute("BEGIN IMMEDIATE")
                try:
                    journal_root = (
                        self.db_path.parent
                        / _AI_WORK_JOURNAL_RELATIVE_DIR
                    )
                    if is_migratable_v5_database:
                        _migrate_v5_to_v8(conn, journal_root)
                    elif is_migratable_v7_database:
                        _migrate_v7_to_v8(conn, journal_root)
                    else:
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
            draft = normalize_platform_draft(
                draft,
                platform,
                {"product_id": product_id},
            )
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
                "SELECT product_id FROM platform_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            if existing and str(existing["product_id"] or "") != product_id:
                raise ValueError(
                    f"草稿 {draft_id} 已绑定商品 {existing['product_id']}，禁止静默换绑到商品 {product_id}。"
                )
            site = str(draft.get("site") or draft.get("site_id") or "").strip()
            conn.execute(
                """
                INSERT INTO platform_drafts (
                    draft_id, product_id, platform, site, status, draft_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    platform=excluded.platform,
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
        draft_id = draft_identity(draft)
        draft["draft_id"] = draft_id
        draft = normalize_platform_draft(
            draft,
            platform,
            {"product_id": product_id},
        )
        with self._connect() as conn:
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
                "SELECT product_id FROM platform_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            if existing and str(existing["product_id"] or "") != product_id:
                raise ValueError(
                    f"草稿 {draft_id} 已绑定商品 {existing['product_id']}，禁止静默换绑到商品 {product_id}。"
                )
            site = str(draft.get("site") or draft.get("site_id") or "").strip()
            conn.execute(
                """
                INSERT INTO platform_drafts (
                    draft_id, product_id, platform, site, status, draft_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    platform=excluded.platform,
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

    @staticmethod
    def _publish_job_record(state: dict[str, Any]) -> dict[str, Any]:
        state = _dict(state)
        job_id = str(state.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("发布任务缺少 job_id。")
        idempotency_key = str(state.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise ValueError("发布任务缺少 idempotency_key。")
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

        return {
            "job_id": job_id,
            "idempotency_key": idempotency_key,
            "product_id": str(product.get("product_id") or ""),
            "draft_id": draft_id,
            "platform": ",".join(sorted(str(key) for key in platforms)),
            "status": str(state.get("status") or ""),
            "stage": stage,
            "attempts": attempts,
            "error": error,
            "payload_json": json_dumps(state),
            "created_at": str(state.get("created_at") or "") or utc_now(),
            "updated_at": str(state.get("updated_at") or "") or utc_now(),
        }

    def create_publish_job(
        self,
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """为一个发布任务原子占用 ``idempotency_key``。"""
        record = self._publish_job_record(state)
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    existing = conn.execute(
                        """
                        SELECT payload_json
                        FROM publish_jobs
                        WHERE idempotency_key = ?
                        """,
                        (record["idempotency_key"],),
                    ).fetchone()
                    if existing:
                        conn.commit()
                        persisted = json_loads(existing["payload_json"], {})
                        if not isinstance(persisted, dict):
                            raise RuntimeError("发布任务持久化数据不是 JSON object。")
                        return persisted, False
                    conn.execute(
                        """
                        INSERT INTO publish_jobs (
                            job_id, idempotency_key, product_id, draft_id,
                            platform, status, stage, attempts, error,
                            payload_json, created_at, updated_at
                        ) VALUES (
                            :job_id, :idempotency_key, :product_id, :draft_id,
                            :platform, :status, :stage, :attempts, :error,
                            :payload_json, :created_at, :updated_at
                        )
                        """,
                        record,
                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        return dict(state), True

    def save_publish_job(self, state: dict[str, Any]) -> None:
        """更新已有发布任务，且禁止改变其幂等绑定。"""
        record = self._publish_job_record(state)
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cursor = conn.execute(
                        """
                        UPDATE publish_jobs
                        SET product_id = :product_id,
                            draft_id = :draft_id,
                            platform = :platform,
                            status = :status,
                            stage = :stage,
                            attempts = :attempts,
                            error = :error,
                            payload_json = :payload_json,
                            updated_at = :updated_at
                        WHERE job_id = :job_id
                          AND idempotency_key = :idempotency_key
                        """,
                        record,
                    )
                    if cursor.rowcount != 1:
                        exists = conn.execute(
                            "SELECT 1 FROM publish_jobs WHERE job_id = ?",
                            (record["job_id"],),
                        ).fetchone()
                        if exists:
                            raise ValueError(
                                "发布任务的 idempotency_key 不可变更。"
                            )
                        raise FileNotFoundError(
                            f"发布任务不存在：{record['job_id']}"
                        )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    def load_publish_job(self, job_id: str) -> dict[str, Any]:
        job_id = str(job_id or "").strip()
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM publish_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return {}
        state = json_loads(row["payload_json"], {})
        return state if isinstance(state, dict) else {}

    def load_publish_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> dict[str, Any]:
        trusted_key = str(idempotency_key or "").strip()
        if not trusted_key:
            return {}
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM publish_jobs WHERE idempotency_key = ?",
                (trusted_key,),
            ).fetchone()
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

    # -- global_tasks / draft_query_snapshots ---------------------------------

    def create_global_task(self, state: dict[str, Any]) -> None:
        """原子创建任务，并由数据库约束同一对话只有一个活动任务。"""

        payload = _dict(state)
        task_id = str(payload.get("task_id") or "").strip()
        conversation_id = str(
            payload.get("ai_work_conversation_id") or ""
        ).strip()
        status = str(payload.get("status") or "").strip()
        created_at = str(payload.get("created_at") or "") or utc_now()
        updated_at = str(payload.get("updated_at") or "") or created_at
        if not task_id or not conversation_id or not status:
            raise ValueError("全局任务缺少 task_id、对话 ID 或状态。")
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    active = conn.execute(
                        """
                        SELECT task_id
                        FROM global_tasks
                        WHERE ai_work_conversation_id = ?
                          AND status NOT IN ('completed', 'failed', 'cancelled')
                        LIMIT 1
                        """,
                        (conversation_id,),
                    ).fetchone()
                    if active:
                        raise ValueError(
                            "同一全局 Agent 对话已有未完成任务："
                            f"{str(active['task_id'] or '')}"
                        )
                    conn.execute(
                        """
                        INSERT INTO global_tasks (
                            task_id, ai_work_conversation_id, status,
                            task_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            conversation_id,
                            status,
                            json_dumps(payload),
                            created_at,
                            updated_at,
                        ),
                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    def save_global_task(self, state: dict[str, Any]) -> None:
        payload = _dict(state)
        task_id = str(payload.get("task_id") or "").strip()
        conversation_id = str(
            payload.get("ai_work_conversation_id") or ""
        ).strip()
        status = str(payload.get("status") or "").strip()
        updated_at = str(payload.get("updated_at") or "") or utc_now()
        if not task_id or not conversation_id or not status:
            raise ValueError("全局任务缺少 task_id、对话 ID 或状态。")
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cursor = conn.execute(
                        """
                        UPDATE global_tasks
                        SET ai_work_conversation_id = ?, status = ?,
                            task_json = ?, updated_at = ?
                        WHERE task_id = ?
                        """,
                        (
                            conversation_id,
                            status,
                            json_dumps(payload),
                            updated_at,
                            task_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise FileNotFoundError(
                            f"全局任务不存在：{task_id}"
                        )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    def load_global_task(self, task_id: str) -> dict[str, Any]:
        task_id = str(task_id or "").strip()
        if not task_id:
            return {}
        with self._connect() as conn:
            row = conn.execute(
                "SELECT task_json FROM global_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        payload = json_loads(row["task_json"], {}) if row else {}
        return payload if isinstance(payload, dict) else {}

    def find_active_global_task(
        self,
        ai_work_conversation_id: str,
    ) -> dict[str, Any]:
        conversation_id = str(ai_work_conversation_id or "").strip()
        if not conversation_id:
            return {}
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT task_json
                FROM global_tasks
                WHERE ai_work_conversation_id = ?
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                ORDER BY updated_at DESC, task_id DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        payload = json_loads(row["task_json"], {}) if row else {}
        return payload if isinstance(payload, dict) else {}

    def list_unfinished_global_tasks(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_json
                FROM global_tasks
                WHERE status NOT IN ('completed', 'failed', 'cancelled')
                ORDER BY created_at ASC, task_id ASC
                """
            ).fetchall()
        return [
            payload
            for row in rows
            if isinstance(
                payload := json_loads(row["task_json"], {}),
                dict,
            )
        ]

    def save_draft_query_snapshot(self, snapshot: dict[str, Any]) -> None:
        """持久化不可变轻量快照；不复制草稿业务数据。"""

        payload = _dict(snapshot)
        snapshot_id = str(payload.get("snapshot_id") or "").strip()
        ordered_ids = payload.get("draft_ids")
        query = payload.get("query")
        aggregates = {
            "total": payload.get("total"),
            "count_by_platform": payload.get("count_by_platform"),
            "count_by_status": payload.get("count_by_status"),
        }
        created_at = str(payload.get("created_at") or "") or utc_now()
        if (
            not snapshot_id
            or not isinstance(ordered_ids, list)
            or not isinstance(query, dict)
            or not isinstance(aggregates["total"], int)
            or not isinstance(aggregates["count_by_platform"], dict)
            or not isinstance(aggregates["count_by_status"], dict)
        ):
            raise ValueError("草稿查询快照缺少 ID、有序草稿 ID、查询条件或聚合统计。")
        normalized_ids = [
            str(item or "").strip()
            for item in ordered_ids
            if str(item or "").strip()
        ]
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT ordered_draft_ids_json, query_json,
                               aggregates_json, created_at
                        FROM draft_query_snapshots
                        WHERE snapshot_id = ?
                        """,
                        (snapshot_id,),
                    ).fetchone()
                    if row:
                        existing_ids = json_loads(
                            row["ordered_draft_ids_json"],
                            [],
                        )
                        existing_query = json_loads(row["query_json"], {})
                        existing_aggregates = json_loads(
                            row["aggregates_json"],
                            {},
                        )
                        if (
                            existing_ids != normalized_ids
                            or existing_query != query
                            or existing_aggregates != aggregates
                        ):
                            raise ValueError("草稿查询 snapshot_id 已绑定其他查询结果。")
                        conn.commit()
                        return
                    conn.execute(
                        """
                        INSERT INTO draft_query_snapshots (
                            snapshot_id, ordered_draft_ids_json,
                            query_json, aggregates_json, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            json_dumps(normalized_ids),
                            json_dumps(query),
                            json_dumps(aggregates),
                            created_at,
                        ),
                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    def load_draft_query_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        snapshot_id = str(snapshot_id or "").strip()
        if not snapshot_id:
            return {}
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ordered_draft_ids_json, query_json,
                       aggregates_json, created_at
                FROM draft_query_snapshots
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
        if not row:
            return {}
        ordered_ids = json_loads(row["ordered_draft_ids_json"], [])
        query = json_loads(row["query_json"], {})
        aggregates = json_loads(row["aggregates_json"], {})
        aggregates = aggregates if isinstance(aggregates, dict) else {}
        return {
            "snapshot_id": snapshot_id,
            "draft_ids": (
                ordered_ids if isinstance(ordered_ids, list) else []
            ),
            "query": query if isinstance(query, dict) else {},
            "total": int(aggregates.get("total") or 0),
            "count_by_platform": (
                aggregates.get("count_by_platform")
                if isinstance(aggregates.get("count_by_platform"), dict)
                else {}
            ),
            "count_by_status": (
                aggregates.get("count_by_status")
                if isinstance(aggregates.get("count_by_status"), dict)
                else {}
            ),
            "created_at": str(row["created_at"] or ""),
        }

    # -- ai_sessions ------------------------------------------------------------

    def upsert_ai_session(
        self,
        session_id: str,
        *,
        parent_session_id: str | None = None,
        day: str,
        status: str,
        last_seq: int,
        updated_at: str,
    ) -> None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        parent_id = str(parent_session_id or "").strip() or None
        with self._connect() as conn:
            try:
                if parent_id:
                    parent = conn.execute(
                        """
                        SELECT parent_session_id FROM ai_sessions
                        WHERE session_id = ?
                        """,
                        (parent_id,),
                    ).fetchone()
                    if parent is None:
                        raise ValueError(
                            "AI 父对话不存在，无法创建子对话。"
                        )
                    if parent["parent_session_id"] is not None:
                        raise ValueError("AI 父对话必须是根对话。")
                    existing = conn.execute(
                        """
                        SELECT parent_session_id FROM ai_sessions
                        WHERE session_id = ?
                        """,
                        (session_id,),
                    ).fetchone()
                    if (
                        existing is not None
                        and existing["parent_session_id"] is not None
                        and str(existing["parent_session_id"])
                        != parent_id
                    ):
                        raise ValueError(
                            "AI 执行对话已属于其他父对话，禁止重新绑定。"
                        )
                conn.execute(
                    """
                    INSERT INTO ai_sessions (
                        session_id, parent_session_id, day, status,
                        last_seq, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        day=excluded.day,
                        status=excluded.status,
                        last_seq=excluded.last_seq,
                        updated_at=excluded.updated_at
                    """,
                    (
                        session_id,
                        parent_id,
                        str(day or ""),
                        str(status or ""),
                        int(last_seq or 0),
                        str(updated_at or "") or utc_now(),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ValueError(
                    "AI 父对话不存在，无法创建子对话。"
                ) from exc
            except BaseException:
                conn.rollback()
                raise

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
            "parent_session_id": (
                str(row["parent_session_id"])
                if row["parent_session_id"] is not None
                else None
            ),
            "day": str(row["day"] or ""),
            "status": str(row["status"] or ""),
            "last_seq": int(row["last_seq"] or 0),
            "updated_at": str(row["updated_at"] or ""),
        }

    def bind_ai_session_parent(
        self,
        session_id: str,
        parent_session_id: str,
    ) -> None:
        """幂等绑定直接父会话；已经属于其他父会话时拒绝抢占。"""

        child_id = str(session_id or "").strip()
        parent_id = str(parent_session_id or "").strip()
        if not child_id or not parent_id:
            raise ValueError("AI 会话父子绑定缺少会话 ID。")
        if child_id == parent_id:
            raise ValueError("AI 对话不能绑定为自己的子对话。")
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    parent = conn.execute(
                        """
                        SELECT parent_session_id FROM ai_sessions
                        WHERE session_id = ?
                        """,
                        (parent_id,),
                    ).fetchone()
                    if parent is None:
                        raise ValueError("AI 父对话不存在，无法关联。")
                    if parent["parent_session_id"] is not None:
                        raise ValueError("AI 父对话必须是根对话。")
                    child = conn.execute(
                        """
                        SELECT parent_session_id FROM ai_sessions
                        WHERE session_id = ?
                        """,
                        (child_id,),
                    ).fetchone()
                    if child is None:
                        raise ValueError("AI 执行对话不存在，无法关联。")
                    existing_parent = child["parent_session_id"]
                    if existing_parent is None:
                        conn.execute(
                            """
                            UPDATE ai_sessions
                            SET parent_session_id = ?
                            WHERE session_id = ?
                            """,
                            (parent_id, child_id),
                        )
                    elif str(existing_parent) != parent_id:
                        raise ValueError(
                            "AI 执行对话已属于其他父对话，禁止重新绑定。"
                        )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    @staticmethod
    def _ai_session_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_id": str(row["session_id"] or ""),
            "parent_session_id": (
                str(row["parent_session_id"])
                if row["parent_session_id"] is not None
                else None
            ),
            "day": str(row["day"] or ""),
            "status": str(row["status"] or ""),
            "last_seq": int(row["last_seq"] or 0),
            "updated_at": str(row["updated_at"] or ""),
        }

    def list_ai_sessions(
        self,
        limit: int = 50,
        *,
        include_children: bool = False,
    ) -> list[dict[str, Any]]:
        where = "" if include_children else "WHERE parent_session_id IS NULL"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM ai_sessions
                {where}
                ORDER BY updated_at DESC, session_id DESC
                LIMIT ?
                """,
                (max(1, int(limit or 50)),),
            ).fetchall()
        return [self._ai_session_row(row) for row in rows]

    def list_ai_session_children(
        self,
        parent_session_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        parent_id = str(parent_session_id or "").strip()
        if not parent_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ai_sessions
                WHERE parent_session_id = ?
                ORDER BY updated_at DESC, session_id DESC
                LIMIT ?
                """,
                (parent_id, max(1, int(limit or 50))),
            ).fetchall()
        return [self._ai_session_row(row) for row in rows]

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

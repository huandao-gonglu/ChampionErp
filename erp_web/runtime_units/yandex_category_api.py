# -*- coding: utf-8 -*-
"""Yandex Market 类目树与类目属性 API 适配（统一 SQLite WAL+FTS5 缓存）。

类目树完整节点（分支 + 叶子类目 + 祖先链）以公共全局作用域（``scope_key="global"``）
写入 ``CategoryCatalogStore``，多账号共用同一份平台类目树（读取与刷新前仍先校验已配置凭据）：
- 支持俄语词干 BM25 检索、纯 ID 直查、树分支展开与同父兄弟叶子扩展；
- 搜索结果与祖先链反查实时结合禁售名单，零全树解压；
- 类目参数（含官方 ``description``、完整枚举值与按业务配置的 ``distinctive`` 变体标志）
  按稳定的 ``business:{business_id}`` 隔离缓存于 SQLite（6 小时窗口，令牌轮换不失效），
  ``force_refresh=True`` 时主动穿透刷新。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from erp_web.marketplaces.yandex_http import (
    fetch_yandex_category_parameters,
    fetch_yandex_category_tree,
)
from erp_web.schemas.category import CategoryBrowseResult, CategoryCorpusInfo
from erp_web.services.forbidden_category_service import (
    forbidden_category_match,
    forbidden_category_reason,
)

logger = logging.getLogger(__name__)


YANDEX_CATEGORY_CACHE_FRESH_TTL = timedelta(hours=6)
YANDEX_CATEGORY_CACHE_MAX_AGE = timedelta(days=7)
YANDEX_CATEGORY_PARAMETERS_TTL_SECONDS = 6 * 3600.0
YANDEX_TAXONOMY_SCOPE = "global"
_STALE_RETRY_COOLDOWN_SECONDS = 60.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load_store_config() -> dict[str, Any]:
    from erp_web.context import get_context

    return get_context().config.load_store_config()


def _catalog_store():
    from erp_web.context import get_context

    return get_context().category_catalog_store


def _yandex_credentials() -> tuple[str, str]:
    """返回 ``(api_token, business_id)``；business_id 来自已验证授权元数据。"""

    config = _load_store_config()
    yandex = config.get("yandex") if isinstance(config.get("yandex"), dict) else {}
    api_token = _text(yandex.get("api_token"))
    business_id = _text(yandex.get("business_id"))
    if not api_token:
        raise RuntimeError("请先填写 Yandex API-Key Token。")
    if not business_id:
        raise RuntimeError("Yandex business_id 尚未通过在线授权校验，请先测试授权。")
    return api_token, business_id


def _business_scope_key(business_id: str) -> str:
    bid = _text(business_id)
    if not bid:
        raise RuntimeError("Yandex business_id 尚未通过在线授权校验，请先测试授权。")
    return f"business:{bid}"


def yandex_credential_scope_hash() -> str:
    """校验当前 Yandex 凭据并返回按 business_id 隔离的属性定义作用域（令牌轮换不失效）。"""

    _, business_id = _yandex_credentials()
    return _business_scope_key(business_id)


def _language_key(language: str) -> str:
    return _text(language).upper() or "RU"


def _node_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children")
    if not isinstance(children, list):
        return []
    return [item for item in children if isinstance(item, dict)]


def _node_is_leaf(node: dict[str, Any]) -> bool:
    if isinstance(node.get("is_leaf"), bool):
        return bool(node["is_leaf"])
    if isinstance(node.get("isLeaf"), bool):
        return bool(node["isLeaf"])
    return not _node_children(node)


def _build_taxonomy_nodes(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """展平完整类目树为分支 + 可发布叶子类目节点，完整记录祖先 ID 链。"""

    nodes: dict[str, dict[str, Any]] = {}
    children_map: dict[str, set[str]] = {}

    def walk(
        node: dict[str, Any],
        names: list[str],
        ancestor_ids: list[str],
        parent_id: str,
    ) -> None:
        name = _text(node.get("name") or node.get("title") or node.get("category_name"))
        category_id = _text(node.get("id") or node.get("category_id") or node.get("categoryId"))
        path = [*names, name] if name else list(names)
        if not category_id:
            for child in _node_children(node):
                walk(child, path, ancestor_ids, parent_id)
            return
        if _node_is_leaf(node):
            if category_id not in nodes:
                slim_raw = {key: value for key, value in node.items() if key != "children"}
                nodes[category_id] = {
                    "node_key": category_id,
                    "category_id": category_id,
                    "description_category_id": "",
                    "parent_key": parent_id,
                    "level": "product_type",
                    "depth": len(path or [name or category_id]),
                    "name": name or category_id,
                    "name_zh": "",
                    "path_segments": path or [name or category_id],
                    "path_text": " / ".join(path or [name or category_id]),
                    "path_segments_zh": [],
                    "path_zh": "",
                    "ancestor_ids": list(ancestor_ids),
                    "child_count": 0,
                    "publishable": True,
                    "raw": slim_raw,
                }
                children_map.setdefault(parent_id, set()).add(category_id)
            return

        nodes.setdefault(
            category_id,
            {
                "node_key": category_id,
                "category_id": category_id,
                "description_category_id": "",
                "parent_key": parent_id,
                "level": "branch",
                "depth": len(path or [name or category_id]),
                "name": name or category_id,
                "name_zh": "",
                "path_segments": path or [name or category_id],
                "path_text": " / ".join(path or [name or category_id]),
                "path_segments_zh": [],
                "path_zh": "",
                "ancestor_ids": list(ancestor_ids),
                "child_count": 0,
                "publishable": False,
                "raw": {},
            },
        )
        children_map.setdefault(parent_id, set()).add(category_id)
        next_ancestors = [*ancestor_ids, category_id]
        for child in _node_children(node):
            walk(child, path, next_ancestors, category_id)

    for root in tree:
        walk(root, [], [], "")

    publishable_ancestors: set[str] = set()
    for node in nodes.values():
        if node["publishable"]:
            publishable_ancestors.update(node["ancestor_ids"])
            if node["parent_key"]:
                publishable_ancestors.add(node["parent_key"])

    valid_nodes = {
        key: node
        for key, node in nodes.items()
        if node["publishable"] or key in publishable_ancestors
    }
    for key, node in valid_nodes.items():
        node["child_count"] = sum(
            1 for child_key in children_map.get(key, set()) if child_key in valid_nodes
        )
    return list(valid_nodes.values())


def _flatten_leaf_categories(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """兼容辅助：从类目树提取全部叶子类目记录并保留祖先 ID 链。"""

    nodes = _build_taxonomy_nodes(tree)
    return [
        {
            "platform": "yandex",
            "site": "global",
            "category_id": node["category_id"],
            "name_original": node["name"],
            "category_path": node["path_text"],
            "path_original": list(node["path_segments"]),
            "parent_id": node["parent_key"],
            "ancestor_ids": list(node["ancestor_ids"]),
            "level": node["depth"],
            "is_leaf": True,
            "keywords": [item for item in (*node["path_segments"], node["category_id"]) if item],
            "raw": deepcopy(node["raw"]),
        }
        for node in nodes
        if node["publishable"]
    ]


def _stable_corpus_hash(records: list[dict[str, Any]]) -> str:
    stable_records = sorted(
        (
            {
                "category_id": _text(record.get("category_id")),
                "name": _text(record.get("name_original") or record.get("name")),
                "path": [
                    _text(item)
                    for item in (
                        record.get("path_original")
                        or record.get("path_segments")
                        or []
                    )
                    if _text(item)
                ],
                "ancestor_ids": [
                    _text(item)
                    for item in (record.get("ancestor_ids") or [])
                    if _text(item)
                ],
            }
            for record in records
            if record.get("publishable", True)
        ),
        key=lambda item: item["category_id"],
    )
    encoded = json.dumps(
        stable_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _is_transient_category_error(exc: Exception) -> bool:
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        return retryable
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in ("timeout", "timed out", "限流", "rate limit", "network")
    )


def _ensure_yandex_taxonomy(
    *,
    language: str = "RU",
    force_refresh: bool = False,
    credentials: tuple[str, ...] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[str, str, CategoryCorpusInfo]:
    resolved_language = _language_key(language)
    if credentials is not None:
        api_token = _text(credentials[0])
        if not api_token:
            raise RuntimeError("请先填写 Yandex API-Key Token。")
    else:
        api_token, _ = _yandex_credentials()
    scope_hash = YANDEX_TAXONOMY_SCOPE

    def live_loader() -> tuple[list[dict[str, Any]], str, str | None]:
        tree = fetch_yandex_category_tree(
            api_token,
            language=resolved_language,
            timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else 30.0,
        )
        nodes = _build_taxonomy_nodes(tree)
        leaves = [item for item in nodes if item.get("publishable")]
        if not leaves:
            raise RuntimeError("Yandex 类目树未返回可发布的叶子类目。")
        return nodes, _stable_corpus_hash(leaves), None

    snapshot, cache_source, stale = _catalog_store().ensure_taxonomy(
        "yandex",
        scope_hash,
        resolved_language,
        live_loader=live_loader,
        force_refresh=force_refresh,
        fresh_ttl=YANDEX_CATEGORY_CACHE_FRESH_TTL,
        max_age=YANDEX_CATEGORY_CACHE_MAX_AGE,
        is_transient_error=_is_transient_category_error,
        cooldown_seconds=_STALE_RETRY_COOLDOWN_SECONDS,
    )
    return scope_hash, resolved_language, snapshot.to_corpus_info(cache_source=cache_source, stale=stale)  # type: ignore[return-value]


def load_yandex_category_corpus(
    *,
    language: str = "RU",
    force_refresh: bool = False,
    credentials: tuple[str, ...] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    """确保 Yandex 类目树在 SQLite 中就绪并返回叶子类目语料。"""

    scope_hash, resolved_language, corpus_info = _ensure_yandex_taxonomy(
        language=language,
        force_refresh=force_refresh,
        credentials=credentials,
        timeout_seconds=timeout_seconds,
    )
    records = _catalog_store().list_leaf_records("yandex", scope_hash, resolved_language)
    return records, corpus_info


def refresh_yandex_category_corpus(
    *,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    return load_yandex_category_corpus(
        force_refresh=True,
        timeout_seconds=timeout_seconds,
    )


def fetch_yandex_category_tree_summary(
    *,
    force_refresh: bool = False,
    credentials: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    api_token = _text(credentials[0]) if credentials else ""
    records, corpus_info = load_yandex_category_corpus(
        force_refresh=force_refresh,
        credentials=(api_token,) if api_token else None,
    )
    if not records:
        raise RuntimeError("Yandex 类目树未返回可发布的叶子类目。")
    sample = records[0]
    return {
        "product_type_count": len(records),
        "sample": {
            "category_id": sample.get("category_id"),
            "path": sample.get("category_path"),
        },
        "cache": {
            "source": corpus_info.get("cache_source"),
            "stale": bool(corpus_info.get("stale")),
            "retrieved_at": corpus_info.get("retrieved_at"),
            "expires_at": corpus_info.get("expires_at"),
            "stale_until": corpus_info.get("stale_until"),
        },
    }


def _load_forbidden_categories() -> dict[str, str]:
    from erp_web.context import get_context

    settings = get_context().config.forbidden_categories()
    rows = settings.get("categories") if isinstance(settings, dict) else []
    return {
        _text(item.get("category_id")): _text(item.get("name"))
        for item in (rows if isinstance(rows, list) else [])
        if isinstance(item, dict) and _text(item.get("category_id"))
    }


def _ancestor_tuple(record: dict[str, Any]) -> tuple[str, ...]:
    raw = record.get("ancestor_ids")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(_text(item) for item in raw if _text(item))


def _annotate_forbidden(
    record: dict[str, Any],
    forbidden_map: dict[str, str],
    *,
    cache_source: str | None = None,
) -> dict[str, Any]:
    result = dict(record)
    ancestors = _ancestor_tuple(result)
    match = forbidden_category_match(
        "yandex", result["category_id"], forbidden_map, ancestor_ids=ancestors
    )
    forbidden = match is not None
    forbidden_reason = (
        forbidden_category_reason(
            "yandex", result["category_id"], forbidden_map, ancestor_ids=ancestors
        )
        if forbidden
        else ""
    )
    result.update(
        {
            "id": result["category_id"],
            "name": result["name_original"],
            "path": result["category_path"],
            "source": "yandex_category_tree",
            "cache_source": cache_source,
            "forbidden": forbidden,
            "forbidden_reason": forbidden_reason,
        }
    )
    if match is not None:
        result["forbidden_matched_category_id"] = match["matched_category_id"]
        result["forbidden_matched_category_name"] = match["matched_category_name"]
    return result


def cached_yandex_category_ancestor_map(
    *,
    api_token: str = "",
    language: str = "RU",
) -> dict[str, tuple[str, ...]]:
    """直接读取全局共享 SQLite 索引的祖先 ID 链映射，不发起远端请求、不解压全树。"""

    del api_token
    return _catalog_store().ancestor_map(
        "yandex", YANDEX_TAXONOMY_SCOPE, _language_key(language)
    )


def yandex_category_ancestor_ids(
    category_id: str,
    *,
    api_token: str = "",
    language: str = "RU",
) -> tuple[str, ...]:
    """O(log N) 单条索引反查指定叶子的可信祖先 ID 链；不发起远端请求。"""

    del api_token
    return _catalog_store().ancestor_ids_for(
        "yandex", YANDEX_TAXONOMY_SCOPE, _language_key(language), category_id
    )


def search_yandex_categories(
    query: str,
    limit: int | None = 20,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """SQLite FTS5 俄语词干 + ID 直查，并实时标注禁售（非禁售优先排序）。"""

    from erp_web.russian_text import normalize_search_terms

    query = _text(query)
    if not query:
        return []
    is_numeric_id = query.isascii() and query.isdigit()
    if not is_numeric_id and not normalize_search_terms(query):
        return []
    scope_hash, resolved_language, corpus_info = _ensure_yandex_taxonomy(
        timeout_seconds=timeout_seconds
    )
    # 搜全量后再做禁售排序并截断，避免禁售类目挤掉合规替代类目。
    raw_matches = _catalog_store().search_taxonomy(
        "yandex",
        scope_hash,
        resolved_language,
        query,
        limit=None if limit is None else max(50, int(limit) * 3),
    )
    forbidden_map = _load_forbidden_categories()
    annotated = [
        _annotate_forbidden(row, forbidden_map, cache_source=corpus_info.get("cache_source"))
        for row in raw_matches
    ]
    annotated.sort(
        key=lambda item: (
            bool(item.get("forbidden")),
            -int(item.get("score") or 0),
            str(item.get("category_path") or ""),
        )
    )
    return annotated if limit is None else annotated[: max(1, min(50, int(limit or 20)))]


def fetch_yandex_category_roots(
    *,
    timeout_seconds: float | None = None,
) -> CategoryBrowseResult:
    scope_hash, resolved_language, corpus_info = _ensure_yandex_taxonomy(
        timeout_seconds=timeout_seconds
    )
    nodes, _ = _catalog_store().list_children("yandex", scope_hash, resolved_language, [])
    return {
        "parent_ids": [],
        "nodes": nodes,  # type: ignore[typeddict-item]
        "source": str(corpus_info.get("cache_source") or "yandex_cache"),
    }


def fetch_yandex_category_children(
    parent_ids: list[str],
    *,
    timeout_seconds: float | None = None,
) -> CategoryBrowseResult:
    normalized = list(dict.fromkeys(_text(p) for p in parent_ids if _text(p)))
    if not normalized:
        raise ValueError("类目树导航至少需要一个 parent_id。")
    if len(normalized) > 2:
        raise ValueError("类目树导航每次最多展开两个父节点。")
    scope_hash, resolved_language, corpus_info = _ensure_yandex_taxonomy(
        timeout_seconds=timeout_seconds
    )
    nodes, unknown = _catalog_store().list_children(
        "yandex", scope_hash, resolved_language, normalized
    )
    if unknown:
        raise RuntimeError("Yandex 类目导航包含未知 parent_id。")
    forbidden_map = _load_forbidden_categories()
    for node in nodes:
        if node.get("level") == "product_type":
            cid = _text(node.get("category_id") or node.get("node_id"))
            ancestors = _catalog_store().ancestor_ids_for(
                "yandex", scope_hash, resolved_language, cid
            )
            match = forbidden_category_match("yandex", cid, forbidden_map, ancestor_ids=ancestors)
            if match is not None:
                node["forbidden"] = True
                node["forbidden_reason"] = forbidden_category_reason(
                    "yandex", cid, forbidden_map, ancestor_ids=ancestors
                )
    return {
        "parent_ids": normalized,
        "nodes": nodes,  # type: ignore[typeddict-item]
        "source": str(corpus_info.get("cache_source") or "yandex_cache"),
    }


def fetch_yandex_category_siblings(
    category_ids: list[str],
    *,
    limit: int = 12,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    scope_hash, resolved_language, corpus_info = _ensure_yandex_taxonomy(
        timeout_seconds=timeout_seconds
    )
    rows = _catalog_store().sibling_leaves(
        "yandex", scope_hash, resolved_language, category_ids, limit=limit
    )
    forbidden_map = _load_forbidden_categories()
    return [
        _annotate_forbidden(row, forbidden_map, cache_source=corpus_info.get("cache_source"))
        for row in rows
    ]


def fetch_yandex_leaf_record(
    category_id: str,
    *,
    include_attributes: bool = False,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    category_id = _text(category_id)
    if not category_id:
        raise RuntimeError("缺少 Yandex 类目 ID。")
    scope_hash, resolved_language, _ = _ensure_yandex_taxonomy(timeout_seconds=timeout_seconds)
    record = _catalog_store().find_leaf_by_category_id(
        "yandex", scope_hash, resolved_language, category_id
    )
    if record is None:
        raise RuntimeError("未找到 Yandex 叶子类目。请从类目检索结果中选择可发布的叶子类目。")
    record["attributes"] = {"required": [], "optional": []}
    if include_attributes:
        parameters = fetch_yandex_category_parameter_definitions(
            category_id,
            timeout_seconds=timeout_seconds,
            force_refresh=force_refresh,
        )
        record["attributes"] = {
            "required": [item for item in parameters if item.get("required")],
            "optional": [item for item in parameters if not item.get("required")],
        }
    return record


def fetch_yandex_category_parameter_definitions(
    category_id: str,
    *,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """读取类目参数并按 6 小时窗口缓存于 SQLite（与定义新鲜期一致，保护小时级配额）。"""

    category_id = _text(category_id)
    api_token, business_id = _yandex_credentials()
    scope_key = _business_scope_key(business_id)
    cache_key = f"yandex:params:{scope_key}:{category_id}"

    def loader() -> list[dict[str, Any]]:
        parameters = fetch_yandex_category_parameters(
            api_token,
            category_id,
            business_id,
            timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else 30.0,
        )
        normalized = [_normalize_yandex_parameter(item) for item in parameters]
        return [item for item in normalized if item.get("parameter_id")]

    return list(
        _catalog_store().load_cached_value(
            cache_key,
            platform="yandex",
            scope_key=scope_key,
            loader=loader,
            ttl_seconds=YANDEX_CATEGORY_PARAMETERS_TTL_SECONDS,
            force_refresh=force_refresh,
        )
    )


def clear_yandex_category_cache(
    cache_root: Path | None = None,
    *,
    credential_scope_hash: str | None = None,
) -> int:
    """按凭据作用域或全量清理 Yandex SQLite 类目树与参数缓存。"""

    del cache_root
    store = _catalog_store()
    removed = store.clear_taxonomy("yandex", scope_key=credential_scope_hash)
    store.clear_cached_values("yandex", scope_key=credential_scope_hash)
    return removed


def _normalize_yandex_parameter(item: dict[str, Any]) -> dict[str, Any]:
    parameter_id = _text(item.get("id") or item.get("parameterId") or item.get("parameter_id"))
    parameter_type = _text(
        item.get("type") or item.get("parameter_type") or "TEXT"
    ).upper()
    values_raw = item.get("values") if isinstance(item.get("values"), list) else []
    values: list[dict[str, str]] = []
    for row in values_raw:
        if not isinstance(row, dict):
            text = _text(row)
            if text:
                values.append({"value_id": "", "value": text})
            continue
        value_id = _text(row.get("id") or row.get("valueId") or row.get("value_id"))
        value = _text(row.get("value") or row.get("name"))
        if value_id or value:
            values.append({"value_id": value_id, "value": value or value_id})
    unit_block = item.get("unit") if isinstance(item.get("unit"), dict) else {}
    units_raw = unit_block.get("units") if isinstance(unit_block.get("units"), list) else []
    units: list[dict[str, str]] = []
    for unit_row in units_raw:
        if not isinstance(unit_row, dict):
            continue
        unit_id = _text(unit_row.get("id"))
        unit_name = _text(unit_row.get("name"))
        if not unit_id and not unit_name:
            continue
        units.append(
            {
                "id": unit_id,
                "name": unit_name or unit_id,
                "full_name": _text(unit_row.get("fullName")),
            }
        )
    default_unit_id = _text(
        unit_block.get("defaultUnitId") or item.get("default_unit_id")
    )
    default_unit_name = next(
        (unit["name"] for unit in units if unit["id"] and unit["id"] == default_unit_id),
        "",
    )
    if not default_unit_name and units:
        default_unit_name = units[0]["name"]
    constraints_raw = item.get("constraints") if isinstance(item.get("constraints"), dict) else {}
    constraints: dict[str, Any] = {}
    if constraints_raw.get("minValue") is not None:
        constraints["min_value"] = constraints_raw.get("minValue")
    if constraints_raw.get("maxValue") is not None:
        constraints["max_value"] = constraints_raw.get("maxValue")
    if constraints_raw.get("maxLength") is not None:
        constraints["max_length"] = constraints_raw.get("maxLength")
    return {
        "distinctive": item.get("distinctive") is True,
        "parameter_id": parameter_id,
        "name": _text(item.get("name") or parameter_id),
        "description": _text(item.get("description")),
        "required": bool(item.get("required") or item.get("isRequired")),
        "parameter_type": parameter_type,
        "is_collection": bool(item.get("multivalue") or item.get("is_collection")),
        "allow_custom_values": bool(
            item.get("allowCustomValues") or item.get("allow_custom_values")
        ),
        "max_value_count": int(item.get("max_value_count") or 0),
        "unit": default_unit_name,
        "unit_options": [unit["name"] for unit in units if unit["name"]],
        "units": units,
        "default_unit": default_unit_name,
        "default_unit_id": default_unit_id,
        "constraints": constraints,
        "values": values,
        "raw": deepcopy(item),
    }


__all__ = [
    "YANDEX_CATEGORY_CACHE_FRESH_TTL",
    "YANDEX_CATEGORY_CACHE_MAX_AGE",
    "YANDEX_CATEGORY_PARAMETERS_TTL_SECONDS",
    "YANDEX_TAXONOMY_SCOPE",
    "cached_yandex_category_ancestor_map",
    "clear_yandex_category_cache",
    "fetch_yandex_category_children",
    "fetch_yandex_category_parameter_definitions",
    "fetch_yandex_category_roots",
    "fetch_yandex_category_siblings",
    "fetch_yandex_category_tree_summary",
    "fetch_yandex_leaf_record",
    "load_yandex_category_corpus",
    "refresh_yandex_category_corpus",
    "search_yandex_categories",
    "yandex_category_ancestor_ids",
    "yandex_credential_scope_hash",
]

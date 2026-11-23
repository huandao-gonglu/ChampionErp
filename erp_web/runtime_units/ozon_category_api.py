# -*- coding: utf-8 -*-
"""Ozon Seller 类目树与属性 API 适配（统一 SQLite WAL+FTS5 缓存 + L1 官方双语合表）。

Ozon 创建商品时需要同时使用 ``description_category_id`` 和 ``type_id``。
本模块把平台公共类目树（含分支与全部 ``(description_category_id, type_id)`` 叶子）、
属性定义与枚举页以全局共享作用域（``scope_key="global"``）写入 ``CategoryCatalogStore``，
多账号共用同一份缓存（读取与刷新前仍先校验当前账号已配置 Ozon 凭据）：
- 刷新类目树、属性定义、非品牌枚举页时成对请求 ``DEFAULT``（发布事实）与
  ``ZH_HANS``（官方中文展示），按 ID 合表落库；``ZH_HANS`` 不可用时优雅降级；
- 支持俄语词干 BM25 检索、纯数字 ID 直查、以及官方中文关键词检索；
- 发布值始终严格保持 ``DEFAULT`` 原文与 ``dictionary_value_id``。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import logging
import ssl
import time
from typing import Any
import urllib.error

from erp_web.marketplaces.config_http import request_ozon_json
from erp_web.russian_text import has_cjk
from erp_web.runtime_units.category_definition_support import (
    skip_attribute_value_localization,
)
from erp_web.runtime_units.category_localization import seed_glossary_from_bilingual
from erp_web.schemas.category import (
    CategoryBrowseResult,
    CategoryCorpusInfo,
    normalize_category_dictionary_id,
)
from erp_web.schemas.category_definition import normalize_canonical_value_type


logger = logging.getLogger(__name__)


OZON_CATEGORY_TREE_URL = "https://api-seller.ozon.ru/v1/description-category/tree"
OZON_CATEGORY_ATTRIBUTES_URL = "https://api-seller.ozon.ru/v1/description-category/attribute"
OZON_CATEGORY_ATTRIBUTE_VALUES_URL = (
    "https://api-seller.ozon.ru/v1/description-category/attribute/values"
)
OZON_CATEGORY_ATTRIBUTE_VALUES_SEARCH_URL = (
    "https://api-seller.ozon.ru/v1/description-category/attribute/values/search"
)
OZON_CATEGORY_CACHE_FRESH_TTL = timedelta(days=1)
OZON_CATEGORY_CACHE_MAX_AGE = timedelta(days=7)
OZON_CATALOG_SCOPE = "global"
_STALE_RETRY_COOLDOWN_SECONDS = 60.0
_ATTRIBUTE_VALUE_CACHE_TTL_SECONDS = 15 * 60.0
_TAXONOMY_LOCALE = "ru-RU"


def _load_store_config() -> dict[str, Any]:
    from erp_web.context import get_context

    return get_context().config.load_store_config()


def _catalog_store():
    from erp_web.context import get_context

    return get_context().category_catalog_store


def _ozon_credentials() -> tuple[str, str]:
    config = _load_store_config()
    ozon = config.get("ozon") if isinstance(config.get("ozon"), dict) else {}
    client_id = str(ozon.get("client_id") or "").strip()
    api_key = str(ozon.get("api_key") or "").strip()
    if not client_id or not api_key:
        raise RuntimeError("请先填写 Ozon Client ID 和 API Key。")
    return client_id, api_key


def ozon_credential_scope_hash() -> str:
    """校验当前已配置 Ozon 凭据并返回全局共享类目缓存作用域。"""

    _ozon_credentials()
    return OZON_CATALOG_SCOPE


def _text(value: Any) -> str:
    return str(value or "").strip()


def _node_title(node: dict[str, Any]) -> str:
    return _text(node.get("type_name") or node.get("category_name") or node.get("title") or node.get("name"))


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in node.get("children", []) if isinstance(item, dict)] if isinstance(node.get("children"), list) else []


def _is_auth_error(exc: Exception) -> bool:
    if (getattr(exc, "details", {}) or {}).get("http_status") in (401, 403) or getattr(exc, "retryable", None) is False:
        return True
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            " 401",
            " 403",
            "unauthorized",
            "forbidden",
            "请先填写",
            "credentials missing",
            "missing credential",
        )
    )


def _is_transient_provider_error(exc: Exception) -> bool:
    if _is_auth_error(exc):
        return False
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            urllib.error.URLError,
            ssl.SSLError,
        ),
    ):
        return True
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            " 408",
            " 425",
            " 429",
            " 500",
            " 502",
            " 503",
            " 504",
            "timeout",
            "timed out",
            "rate limit",
            "temporarily unavailable",
            "connection",
            "network",
            "ssl",
            "unexpected_eof",
            "unexpected eof",
            "connection reset",
        )
    )


def _load_tree_from_remote(
    client_id: str,
    api_key: str,
    *,
    language: str = "DEFAULT",
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    options = {} if timeout_seconds is None else {"timeout_seconds": timeout_seconds}
    response = request_ozon_json(
        "POST",
        OZON_CATEGORY_TREE_URL,
        client_id,
        api_key,
        {"language": language},
        **options,
    )
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, list):
        raise RuntimeError("Ozon 类目树响应缺少 result 列表。")
    return [item for item in result if isinstance(item, dict)]


def _load_bilingual_trees_from_remote(
    client_id: str,
    api_key: str,
    *,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """先拉取 DEFAULT（必填发布事实），成功后再补拉 ZH_HANS（官方中文展示，失败优雅降级）。"""

    deadline_at = (
        time.monotonic() + float(timeout_seconds)
        if timeout_seconds is not None
        else None
    )
    tree_default = _load_tree_from_remote(
        client_id,
        api_key,
        language="DEFAULT",
        timeout_seconds=timeout_seconds,
    )
    if not tree_default:
        return tree_default, []
    try:
        remaining = (
            max(1.0, deadline_at - time.monotonic())
            if deadline_at is not None
            else None
        )
        tree_zh = _load_tree_from_remote(
            client_id,
            api_key,
            language="ZH_HANS",
            timeout_seconds=remaining,
        )
    except Exception:
        logger.debug("Ozon ZH_HANS 类目树拉取失败，已降级为仅 DEFAULT", exc_info=True)
        tree_zh = []
    return tree_default, tree_zh


def _extract_zh_tree_maps(
    tree_zh: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[tuple[str, str], str], dict[str, str]]:
    """从 ZH_HANS 树提取 branch_id -> 中文名、(desc_id, type_id) -> 中文名、type_id -> 中文名。"""

    branch_zh: dict[str, str] = {}
    leaf_pair_zh: dict[tuple[str, str], str] = {}
    leaf_type_zh: dict[str, str] = {}

    def walk(node: dict[str, Any], desc_id: str = "") -> None:
        title = _node_title(node)
        cur_desc = _text(node.get("description_category_id")) or desc_id
        node_branch_id = _text(node.get("description_category_id") or node.get("category_id"))
        type_id = _text(node.get("type_id"))
        if node_branch_id and not type_id and title and has_cjk(title):
            branch_zh.setdefault(node_branch_id, title)
        if type_id and title and has_cjk(title):
            leaf_pair_zh[(cur_desc, type_id)] = title
            leaf_type_zh.setdefault(type_id, title)
        for child in _children(node):
            walk(child, cur_desc)

    for root in tree_zh:
        walk(root)
    return branch_zh, leaf_pair_zh, leaf_type_zh


def _build_taxonomy_nodes(
    tree_default: list[dict[str, Any]],
    tree_zh: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """展平 Ozon 树为完整分支 + 复合键叶子节点，并合并官方中文。"""

    branch_zh, leaf_pair_zh, leaf_type_zh = _extract_zh_tree_maps(tree_zh or [])
    nodes: dict[str, dict[str, Any]] = {}
    children_map: dict[str, set[str]] = {}
    glossary_seed: dict[str, str] = {}

    def walk(
        node: dict[str, Any],
        names: list[str],
        names_zh: list[str],
        branch_ids: list[str],
        description_category_id: str = "",
    ) -> None:
        title = _node_title(node)
        node_desc_id = _text(node.get("description_category_id")) or description_category_id
        node_branch_id = _text(node.get("description_category_id") or node.get("category_id"))
        type_id = _text(node.get("type_id"))

        if type_id:
            if bool(node.get("disabled")):
                return
            name = title or type_id
            zh_name = leaf_pair_zh.get((node_desc_id, type_id)) or leaf_type_zh.get(type_id, "")
            if zh_name and name and zh_name != name:
                glossary_seed[name] = zh_name
            path_orig = [*names, name] if name else list(names)
            path_zh_list = [*names_zh, zh_name or name] if any((*names_zh, zh_name)) else []
            has_any_zh = bool(zh_name or any(has_cjk(seg) for seg in names_zh))
            node_key = f"{node_desc_id}:{type_id}" if node_desc_id else type_id
            parent_key = branch_ids[-1] if branch_ids else node_desc_id
            slim_raw = {k: v for k, v in node.items() if k != "children"}
            nodes[node_key] = {
                "node_key": node_key,
                "category_id": type_id,
                "description_category_id": node_desc_id,
                "parent_key": parent_key,
                "level": "product_type",
                "depth": len(path_orig),
                "name": name,
                "name_zh": zh_name,
                "path_segments": path_orig,
                "path_text": " / ".join(path_orig),
                "path_segments_zh": path_zh_list if has_any_zh else [],
                "path_zh": " / ".join(path_zh_list) if has_any_zh else "",
                "ancestor_ids": list(branch_ids),
                "child_count": 0,
                "publishable": True,
                "raw": slim_raw,
            }
            children_map.setdefault(parent_key, set()).add(node_key)
            return

        next_names = [*names, title] if title else list(names)
        zh_branch = branch_zh.get(node_branch_id, "") if node_branch_id else ""
        if zh_branch and title and zh_branch != title:
            glossary_seed[title] = zh_branch
        next_names_zh = [*names_zh, zh_branch or title] if (zh_branch or title) else list(names_zh)
        next_branch_ids = [*branch_ids, node_branch_id] if node_branch_id else list(branch_ids)

        if node_branch_id:
            parent_key = branch_ids[-1] if branch_ids else ""
            has_any_zh = bool(zh_branch or any(has_cjk(seg) for seg in names_zh))
            nodes.setdefault(
                node_branch_id,
                {
                    "node_key": node_branch_id,
                    "category_id": node_branch_id,
                    "description_category_id": node_branch_id,
                    "parent_key": parent_key,
                    "level": "branch",
                    "depth": len(next_names) or 1,
                    "name": title or node_branch_id,
                    "name_zh": zh_branch,
                    "path_segments": next_names or [title or node_branch_id],
                    "path_text": " / ".join(next_names or [title or node_branch_id]),
                    "path_segments_zh": next_names_zh if has_any_zh else [],
                    "path_zh": " / ".join(next_names_zh) if has_any_zh else "",
                    "ancestor_ids": list(branch_ids),
                    "child_count": 0,
                    "publishable": False,
                    "raw": {},
                },
            )
            children_map.setdefault(parent_key, set()).add(node_branch_id)

        for child in _children(node):
            walk(child, next_names, next_names_zh, next_branch_ids, node_desc_id)

    for root in tree_default:
        walk(root, [], [], [])

    # 仅保留包含至少一个可发布叶子的分支，避免导航出现空分支。
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
    return list(valid_nodes.values()), glossary_seed


def _flatten_product_types(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """兼容辅助：从树中提取全部可发布商品类型记录（保留全部复合键对）。"""

    nodes, _ = _build_taxonomy_nodes(tree, None)
    records: list[dict[str, Any]] = []
    for node in nodes:
        if not node["publishable"]:
            continue
        type_id = node["category_id"]
        desc_id = node["description_category_id"]
        records.append(
            {
                "platform": "ozon",
                "site": "global",
                "node_key": node["node_key"],
                "category_id": type_id,
                "description_category_id": desc_id,
                "subject_id": desc_id,
                "type_id": type_id,
                "name_original": node["name"],
                "name_zh": node["name_zh"],
                "name_cn": node["name_zh"],
                "category_path": node["path_text"],
                "path_zh": node["path_zh"],
                "path_original": list(node["path_segments"]),
                "path_ids": list(node["ancestor_ids"]) or [desc_id, type_id],
                "path_cn": list(node["path_segments_zh"]),
                "parent_id": desc_id,
                "ancestor_ids": list(node["ancestor_ids"]),
                "level": node["depth"],
                "is_leaf": True,
                "raw": deepcopy(node["raw"]),
            }
        )
    return records


def _stable_corpus_hash(records: list[dict[str, Any]]) -> str:
    stable_records = sorted(
        (
            {
                "category_id": _text(record.get("category_id") or record.get("type_id")),
                "description_category_id": _text(record.get("description_category_id")),
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
            }
            for record in records
            if record.get("publishable", True)
        ),
        key=lambda item: (
            item["category_id"],
            item["description_category_id"],
            item["name"],
        ),
    )
    encoded = json.dumps(
        stable_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _ensure_ozon_taxonomy(
    *,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
    credentials: tuple[str, str] | None = None,
) -> tuple[str, CategoryCorpusInfo]:
    client_id, api_key = credentials or _ozon_credentials()
    client_id = _text(client_id)
    api_key = _text(api_key)
    if not client_id or not api_key:
        raise RuntimeError("请先填写 Ozon Client ID 和 API Key。")
    scope_hash = OZON_CATALOG_SCOPE
    deadline_at = (
        time.monotonic() + float(timeout_seconds)
        if timeout_seconds is not None
        else None
    )

    def remaining_timeout() -> float | None:
        if deadline_at is None:
            return None
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Ozon 类目语料加载超过 deadline")
        return remaining

    def live_loader() -> tuple[list[dict[str, Any]], str, str | None]:
        tree_default, tree_zh = _load_bilingual_trees_from_remote(
            client_id,
            api_key,
            timeout_seconds=remaining_timeout(),
        )
        nodes, glossary_seed = _build_taxonomy_nodes(tree_default, tree_zh)
        leaves = [item for item in nodes if item.get("publishable")]
        if not leaves:
            raise RuntimeError("Ozon 类目树未返回可发布的商品类型。")
        if glossary_seed:
            seed_glossary_from_bilingual("ozon", glossary_seed)
        return nodes, _stable_corpus_hash(leaves), None

    snapshot, cache_source, stale = _catalog_store().ensure_taxonomy(
        "ozon",
        scope_hash,
        _TAXONOMY_LOCALE,
        live_loader=live_loader,
        force_refresh=force_refresh,
        fresh_ttl=OZON_CATEGORY_CACHE_FRESH_TTL,
        max_age=OZON_CATEGORY_CACHE_MAX_AGE,
        is_transient_error=_is_transient_provider_error,
        cooldown_seconds=_STALE_RETRY_COOLDOWN_SECONDS,
    )
    return scope_hash, snapshot.to_corpus_info(cache_source=cache_source, stale=stale)  # type: ignore[return-value]


def load_ozon_category_corpus(
    *,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
    credentials: tuple[str, str] | None = None,
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    """确保 Ozon 类目树在 SQLite 中就绪并返回可发布商品类型列表。"""

    scope_hash, corpus_info = _ensure_ozon_taxonomy(
        timeout_seconds=timeout_seconds,
        force_refresh=force_refresh,
        credentials=credentials,
    )
    records = _catalog_store().list_leaf_records("ozon", scope_hash, _TAXONOMY_LOCALE)
    return records, corpus_info


def search_ozon_categories(
    query: str,
    limit: int | None = 20,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """SQLite FTS5 中俄双语词干 + ID 直查检索（零全表扫描、零全树拷贝）。"""

    query = _text(query)
    if not query:
        return []
    scope_hash, corpus_info = _ensure_ozon_taxonomy(timeout_seconds=timeout_seconds)
    matches = _catalog_store().search_taxonomy(
        "ozon",
        scope_hash,
        _TAXONOMY_LOCALE,
        query,
        limit=limit,
    )
    results: list[dict[str, Any]] = []
    for record in matches:
        result = dict(record)
        result.update(
            {
                "id": result["category_id"],
                "name": result["name_original"],
                "path": result["category_path"],
                "source": "ozon_category_tree",
                "cache_source": corpus_info.get("cache_source"),
            }
        )
        results.append(result)
    return results


def fetch_ozon_category_roots(
    *,
    timeout_seconds: float | None = None,
) -> CategoryBrowseResult:
    scope_hash, corpus_info = _ensure_ozon_taxonomy(timeout_seconds=timeout_seconds)
    nodes, _ = _catalog_store().list_children("ozon", scope_hash, _TAXONOMY_LOCALE, [])
    return {
        "parent_ids": [],
        "nodes": nodes,  # type: ignore[typeddict-item]
        "source": str(corpus_info.get("cache_source") or "ozon_cache"),
    }


def fetch_ozon_category_children(
    parent_ids: list[str],
    *,
    timeout_seconds: float | None = None,
) -> CategoryBrowseResult:
    normalized = list(dict.fromkeys(_text(p) for p in parent_ids if _text(p)))
    if not normalized:
        raise ValueError("类目树导航至少需要一个 parent_id。")
    if len(normalized) > 2:
        raise ValueError("类目树导航每次最多展开两个父节点。")
    scope_hash, corpus_info = _ensure_ozon_taxonomy(timeout_seconds=timeout_seconds)
    nodes, unknown = _catalog_store().list_children(
        "ozon", scope_hash, _TAXONOMY_LOCALE, normalized
    )
    if unknown:
        raise RuntimeError("Ozon 类目导航包含未知 parent_id。")
    return {
        "parent_ids": normalized,
        "nodes": nodes,  # type: ignore[typeddict-item]
        "source": str(corpus_info.get("cache_source") or "ozon_cache"),
    }


def fetch_ozon_category_siblings(
    category_ids: list[str],
    *,
    limit: int = 12,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    scope_hash, corpus_info = _ensure_ozon_taxonomy(timeout_seconds=timeout_seconds)
    rows = _catalog_store().sibling_leaves(
        "ozon", scope_hash, _TAXONOMY_LOCALE, category_ids, limit=limit
    )
    for row in rows:
        row["id"] = row["category_id"]
        row["name"] = row["name_original"]
        row["path"] = row["category_path"]
        row["source"] = "ozon_category_tree"
        row["cache_source"] = corpus_info.get("cache_source")
    return rows


def fetch_ozon_category_tree_summary(
    *,
    force_refresh: bool = False,
    credentials: tuple[str, str] | None = None,
) -> dict[str, Any]:
    product_types, corpus_info = load_ozon_category_corpus(
        force_refresh=force_refresh,
        credentials=credentials,
    )
    if not product_types:
        raise RuntimeError("Ozon 类目树未返回可发布的商品类型。")
    sample = product_types[0]
    return {
        "product_type_count": len(product_types),
        "sample": {
            "type_id": sample.get("type_id"),
            "description_category_id": sample.get("description_category_id"),
            "path": sample.get("category_path"),
            "path_zh": sample.get("path_zh") or "",
        },
        "cache": {
            "source": corpus_info.get("cache_source"),
            "stale": bool(corpus_info.get("stale")),
            "retrieved_at": corpus_info.get("retrieved_at"),
            "expires_at": corpus_info.get("expires_at"),
            "stale_until": corpus_info.get("stale_until"),
        },
    }


def refresh_ozon_category_corpus(
    *,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    return load_ozon_category_corpus(
        timeout_seconds=timeout_seconds,
        force_refresh=True,
    )


def _normalize_attribute(
    item: dict[str, Any],
    *,
    zh_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dictionary_id = normalize_category_dictionary_id(item.get("dictionary_id"))
    raw_type = _text(item.get("type")) or "string"
    zh_name = _text(zh_item.get("name")) if isinstance(zh_item, dict) else ""
    zh_desc = _text(zh_item.get("description")) if isinstance(zh_item, dict) else ""
    return {
        "id": _text(item.get("id")),
        "name": _text(item.get("name") or item.get("id")),
        "name_zh": zh_name if has_cjk(zh_name) else "",
        "required": bool(item.get("is_required")),
        "value_type": normalize_canonical_value_type(raw_type) or "string",
        "platform_type": raw_type,
        "unit": _text(item.get("unit")),
        "dictionary_id": dictionary_id,
        "is_dictionary": bool(dictionary_id),
        "is_collection": bool(item.get("is_collection")),
        "max_value_count": int(item.get("max_value_count") or 0),
        "category_dependent": bool(item.get("category_dependent")),
        "options": [],
        "description": _text(item.get("description")),
        "description_zh": zh_desc if has_cjk(zh_desc) else "",
        "raw": deepcopy(item),
    }


def _attribute_value_page(
    *,
    description_category_id: int,
    type_id: int,
    attribute_id: int,
    last_value_id: int,
    client_id: str,
    api_key: str,
    timeout_seconds: float,
    skip_zh: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    scope_hash = OZON_CATALOG_SCOPE
    cache_key = f"ozon:attr_vals:{scope_hash}:{description_category_id}:{type_id}:{attribute_id}:{last_value_id}"

    def fetch_page() -> dict[str, Any]:
        payload_default = {
            "description_category_id": description_category_id,
            "type_id": type_id,
            "attribute_id": attribute_id,
            "language": "DEFAULT",
            "last_value_id": last_value_id,
            "limit": 2000,
        }

        def fetch_zh() -> dict[str, str]:
            if skip_zh:
                return {}
            try:
                zh_resp = request_ozon_json(
                    "POST",
                    OZON_CATEGORY_ATTRIBUTE_VALUES_URL,
                    client_id,
                    api_key,
                    {**payload_default, "language": "ZH_HANS"},
                    timeout_seconds=timeout_seconds,
                )
                zh_rows = zh_resp.get("result") if isinstance(zh_resp, dict) else None
                if not isinstance(zh_rows, list):
                    return {}
                return {
                    _text(row.get("id")): _text(row.get("value"))
                    for row in zh_rows
                    if isinstance(row, dict)
                    and _text(row.get("id"))
                    and has_cjk(_text(row.get("value")))
                }
            except Exception:
                logger.debug("Ozon ZH_HANS 枚举页读取失败，已降级为 DEFAULT", exc_info=True)
                return {}

        if skip_zh:
            response = request_ozon_json(
                "POST",
                OZON_CATEGORY_ATTRIBUTE_VALUES_URL,
                client_id,
                api_key,
                payload_default,
                timeout_seconds=timeout_seconds,
            )
            zh_by_id: dict[str, str] = {}
        else:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ozon-vals") as pool:
                fut_def = pool.submit(
                    copy_context().run,
                    request_ozon_json,
                    "POST",
                    OZON_CATEGORY_ATTRIBUTE_VALUES_URL,
                    client_id,
                    api_key,
                    payload_default,
                    timeout_seconds=timeout_seconds,
                )
                fut_zh = pool.submit(copy_context().run, fetch_zh)
                response = fut_def.result()
                zh_by_id = fut_zh.result()

        raw_values = response.get("result") if isinstance(response, dict) else None
        if not isinstance(raw_values, list):
            raise RuntimeError("Ozon 属性枚举响应缺少 result 列表。")
        values: list[dict[str, Any]] = []
        glossary_seed: dict[str, str] = {}
        for item in raw_values:
            if not isinstance(item, dict):
                continue
            vid = _text(item.get("id"))
            val = _text(item.get("value"))
            if not vid or not val:
                continue
            label_zh = zh_by_id.get(vid, "")
            if label_zh and label_zh != val:
                glossary_seed[val] = label_zh
            values.append(
                {
                    "id": vid,
                    "value": val,
                    "label_zh": label_zh,
                    "info": _text(item.get("info")),
                    "picture": _text(item.get("picture")),
                }
            )
        if glossary_seed:
            seed_glossary_from_bilingual("ozon", glossary_seed)
        has_more = bool(response.get("has_next")) if "has_next" in response else len(raw_values) >= 2000
        return {"start_after": last_value_id, "values": values, "has_more": has_more}

    # 若第 0 页已缓存且覆盖当前游标（常见于小/中字典），直接切分避免多余请求。
    if last_value_id > 0:
        first_key = f"ozon:attr_vals:{scope_hash}:{description_category_id}:{type_id}:{attribute_id}:0"
        first_cached = _catalog_store().get_cached_value(first_key)
        if isinstance(first_cached, dict):
            rows = first_cached.get("values") or []
            for offset, row in enumerate(rows):
                if row.get("id") == str(last_value_id):
                    remainder = rows[offset + 1 :]
                    if remainder or not first_cached.get("has_more"):
                        return remainder, bool(first_cached.get("has_more"))
                    break

    cached = _catalog_store().load_cached_value(
        cache_key,
        platform="ozon",
        scope_key=scope_hash,
        loader=fetch_page,
        ttl_seconds=_ATTRIBUTE_VALUE_CACHE_TTL_SECONDS,
    )
    return list(cached.get("values") or []), bool(cached.get("has_more"))


def _search_attribute_values(
    *,
    description_category_id: int,
    type_id: int,
    attribute_id: int,
    value: str,
    limit: int,
    client_id: str,
    api_key: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    scope_hash = OZON_CATALOG_SCOPE
    cache_key = (
        f"ozon:attr_search:{scope_hash}:{description_category_id}:{type_id}:"
        f"{attribute_id}:{value.casefold()}:{limit}"
    )

    def fetch_search() -> list[dict[str, Any]]:
        response = request_ozon_json(
            "POST",
            OZON_CATEGORY_ATTRIBUTE_VALUES_SEARCH_URL,
            client_id,
            api_key,
            {
                "attribute_id": attribute_id,
                "description_category_id": description_category_id,
                "limit": limit,
                "type_id": type_id,
                "value": value,
            },
            timeout_seconds=timeout_seconds,
        )
        raw_values = response.get("result") if isinstance(response, dict) else None
        if not isinstance(raw_values, list):
            raise RuntimeError("Ozon 属性枚举搜索响应缺少 result 列表。")
        return [
            {
                "id": _text(item.get("id")),
                "value": _text(item.get("value")),
                "label_zh": "",
                "info": _text(item.get("info")),
                "picture": _text(item.get("picture")),
            }
            for item in raw_values
            if isinstance(item, dict)
            and _text(item.get("id"))
            and _text(item.get("value"))
        ]

    return list(
        _catalog_store().load_cached_value(
            cache_key,
            platform="ozon",
            scope_key=scope_hash,
            loader=fetch_search,
            ttl_seconds=_ATTRIBUTE_VALUE_CACHE_TTL_SECONDS,
        )
    )


def fetch_ozon_category_attribute_values(
    category_id: str,
    attribute_id: str,
    *,
    query: str = "",
    limit: int = 50,
    start_after_value_id: int | str = 0,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    type_id_text = _text(category_id)
    attribute_id_text = _text(attribute_id)
    if not type_id_text or not attribute_id_text:
        raise ValueError("缺少 Ozon 商品类型 ID 或属性 ID。")
    try:
        type_id = int(type_id_text)
        requested_attribute_id = int(attribute_id_text)
    except ValueError as exc:
        raise ValueError("Ozon 商品类型 ID 和属性 ID 必须是整数。") from exc
    try:
        last_value_id = int(str(start_after_value_id or "0").strip() or 0)
    except ValueError:
        last_value_id = 0
    if last_value_id < 0:
        last_value_id = 0
    raw_query = _text(query)
    if raw_query and len(raw_query) < 2:
        raise ValueError("Ozon 属性枚举搜索词至少需要 2 个字符。")

    deadline_at = (
        time.monotonic() + float(timeout_seconds)
        if timeout_seconds is not None
        else time.monotonic() + 30
    )
    scope_hash, _ = _ensure_ozon_taxonomy(
        timeout_seconds=max(0.1, deadline_at - time.monotonic())
    )
    record = _catalog_store().find_leaf_by_category_id(
        "ozon", scope_hash, _TAXONOMY_LOCALE, type_id_text
    )
    if record is None:
        raise RuntimeError("未找到 Ozon 商品类型。请从类目检索结果中选择可发布的商品类型。")
    try:
        description_category_id = int(_text(record.get("description_category_id")))
    except ValueError as exc:
        raise RuntimeError("Ozon description_category_id 格式无效。") from exc
    client_id, api_key = _ozon_credentials()
    safe_limit = max(1, min(100, int(limit or 50)))

    if raw_query:
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Ozon 属性枚举搜索超时。")
        matches = _search_attribute_values(
            description_category_id=description_category_id,
            type_id=type_id,
            attribute_id=requested_attribute_id,
            value=raw_query,
            limit=safe_limit,
            client_id=client_id,
            api_key=api_key,
            timeout_seconds=remaining,
        )
        return {
            "ok": True,
            "platform": "ozon",
            "category_id": type_id_text,
            "description_category_id": str(description_category_id),
            "attribute_id": attribute_id_text,
            "query": raw_query,
            "values": matches[:safe_limit],
            "scanned": len(matches),
            "complete": len(matches) < safe_limit,
            "has_more": False,
        }

    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Ozon 属性枚举读取超时。")
    page, remote_has_more = _attribute_value_page(
        description_category_id=description_category_id,
        type_id=type_id,
        attribute_id=requested_attribute_id,
        last_value_id=last_value_id,
        client_id=client_id,
        api_key=api_key,
        timeout_seconds=remaining,
        skip_zh=skip_attribute_value_localization("ozon", attribute_id_text),
    )
    result_values = page[:safe_limit]
    has_more = len(page) > safe_limit or remote_has_more
    return {
        "ok": True,
        "platform": "ozon",
        "category_id": type_id_text,
        "description_category_id": str(description_category_id),
        "attribute_id": attribute_id_text,
        "query": raw_query,
        "values": result_values,
        "scanned": len(page),
        "complete": not has_more,
        "has_more": has_more,
    }


def fetch_ozon_category_record(
    category_id: str,
    include_attributes: bool = False,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    type_id = _text(category_id)
    if not type_id:
        raise RuntimeError("缺少 Ozon 商品类型 ID。")
    deadline_at = (
        time.monotonic() + float(timeout_seconds)
        if timeout_seconds is not None
        else None
    )

    def remaining_timeout() -> float:
        if deadline_at is None:
            return 30.0
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Ozon 类目详情 deadline 已耗尽")
        return remaining

    scope_hash, _ = _ensure_ozon_taxonomy(
        timeout_seconds=None if deadline_at is None else remaining_timeout()
    )
    record = _catalog_store().find_leaf_by_category_id(
        "ozon", scope_hash, _TAXONOMY_LOCALE, type_id
    )
    if record is None:
        raise RuntimeError("未找到 Ozon 商品类型。请从类目检索结果中选择可发布的商品类型。")
    record["attributes"] = {"required": [], "optional": []}
    if not include_attributes:
        return record

    client_id, api_key = _ozon_credentials()
    description_category_id = _text(record.get("description_category_id"))
    try:
        request_category_id: int | str = int(description_category_id)
        request_type_id: int | str = int(_text(record.get("type_id")))
    except ValueError as exc:
        raise RuntimeError("Ozon 类目 ID 格式无效。") from exc

    payload_default = {
        "description_category_id": request_category_id,
        "type_id": request_type_id,
        "language": "DEFAULT",
    }
    options = {} if deadline_at is None else {"timeout_seconds": remaining_timeout()}

    def fetch_zh_attrs() -> dict[str, dict[str, Any]]:
        try:
            zh_resp = request_ozon_json(
                "POST",
                OZON_CATEGORY_ATTRIBUTES_URL,
                client_id,
                api_key,
                {**payload_default, "language": "ZH_HANS"},
                **options,
            )
            zh_rows = zh_resp.get("result") if isinstance(zh_resp, dict) else None
            if not isinstance(zh_rows, list):
                return {}
            return {
                _text(item.get("id")): item
                for item in zh_rows
                if isinstance(item, dict) and _text(item.get("id"))
            }
        except Exception:
            logger.debug("Ozon ZH_HANS 属性定义拉取失败，已降级为 DEFAULT", exc_info=True)
            return {}

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ozon-attrs") as pool:
        fut_def = pool.submit(
            copy_context().run,
            request_ozon_json,
            "POST",
            OZON_CATEGORY_ATTRIBUTES_URL,
            client_id,
            api_key,
            payload_default,
            **options,
        )
        fut_zh = pool.submit(copy_context().run, fetch_zh_attrs)
        response = fut_def.result()
        zh_by_id = fut_zh.result()

    raw_attributes = response.get("result") if isinstance(response, dict) else None
    if not isinstance(raw_attributes, list):
        raise RuntimeError("Ozon 类目属性响应缺少 result 列表。")
    normalized = [
        _normalize_attribute(item, zh_item=zh_by_id.get(_text(item.get("id"))))
        for item in raw_attributes
        if isinstance(item, dict) and _text(item.get("id"))
    ]
    glossary_seed: dict[str, str] = {}
    for attr in normalized:
        if attr.get("name_zh") and attr["name"] != attr["name_zh"]:
            glossary_seed[attr["name"]] = attr["name_zh"]
        if attr.get("description_zh") and attr["description"] != attr["description_zh"]:
            glossary_seed[attr["description"]] = attr["description_zh"]
    if glossary_seed:
        seed_glossary_from_bilingual("ozon", glossary_seed)

    record["attributes"] = {
        "required": [item for item in normalized if item.get("required")],
        "optional": [item for item in normalized if not item.get("required")],
    }
    return record


def clear_ozon_category_tree_cache(
    *,
    include_persistent: bool = True,
    credential_scope_hash: str | None = None,
) -> int:
    """按作用域或全量清理 Ozon 类目树与枚举值缓存。"""

    del include_persistent
    store = _catalog_store()
    removed = store.clear_taxonomy("ozon", scope_key=credential_scope_hash)
    store.clear_cached_values("ozon", scope_key=credential_scope_hash)
    return removed


__all__ = [
    "OZON_CATALOG_SCOPE",
    "OZON_CATEGORY_ATTRIBUTES_URL",
    "OZON_CATEGORY_ATTRIBUTE_VALUES_SEARCH_URL",
    "OZON_CATEGORY_ATTRIBUTE_VALUES_URL",
    "OZON_CATEGORY_CACHE_FRESH_TTL",
    "OZON_CATEGORY_CACHE_MAX_AGE",
    "OZON_CATEGORY_TREE_URL",
    "clear_ozon_category_tree_cache",
    "fetch_ozon_category_attribute_values",
    "fetch_ozon_category_children",
    "fetch_ozon_category_record",
    "fetch_ozon_category_roots",
    "fetch_ozon_category_siblings",
    "fetch_ozon_category_tree_summary",
    "load_ozon_category_corpus",
    "ozon_credential_scope_hash",
    "refresh_ozon_category_corpus",
    "search_ozon_categories",
]

"""单次发布校验（或一次批量只读预检）的共享读取；不跨请求复用、不持久化。"""
from dataclasses import dataclass, field
import json
import sqlite3
import threading
from typing import Any

from erp_web.context import get_context
from erp_web.services.image_delivery_service import ImageInspectionCache


@dataclass
class PublishValidationSession:
    # 组入口已检查逐 SKU 的成本、尺寸和库存，叶子只检查报价身份与平台规则。
    sku_inputs_checked: bool = False
    images: ImageInspectionCache = field(default_factory=ImageInspectionCache)
    logistics_errors: dict[str, str] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    _shared_values: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _platform_sku_index: dict[str, tuple[dict[str, set[str]], set[str]]] = field(
        default_factory=dict, repr=False, compare=False,
    )
    _yandex_ancestors_by_key: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict, repr=False, compare=False,
    )
    _yandex_schema_cache: tuple[dict[str, Any] | None, dict[str, dict[str, Any]], list[dict[str, Any]]] | None = field(
        default=None, repr=False, compare=False,
    )

    def for_sku_group(self) -> "PublishValidationSession":
        """构造共享只读查找缓存的组内视图，隔离每份草稿的图片与类目临时缓存。"""
        return PublishValidationSession(
            sku_inputs_checked=True,
            images=ImageInspectionCache(),
            logistics_errors=self.logistics_errors,
            _lock=self._lock,
            _shared_values=self._shared_values,
            _platform_sku_index=self._platform_sku_index,
            _yandex_ancestors_by_key=self._yandex_ancestors_by_key,
        )

    def yandex_schema_definitions(
        self,
        category_record: dict[str, Any] | None,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """在单次发布校验中复用 Yandex 属性索引与必填属性子集。"""
        cached = self._yandex_schema_cache
        if cached is not None and cached[0] is category_record:
            return cached[1], cached[2]
        attributes = (
            category_record.get("attributes")
            if isinstance(category_record, dict)
            and isinstance(category_record.get("attributes"), dict)
            else {}
        )
        definitions: dict[str, dict[str, Any]] = {}
        for group in ("required", "optional"):
            rows = attributes.get(group) if isinstance(attributes.get(group), list) else []
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                attr_id = str(raw.get("id") or "").strip()
                if attr_id:
                    definitions[attr_id] = raw
        required_defs = [item for item in definitions.values() if item.get("required")]
        self._yandex_schema_cache = (category_record, definitions, required_defs)
        return definitions, required_defs

    @property
    def store_config(self) -> dict[str, Any]:
        with self._lock:
            if "store_config" not in self._shared_values:
                self._shared_values["store_config"] = get_context().config.load_store_config()
            return self._shared_values["store_config"]

    @property
    def forbidden_categories(self) -> dict[str, str]:
        with self._lock:
            if "forbidden_categories" not in self._shared_values:
                settings = get_context().config.forbidden_categories()
                self._shared_values["forbidden_categories"] = {
                    item["category_id"]: item["name"] for item in settings["categories"]
                }
            return self._shared_values["forbidden_categories"]

    def yandex_ancestor_ids(
        self,
        category_id: str,
        *,
        api_token: str = "",
        category_record: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        cid = str(category_id or "").strip()
        if not cid:
            return ()
        if cid.isascii() and cid.isdigit():
            cid = str(int(cid))
        token = str(api_token or "").strip()
        cache_key = (token, cid)
        with self._lock:
            cached = self._yandex_ancestors_by_key.get(cache_key)
        if cached is None:
            # 按类目 ID 走 O(log N) 单条索引查询，不把整棵 Yandex 类目树读进内存。
            looked_up = get_context().category_catalog.cached_category_ancestor_ids(
                "yandex",
                cid,
                api_token=token,
            )
            with self._lock:
                cached = self._yandex_ancestors_by_key.setdefault(cache_key, tuple(looked_up or ()))
        if cached:
            return cached
        if isinstance(category_record, dict):
            raw = category_record.get("ancestor_ids")
            if isinstance(raw, (list, tuple)):
                return tuple(str(item).strip() for item in raw if str(item).strip())
        return ()

    @property
    def tariff_versions(self) -> dict[str, str]:
        with self._lock:
            if "tariff_versions" not in self._shared_values:
                self._shared_values["tariff_versions"] = get_context().tariffs.versions()
            return self._shared_values["tariff_versions"]

    def yandex_logistics_error(self, plan: dict[str, Any]) -> str:
        key = json.dumps(plan, sort_keys=True, ensure_ascii=False)
        with self._lock:
            if key in self.logistics_errors:
                return self.logistics_errors[key]
        try:
            get_context().yandex_logistics.validate(plan)
        except ValueError as exc:
            error_text = str(exc)
        else:
            error_text = ""
        with self._lock:
            self.logistics_errors[key] = error_text
        return error_text

    def yandex_shipping_selection_error(self, selection: dict[str, Any]) -> str:
        """校验草稿指定的 Yandex 发布仓库与物流路线是否属于当前账号可用方案。"""
        if not isinstance(selection, dict):
            return ""
        warehouse_id = str(selection.get("warehouse_id") or "").strip()
        route_id = str(selection.get("route_id") or "").strip()
        if not warehouse_id and not route_id:
            return ""
        with self._lock:
            plans_cache = self._shared_values.get("yandex_plans")
        if plans_cache is None:
            try:
                plans = get_context().yandex_logistics.plans()
                loaded_plans = list(plans) if isinstance(plans, list) else []
            except Exception:
                loaded_plans = []
            with self._lock:
                plans_cache = self._shared_values.setdefault("yandex_plans", loaded_plans)
        matched = any(
            (not warehouse_id or str(plan.get("publish_warehouse_id") or "").strip() == warehouse_id)
            and (not route_id or str(plan.get("route_id") or "").strip() == route_id)
            for plan in plans_cache
        )
        if not matched:
            return "草稿指定的 Yandex 发布仓库或物流方式在当前账号不可用，请重新选择或重算核价"
        return ""

    def _load_platform_sku_index(self, platform: str) -> tuple[dict[str, set[str]], set[str]]:
        key = str(platform or "").strip().lower()
        with self._lock:
            if key in self._platform_sku_index:
                return self._platform_sku_index[key]
        draft_skus: dict[str, set[str]] = {}
        online_skus: set[str] = set()
        try:
            db = get_context().db
            with db._connect() as conn:
                try:
                    rows = conn.execute(
                        "SELECT pd.draft_id, json_extract(je.value, '$.sku') AS sku "
                        "FROM platform_drafts pd, json_each(pd.draft_json, '$.sku_items') je "
                        "WHERE pd.platform = ? "
                        "AND COALESCE(json_extract(je.value, '$.selected'), 1) != 0 "
                        "AND json_extract(je.value, '$.sku') IS NOT NULL "
                        "AND json_extract(je.value, '$.sku') != ''",
                        (key,),
                    ).fetchall()
                    for row in rows:
                        draft_id = str(row["draft_id"] or "").strip()
                        code = str(row["sku"] or "").strip()
                        if draft_id and code:
                            draft_skus.setdefault(code, set()).add(draft_id)
                except sqlite3.OperationalError:
                    # 兼容包含非法 JSON 草稿或精简 SQLite 环境的读取回退。
                    rows = conn.execute(
                        "SELECT draft_id, draft_json FROM platform_drafts WHERE platform = ?",
                        (key,),
                    ).fetchall()
                    for row in rows:
                        draft_id = str(row["draft_id"] or "").strip()
                        if not draft_id:
                            continue
                        try:
                            payload = json.loads(row["draft_json"] or "{}")
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(payload, dict):
                            continue
                        for item in payload.get("sku_items") or []:
                            if not isinstance(item, dict) or item.get("selected") is False:
                                continue
                            code = str(item.get("sku") or "").strip()
                            if code:
                                draft_skus.setdefault(code, set()).add(draft_id)
                online_rows = conn.execute(
                    "SELECT json_extract(snapshot_json, '$.seller_sku') AS seller_sku "
                    "FROM online_listings WHERE platform = ?",
                    (key,),
                ).fetchall()
                for row in online_rows:
                    code = str(row["seller_sku"] or "").strip()
                    if code:
                        online_skus.add(code)
        except (AttributeError, RuntimeError, sqlite3.Error):
            pass
        result = (draft_skus, online_skus)
        with self._lock:
            return self._platform_sku_index.setdefault(key, result)

    def local_sku_conflict(
        self,
        platform: str,
        seller_sku: str,
        *,
        exclude_draft_id: str = "",
        already_published: bool = False,
    ) -> str:
        """检查卖家 SKU 是否与当前账号同平台的其他草稿或在线商品重复。"""
        code = str(seller_sku or "").strip()
        if not code:
            return ""
        draft_skus, online_skus = self._load_platform_sku_index(platform)
        other_drafts = sorted(
            did for did in draft_skus.get(code, set())
            if did and did != str(exclude_draft_id or "").strip()
        )
        reasons: list[str] = []
        if other_drafts:
            preview = "、".join(other_drafts[:2])
            suffix = f" 等 {len(other_drafts)} 份草稿" if len(other_drafts) > 2 else ""
            reasons.append(f"其他草稿（{preview}{suffix}）")
        if not already_published and code in online_skus:
            reasons.append("本地在线商品快照")
        if not reasons:
            return ""
        return f"卖家 SKU「{code}」与当前账号的{'、'.join(reasons)}重复，请确认是否为同一商品或修改 SKU 避免冲突"

"""草稿 6 步准备进度与禁售状态的纯函数派生（页面列表与 AI 共用）。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from erp_web.services.forbidden_category_service import (
    forbidden_category_match,
    forbidden_category_reason,
)

_FORBIDDEN_BLOCK_CODES = frozenset(
    {"CATEGORY_FORBIDDEN", "CATEGORY_FORBIDDEN_NO_ALTERNATIVE"}
)
_UNRESOLVED_BLOCK_CODES = frozenset(
    {"CATEGORY_MATCH_UNRESOLVED", "ABSTAIN_NO_MATCH"}
)
_ATTRIBUTE_ERROR_CODES = frozenset(
    {
        "REQUIRED_ATTRIBUTE_MISSING",
        "ATTRIBUTE_DICTIONARY_VALUE_REQUIRED",
        "ATTRIBUTE_UNIT_INVALID",
        "ATTRIBUTE_TEXT_FORMAT_INVALID",
        "ATTRIBUTE_LANGUAGE_INVALID",
        "BRAND_MISSING",
    }
)
_PUBLISHED_STATUSES = frozenset(
    {"published", "real_publish_success", "success", "imported"}
)
_PLATFORM_LABELS = {
    "mercadolibre": "美客多",
    "yandex": "Yandex",
    "ozon": "Ozon",
}
_PUBLISH_STATUS_LABELS = {
    "publishing": ("发布中", "info"),
    "queued": ("排队发布", "info"),
    "running": ("发布中", "info"),
    "retrying": ("重试中", "info"),
    "pending_confirmation": ("已受理，待确认", "info"),
    "published": ("已发布", "success"),
    "partial": ("部分发布", "warning"),
    "failed": ("发布失败", "danger"),
    "publish_failed": ("发布失败", "danger"),
    "real_publish_failed": ("发布失败", "danger"),
    "interrupted": ("发布中断", "danger"),
    "outcome_unknown": ("结果待确认", "danger"),
    "needs_attention": ("需人工处理", "danger"),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm_cid(value: Any) -> str:
    cid = _text(value)
    if cid.isascii() and cid.isdigit():
        return str(int(cid))
    return cid


def _non_empty_attribute_count(attributes: Any) -> int:
    if not isinstance(attributes, Mapping):
        return 0
    count = 0
    for value in attributes.values():
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                count += 1
        elif isinstance(value, (list, tuple, dict, set)):
            if len(value) > 0:
                count += 1
        else:
            count += 1
    return count


def _review_attribute_count(validation_errors: Any) -> int:
    if not isinstance(validation_errors, list):
        return 0
    count = 0
    for item in validation_errors:
        if (
            isinstance(item, Mapping)
            and _text(item.get("code")).upper() == "NEED_REVIEW_ATTRIBUTES"
        ):
            count += 1
    return count


def _target_key(platform: str, site: str) -> str:
    return f"{_text(platform).lower()}:{_text(site).lower()}"


def _market_label(platform: str, site: str) -> str:
    platform_key = _text(platform).lower()
    base = _PLATFORM_LABELS.get(platform_key, platform_key or "未知平台")
    site_code = _text(site)
    if not site_code or site_code.lower() == "global":
        return base
    return f"{base} · {site_code.upper()}"


def build_forbidden_categories_map(settings: Any) -> dict[str, str]:
    """将 shared forbidden_categories 配置归一化为 {category_id: name} 字典。"""
    if not isinstance(settings, Mapping):
        return {}
    rows = settings.get("categories")
    if not isinstance(rows, list):
        return {}
    result: dict[str, str] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        cid = _norm_cid(item.get("category_id"))
        if cid:
            result[cid] = _text(item.get("name"))
    return result


def draft_progress_facts_from_raw(
    raw: Mapping[str, Any],
    *,
    primary_platform: str = "",
    primary_site: str = "",
    processed_image_count: int = 0,
) -> dict[str, Any]:
    """从完整草稿字典提取供 compute_draft_progress 使用的轻量事实。"""
    platform_default = _text(raw.get("platform") or primary_platform).lower()
    site_default = _text(raw.get("site") or primary_site)
    raw_targets = raw.get("target_sites")
    targets_list = (
        [t for t in raw_targets if isinstance(t, Mapping)]
        if isinstance(raw_targets, list)
        else []
    )
    if not targets_list:
        targets_list = [
            {
                "platform": platform_default,
                "site": site_default,
                "category_id": raw.get("category_id"),
                "description_category_id": raw.get("description_category_id"),
                "category_path": raw.get("category_path"),
                "category_block": raw.get("category_block"),
                "attributes": raw.get("attributes"),
                "validation_errors": raw.get("validation_errors"),
                "last_precheck": raw.get("last_precheck"),
                "status": raw.get("status"),
                "publish_status": raw.get("publish_status"),
            }
        ]

    sku_items = raw.get("sku_items") if isinstance(raw.get("sku_items"), list) else []
    selected_skus = [
        row for row in sku_items if isinstance(row, Mapping) and row.get("selected")
    ]
    sku_attr_targets: set[str] = set()
    selected_sku_pricing: list[dict[str, Any]] = []
    for row in selected_skus:
        attrs_by_target = row.get("attributes_by_target")
        if isinstance(attrs_by_target, Mapping):
            for key, attr_map in attrs_by_target.items():
                if _non_empty_attribute_count(attr_map) > 0:
                    sku_attr_targets.add(_text(key).lower())
        pricing = row.get("pricing") if isinstance(row.get("pricing"), Mapping) else {}
        pricing_targets = (
            pricing.get("targets") if isinstance(pricing.get("targets"), Mapping) else {}
        )
        selected_sku_pricing.append(
            {
                "applied": pricing.get("applied") is True,
                "targets": [
                    _text(key).lower()
                    for key in pricing_targets.keys()
                    if _text(key)
                ],
            }
        )

    projected_targets: list[dict[str, Any]] = []
    for target in targets_list:
        t_platform = _text(target.get("platform") or platform_default).lower()
        t_site = _text(target.get("site") or target.get("site_id") or site_default)
        t_key = _target_key(t_platform, t_site)
        last_precheck = (
            target.get("last_precheck")
            if isinstance(target.get("last_precheck"), Mapping)
            else {}
        )
        precheck_errors = (
            last_precheck.get("errors")
            if isinstance(last_precheck.get("errors"), list)
            else []
        )
        error_codes = [
            _text(err.get("code")).upper()
            for err in precheck_errors
            if isinstance(err, Mapping) and _text(err.get("code"))
        ]
        error_messages = [
            _text(err.get("message") if isinstance(err, Mapping) else err)
            for err in precheck_errors
            if _text(err.get("message") if isinstance(err, Mapping) else err)
        ][:3]
        attr_count = _non_empty_attribute_count(target.get("attributes"))
        if attr_count == 0 and t_key in sku_attr_targets:
            attr_count = 1
        projected_targets.append(
            {
                "platform": t_platform,
                "site": t_site,
                "category_id": _text(target.get("category_id")),
                "description_category_id": _text(target.get("description_category_id")),
                "category_path": _text(target.get("category_path")),
                "category_block": (
                    dict(target.get("category_block"))
                    if isinstance(target.get("category_block"), Mapping)
                    else {}
                ),
                "attribute_count": attr_count,
                "review_attribute_count": _review_attribute_count(
                    target.get("validation_errors")
                ),
                "precheck_has": bool(last_precheck),
                "precheck_ok": last_precheck.get("ok") is True,
                "precheck_error_count": len(precheck_errors),
                "precheck_error_codes": error_codes,
                "precheck_error_messages": error_messages,
                "status": _text(target.get("status")).lower(),
                "publish_status": _text(target.get("publish_status")).lower(),
            }
        )

    images = raw.get("images") if isinstance(raw.get("images"), list) else []
    image_prep = (
        raw.get("image_preparation")
        if isinstance(raw.get("image_preparation"), Mapping)
        else {}
    )
    return {
        "title": _text(raw.get("title")),
        "has_description": bool(_text(raw.get("description"))),
        "copy_generated_at": _text(raw.get("copy_generated_at")),
        "copy_source": _text(raw.get("copy_source")),
        "image_count": len(images),
        "processed_image_count": max(0, int(processed_image_count or 0)),
        "image_preparation_mode": _text(image_prep.get("mode")).lower(),
        "images_manually_modified_at": _text(raw.get("images_manually_modified_at")),
        "selected_sku_pricing": selected_sku_pricing,
        "targets": projected_targets,
    }


def _evaluate_persisted_category_block(
    *,
    platform: str,
    block: Mapping[str, Any],
    forbidden_categories: Mapping[str, str] | None,
    ancestors_of: Callable[[str, str], tuple[str, ...]],
) -> tuple[bool, bool, str]:
    """返回 (is_forbidden, is_unresolved, reason)。"""
    if not isinstance(block, Mapping) or not block:
        return False, False, ""
    code = _text(block.get("code")).upper()
    reason = _text(block.get("reason"))
    if code in _FORBIDDEN_BLOCK_CODES:
        if forbidden_categories is not None:
            matched_forbidden = [
                _norm_cid(item)
                for item in (
                    block.get("matched_forbidden_ids")
                    if isinstance(block.get("matched_forbidden_ids"), list)
                    else []
                )
                if _norm_cid(item)
            ]
            matched_cids = [
                _norm_cid(item)
                for item in (
                    block.get("matched_category_ids")
                    if isinstance(block.get("matched_category_ids"), list)
                    else []
                )
                if _norm_cid(item)
            ]
            if matched_forbidden:
                if not any(fid in forbidden_categories for fid in matched_forbidden):
                    return False, False, ""
            elif matched_cids and platform == "yandex":
                still_forbidden = any(
                    forbidden_category_match(
                        "yandex",
                        cid,
                        dict(forbidden_categories),
                        ancestor_ids=ancestors_of("yandex", cid),
                    )
                    is not None
                    for cid in matched_cids
                )
                if not still_forbidden:
                    return False, False, ""
            elif not forbidden_categories:
                return False, False, ""
        return True, False, reason or "最匹配的类目属于禁售范围，且无合规替代类目。"
    if code in _UNRESOLVED_BLOCK_CODES:
        return False, True, reason or "类目匹配待人工确认。"
    return False, False, ""


def compute_draft_progress(
    facts: Mapping[str, Any],
    *,
    publish_state: str = "unpublished",
    display_status: str = "",
    forbidden_categories: Mapping[str, str] | None = None,
    ancestor_ids_loader: Callable[[str, str], Iterable[str]] | None = None,
) -> dict[str, Any]:
    """根据真实草稿事实推导主状态、6 步进度与分市场明细。"""
    normalized_publish_state = _text(publish_state).lower() or "unpublished"
    normalized_display = _text(display_status).lower()
    forbidden_dict = (
        dict(forbidden_categories) if forbidden_categories is not None else None
    )
    ancestor_cache: dict[tuple[str, str], tuple[str, ...]] = {}

    def _ancestors(platform: str, category_id: str) -> tuple[str, ...]:
        key = (platform, category_id)
        if key not in ancestor_cache:
            if ancestor_ids_loader is None or not category_id:
                ancestor_cache[key] = ()
            else:
                try:
                    raw = ancestor_ids_loader(platform, category_id)
                    ancestor_cache[key] = tuple(
                        _text(item) for item in (raw or ()) if _text(item)
                    )
                except Exception:
                    ancestor_cache[key] = ()
        return ancestor_cache[key]

    raw_targets = facts.get("targets") if isinstance(facts.get("targets"), list) else []
    targets = [t for t in raw_targets if isinstance(t, Mapping)]

    selected_sku_pricing = [
        row
        for row in (
            facts.get("selected_sku_pricing")
            if isinstance(facts.get("selected_sku_pricing"), list)
            else []
        )
        if isinstance(row, Mapping)
    ]
    selected_sku_count = len(selected_sku_pricing)

    markets: list[dict[str, Any]] = []
    forbidden_markets: list[dict[str, Any]] = []
    unresolved_markets: list[str] = []
    category_done_count = 0
    attr_done_count = 0
    attr_blocked_messages: list[str] = []
    total_review_attributes = 0
    pricing_done_targets = 0
    any_pricing_applied = any(bool(row.get("applied")) for row in selected_sku_pricing)
    precheck_passed_count = 0
    precheck_failed_targets = 0
    total_precheck_errors = 0
    precheck_error_samples: list[str] = []

    for target in targets:
        platform = _text(target.get("platform")).lower()
        site = _text(target.get("site"))
        t_key = _target_key(platform, site)
        label = _market_label(platform, site)
        category_id = _text(target.get("category_id"))
        block = (
            target.get("category_block")
            if isinstance(target.get("category_block"), Mapping)
            else {}
        )
        t_status = _text(target.get("status")).lower()
        t_pub = _text(target.get("publish_status")).lower()
        t_published = (
            t_status in _PUBLISHED_STATUSES
            or t_pub in _PUBLISHED_STATUSES
            or normalized_publish_state == "published"
        )

        is_forbidden = False
        forbidden_reason = ""
        is_unresolved = False
        unresolved_reason = ""

        # 来源 B：实时比对当前共享禁售名单（对已选 Yandex 类目生效）
        if platform == "yandex" and category_id and forbidden_dict:
            live_reason = forbidden_category_reason(
                "yandex",
                category_id,
                forbidden_dict,
                ancestor_ids=_ancestors("yandex", category_id),
            )
            if live_reason:
                is_forbidden = True
                forbidden_reason = live_reason

        # 来源 A：持久化类目筛查阻断记录（未选类目时也能标出禁售或待人工确认）
        if not is_forbidden and block:
            block_forbidden, block_unresolved, block_reason = (
                _evaluate_persisted_category_block(
                    platform=platform,
                    block=block,
                    forbidden_categories=forbidden_dict,
                    ancestors_of=_ancestors,
                )
            )
            if block_forbidden:
                is_forbidden = True
                forbidden_reason = block_reason
            elif block_unresolved and not category_id:
                is_unresolved = True
                unresolved_reason = block_reason

        cat_done = bool(category_id) and not is_forbidden
        if cat_done:
            category_done_count += 1
        if is_forbidden:
            forbidden_markets.append({"label": label, "reason": forbidden_reason})
        elif is_unresolved:
            unresolved_markets.append(f"{label}（{unresolved_reason}）")

        attr_count = max(0, int(target.get("attribute_count") or 0))
        review_count = max(0, int(target.get("review_attribute_count") or 0))
        total_review_attributes += review_count

        precheck_has = bool(target.get("precheck_has"))
        precheck_ok = bool(target.get("precheck_ok"))
        precheck_err_count = max(0, int(target.get("precheck_error_count") or 0))
        precheck_codes = {
            _text(c).upper()
            for c in (
                target.get("precheck_error_codes")
                if isinstance(target.get("precheck_error_codes"), list)
                else []
            )
            if _text(c)
        }
        precheck_msgs = [
            _text(m)
            for m in (
                target.get("precheck_error_messages")
                if isinstance(target.get("precheck_error_messages"), list)
                else []
            )
            if _text(m)
        ]

        # 若最近预检本身因 CATEGORY_FORBIDDEN 失败，同样视为禁售
        if not is_forbidden and "CATEGORY_FORBIDDEN" in precheck_codes:
            is_forbidden = True
            forbidden_reason = next(
                (msg for msg in precheck_msgs if "禁售" in msg),
                "类目命中禁售名单",
            )
            forbidden_markets.append({"label": label, "reason": forbidden_reason})
            if cat_done:
                category_done_count = max(0, category_done_count - 1)
                cat_done = False

        if precheck_has and precheck_ok and not is_forbidden:
            precheck_passed_count += 1
        elif precheck_has and not precheck_ok and not is_forbidden:
            precheck_failed_targets += 1
            total_precheck_errors += max(1, precheck_err_count)
            for msg in precheck_msgs[:2]:
                if len(precheck_error_samples) < 3:
                    precheck_error_samples.append(f"{label}：{msg}")

        if precheck_codes & _ATTRIBUTE_ERROR_CODES:
            attr_blocked_messages.append(f"{label} 缺少必填或存在格式错误的属性")
        elif attr_count > 0 or (precheck_has and precheck_ok):
            attr_done_count += 1

        target_pricing_done = bool(selected_sku_count) and all(
            bool(row.get("applied"))
            and t_key in {str(k).lower() for k in (row.get("targets") or [])}
            for row in selected_sku_pricing
        )
        if target_pricing_done:
            pricing_done_targets += 1

        # 分市场摘要行（供悬停 hint 展示）
        parts: list[str] = []
        if is_forbidden:
            market_status = "禁售类目"
            market_tone = "danger"
            market_summary = forbidden_reason or "类目命中禁售名单"
        elif t_published:
            market_status = "已发布"
            market_tone = "success"
            market_summary = "已确认发布成功"
        else:
            parts.append("类目 ✓" if cat_done else ("类目待确认" if is_unresolved else "未选类目"))
            if review_count > 0:
                parts.append(f"属性待复核 {review_count} 项")
            elif attr_count > 0:
                parts.append(f"属性 {attr_count} 项 ✓")
            else:
                parts.append("未填属性")
            parts.append("核价 ✓" if target_pricing_done else "未核价")
            if precheck_has and precheck_ok:
                parts.append("预检通过 ✓")
                market_status = "可发布"
                market_tone = "success"
            elif precheck_has and not precheck_ok:
                parts.append(f"预检未通过({max(1, precheck_err_count)})")
                market_status = "预检未通过"
                market_tone = "danger"
            elif is_unresolved or review_count > 0:
                market_status = "待确认"
                market_tone = "warning"
            elif cat_done or attr_count > 0 or target_pricing_done:
                market_status = "准备中"
                market_tone = "info"
            else:
                market_status = "待准备"
                market_tone = "muted"
            market_summary = " · ".join(parts)

        markets.append(
            {
                "platform": platform,
                "site": site,
                "label": label,
                "forbidden": is_forbidden,
                "forbidden_reason": forbidden_reason,
                "status_label": market_status,
                "tone": market_tone,
                "summary": market_summary,
            }
        )

    target_count = len(targets)
    any_forbidden = bool(forbidden_markets)

    # 1. 类目
    if any_forbidden:
        first_forbidden = forbidden_markets[0]
        if target_count > 1 and len(forbidden_markets) < target_count:
            cat_summary = f"{first_forbidden['label']} 禁售（{first_forbidden['reason']}）"
        else:
            cat_summary = f"命中禁售：{first_forbidden['reason']}"
        cat_state = "blocked"
    elif target_count > 0 and category_done_count == target_count:
        cat_state = "done"
        cat_summary = (
            "已选叶子类目"
            if target_count == 1
            else f"{category_done_count}/{target_count} 市场已选类目"
        )
    elif unresolved_markets:
        cat_state = "attention"
        cat_summary = f"类目待人工确认：{unresolved_markets[0]}"
    elif category_done_count > 0:
        cat_state = "partial"
        cat_summary = f"{category_done_count}/{target_count} 市场已选类目"
    else:
        cat_state = "pending"
        cat_summary = "尚未选择类目"

    # 2. 文案
    has_title = bool(_text(facts.get("title")))
    has_description = bool(facts.get("has_description"))
    if has_title and has_description:
        copy_state = "done"
        copy_summary = (
            "目标语言文案已生成"
            if _text(facts.get("copy_generated_at")) or _text(facts.get("copy_source"))
            else "标题与描述已填写"
        )
    elif has_title or has_description:
        copy_state = "partial"
        copy_summary = "仅填写标题，缺少商品描述" if has_title else "仅填写描述，缺少标题"
    else:
        copy_state = "pending"
        copy_summary = "尚未填写标题与描述"

    # 3. 图片
    image_count = max(0, int(facts.get("image_count") or 0))
    processed_image_count = max(0, int(facts.get("processed_image_count") or 0))
    image_prep_mode = _text(facts.get("image_preparation_mode")).lower()
    images_manual_at = _text(facts.get("images_manually_modified_at"))
    if image_prep_mode in {"generated", "kept"}:
        image_state = "done"
        image_summary = (
            f"图片已加工就绪（{image_count} 张）"
            if image_prep_mode == "generated"
            else f"图片已确认保留（{image_count} 张）"
        )
    elif images_manual_at:
        image_state = "attention"
        image_summary = "图片有手工改动，待确认保留或重做"
    elif processed_image_count > 0:
        image_state = "done"
        image_summary = f"已有 {processed_image_count} 张加工/上传图"
    elif image_count > 0:
        image_state = "partial"
        image_summary = f"已选 {image_count} 张原图，尚未加工"
    else:
        image_state = "pending"
        image_summary = "尚未选择草稿图片"

    # 4. 属性
    if attr_blocked_messages:
        attr_state = "blocked"
        attr_summary = attr_blocked_messages[0]
    elif total_review_attributes > 0:
        attr_state = "attention"
        attr_summary = f"{total_review_attributes} 项 AI 推断属性待复核"
    elif target_count > 0 and attr_done_count == target_count:
        attr_state = "done"
        attr_summary = (
            "类目属性已填写"
            if target_count == 1
            else f"{attr_done_count}/{target_count} 市场已填属性"
        )
    elif attr_done_count > 0:
        attr_state = "partial"
        attr_summary = f"{attr_done_count}/{target_count} 市场已填属性"
    else:
        attr_state = "pending"
        attr_summary = "尚未填写类目属性"

    # 5. 核价
    if selected_sku_count == 0:
        pricing_state = "pending"
        pricing_summary = "尚未勾选参与销售的 SKU"
    elif target_count > 0 and pricing_done_targets == target_count:
        pricing_state = "done"
        pricing_summary = (
            f"{selected_sku_count} 个 SKU 已完成核价"
            if target_count == 1
            else f"{pricing_done_targets}/{target_count} 市场 · {selected_sku_count} 个 SKU 已核价"
        )
    elif pricing_done_targets > 0 or any_pricing_applied:
        pricing_state = "partial"
        pricing_summary = (
            f"{pricing_done_targets}/{target_count} 市场已核价，部分 SKU 待补全"
        )
    else:
        pricing_state = "pending"
        pricing_summary = f"{selected_sku_count} 个已选 SKU 待核价"

    # 6. 预检
    if normalized_publish_state == "published":
        precheck_state = "done"
        precheck_summary = "已确认发布成功"
    elif precheck_failed_targets > 0:
        precheck_state = "blocked"
        precheck_summary = f"预检未通过（{total_precheck_errors} 项错误）"
    elif target_count > 0 and precheck_passed_count == target_count:
        precheck_state = "done"
        precheck_summary = (
            "发布预检已通过"
            if target_count == 1
            else f"{precheck_passed_count}/{target_count} 市场预检通过"
        )
    elif precheck_passed_count > 0:
        precheck_state = "partial"
        precheck_summary = f"{precheck_passed_count}/{target_count} 市场预检通过"
    else:
        precheck_state = "pending"
        precheck_summary = "尚未执行发布预检" if not any_forbidden else "未预检（命中禁售）"

    steps = [
        {"key": "category", "short": "类", "label": "类目", "tab": "category", "state": cat_state, "summary": cat_summary},
        {"key": "copy", "short": "文", "label": "文案", "tab": "text", "state": copy_state, "summary": copy_summary},
        {"key": "images", "short": "图", "label": "图片", "tab": "images", "state": image_state, "summary": image_summary},
        {"key": "attributes", "short": "属", "label": "属性", "tab": "category", "state": attr_state, "summary": attr_summary},
        {"key": "pricing", "short": "价", "label": "核价", "tab": "pricing", "state": pricing_state, "summary": pricing_summary},
        {"key": "precheck", "short": "检", "label": "预检", "tab": "precheck", "state": precheck_state, "summary": precheck_summary},
    ]
    completed_steps = sum(1 for step in steps if step["state"] == "done")
    has_attention = any(step["state"] == "attention" for step in steps)
    has_partial = any(step["state"] == "partial" for step in steps)

    # 主状态（规则：未发布/未在发布中时，只要任一市场命中禁售，直接归为「禁售类目」）
    alert = ""
    if normalized_publish_state == "publishing":
        status_code = normalized_display if normalized_display in _PUBLISH_STATUS_LABELS else "publishing"
        status_label, status_tone = _PUBLISH_STATUS_LABELS.get(status_code, ("发布中", "info"))
        prep_stage = "publishing"
    elif normalized_publish_state == "published":
        status_code = "published"
        status_label, status_tone = "已发布", "success"
        prep_stage = "published"
    elif any_forbidden:
        status_code = "category_forbidden"
        status_label = "禁售类目"
        status_tone = "danger"
        prep_stage = "category_forbidden"
        alert = "；".join(
            f"{item['label']}：{item['reason']}" for item in forbidden_markets
        )
    elif normalized_publish_state == "publish_failed":
        status_code = normalized_display if normalized_display in _PUBLISH_STATUS_LABELS else "publish_failed"
        status_label, status_tone = _PUBLISH_STATUS_LABELS.get(status_code, ("发布失败", "danger"))
        prep_stage = "publish_failed"
    elif precheck_failed_targets > 0:
        status_code = "not_ready"
        status_label = (
            f"预检未通过 ({total_precheck_errors})"
            if total_precheck_errors > 0
            else "预检未通过"
        )
        status_tone = "danger"
        prep_stage = "not_ready"
        alert = "；".join(precheck_error_samples) if precheck_error_samples else precheck_summary
    elif precheck_state == "done":
        status_code = "ready_to_publish"
        status_label = "可发布"
        status_tone = "success"
        prep_stage = "ready_to_publish"
    elif has_attention:
        status_code = "needs_confirmation"
        status_label = "待确认"
        status_tone = "warning"
        prep_stage = "needs_confirmation"
        alert = "；".join(
            step["summary"] for step in steps if step["state"] == "attention"
        )
    elif completed_steps > 0 or has_partial:
        status_code = "preparing"
        status_label = f"准备中 {completed_steps}/6"
        status_tone = "info"
        prep_stage = "preparing"
    else:
        status_code = "pending"
        status_label = "待准备"
        status_tone = "muted"
        prep_stage = "pending"

    return {
        "forbidden": any_forbidden,
        "status_code": status_code,
        "status_label": status_label,
        "status_tone": status_tone,
        "prep_stage": prep_stage,
        "completed_steps": completed_steps,
        "total_steps": len(steps),
        "alert": alert,
        "steps": steps,
        "markets": markets,
    }


__all__ = [
    "build_forbidden_categories_map",
    "compute_draft_progress",
    "draft_progress_facts_from_raw",
]

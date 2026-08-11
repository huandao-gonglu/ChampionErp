# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from erp_web.context import get_context
from erp_web.services import html_extract_service

from .collect_helpers import collect_time_iso
from .draft_publish_context import (
    draft_publish_targets,
    merge_target_listing_into_draft,
)
from .image_pool_core import source_image_refs
from .publish_helpers import precheck_item, remote_publish_identity

if TYPE_CHECKING:
    from erp_web.context import AppContext

def page_snapshot_from_html(url: str, html: str, text: str = "", title: str = "", image_urls: list[str] | None = None) -> dict[str, Any]:
    return {
        "url": url,
        "html": html,
        "text": text or html_extract_service.html_to_text(html),
        "title": title or html_extract_service.extract_page_title(html),
        "image_urls": image_urls or html_extract_service.extract_product_image_urls(html, url, limit=20),
    }


def append_publish_log(entry: dict[str, Any]) -> None:
    """插入 publish_logs 表（大报文仍写 artifacts 文件，表存路径）。"""
    get_context().db.insert_publish_log(entry)


def load_publish_logs(limit: int = 200) -> list[dict[str, Any]]:
    return get_context().db.list_publish_logs(limit=limit)


def publish_bus_terminal_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value == "success":
        return "published"
    if value in {"failed", "not_ready", "ready_for_real_publish", "skipped"}:
        return value
    return ""


def publish_bus_log_exists(
    job_id: str,
    platform: str,
    *,
    context: "AppContext | None" = None,
) -> bool:
    runtime_context = context or get_context()
    return runtime_context.db.publish_log_exists(
        str(job_id or ""),
        str(platform or ""),
    )


def apply_publish_bus_result_to_draft(
    draft: dict[str, Any],
    job_state: dict[str, Any],
    platform: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    draft = dict(draft or {})
    terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
    if not terminal_status:
        return draft
    updates: dict[str, Any] = {"publish_status": terminal_status}
    if terminal_status == "published":
        updates["status"] = "published"
        updates["validation_errors"] = []
    elif str(item.get("error") or ""):
        updates["validation_errors"] = [
            precheck_item(
                "PUBLISH_BUS_FAILED",
                "publish",
                str(item.get("error") or ""),
                "error",
                "按字段提示修复后重试",
            )
        ]
    updates["last_publish_task"] = {
        "job_id": str(job_state.get("job_id") or ""),
        "status": terminal_status,
        "platform_status": str(item.get("status") or ""),
        "stage": str(item.get("stage") or ""),
        "error": str(item.get("error") or ""),
        "attempts": item.get("attempts", 0),
        **remote_publish_identity(item.get("result")),
        "updated_at": str(item.get("updated_at") or job_state.get("updated_at") or collect_time_iso()),
    }
    target = {
        "platform": platform,
        "site": str(item.get("site") or draft.get("site") or ""),
    }
    return merge_target_listing_into_draft(draft, target, updates)


def append_publish_bus_terminal_log(
    product: dict[str, Any],
    draft: dict[str, Any],
    job_state: dict[str, Any],
    platform: str,
    item: dict[str, Any],
    *,
    context: "AppContext | None" = None,
) -> None:
    runtime_context = context or get_context()
    job_id = str(job_state.get("job_id") or "")
    if (
        job_id
        and runtime_context.db.publish_log_exists(job_id, platform)
    ):
        return
    from .publish_logs_runtime import (
        _product_id_for_log,
        _write_publish_artifacts,
    )

    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    payload = {
        "job_id": job_id,
        "platform": platform,
        "product_id": str(product.get("product_id") or ""),
        "stage": item.get("stage") or "",
        "attempts": item.get("attempts", 0),
    }
    payload_path, response_path = _write_publish_artifacts(
        f"publish-bus-{platform}",
        payload,
        result or item,
        output_dir=runtime_context.paths.output_dir,
        artifact_key=f"{job_id}:{platform}" if job_id else "",
    )
    error_map = result.get("error_map") if isinstance(result.get("error_map"), dict) else {}
    field_errors = error_map.get("field_errors") if isinstance(error_map.get("field_errors"), dict) else {}
    terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
    runtime_context.db.insert_publish_log_once(
        {
            "job_id": job_id,
            "product_id": str(item.get("product_id") or product.get("product_id") or _product_id_for_log(product, platform)),
            "platform": platform,
            "draft_id": str(item.get("draft_id") or draft.get("draft_id") or ""),
            "status": terminal_status or str(item.get("status") or ""),
            "started_at": str(item.get("created_at") or job_state.get("created_at") or ""),
            "finished_at": str(item.get("updated_at") or job_state.get("updated_at") or collect_time_iso()),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": str(result.get("error_code") or result.get("status") or item.get("status") or ""),
            "error_message": str(item.get("error") or result.get("error") or ""),
            "field_errors": field_errors,
            "next_action": "按字段提示修复后重试" if terminal_status in {"failed", "not_ready"} else "",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": platform,
            "sku": str(draft.get("sku") or ""),
            "error": str(item.get("error") or result.get("error") or ""),
            "image": source_image_refs(product)[:1],
        }
    )


def persist_publish_bus_terminal_results(
    job_state: dict[str, Any],
    *,
    context: "AppContext | None" = None,
) -> dict[str, Any]:
    if not isinstance(job_state, dict):
        return {}
    runtime_context = context or get_context()
    product = job_state.get("product") if isinstance(job_state.get("product"), dict) else {}
    job_product_id = str(product.get("product_id") or "").strip()
    persisted_drafts: dict[str, dict[str, Any]] = {}
    platforms = job_state.get("platforms") if isinstance(job_state.get("platforms"), dict) else {}
    for platform, item in platforms.items():
        if not isinstance(item, dict):
            continue
        terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
        if not terminal_status:
            continue
        draft_id = str(item.get("draft_id") or "").strip()
        target_product_id = str(item.get("product_id") or job_product_id).strip()
        if not draft_id or not target_product_id:
            raise RuntimeError(
                f"发布任务 {job_state.get('job_id') or ''} 缺少 draft_id 或 product_id，已阻止不安全的终态回写。"
            )
        draft = runtime_context.db.load_draft_model(draft_id)
        if not draft:
            raise RuntimeError(f"发布任务绑定草稿不存在：{draft_id}")
        stored_product_id = str(draft.get("product_id") or "").strip()
        source_product_id = str(draft.get("source_product_id") or stored_product_id).strip()
        target_platform = str(platform).strip().lower()
        target_site = str(item.get("site") or "").strip().lower()
        allowed_target_keys = {
            (
                str(target.get("platform") or "").strip().lower(),
                str(target.get("site") or "").strip().lower(),
            )
            for target in draft_publish_targets(draft)
        }
        if (
            stored_product_id != target_product_id
            or source_product_id != target_product_id
        ):
            raise RuntimeError(
                "发布任务草稿绑定不一致："
                f"job={job_state.get('job_id') or ''}, draft={draft_id}, "
                f"expected_product={target_product_id}, "
                f"actual_product={stored_product_id}/{source_product_id}"
            )
        if not target_site or (target_platform, target_site) not in allowed_target_keys:
            raise RuntimeError(
                "发布任务目标不属于绑定草稿："
                f"job={job_state.get('job_id') or ''}, draft={draft_id}, "
                f"target={target_platform}/{target_site or '-'}"
            )
        draft = apply_publish_bus_result_to_draft(
            draft,
            job_state,
            str(platform),
            item,
        )
        runtime_context.db.upsert_draft_model(
            target_product_id,
            str(draft.get("platform") or ""),
            draft,
        )
        saved_draft = runtime_context.db.load_draft_model(draft_id)
        append_publish_bus_terminal_log(
            product,
            saved_draft,
            job_state,
            str(platform),
            item,
            context=runtime_context,
        )
        persisted_drafts[str(platform)] = {
            "draft_id": draft_id,
            "product_id": target_product_id,
            "site": str(item.get("site") or saved_draft.get("site") or ""),
            "status": terminal_status,
        }
    if persisted_drafts:
        job_state["persisted_drafts"] = persisted_drafts
    return job_state


__all__ = [
    "apply_publish_bus_result_to_draft",
    "append_publish_bus_terminal_log",
    "load_publish_logs",
    "page_snapshot_from_html",
    "persist_publish_bus_terminal_results",
    "publish_bus_log_exists",
    "publish_bus_terminal_status",
]

# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from typing import Any

from erp_web.product_model import default_draft
from erp_web.services import html_extract_service as legacy

from .category_store import read_json, write_json
from .collect_helpers import collect_time_iso
from .product_store import load_product_from_index, normalize_list, normalize_product_fields, save_product
from .publish_helpers import _draft_for_platform, precheck_item
from .runtime_common import PUBLISH_LOG_PATH

def page_snapshot_from_html(url: str, html: str, text: str = "", title: str = "", image_urls: list[str] | None = None) -> dict[str, Any]:
    return {
        "url": url,
        "html": html,
        "text": text or legacy.html_to_text(html),
        "title": title or legacy.extract_page_title(html),
        "image_urls": image_urls or legacy.extract_product_image_urls(html, url, limit=20),
    }


def append_publish_log(entry: dict[str, Any]) -> None:
    logs = read_json(PUBLISH_LOG_PATH, [])
    if not isinstance(logs, list):
        logs = []
    logs.insert(0, entry)
    write_json(PUBLISH_LOG_PATH, logs[:200])


def load_publish_logs() -> list[dict[str, Any]]:
    logs = read_json(PUBLISH_LOG_PATH, [])
    return logs if isinstance(logs, list) else []


def publish_bus_terminal_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value == "success":
        return "published"
    if value in {"failed", "not_ready", "ready_for_real_publish", "skipped"}:
        return value
    return ""


def publish_bus_log_exists(job_id: str, platform: str) -> bool:
    for item in load_publish_logs():
        if str(item.get("job_id") or "") == str(job_id or "") and str(item.get("platform") or "") == str(platform or ""):
            return True
    return False


def apply_publish_bus_result_to_product(product: dict[str, Any], job_state: dict[str, Any], platform: str, item: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product or {})
    terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
    if not terminal_status:
        return product
    drafts = product.setdefault("drafts", {})
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else default_draft(platform)
    draft["publish_status"] = terminal_status
    if terminal_status == "published":
        draft["status"] = "published"
        draft["validation_errors"] = []
    elif str(item.get("error") or ""):
        draft["validation_errors"] = [
            precheck_item(
                "PUBLISH_BUS_FAILED",
                "publish",
                str(item.get("error") or ""),
                "error",
                "按字段提示修复后重试",
            )
        ]
    draft["last_publish_task"] = {
        "job_id": str(job_state.get("job_id") or ""),
        "status": terminal_status,
        "platform_status": str(item.get("status") or ""),
        "stage": str(item.get("stage") or ""),
        "error": str(item.get("error") or ""),
        "attempts": item.get("attempts", 0),
        "updated_at": str(item.get("updated_at") or job_state.get("updated_at") or collect_time_iso()),
    }
    drafts[platform] = draft
    product["drafts"] = drafts
    return product


def append_publish_bus_terminal_log(product: dict[str, Any], job_state: dict[str, Any], platform: str, item: dict[str, Any]) -> None:
    job_id = str(job_state.get("job_id") or "")
    if publish_bus_log_exists(job_id, platform):
        return
    from .publish_logs_runtime import _product_id_for_log, _write_publish_artifacts

    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    payload = {
        "job_id": job_id,
        "platform": platform,
        "product_id": str(product.get("product_id") or ""),
        "stage": item.get("stage") or "",
        "attempts": item.get("attempts", 0),
    }
    payload_path, response_path = _write_publish_artifacts(f"publish-bus-{platform}", payload, result or item)
    error_map = result.get("error_map") if isinstance(result.get("error_map"), dict) else {}
    field_errors = error_map.get("field_errors") if isinstance(error_map.get("field_errors"), dict) else {}
    terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
    append_publish_log(
        {
            "job_id": job_id,
            "product_id": str(product.get("product_id") or _product_id_for_log(product, platform)),
            "platform": platform,
            "draft_id": str(_draft_for_platform(product, platform).get("sku") or ""),
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
            "sku": str(_draft_for_platform(product, platform).get("sku") or ""),
            "error": str(item.get("error") or result.get("error") or ""),
            "image": normalize_list(product.get("source_image_urls"))[:1],
        }
    )


def persist_publish_bus_terminal_results(job_state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(job_state, dict):
        return {}
    product = job_state.get("product") if isinstance(job_state.get("product"), dict) else {}
    product_id = str(product.get("product_id") or "").strip()
    if product_id:
        loaded = load_product_from_index(product_id, "")
        if loaded:
            product = loaded
    changed = False
    platforms = job_state.get("platforms") if isinstance(job_state.get("platforms"), dict) else {}
    for platform, item in platforms.items():
        if not isinstance(item, dict):
            continue
        terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
        if not terminal_status:
            continue
        product = apply_publish_bus_result_to_product(product, job_state, str(platform), item)
        append_publish_bus_terminal_log(product, job_state, str(platform), item)
        changed = True
    if changed:
        saved = save_product(product)
        job_state["product"] = saved
    return job_state


__all__ = [
    "apply_publish_bus_result_to_product",
    "append_publish_bus_terminal_log",
    "load_publish_logs",
    "page_snapshot_from_html",
    "persist_publish_bus_terminal_results",
    "publish_bus_log_exists",
    "publish_bus_terminal_status",
]

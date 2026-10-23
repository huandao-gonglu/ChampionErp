# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from typing import Callable

from erp_web.schemas.publish import PublishPreparationProgress
from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler
from ..facades import publish_facade

PostHandler = Callable[[JsonRequestHandler], None]


def handle_publish_precheck(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.precheck_publish_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_publish_payload_preview(handler: JsonRequestHandler) -> None:
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    if "application/x-ndjson" not in getattr(handler, "headers", {}).get("Accept", ""):
        result, status = publish_facade.preview_publish_payload(body)
        handler.send_json(result, status)
        return

    # 同一请求推送实际进度与最终回执，不创建第二次上传或后台轮询任务。
    handler.send_sse_headers({
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
        "Connection": "close",
    })
    disconnected = False

    def emit(event: dict) -> None:
        nonlocal disconnected
        if disconnected:
            return
        try:
            handler.write_sse_chunk((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            # 断开连接不撤销已发出的图片上传，继续完成原有持久化边界。
            disconnected = True

    def progress(value: PublishPreparationProgress) -> None:
        emit({"type": "progress", **value})

    try:
        result, status = publish_facade.preview_publish_payload(body, on_progress=progress)
    except Exception:
        logging.getLogger(__name__).exception("发布素材准备进度流执行失败")
        result, status = {"ok": False, "error": "准备发布资料失败，请重试。"}, 500
    if status >= 400:
        logging.getLogger(__name__).warning(
            "发布素材准备失败：status=%s error_code=%s", status, result.get("error_code", "")
        )
    emit({"type": "result", "status": status, "data": result})


def handle_publish_bus_enqueue(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.enqueue_publish_job(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_publish_bus_reconcile(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.reconcile_publish_job(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/publish-precheck": handle_publish_precheck,
    "/api/publish-payload-preview": handle_publish_payload_preview,
    "/api/publish-bus/enqueue": handle_publish_bus_enqueue,
    "/api/publish-bus/reconcile": handle_publish_bus_reconcile,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True

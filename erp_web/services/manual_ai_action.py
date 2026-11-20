"""连接手动发起的业务操作与普通对话，记录实际执行回执。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic_ai import RunCancelled
from pydantic_ai.messages import ModelRequest, UserPromptPart

from erp_web.schemas.chat_conversations import is_global_id
from erp_web.services.ai_conversation_scope import bind_ai_conversation
from erp_web.services.ai_model_errors import safe_model_error_text
from erp_web.services.ai_run_cancellation import bind_cancellation_token, check_cancellation
from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnAlreadyAcceptedError

_logger = logging.getLogger(__name__)

ACTION_TITLES = {
    "category": "AI 匹配类目",
    "copy": "AI 生成文案",
    "copy_batch": "AI 批量生成文案",
    "translation": "AI 翻译",
    "image_prompt": "生成图片提示词",
    "image_translate": "AI 翻译图片",
    "image_edit": "AI 修改图片",
    "image_plan": "AI 规划套图",
    "image_set": "AI 生成套图",
    "image_continue": "继续 AI 图片处理",
    "research": "AI 选品调研",
    "model_test": "测试 AI 模型",
}

_RESULT_FIELDS = {
    "category": ("status", "target", "selected_category_id", "decision", "candidates", "failure", "items"),
    "copy": ("copy", "language", "mode"),
    "copy_batch": ("total", "success_count", "failed_count", "items"),
    "translation": ("translations",),
    "image_prompt": ("prompt",),
    "image_plan": ("plan",),
    "model_test": ("model_id", "model", "tested_capabilities", "supported_capabilities", "capability_results"),
}
_SCOPE_FIELDS = ("product_id", "draft_id", "platform", "site", "category_id", "language", "target_language", "mode", "operation", "request_id")


def _scope(body: dict[str, Any]) -> dict[str, Any]:
    scope = {key: body[key][:300] for key in _SCOPE_FIELDS if isinstance(body.get(key), str) and body[key]}
    for key in ("product_ids", "selected_image_ids", "source_image_ids", "sku_ids", "keywords"):
        if isinstance(values := body.get(key), list):
            scope[key] = [value[:300] for value in values[:200] if isinstance(value, str)]
    if isinstance(body.get("targets"), list):
        scope["targets"] = [
            {key: target[key][:300] for key in ("platform", "site", "language") if isinstance(target.get(key), str)}
            for target in body["targets"][:100] if isinstance(target, dict)
        ]
    if isinstance(body.get("prompt"), str) and body["prompt"]:
        scope["prompt"] = body["prompt"][:4000]
    if isinstance(markets := body.get("markets"), dict) and isinstance(ids := markets.get("target_markets"), list):
        scope["target_markets"] = [value[:300] for value in ids[:100] if isinstance(value, str)]
    return scope


def _result_value(value: Any, *, depth: int = 0) -> Any:
    """只保留有界业务结果，并排除模型配置、原始响应和凭据字段。"""
    if isinstance(value, str):
        return value[:4000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if depth >= 7:
        return None
    if isinstance(value, list):
        return [_result_value(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, dict):
        return {
            key: safe_model_error_text(item) if key in {"error", "message"} and isinstance(item, str)
            else _result_value(item, depth=depth + 1)
            for key, item in list(value.items())[:200]
            if not any(part in key.lower() for part in ("secret", "token", "api_key", "apikey", "authorization", "password", "raw", "cookie"))
        }
    return None


def action_receipt(action: str, body: dict[str, Any], result: dict[str, Any], status: int) -> dict[str, Any]:
    job = None
    image = result.get("image_batch")
    if isinstance(image, dict) and image.get("conversation_id"):
        job = {"kind": "image_batch", "id": image["conversation_id"]}
    if action == "image_continue" and (preparation := result.get("image_preparation")):
        if preparation["batch_id"] and preparation["status"] == "queued":
            job = {"kind": "image_preparation", "id": preparation["batch_id"]}
    if action == "research" and isinstance(run := result.get("run"), dict) and run.get("run_id"):
        job = {"kind": "research", "id": run["run_id"]}
    error = result.get("failure") or result.get("error") or result.get("error_message")
    if isinstance(error, dict):
        error = error.get("message")
    failed = status >= 400 or result.get("ok") is False or bool(result.get("failed_count"))
    state = "waiting" if job else "failed" if failed else "completed"
    if action == "image_continue" and (preparation := result.get("image_preparation")):
        if preparation["status"] in {"needs_confirmation", "needs_selection"}:
            state = "needs_input"
    if action == "category" and (result.get("candidates") or any(item.get("candidates") for item in result.get("items", []))):
        state = "needs_input"
        error = "已生成候选类目，请核对后在草稿中确认采用。"
    if action == "copy_batch" and result.get("success_count") and result.get("failed_count"):
        state = "partial"
    fields = {key: result[key] for key in _RESULT_FIELDS.get(action, ()) if key in result}
    if action == "translation" and "translations" in fields:
        fields["source"] = body.get("content", {})
    if action == "copy_batch" and "items" in fields:
        keys = ("product_id", "platform", "ok", "status", "title", "warning", "error")
        fields["items"] = [{key: row[key] for key in keys if key in row} for row in fields["items"][:200]]
    fields = _result_value(fields)
    # 大批量内容保存在业务原有结果中，回执保留统计和可定位的对象。
    if len(json.dumps(fields, ensure_ascii=False)) > 32_000:
        fields = {
            key: dict(list(value.items())[:20]) if isinstance(value, dict) else value[:20] if isinstance(value, list) else value
            for key, value in fields.items()
        }
        fields["truncated"] = True
        if len(json.dumps(fields, ensure_ascii=False)) > 32_000:
            fields = {"truncated": True, **{key: value for key, value in fields.items() if isinstance(value, (int, bool, float))}}
    receipt = {
        "title": ACTION_TITLES[action], "state": state, "scope": _scope(body),
        "message": safe_model_error_text(error or result.get("message") or "")[:800],
        "result": fields,
    }
    if code := result.get("error_code"):
        receipt["error_code"] = safe_model_error_text(code)
    if job:
        receipt["job"] = job
    return receipt


def execute_manual_ai_action(context, conversation_id: str, action: str, body: dict, execute: Callable):
    """在原 HTTP 请求内调用业务 owner，同一会话 ID 只接收一次手动操作。"""
    from erp_web.services.global_agent_chat_service import (
        GLOBAL_CHAT_ACTOR_ID,
        GLOBAL_CHAT_PROFILE_ID,
        GLOBAL_CHAT_TENANT_ID,
    )

    if not is_global_id(conversation_id):
        return {"ok": False, "error": "手动操作的对话标识无效。", "error_code": "AI_CHAT_CONVERSATION_ID_INVALID"}, 400
    title = ACTION_TITLES[action]
    subject = _scope(body)
    message_id = conversation_id
    message = ModelRequest(
        parts=[UserPromptPart(title)],
        conversation_id=conversation_id,
        metadata={"user_message_id": message_id, "web_input": True, "manual_ai_action": {"title": title, "scope": subject}},
    )
    registry = context.chat_runs
    store = context.agent_calls
    with registry.input_guard():
        if context.conversation_titles.is_deleted(conversation_id):
            return {"ok": False, "error": "这段对话已删除。", "error_code": "CHAT_DELETED"}, 410
        if not registry.acquire(conversation_id):
            return {"ok": False, "error": "该操作已接收，请在对话中查看进度。", "error_code": "AI_CHAT_TURN_ALREADY_ACCEPTED"}, 409
        try:
            store.start_manual_action(
                conversation_id, message, subject, profile_id=GLOBAL_CHAT_PROFILE_ID,
                actor_id=GLOBAL_CHAT_ACTOR_ID, tenant_id=GLOBAL_CHAT_TENANT_ID,
            )
        except AiChatTurnAlreadyAcceptedError:
            registry.release(conversation_id)
            return {"ok": False, "error": "该操作已接收，请在对话中查看结果。", "error_code": "AI_CHAT_TURN_ALREADY_ACCEPTED"}, 409
        except BaseException:
            registry.release(conversation_id)
            raise

    receipt = {**message.metadata["manual_ai_action"], "state": "unknown", "message": "操作结果待核对。"}
    try:
        with bind_ai_conversation(conversation_id), bind_cancellation_token(registry.token(conversation_id)):
            check_cancellation()
            result, status = execute(body)
        receipt = action_receipt(action, body, result, status)
        return result, status
    except RunCancelled:
        receipt = {**receipt, "state": "cancelled", "message": "操作已停止，请核对已产生的业务结果。"}
        return {"ok": False, "error": receipt["message"], "error_code": "AI_CHAT_CANCELLED"}, 409
    except Exception as exc:
        receipt = {**receipt, "state": "failed", "message": safe_model_error_text(exc)[:800] or "业务操作执行失败。"}
        raise
    finally:
        with registry.input_guard():
            try:
                store.finish_manual_action(conversation_id, receipt)
            except Exception:
                _logger.exception("保存手动 AI 操作回执失败：%s", conversation_id)
            finally:
                superseded = registry.is_superseded(conversation_id)
                registry.release(conversation_id)
                if superseded:
                    registry.new_input(conversation_id)
                if not store.release_replacement(conversation_id):
                    store.release_queue(conversation_id)
        store.notify_changed(conversation_id)


def read_manual_ai_action(store, metadata: dict, *, active: bool = False) -> dict | None:
    """按原始用户输入定位同一次业务操作。分支会话仍可引用原回执。"""
    source = metadata.get("user_message_id")
    opening = metadata.get("manual_ai_action")
    if not isinstance(opening, dict) or not isinstance(source, str) or not is_global_id(source):
        return None
    record = store.receipt(source, source)
    if record is None or record["tool_name"] != "manual_ai_action":
        return {**opening, "state": "unknown", "message": "原操作回执暂不可读取。"}
    if not record["output_json"]:
        state = "running" if active else "unknown"
        return {**opening, "state": state, "message": "" if active else "操作结果待核对。"}
    result = json.loads(record["output_json"])
    # 复用服务重启时已有的未知写入恢复，不把中断的操作再次排队。
    if "state" not in result:
        return {**opening, "state": "unknown", "message": "操作中断，结果待核对。"}
    return result


def manual_action_with_progress(receipt: dict, *, include_image_status: bool = True) -> dict:
    """进度来自领域 Job 当前快照，展示和后续追问共用同一读取。"""
    from erp_web.context import get_context

    job = receipt.get("job")
    if not job:
        return receipt
    context = get_context()
    if job["kind"] == "image_batch" and include_image_status:
        batch = context.image_batches.get(job["id"])
        if batch is not None:
            counts = batch.snapshot()
            state = ("waiting" if not counts["terminal"] else "unknown" if any(item.outcome_unknown for item in batch.items)
                     else "partial" if counts["failed"] and counts["success"] else "failed" if counts["failed"] else "completed")
            return {**receipt, "state": state, "progress": {
                key: counts[key] for key in ("total", "queued", "running", "success", "failed")
            }}
    if job["kind"] == "image_preparation":
        result = context.image_batches.preparation(job["id"])
        if result:
            state = {"queued": "waiting", "failed": "failed", "needs_selection": "needs_input",
                     "needs_confirmation": "needs_input"}.get(result.status, "completed")
            return {**receipt, "state": state, "message": result.message}
    if job["kind"] == "research":
        run = context.research.get(job["id"])
        if run is not None:
            state = run.get("status")
            items = run.get("items") or []
            return {**receipt, "state": state if state in ("completed", "failed") else "waiting",
                    "message": run.get("progress_description") or run.get("description") or receipt["message"],
                    "result": {"items": _result_value(items[:20])}}
        return {**receipt, "state": "unknown", "message": "暂时未找到本次选品调研的运行记录。"}
    return receipt

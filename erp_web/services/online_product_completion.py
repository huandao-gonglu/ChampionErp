"""从本轮原生回执检查可恢复的在线修改遗漏，不保存计划或执行工具。"""
import json
from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolCallPart, ToolReturnPart, UserPromptPart

from erp_web.services.ai_code_mode import iter_message_parts
from erp_web.services.ai_agent_dependencies import AiAgentDependencies
from erp_web.services.online_product_changes import contains, same_number
from erp_web.product_model.online_operation_model import NONBLOCKING_STATUSES


def _already_observed(command: dict, record: dict) -> bool:
    """只有读取回执明确覆盖目标值时才免于重复写入；缺失或未知不当作成功。"""
    missing = object()

    def field(path):
        values = record.get("values")
        if isinstance(values, dict) and path in values:
            return values[path]
        current = record if values is None else values
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                return missing
            current = current[key]
        return current

    changes = command.get("changes") or {}
    if not changes:
        return False
    operation = command.get("operation")
    if operation == "content":
        return all(field("content." + key) is not missing and contains(field("content." + key), value)
                   for key, value in changes.items())
    if operation in {"stock", "price"}:
        rows = field("stocks" if operation == "stock" else "prices")
        if not isinstance(rows, list):
            return False
        row = next((row for row in rows if row.get("id") == command.get("scope_id")), {})
        if operation == "stock":
            return row.get("quantity") is not None and row.get("quantity") == changes.get("quantity")
        return row.get("currency") == changes.get("currency") and same_number(row.get("amount"), changes.get("amount"))
    return False


def _object(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def recoverable_online_changes(messages) -> list[dict]:
    """只认实际尝试的写入；读过的商品不自动成为写入目标。后续用户输入重置范围。"""
    calls, attempts, jobs = {}, {}, {}

    def record(command, result):
        if not command.get("listing_id"):
            return
        key = (command["listing_id"], command.get("operation"), command.get("scope_id", ""))
        # 更新目标或收到拒绝审批时，旧的失败不应继续驱动新操作。
        attempts.pop(key, None)
        if result.get("accepted"):
            return
        error = _object(result.get("error"))
        code = result.get("error_code") or error.get("code")
        details = result.get("details") or error.get("details") or {}
        if code in {"ONLINE_VERSION_STALE", "ONLINE_CONFLICT"}:
            attempts[key] = {"listing_id": command["listing_id"], "operation": command.get("operation"),
                             "scope_id": command.get("scope_id", ""), "error_code": code,
                             "conflict_job_id": details.get("conflict_job_id"), "command": command}

    for message in messages:
        for part in iter_message_parts(message):
            if isinstance(part, UserPromptPart):
                calls.clear()
                attempts.clear()
                jobs.clear()
            elif isinstance(part, ToolCallPart):
                # 原生参数重试的失败调用仍保留在历史中，不让无效 JSON 破坏最终答复。
                calls[part.tool_call_id] = (part.tool_name, _object(part.args))
            elif isinstance(part, ToolReturnPart):
                name, args = calls.get(part.tool_call_id, ("", {}))
                result = _object(part.content)
                if name == "online_products_change":
                    record(args, result)
                elif name == "online_products_changes_apply":
                    by_id = {item["listing_id"]: item for item in result.get("items", [])}
                    for item in args.get("items", []):
                        record(item, by_id.get(item["listing_id"], result))
                if name in {"online_products_job_read", "online_products_reconcile", "online_products_close"}:
                    job = result.get("job") or {}
                    if job:
                        jobs[job["id"]] = job["status"]
                    error = _object(result.get("error"))
                    details = error.get("details") or {}
                    if error.get("code") in {"ONLINE_CLOSE_NOT_APPLICABLE", "ONLINE_RECONCILE_NOT_APPLICABLE"} and details.get("status"):
                        jobs[args.get("job_id")] = details["status"]
                if name == "online_products_read":
                    records = list(result.get("records") or [])
                    if result.get("item"):
                        records.append(result["item"])
                    for record_result in records:
                        for key, attempt in list(attempts.items()):
                            if record_result.get("id") == attempt["listing_id"] and _already_observed(attempt["command"], record_result):
                                attempts.pop(key)
    return [{key: value for key, value in attempt.items() if key != "command"}
            for attempt in attempts.values() if attempt["error_code"] == "ONLINE_VERSION_STALE"
            or jobs.get(attempt["conflict_job_id"]) in NONBLOCKING_STATUSES]


def validate_online_completion(ctx: RunContext[AiAgentDependencies], output: Any):
    """复用原生输出重试；审批、取消、资源边界及外部等待不由完成检查接管。"""
    if not isinstance(output, str):
        return output
    remaining = ctx.deps.tool_runtime.max_tool_calls - ctx.usage.tool_calls
    if remaining < 3 or ctx.deps.execution_context.remaining_seconds() < 120:
        return output
    pending = recoverable_online_changes(ctx.messages)
    if pending:
        raise ModelRetry(
            f"本轮仍有 {len(pending)} 个已尝试的新修改未入队，回执显示版本过期或旧任务已不再阻塞。"
            "请先读取这些目标当前所需字段、版本和任务状态，完成仍适用且已授权的修改后再结束。"
            "不要对 partial 等终态重复 close，不重放旧请求或已成功项，不扩大 SKU、字段或仓库范围。"
            "若出现新的真实阻塞或审批拒绝，按实际回执说明。仅重提失败子集时不声明整组覆盖。目标："
            + json.dumps(pending, ensure_ascii=False)
        )
    return output


__all__ = ["recoverable_online_changes", "validate_online_completion"]

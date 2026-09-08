"""使用当前配置的真实模型评测类目匹配；不修改商品、草稿或原会话。"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart, UserPromptPart

from erp_web.context import get_context
from erp_web.db import ErpDatabase
from erp_web.facades.category_match_facade import finalize_category_match, setup_category_match_search
from erp_web.schemas.category import CategoryCandidateLedger
from erp_web.services.ai_agent_factory import AiAgentExecutionError, AiAgentFactory
from erp_web.services.ai_agent_instrumentation import AiAgentInstrumentation
from erp_web.services.category_match_agent_service import CATEGORY_MATCH_DEADLINE_SECONDS, run_category_match_agent
from erp_web.stores.pydantic_message_store import PydanticMessageStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--conversation-id", help="读取历史 category.product_match 的首轮商品事实")
    source.add_argument("--input", type=Path, help="包含 target 和 product 的 JSON 文件")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--model-id", help="仅本次评测改用已配置的模型 ID，不保存应用配置")
    parser.add_argument("--expected-category-id", action="append", default=[], help="仅填写人工确认的类目 ID，可重复传入")
    parser.add_argument("--output", type=Path, help="报告文件；默认与隔离评测数据库一起保存在临时目录")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat 必须大于 0")
    context = get_context()
    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        history = context.pydantic_messages.get(args.conversation_id)
        if history is None:
            parser.error("原会话不存在")
        content = next(
            part.content for message in history.model_messages() for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        payload = json.loads(content[content.index("{"):])
    root = Path(tempfile.mkdtemp(prefix="champion-category-eval-"))
    db = ErpDatabase(root / "evaluation.sqlite3")
    store = PydanticMessageStore(db)
    app_config = context.config.load_app_config()
    if args.model_id:
        if not any(model.get("id") == args.model_id for model in app_config["ai_models"]):
            parser.error("--model-id 必须来自已配置的模型列表")
        app_config["ai_use_case_bindings"]["category.product_match"] = {"model_id": args.model_id}
    factory = AiAgentFactory(
        app_dir=context.paths.app_dir, app_config=app_config,
        message_store=store, instrumentation=AiAgentInstrumentation(root / "agent_spans.jsonl"),
    )
    report_path = args.output or root / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    reports = []
    for index in range(args.repeat):
        start = time.monotonic()
        deadline = start + CATEGORY_MATCH_DEADLINE_SECONDS
        ledger = CategoryCandidateLedger()
        prepared, toolset = setup_category_match_search(payload["target"], payload["product"], ledger, deadline)
        run, error = None, None
        try:
            run = run_category_match_agent(prepared, toolset, ledger, timeout_seconds=deadline - time.monotonic(), factory=factory)
        except AiAgentExecutionError as exc:
            error = exc
        result = finalize_category_match(
            normalized_target=payload["target"], ledger=ledger, deadline_at=deadline,
            agent_run=run, agent_error=error,
        )
        conversation_id = run.outcome.conversation_id if run else error.conversation_id
        history = store.get(conversation_id)
        messages = history.model_messages() if history else []
        responses = [message for message in messages if isinstance(message, ModelResponse)]
        calls = [part for message in messages for part in message.parts if isinstance(part, ToolCallPart) and part.tool_name in {"search_categories", "browse_categories"}]
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart) and part.tool_name in {"search_categories", "browse_categories"}]
        first_ids = {row["category_id"] for row in returns[0].content.get("candidates", [])} if returns else set()
        expected = set(args.expected_category_id)
        record = {
            "run": index + 1, "conversation_id": conversation_id,
            "seconds": round(time.monotonic() - start, 2), "model_requests": len(responses),
            "tool_calls_requested": len(calls), "tool_calls_returned": len(returns),
            "validation_retries": sum(isinstance(part, RetryPromptPart) for message in messages for part in message.parts),
            "keyword_queries": ledger.attempts, "selected_category_id": result["selected_category_id"],
            "status": result["status"], "failure": result["failure"], "decision": result["decision"],
            "model_output": run.output if run else None,
            "response_models": sorted({message.model_name for message in responses if message.model_name}),
            "first_batch_completed": len(returns) == 1 and result["status"] == "completed",
            "two_request_completed": len(responses) == 2 and len(returns) == 1 and result["status"] == "completed",
            "first_batch_recalled_expected": bool(first_ids & expected) if expected else None,
            "selected_expected": result["selected_category_id"] in expected if expected else None,
            "tool_inputs": [part.args_as_dict() for part in calls],
            "input_tokens": sum(message.usage.input_tokens for message in responses),
            "output_tokens": sum(message.usage.output_tokens for message in responses),
        }
        reports.append(record)
        report_path.write_text(json.dumps({"source_conversation_id": args.conversation_id,
            "configured_model_id": app_config["ai_use_case_bindings"]["category.product_match"]["model_id"],
            "evaluation_database": str(root / "evaluation.sqlite3"), "runs": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(report_path), **{key: record[key] for key in (
            "run", "seconds", "model_requests", "tool_calls_returned", "selected_category_id", "status", "first_batch_completed", "selected_expected",
        )}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

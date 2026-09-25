"""颜色编码案例的真实模型隔离验收；业务数据和回执只写入系统临时目录。"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from erp_web.app_config import normalize_app_config
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.services.ai_agent_factory import AiAgentFactory
from erp_web.services.ai_model_factory import create_pydantic_model_binding_for_use_case
from erp_web.services.ai_tool_registry import AiToolSet, deadline_aware_tool_executor
from erp_web.stores.config_store import _apply_app_runtime_secrets
from tests.runtime_test_utils import temp_app_context
from tests.test_native_agent_integration import service, body, CONVERSATION


def load_case(root: Path, conversation_id: str):
    """生产数据库仅以只读连接取得资料和模型配置，绝不实例化生产 AppContext。"""
    with sqlite3.connect((root / "erp.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        messages = json.loads(db.execute(
            "SELECT messages_json FROM pydantic_message_histories WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0])
        readings, definitions, prompt = [], [], ""
        for message in messages:
            for part in message.get("parts", []):
                if part.get("part_kind") == "user-prompt" and not prompt:
                    prompt = part["content"]
                returns = (part.get("metadata") or {}).get("tool_returns") or {}
                for item in returns.values() if isinstance(returns, dict) else returns:
                    content = item.get("content") or {}
                    if item.get("tool_name") == "draft_attributes_read" and content.get("skus"):
                        readings.append(content)
                    if item.get("tool_name") == "category_attributes_query" and not definitions:
                        definitions = content.get("attributes", [])
        if not readings or not definitions:
            raise ValueError("对话缺少 SKU 读取或平台属性定义，无法建立隔离样本。")
        initial = readings[0]
        if len(initial["skus"]) != initial["sku_count"] or initial.get("next_offset") is not None:
            raise ValueError("此验收需要首份完整 SKU 快照，不能把局部分页当作原始全量状态。")
        if not any(item["id"] == "14871214" for item in definitions):
            raise ValueError("当前验收器只评分颜色编码案例，需要对应属性定义。")
        draft_id = initial["draft_id"]
        row = db.execute("SELECT product_id, platform, draft_json FROM platform_drafts WHERE draft_id=?", (draft_id,)).fetchone()
        product = json.loads(db.execute("SELECT product_json FROM products WHERE product_id=?", (row[0],)).fetchone()[0])
        draft = json.loads(row[2])
        draft.update(draft_id=draft_id, product_id=row[0], platform=row[1])
        target = initial["targets"][0]
        key = f"{target['platform']}:{target['site']}".lower()
        by_id = {item["sku_id"]: item for item in initial["skus"]}
        for item in draft["sku_items"]:
            if item["sku_id"] in by_id:
                item.setdefault("attributes_by_target", {})[key] = deepcopy(by_id[item["sku_id"]]["attributes"])
        product["product_id"] = row[0]
        product["drafts"] = {row[1]: draft}
        secrets = {path: json.loads(value) for path, value in db.execute(
            "SELECT secret_path,secret_json FROM runtime_secrets WHERE namespace='app_config'",
        )}
    static = json.loads((root / "config/app_config.json").read_text())
    config = normalize_app_config(_apply_app_runtime_secrets(static, secrets))
    return product, initial, definitions, prompt, config


def evaluate(args):
    product, initial, definitions, prompt, config = load_case(args.config_root.resolve(), args.conversation)
    target, draft_id = initial["targets"][0], initial["draft_id"]
    binding = create_pydantic_model_binding_for_use_case(args.config_root, config, "global.chat")
    record = {**target, "attributes": {"required": [d for d in definitions if d.get("required")],
                                        "optional": [d for d in definitions if not d.get("required")]}}
    with tempfile.TemporaryDirectory(prefix="erp-live-eval-") as directory, temp_app_context(Path(directory)) as app:
        for name in ("agents.md", "prompts"):
            source, dest = args.config_root / "config" / name, Path(directory) / "config" / name
            if source.is_dir(): shutil.copytree(source, dest, dirs_exist_ok=True)
            elif source.exists(): shutil.copy2(source, dest)
        app.products.save_product(product)
        before = app.products.draft_record(draft_id)
        def page(*a, cursor="", limit=20, **kw):
            offset = int(cursor or 0); end = offset + limit
            return {"category_id": target["category_id"], "attributes": deepcopy(definitions[offset:end]),
                    "has_more": end < len(definitions), "next_cursor": str(end) if end < len(definitions) else ""}
        def values(*args, **kwargs):
            # 只返回样本中真实读取过的候选；不访问外部平台。
            definition = next((item for item in definitions if item["id"] == args[2]), {})
            return {"values": deepcopy(definition.get("options") or []), "has_more": False, "next_cursor": ""}
        with patch("erp_web.facades.agent_capability_facade.fetch_category_attribute_page", side_effect=page), \
             patch("erp_web.facades.agent_capability_facade.fetch_category_attribute_values", side_effect=values):
            toolset = build_global_chat_toolset(app)
        allowed = {"draft_attributes_read", "draft_read", "product_read", "inspect_source_facts",
                   "category_attributes_query", "category_attribute_values_query", "draft_sku_attributes_update",
                   "product_attributes_update", "draft_changes_apply", "draft_sku_package_update"}
        calls, write_seconds = Counter(), []
        def guarded(name, original):
            @deadline_aware_tool_executor
            def execute(arguments, execution):
                calls[name] += 1
                if name not in allowed:
                    raise ValueError("隔离验收禁止外部业务操作。")
                started = time.monotonic()
                result = original(arguments, execution)
                if name in {"draft_sku_attributes_update", "product_attributes_update", "draft_changes_apply", "draft_sku_package_update"}:
                    write_seconds.append(time.monotonic() - started)
                return result
            return execute
        toolset = AiToolSet(toolset.toolset_id, {name: replace(item, executor=guarded(name, item.executor)) for name, item in toolset.bindings.items()})
        ui = service(Path(directory), binding.model, toolset)
        ui.chat_service.factory = AiAgentFactory(
            app_dir=directory, app_config={}, message_store=app.pydantic_messages,
            model_binding_factory=lambda *a, **kw: binding,
        )
        # service 与 context 指向同一临时数据库；真实模型沿用生产工厂和 UI 执行入口。
        started = time.monotonic()
        with patch("erp_web.runtime_units.category_attribute_updates.fetch_category_record", return_value=record), \
             patch("erp_web.runtime_units.category_attribute_updates.fetch_category_attribute_values", side_effect=values):
            asyncio.run(ui.prepare_run(body(
                prompt + f"\n当前草稿：{draft_id}，平台：{target['platform']}，站点：{target['site']}。",
                target_draft_ids=[draft_id],
            )).stream(lambda _: None))
        elapsed = time.monotonic() - started
        history = ui.chat_service.trusted_history(CONVERSATION)
        final = app.products.draft_record(draft_id)
        target_key = f"{target['platform']}:{target['site']}".lower()
        source = {row["id"]: row for row in product["sku_items"]}
        wrong = []
        for row in final["sku_items"]:
            if not row.get("selected"): continue
            value = (row.get("attributes_by_target", {}).get(target_key, {}).get("14871214") or {}).get("values", [{}])[0].get("value")
            match = re.match(r"[A-Za-z]+[0-9]+", source[row["sku_id"]]["options"]["尺寸"])
            expected = match.group() if match else None
            if value != expected: wrong.append({"sku_id": row["sku_id"], "expected": expected, "actual": value})
        def unrelated_sku_fields(row):
            snapshot = deepcopy(row)
            snapshot.get("attributes_by_target", {}).get(target_key, {}).pop("14871214", None)
            return snapshot
        previous = {row["sku_id"]: row for row in before["sku_items"]}
        unexpected = [row["sku_id"] for row in final["sku_items"]
                      if unrelated_sku_fields(row) != unrelated_sku_fields(previous[row["sku_id"]])]
        unexpected.extend(key for key in ("title", "brand", "model", "description", "attributes", "package_dimensions")
                          if before.get(key) != final.get(key))
        responses = [m for m in history if m.kind == "response"]
        completed = bool(responses and any(p.part_kind == "text" for p in responses[-1].parts)
                         and not any(p.part_kind == "tool-call" for p in responses[-1].parts))
        steps = [{"kind": p.part_kind, "tool": getattr(p, "tool_name", ""),
                  "content": getattr(p, "args", None) if p.part_kind == "tool-call" else str(getattr(p, "content", ""))[:1500]}
                 for m in history for p in m.parts if p.part_kind in {"tool-call", "retry-prompt"}]
        report = {"passed": completed and not wrong and not unexpected, "completed": completed, "unexpected_changes": unexpected, "steps": steps, "model": binding.model_name, "elapsed_seconds": round(elapsed, 3), "calls": dict(calls),
                  "write_seconds": round(sum(write_seconds), 3), "model_requests": len(responses),
                  "input_tokens": sum(m.usage.input_tokens for m in responses),
                  "output_tokens": sum(m.usage.output_tokens for m in responses), "wrong": wrong,
                  "final": "\n".join(p.content for m in responses for p in m.parts if p.part_kind == "text")}
        if args.output:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, default=ROOT)
    parser.add_argument("--conversation", required=True, help="只读取得原始用户要求、SKU 状态与属性定义的对话")
    parser.add_argument("--output", type=Path, help="可选验收摘要，建议写入系统临时目录")
    sys.exit(evaluate(parser.parse_args()))

"""持久领域工具的领取、投递与 Job 对账；不选择 Agent 的下一步工具。"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic_ai import DeferredToolResults, RunCancelled
from erp_web.schemas.ai_tools import AiToolCommand
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_tool_runtime import AiToolRuntime
from erp_web.services.global_agent_chat_service import GLOBAL_CHAT_PROFILE
from erp_web.services.ai_chat_detached_runner import get_detached_chat_runner
from erp_web.services.vercel_ai_ui_service import VercelAiChatRun
from erp_web.services.ai_run_cancellation import bind_cancellation_token, check_cancellation

_logger = logging.getLogger(__name__)


class AgentJobService:
    def __init__(self, *, ui_service, job_readers: dict[str, Any]):
        self.ui_service = ui_service
        self.store = ui_service.call_store
        self.job_readers = job_readers
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="erp-tool-job")
        self.futures: dict[tuple[str, str], Any] = {}

    def scan(self):
        with self.ui_service.run_registry.input_guard():
            self._scan()

    def _scan(self):
        # 扫描线程只读取/领取/投递，不同步运行工具或模型。
        for key, future in list(self.futures.items()):
            if future.done():
                try:
                    future.result()
                except RunCancelled:
                    receipt = self.store.receipt(*key)
                    if receipt and receipt["status"] == "executing":
                        self.store.mark_unknown(*key)
                except Exception:
                    _logger.exception("后台工具投递失败：%s", key)
                    receipt = self.store.receipt(*key)
                    if receipt and receipt["status"] == "executing":
                        self.store.mark_unknown(*key)
                del self.futures[key]
        for row in self.store.work():
            key = (row["conversation_id"], row["tool_call_id"])
            if key not in self.futures and len(self.futures) < 8:
                self.futures[key] = self.pool.submit(
                    self.execute_or_reconcile, row,
                    self.ui_service.run_registry.token(row["conversation_id"]),
                )
        for conversation_id in self.store.conversations():
            pending = self.store.pending(conversation_id)
            if pending:
                requests, results, _ = pending
                for call in requests.calls:
                    if call.tool_call_id in results.calls:
                        continue
                    receipt = self.store.receipt(conversation_id, call.tool_call_id)
                    if (
                        receipt
                        and receipt["status"] == "completed"
                        and receipt["output_json"]
                    ):
                        self.store.record_results(
                            conversation_id,
                            DeferredToolResults(
                                calls={
                                    call.tool_call_id: json.loads(
                                        receipt["output_json"]
                                    )
                                }
                            ),
                        )
                if not self.store.ready(conversation_id):
                    continue
            if not self.ui_service.run_registry.acquire(conversation_id):
                continue
            run = VercelAiChatRun(conversation_id, "background", self.ui_service)
            try:
                get_detached_chat_runner().submit(
                    run._produce_and_finalize(lambda _: None)
                )
            except Exception:
                self.ui_service.run_registry.release(conversation_id)
                raise

    def execute_or_reconcile(self, row, token=None):
        with bind_cancellation_token(token or self.ui_service.run_registry.token(row["conversation_id"])):
            return self._execute_or_reconcile(row)

    def _execute_or_reconcile(self, row):
        conversation_id, call_id = row["conversation_id"], row["tool_call_id"]
        if row["status"] == "waiting_job":
            job = json.loads(row["job_json"])
            reader = self.job_readers.get(job.get("job_type"))
            if reader is None:
                self.store.finish(
                    conversation_id,
                    call_id,
                    {
                        "ok": False,
                        "job": job,
                        "error": {
                            "code": "JOB_READER_UNAVAILABLE",
                            "message": "后台任务缺少状态读取器；结果待对账。",
                        },
                    },
                )
                return
            state = reader.read_job_state(job["job_id"])
            if state.status == "running":
                self.store.touch_job(conversation_id, call_id)
                return
            self.store.finish(
                conversation_id,
                call_id,
                {
                    "ok": state.status == "success",
                    "job": job,
                    "status": state.status,
                    "evidence": state.model_dump(mode="json"),
                },
            )
            return
        if not self.store.claim_job(conversation_id, call_id):
            return
        check_cancellation()
        if self.store.inbox(conversation_id):
            self.store.finish(
                conversation_id,
                call_id,
                {
                    "ok": False,
                    "error": {
                        "code": "USER_UPDATE_PENDING",
                        "message": "用户已更新要求；本次尚未执行，请按最新用户消息重新决定。",
                    },
                },
            )
            return
        toolset = self.ui_service.chat_service.toolset
        binding = toolset.get(row["tool_name"])
        if binding is None:
            self.store.finish(
                conversation_id,
                call_id,
                {
                    "ok": False,
                    "error": {
                        "code": "TOOL_NOT_ALLOWED",
                        "message": "工具已不在当前权限目录。",
                    },
                },
            )
            return
        meta = json.loads(row["execution_json"])
        context = AiExecutionContext.create(
            # 一次完整目标可含数百个 SKU；各 focused Agent 仍受自身 deadline 限制。
            # 后台业务预算独立于主对话，覆盖来源翻译及有限批次，避免再次卡在 15 分钟。
            timeout_seconds=3600,
            budget_profile="global.chat.jobs",
            cancellation_check=check_cancellation,
            permissions=GLOBAL_CHAT_PROFILE.permissions,
            allow_write=True,
            business_scope={
                "conversation_id": conversation_id,
                "tool_call_id": call_id,
                "target_draft_ids": json.dumps(
                    self.store.selected_drafts(conversation_id)
                ),
                "approver": str(meta.get("approver", "")),
                "approval_confirmed_at": str(meta.get("approval_confirmed_at", "")),
            },
            idempotency_context={"operation_key": f"{conversation_id}:{call_id}"},
            approved_tool_call_ids={call_id} if meta.get("approver") else set(),
            approval_digest=meta.get("approval_digest", ""),
            approval_revision=meta.get("approval_revision", 0),
        )
        runtime = AiToolRuntime(
            toolset=toolset,
            execution_context=context,
            max_output_bytes=GLOBAL_CHAT_PROFILE.max_tool_output_bytes,
        )
        result = runtime.execute(
            AiToolCommand(
                call_id=call_id,
                tool_name=binding.definition.name,
                tool_version=binding.definition.version,
                arguments=json.loads(row["arguments_json"]),
                round=1,
            )
        )
        output = result.to_dict()["output"] if result.ok else result.to_dict()
        self.store.finish(
            conversation_id,
            call_id,
            output,
            job=bool(
                result.ok
                and isinstance(output, dict)
                and output.get("job_id")
                and output.get("job_type")
            ),
        )

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

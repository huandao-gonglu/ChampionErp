"""主对话提供通用读取入口，并按包装缺项调整原生工具可见性。"""

from asyncio import to_thread
from dataclasses import replace
from functools import lru_cache

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.tools import ToolDefinition

from erp_web.product_model.sku_model import PACKAGE_FIELDS, positive_measurement
from erp_web.services.ai_agent_dependencies import AiAgentDependencies
from erp_web.services.ai_code_mode import iter_message_parts
from erp_web.stores.product_store import ProductStore


PACKAGE_TOOLS = frozenset({
    "product_read", "product_image_read", "draft_package_estimates_preview", "draft_package_estimates_confirm",
})


def prepare_chat_tools(products: ProductStore):
    # 一个原生步骤可能为直接调用和 CodeMode 多次准备目录；共用这一步的业务快照。
    @lru_cache(maxsize=1)
    def missing_size(run_id: str, step: int, draft_ids: tuple[str, ...]) -> bool:
        for draft_id in draft_ids:
            content, error, _ = products.load_draft_content(draft_id)
            if error:
                continue
            selected = {row["sku_id"] for row in content["draft"]["sku_items"] if row.get("selected")}
            for sku in content["product"]["sku_items"]:
                if sku["id"] in selected and sku.get("active", True) and any(
                    positive_measurement(sku["package_dimensions"].get(edge)) is None for edge in PACKAGE_FIELDS[:3]
                ):
                    return True
        return False

    async def prepare(ctx: RunContext[AiAgentDependencies], tools: list[ToolDefinition]) -> list[ToolDefinition]:
        # 通用读取是稳定的基础入口，避免为普通查询搜索出一批无关业务工具。
        tools = [replace(tool, defer_loading=False) if tool.name in {'business_data_catalog', 'business_data_read'} else tool for tool in tools]
        support = ctx.deps.tool_runtime.run_support
        if support is None or not {"product.read", "draft.read"} <= ctx.deps.permissions:
            return tools
        page = support.page_context or {}
        draft_ids = support.target_draft_ids or (
            (page["draft_id"],) if page.get("draft_id") else tuple(page.get("selected_draft_ids") or ())
        )
        # 用户也可以在文字中指定其他草稿。按实际业务回执定位，覆盖脚本内调用。
        observed = []
        for message in reversed(ctx.messages):
            for part in iter_message_parts(message):
                if not isinstance(part, ToolReturnPart) or not isinstance(part.content, dict):
                    continue
                if part.tool_name == "draft_read":
                    draft = part.content.get("draft") or {}
                    if draft_id := draft.get("draft_id"):
                        observed.append(draft_id)
                elif part.tool_name == "draft_pricing_preview":
                    error = part.content.get("error") or {}
                    if error.get("code") == "PRICING_INPUT_INVALID" and (draft_id := (error.get("details") or {}).get("draft_id")):
                        observed.append(draft_id)
            if any(isinstance(part, UserPromptPart) for part in message.parts):
                break
        draft_ids = tuple(dict.fromkeys((*observed, *draft_ids)))
        if draft_ids and await to_thread(missing_size, ctx.run_id, ctx.run_step, draft_ids):
            return [replace(tool, defer_loading=False) if tool.name in PACKAGE_TOOLS else tool for tool in tools]
        return tools

    return prepare

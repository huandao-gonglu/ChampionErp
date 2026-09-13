"""有权限、可定位来源的相关对话查询；不建立通用 Memory。"""

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.stores.agent_call_store import AgentCallStore
from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnClaimStore


class ConversationFactQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str = Field(min_length=1, max_length=160)
    conversation_id: str = Field(default="", max_length=160)


class ConversationFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str
    draft_ids: list[str]
    text: str


class ConversationFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    facts: list[ConversationFact]


@dataclass(frozen=True)
class ConversationFactScope:
    calls: AgentCallStore
    claims: AiChatTurnClaimStore


@ai_tool(
    name="conversation_facts_query",
    description="按草稿查询相关真实用户消息，返回有权限的来源消息 ID、原文及实体范围。可指定其他已知 conversation_id。",
    permission="draft.read",
)
def conversation_facts_query(
    request: ConversationFactQuery,
    scope: Annotated[ConversationFactScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ConversationFacts:
    conversation_id = request.conversation_id or execution.business_scope.get(
        "conversation_id", ""
    )
    claim = scope.claims.find_for_conversation(conversation_id)
    facts = []
    if (
        claim
        and claim.actor_id == execution.actor_id
        and claim.tenant_id == execution.tenant_id
    ):
        facts = [
            fact
            for fact in scope.calls.user_facts(conversation_id)
            if request.draft_id in fact["draft_ids"] or request.draft_id in fact["text"]
        ]
    return ConversationFacts(conversation_id=conversation_id, facts=facts)


CONVERSATION_FACT_CAPABILITIES = (conversation_facts_query,)

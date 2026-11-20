import type { UIMessage } from 'ai'

export interface PydanticConversationSummary {
  title?: string
  pinned?: boolean
  conversation_id: string
  created_at: string
  updated_at: string
}

export interface PydanticConversationListResponse {
  ok: boolean
  conversations: PydanticConversationSummary[]
}

export interface PydanticConversationDetailResponse {
  ok: boolean
  conversation_id: string
  created_at: string
  updated_at: string
  messages: unknown[]
}

/** 全局对话 conversation ID 前缀，后端同时用它做快速路由过滤。 */
export const GLOBAL_CHAT_CONVERSATION_PREFIX = 'conversation_global_chat_'

/** Vercel `UIMessagePart`（默认泛型），供纯展示组件渲染单个 part。 */
export type AiUiPart = UIMessage['parts'][number]

export interface AiTurnTiming {
  started_at: string
  finished_at?: string
  status?: string
}

/** 服务端在 UIMessage.metadata 中附加的脚本写回执，不改变原生工具状态。 */
export interface BusinessWriteReceipt {
  tool_call_id: string
  tool_name: string
  output: Record<string, unknown>
}

/**
 * `/ui-messages` 派生读取响应。`messages` 是官方 Adapter `dump_messages()`
 * 以 JSON alias 序列化出的 Vercel `UIMessage[]`。
 */
export interface AiWorkUiMessagesResponse {
  ok: boolean
  conversation_id: string
  history_version: number
  created_at: string
  updated_at: string
  messages: UIMessage[]
  parallel_replies?: AiWorkUiMessagesResponse[]
  run_error?: { code: string; message: string } | null
  run_active?: boolean
  latest_message_id?: string | null
  run_status?: 'claimed' | 'completed' | 'failed' | 'cancelled' | null
  pending_tool_calls?: PendingToolCall[]
  received_messages?: { message_id: string; status: string; text: string }[]
}

export interface AiChatCancelResponse {
  ok: boolean
  conversation_id: string
}

export interface PendingToolCall {
  tool_call_id: string
  tool_name: string
  kind: 'approval' | 'external' | 'input'
  requested_at?: string
  title?: string
  question?: string
  options?: string[]
  summary: string
  progress?: string
}
export interface ManualAiAction {
  title: string
  state: 'running' | 'waiting' | 'completed' | 'failed' | 'partial' | 'needs_input' | 'cancelled' | 'unknown'
  message?: string
  scope?: Record<string, unknown>
  result?: Record<string, unknown>
  job?: { kind: 'image_batch' | 'image_preparation' | 'research'; id: string }
}

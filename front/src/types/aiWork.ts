import type { UIMessage } from 'ai'

export interface PydanticConversationSummary {
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

/**
 * `/ui-messages` 派生读取响应。`messages` 是官方 Adapter `dump_messages()`
 * 以 JSON alias 序列化出的 Vercel `UIMessage[]`。
 */
export interface AiWorkUiMessagesResponse {
  ok: boolean
  conversation_id: string
  created_at: string
  updated_at: string
  messages: UIMessage[]
}

export type GlobalTaskStatus =
  | 'running'
  | 'needs_input'
  | 'pending_approval'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface GlobalTaskApprovalRequest {
  step_id: string
  capability_name: string
  capability_version: string
  task_revision: number
  digest: string
  payload: Record<string, unknown>
  requested_at: string
}

export interface GlobalTaskStep {
  step_id: string
  capability_name: string
  status: string
  result?: Record<string, unknown> | null
  error?: { code?: string; message?: string } | null
}

export interface GlobalTaskState {
  task_id: string
  goal: string
  status: GlobalTaskStatus
  steps: GlobalTaskStep[]
  current_step_index: number
  pending_approval?: GlobalTaskApprovalRequest | null
  assistant_message?: string
  error_code?: string
  error_message?: string
}

export interface GlobalTaskResponse {
  ok: true
  task_id: string
  task: GlobalTaskState
}

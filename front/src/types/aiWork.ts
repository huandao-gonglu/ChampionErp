export type AiWorkEventType =
  | 'RUN_STARTED'
  | 'RUN_FINISHED'
  | 'RUN_ERROR'
  | 'RUN_DEFERRED'
  | 'RUN_RESUMED'
  | 'STEP_STARTED'
  | 'STEP_FINISHED'
  | 'REASONING_MESSAGE_START'
  | 'REASONING_MESSAGE_CONTENT'
  | 'REASONING_MESSAGE_END'
  | 'TEXT_MESSAGE_START'
  | 'TEXT_MESSAGE_CONTENT'
  | 'TEXT_MESSAGE_END'
  | 'CUSTOM'
  | 'RAW'

export interface AiWorkEvent {
  schema_version: number
  seq: number
  timestamp: number
  occurred_at: string
  type: AiWorkEventType
  threadId: string
  runId: string
  conversation_id: string
  messageId?: string
  role?: string
  delta?: string
  name?: string
  value?: unknown
  message?: string
  code?: string
  retryable?: boolean
  input?: unknown
  result?: unknown
  event?: unknown
  source?: string
  rawEvent?: unknown
  trace_id?: string
  run_id?: string
}

export interface AiWorkConversationSummary {
  conversation_id: string
  parent_conversation_id: string | null
  use_case_id: string
  capability: string
  provider_id: string
  provider: string
  model_id: string
  model: string
  stream: boolean
  required_capabilities: string[]
  timeout_seconds: number | null
  status: 'running' | 'waiting_approval' | 'completed' | 'failed' | 'interrupted'
  latest_task_status?:
    | 'planning'
    | 'running'
    | 'needs_input'
    | 'waiting_publish_confirmation'
    | 'waiting_publish_result'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | null
  created_at: string
  updated_at: string
  last_seq: number
  event_count: number
  error: string
}

export interface AiWorkConversationListResponse {
  ok: boolean
  conversations: AiWorkConversationSummary[]
}

export type AiWorkConversationChildrenResponse = AiWorkConversationListResponse

export interface AiWorkConversationResponse {
  ok: boolean
  conversation_id: string
  conversation: AiWorkConversationSummary
  events: AiWorkEvent[]
}

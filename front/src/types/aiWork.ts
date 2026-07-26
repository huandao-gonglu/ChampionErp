export type AiWorkEventType =
  | 'RUN_STARTED'
  | 'RUN_FINISHED'
  | 'RUN_ERROR'
  | 'STEP_STARTED'
  | 'STEP_FINISHED'
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
  result?: unknown
  event?: unknown
  source?: string
  rawEvent?: unknown
}

export interface AiWorkConversationSummary {
  conversation_id: string
  use_case_id: string
  capability: string
  provider_id: string
  provider: string
  model_id: string
  model: string
  stream: boolean
  required_capabilities: string[]
  timeout_seconds: number | null
  status: 'running' | 'completed' | 'failed' | 'interrupted'
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

export interface AiWorkConversationResponse {
  ok: boolean
  conversation_id: string
  events: AiWorkEvent[]
}

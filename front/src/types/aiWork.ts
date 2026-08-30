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
  history_version: number
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

/** needs_input 类型化待补字段的输入类型；与后端 AiToolRequiredInput 对齐。 */
export type GlobalTaskInputType = 'text' | 'select' | 'multi_select' | 'json_object' | 'string_list'

/** 待补字段的提交归属路径；与后端 AiToolRequiredInput.input_owner 对齐。 */
export type GlobalTaskInputOwner = 'step' | 'provided_attributes' | 'pricing_input'

/** 待补字段选项；界面显示 label，提交稳定 value。 */
export interface GlobalTaskInputOption {
  value: string
  label: string
}

export interface GlobalTaskRequiredInput {
  key: string
  label: string
  reason?: string
  input_type?: GlobalTaskInputType
  options?: GlobalTaskInputOption[]
  input_owner?: GlobalTaskInputOwner
}

export interface GlobalTaskState {
  task_id: string
  goal: string
  status: GlobalTaskStatus
  steps: GlobalTaskStep[]
  current_step_index: number
  pending_approval?: GlobalTaskApprovalRequest | null
  pending_inputs?: GlobalTaskRequiredInput[]
  assistant_message?: string
  error_code?: string
  error_message?: string
}

/** 执行进度展示状态；与后端 GlobalTaskProgressStatus 对齐。 */
export type GlobalTaskProgressStatus =
  | 'queued'
  | 'running'
  | 'waiting'
  | 'retrying'
  | 'completed'
  | 'failed'

/** Job 内部活动（子步骤）；code/label 已由后端白名单映射。 */
export interface GlobalTaskProgressActivity {
  code: string
  label: string
  status: GlobalTaskProgressStatus
  completed_at: string | null
}

/** 当前顶层步骤投影。 */
export interface GlobalTaskCurrentStepProgress {
  index: number
  ordinal: number
  total: number
  capability_name: string
  label: string
  status: string
}

/** 活跃领域 Job 的通用进度投影。 */
export interface GlobalTaskActiveJobProgress {
  job_id: string
  job_type: string
  status: GlobalTaskProgressStatus
  stage_code: string
  stage_label: string
  summary: string
  started_at: string
  updated_at: string | null
  elapsed_seconds: number
  phase_started_at: string | null
  phase_elapsed_seconds: number | null
  attempt: number | null
  retry_count: number | null
  next_check_at: string | null
  last_external_status: string
}

/** 计算型只读进度视图；observed_at 是前端计时锚点。 */
export interface GlobalTaskExecutionProgress {
  observed_at: string
  task_elapsed_seconds: number
  current_step: GlobalTaskCurrentStepProgress | null
  active_job: GlobalTaskActiveJobProgress | null
  activities: GlobalTaskProgressActivity[]
}

export interface GlobalTaskResponse {
  ok: true
  task_id: string
  task: GlobalTaskState
  /** 计算型只读进度视图；后端进度投影失败时可能为 null。 */
  execution_progress?: GlobalTaskExecutionProgress | null
}

/** conversation → 未解决 Deferred 任务的只读关联；无 ready 任务时 task 为 null。 */
export interface ConversationTaskLinkResponse {
  ok: boolean
  conversation_id: string
  task_id: string
  link_status: string
  task: GlobalTaskState | null
}

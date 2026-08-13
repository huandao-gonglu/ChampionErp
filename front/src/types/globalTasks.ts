export type GlobalTaskStatus =
  | 'planning'
  | 'running'
  | 'needs_input'
  | 'waiting_publish_confirmation'
  | 'waiting_publish_result'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type GlobalTaskStepStatus =
  | 'pending'
  | 'running'
  | 'needs_input'
  | 'completed'
  | 'failed'

export interface GlobalTaskStep {
  step_id: string
  capability: string
  objective: string
  status: GlobalTaskStepStatus
  inputs: Record<string, unknown>
  result_summary: string
  result_ref: string
  error_code: string
}

export interface GlobalTaskRequiredInput {
  key: string
  label: string
  reason: string
  input_type: 'text' | 'select' | 'json_object' | 'string_list'
  options: string[]
  input_owner: 'step' | 'provided_attributes' | 'pricing_input'
}

export interface GlobalTaskPublishConfirmation {
  status: 'none' | 'pending' | 'confirmed'
  validation_digest: string
  summary: Record<string, unknown>
  confirmed_at: string | null
}

export interface GlobalTaskState {
  schema_version: 1
  task_id: string
  task_kind: string
  goal: string
  product_id: string
  platform: string
  status: GlobalTaskStatus
  steps: GlobalTaskStep[]
  current_step_index: number
  pending_inputs: GlobalTaskRequiredInput[]
  pending_input_owner: 'none' | 'planning' | 'capability'
  publish_confirmation: GlobalTaskPublishConfirmation
  publish_idempotency_key: string
  publish_job_id: string
  draft_query_snapshot_id: string
  ai_work_conversation_id: string
  agent_execution_conversation_ids: string[]
  assistant_message: string
  plan_explanation: string
  error_code: string
  error_message: string
  created_at: string
  updated_at: string
}

export interface GlobalTaskResponse {
  ok: true
  task: GlobalTaskState
  task_id: string
  ai_work_conversation_id: string
}

export interface GlobalTaskStartRequest {
  goal: string
  task_kind?: 'global.agent.chat'
  product_id?: string
  platform?: string
  ai_work_conversation_id?: string
  draft_query_snapshot_id?: string
}

export interface GlobalTaskStateRequest {
  task_id: string
}

export interface GlobalTaskInputRequest {
  task_id: string
  message: string
  inputs?: Record<string, unknown>
}

export interface GlobalTaskPublishConfirmRequest {
  task_id: string
}

export interface GlobalTaskCancelRequest {
  task_id: string
}

import { apiClient } from './client'
import type { BackendAppStateResponse } from '@/types/workflow.generated'
import type {
  GlobalTaskExecutionProgress,
  GlobalTaskProgressActivity,
  GlobalTaskProgressStatus,
  GlobalTaskResponse,
} from '@/types/aiWork'

const GLOBAL_TASK_STATE_PATH = '/api/v1/global-tasks'
const GLOBAL_TASK_INPUT_PATH = '/api/global-task-input'
const GLOBAL_TASK_APPROVE_PATH = '/api/global-task-approve'
const GLOBAL_TASK_REJECT_PATH = '/api/global-task-reject'
const GLOBAL_TASK_CANCEL_PATH = '/api/global-task-cancel'
const APPROVAL_TOKEN_HEADER = 'X-Approval-Token'

const PROGRESS_STATUSES: ReadonlySet<GlobalTaskProgressStatus> = new Set([
  'queued',
  'running',
  'waiting',
  'retrying',
  'completed',
  'failed',
])

async function loadApprovalToken(): Promise<string> {
  const response = await apiClient.get<BackendAppStateResponse>('/api/state')
  const token = String(response.data.approvalToken || '').trim()
  if (!token) {
    throw new Error('受信审批凭据缺失，请刷新页面后重试。')
  }
  return token
}

/** 把任意输入收敛为非负整数；非法/负数/NaN 一律归 0。 */
function clampNonNegativeInt(value: unknown): number {
  const num = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(num) || num < 0) return 0
  return Math.floor(num)
}

/** 规范化 ISO 时间字符串；空值/非字符串归 null。 */
function normalizeIso(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** 未知状态回落为 running；保证展示层永远拿到受控枚举。 */
function normalizeProgressStatus(value: unknown): GlobalTaskProgressStatus {
  const text = String(value || '').trim().toLowerCase()
  return PROGRESS_STATUSES.has(text as GlobalTaskProgressStatus)
    ? (text as GlobalTaskProgressStatus)
    : 'running'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function normalizeActivity(raw: unknown): GlobalTaskProgressActivity | null {
  if (!isRecord(raw)) return null
  const code = String(raw.code || '').trim()
  if (!code) return null
  return {
    code,
    label: String(raw.label || code),
    status: normalizeProgressStatus(raw.status),
    completed_at: normalizeIso(raw.completed_at),
  }
}

/** 规范化后端进度视图；缺失/非法字段安全降级，绝不抛出。 */
export function normalizeExecutionProgress(
  raw: unknown,
): GlobalTaskExecutionProgress | null {
  if (!isRecord(raw)) return null
  const observedAt = normalizeIso(raw.observed_at)
  if (!observedAt) return null

  const currentStepRaw = isRecord(raw.current_step) ? raw.current_step : null
  const activeJobRaw = isRecord(raw.active_job) ? raw.active_job : null
  const activitiesRaw = Array.isArray(raw.activities) ? raw.activities : []

  const currentStep = currentStepRaw
    ? {
        index: clampNonNegativeInt(currentStepRaw.index),
        ordinal: clampNonNegativeInt(currentStepRaw.ordinal),
        total: clampNonNegativeInt(currentStepRaw.total),
        capability_name: String(currentStepRaw.capability_name || ''),
        label: String(
          currentStepRaw.label || currentStepRaw.capability_name || '',
        ),
        status: String(currentStepRaw.status || 'running'),
      }
    : null

  const activeJob = activeJobRaw
    ? {
        job_id: String(activeJobRaw.job_id || ''),
        job_type: String(activeJobRaw.job_type || ''),
        status: normalizeProgressStatus(activeJobRaw.status),
        stage_code: String(activeJobRaw.stage_code || ''),
        stage_label: String(activeJobRaw.stage_label || ''),
        summary: String(activeJobRaw.summary || ''),
        started_at: normalizeIso(activeJobRaw.started_at) || observedAt,
        updated_at: normalizeIso(activeJobRaw.updated_at),
        elapsed_seconds: clampNonNegativeInt(activeJobRaw.elapsed_seconds),
        phase_started_at: normalizeIso(activeJobRaw.phase_started_at),
        phase_elapsed_seconds:
          activeJobRaw.phase_elapsed_seconds == null
            ? null
            : clampNonNegativeInt(activeJobRaw.phase_elapsed_seconds),
        attempt:
          activeJobRaw.attempt == null
            ? null
            : clampNonNegativeInt(activeJobRaw.attempt),
        retry_count:
          activeJobRaw.retry_count == null
            ? null
            : clampNonNegativeInt(activeJobRaw.retry_count),
        next_check_at: normalizeIso(activeJobRaw.next_check_at),
        last_external_status: String(activeJobRaw.last_external_status || ''),
      }
    : null

  const activities = activitiesRaw
    .map((item) => normalizeActivity(item))
    .filter((item): item is GlobalTaskProgressActivity => item !== null)

  return {
    observed_at: observedAt,
    task_elapsed_seconds: clampNonNegativeInt(raw.task_elapsed_seconds),
    current_step: currentStep,
    active_job: activeJob,
    activities,
  }
}

/** 写/读响应统一规范化进度字段，避免写操作后任务卡短暂丢失进度。 */
function withNormalizedProgress(
  response: GlobalTaskResponse,
): GlobalTaskResponse {
  return {
    ...response,
    execution_progress: normalizeExecutionProgress(response.execution_progress),
  }
}

/** 按 task_id 纯读任务状态；GET 请求，不推进任务。 */
export async function fetchGlobalTask(taskId: string): Promise<GlobalTaskResponse> {
  const response = await apiClient.get<GlobalTaskResponse>(
    `${GLOBAL_TASK_STATE_PATH}/${encodeURIComponent(taskId)}`,
  )
  return withNormalizedProgress(response.data)
}

/** 为待补资料任务提交补充字段；后端合并后交 worker 继续执行。 */
export async function submitGlobalTaskInput(
  taskId: string,
  args: Record<string, unknown>,
): Promise<GlobalTaskResponse> {
  const response = await apiClient.post<GlobalTaskResponse>(GLOBAL_TASK_INPUT_PATH, {
    task_id: taskId,
    arguments: args,
  })
  return withNormalizedProgress(response.data)
}

export async function approveGlobalTask(
  taskId: string,
  stepId = '',
): Promise<GlobalTaskResponse> {
  const token = await loadApprovalToken()
  const response = await apiClient.post<GlobalTaskResponse>(
    GLOBAL_TASK_APPROVE_PATH,
    { task_id: taskId, step_id: stepId },
    { headers: { [APPROVAL_TOKEN_HEADER]: token } },
  )
  return withNormalizedProgress(response.data)
}

export async function rejectGlobalTask(
  taskId: string,
  stepId: string,
  reason: string,
): Promise<GlobalTaskResponse> {
  const token = await loadApprovalToken()
  const response = await apiClient.post<GlobalTaskResponse>(
    GLOBAL_TASK_REJECT_PATH,
    { task_id: taskId, step_id: stepId, reason },
    { headers: { [APPROVAL_TOKEN_HEADER]: token } },
  )
  return withNormalizedProgress(response.data)
}

/** 取消尚未终结的任务。 */
export async function cancelGlobalTask(taskId: string): Promise<GlobalTaskResponse> {
  const response = await apiClient.post<GlobalTaskResponse>(GLOBAL_TASK_CANCEL_PATH, {
    task_id: taskId,
  })
  return withNormalizedProgress(response.data)
}

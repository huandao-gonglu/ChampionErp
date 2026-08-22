import { apiClient } from './client'
import type { BackendAppStateResponse } from '@/types/workflow.generated'
import type { GlobalTaskResponse } from '@/types/aiWork'

const GLOBAL_TASK_STATE_PATH = '/api/v1/global-tasks'
const GLOBAL_TASK_INPUT_PATH = '/api/global-task-input'
const GLOBAL_TASK_APPROVE_PATH = '/api/global-task-approve'
const GLOBAL_TASK_REJECT_PATH = '/api/global-task-reject'
const GLOBAL_TASK_CANCEL_PATH = '/api/global-task-cancel'
const APPROVAL_TOKEN_HEADER = 'X-Approval-Token'

async function loadApprovalToken(): Promise<string> {
  const response = await apiClient.get<BackendAppStateResponse>('/api/state')
  const token = String(response.data.approvalToken || '').trim()
  if (!token) {
    throw new Error('受信审批凭据缺失，请刷新页面后重试。')
  }
  return token
}

/** 按 task_id 纯读任务状态；GET 请求，不推进任务。 */
export async function fetchGlobalTask(taskId: string): Promise<GlobalTaskResponse> {
  const response = await apiClient.get<GlobalTaskResponse>(
    `${GLOBAL_TASK_STATE_PATH}/${encodeURIComponent(taskId)}`,
  )
  return response.data
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
  return response.data
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
  return response.data
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
  return response.data
}

/** 取消尚未终结的任务。 */
export async function cancelGlobalTask(taskId: string): Promise<GlobalTaskResponse> {
  const response = await apiClient.post<GlobalTaskResponse>(GLOBAL_TASK_CANCEL_PATH, {
    task_id: taskId,
  })
  return response.data
}

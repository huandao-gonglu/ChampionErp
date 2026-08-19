import { apiClient } from './client'
import type { BackendAppStateResponse } from '@/types/workflow.generated'
import type { GlobalTaskResponse } from '@/types/aiWork'

const GLOBAL_TASK_STATE_PATH = '/api/global-task-state'
const GLOBAL_TASK_REFRESH_PATH = '/api/global-task-refresh'
const GLOBAL_TASK_APPROVE_PATH = '/api/global-task-approve'
const GLOBAL_TASK_REJECT_PATH = '/api/global-task-reject'
const APPROVAL_TOKEN_HEADER = 'X-Approval-Token'

async function loadApprovalToken(): Promise<string> {
  const response = await apiClient.get<BackendAppStateResponse>('/api/state')
  const token = String(response.data.approvalToken || '').trim()
  if (!token) {
    throw new Error('受信审批凭据缺失，请刷新页面后重试。')
  }
  return token
}

export async function fetchGlobalTask(taskId: string): Promise<GlobalTaskResponse> {
  const response = await apiClient.post<GlobalTaskResponse>(GLOBAL_TASK_STATE_PATH, {
    task_id: taskId,
  })
  return response.data
}

export async function refreshGlobalTask(taskId: string): Promise<GlobalTaskResponse> {
  const response = await apiClient.post<GlobalTaskResponse>(GLOBAL_TASK_REFRESH_PATH, {
    task_id: taskId,
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

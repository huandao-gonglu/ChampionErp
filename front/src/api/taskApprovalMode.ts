import { apiClient } from './client'
import type { BackendAppStateResponse } from '@/types/workflow.generated'

export type TaskApprovalMode = 'ask' | 'full'

const STATE_PATH = '/api/state'
const SAVE_SETTINGS_PATH = '/api/save-settings'
const APPROVAL_TOKEN_HEADER = 'X-Approval-Token'

function normalizeTaskApprovalMode(value: unknown): TaskApprovalMode {
  return value === 'full' ? 'full' : 'ask'
}

export async function fetchTaskApprovalMode(): Promise<TaskApprovalMode> {
  const response = await apiClient.get<BackendAppStateResponse>(STATE_PATH)
  return normalizeTaskApprovalMode(response.data.appConfig?.task_approval_mode)
}

export async function saveTaskApprovalMode(
  mode: TaskApprovalMode,
): Promise<TaskApprovalMode> {
  const state = await apiClient.get<BackendAppStateResponse>(STATE_PATH)
  const token = String(state.data.approvalToken || '').trim()
  if (!token) {
    throw new Error('受信审批凭据缺失，请刷新页面后重试。')
  }
  const response = await apiClient.post<{
    ok: boolean
    appConfig: Record<string, unknown>
  }>(
    SAVE_SETTINGS_PATH,
    { appConfig: { task_approval_mode: mode } },
    { headers: { [APPROVAL_TOKEN_HEADER]: token } },
  )
  return normalizeTaskApprovalMode(
    response.data.appConfig?.task_approval_mode,
  )
}


import { apiClient } from './client'

export type AiToolApprovalMode = 'ask' | 'full'

function readMode(value: unknown): AiToolApprovalMode {
  if (value !== 'ask' && value !== 'full') throw new Error('服务端未返回有效的审批模式，请刷新重试')
  return value
}

export async function fetchAiApprovalMode(): Promise<AiToolApprovalMode> {
  const { data } = await apiClient.get<{ appConfig: { ai_tool_approval_mode: AiToolApprovalMode } }>('/api/state')
  return readMode(data.appConfig?.ai_tool_approval_mode)
}

export async function saveAiApprovalMode(mode: AiToolApprovalMode): Promise<AiToolApprovalMode> {
  const { data: state } = await apiClient.get<{ approvalToken: string }>('/api/state')
  const { data } = await apiClient.post<{ ok: boolean; mode: AiToolApprovalMode; error?: string }>(
    '/api/ai/approval-mode', { mode }, { headers: { 'X-Approval-Token': state.approvalToken } },
  )
  if (!data.ok) throw new Error(data.error || '审批模式未保存')
  return readMode(data.mode)
}

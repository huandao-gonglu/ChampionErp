import { apiClient } from './client'
export interface RequestBlock {
  id: string
  platform: string
  account: string
  scope: string
  interface: string
  code: string
  message: string
  http_status: number
  created_at: number
  blocked_count: number
  resume_at: number
  recovery_mode: 'probe' | 'confirm' | 'confirm_request' | 'waiting' | 'verify_result'
}
export interface RequestInterruption {
  id: string
  platform: string
  interface: string
  trigger: string
  created_at: number
  code: string
  message: string
  local_rejection: boolean
}
export interface RequestControlStatus {
  ok: boolean
  server_time: number
  blocks: RequestBlock[]
  history: RequestInterruption[]
  total: number
}
export async function fetchRequestControl(offset = 0): Promise<RequestControlStatus> {
  return (await apiClient.get('/api/external-requests/status', { params: { offset }, timeout: 10000 })).data
}
export async function recoverRequestBlock(blockId: string, reason = ''): Promise<{ ok: boolean; message: string }> {
  return (await apiClient.post('/api/external-requests/recover', { block_id: blockId, reason })).data
}

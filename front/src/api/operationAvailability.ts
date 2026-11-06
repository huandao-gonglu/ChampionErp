import { apiClient } from './client'
import { setOperationAvailability } from '@/utils/desktop'
import type { BackendOperationAvailability } from '@/types/workflow.generated'

let revision = 0

export function resetOperationAvailability() {
  ++revision
  setOperationAvailability(null)
}

export async function refreshOperationAvailability() {
  const request = ++revision
  try {
    const { data } = await apiClient.get<BackendOperationAvailability>('/api/operation-availability')
    if (typeof data?.desktop_reason !== 'string' || !data.blocked_use_cases || typeof data.blocked_use_cases !== 'object') {
      throw new Error('操作能力响应无效。')
    }
    if (request === revision) setOperationAvailability(data)
  } catch {
    if (request === revision) setOperationAvailability(null, '暂时无法确认当前端是否可用，请刷新重试。')
  }
}

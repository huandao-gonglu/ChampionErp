import { apiClient } from '@/api/client'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const authPath = '/api/alibaba/authorization'

export async function fetchAlibabaAuthorization(): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.get(authPath)).data
}
export async function startAlibabaAuthorization(): Promise<{ ok: boolean; url: string }> {
  return (await apiClient.post(authPath + '/start', {})).data
}

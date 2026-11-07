import { apiClient } from '@/api/client'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

function authPath(crossborder: boolean) {
  return crossborder ? '/api/alibaba/crossborder/authorization' : '/api/alibaba/authorization'
}
export async function fetchAlibabaAuthorization(crossborder = false): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.get(authPath(crossborder))).data
}
export async function startAlibabaAuthorization(crossborder = false): Promise<{ ok: boolean; url: string }> {
  return (await apiClient.post(authPath(crossborder) + '/start', {})).data
}

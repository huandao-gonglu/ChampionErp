import { apiClient } from '@/api/client'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const authPath = '/api/alibaba/authorization'

export async function fetchAlibabaAuthorization(): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.get(authPath)).data
}
export async function startAlibabaAuthorization(): Promise<{ ok: boolean; url: string }> {
  return (await apiClient.post(authPath + '/start', {})).data
}
export async function completeAlibabaAuthorization(callbackUrl: string): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.post(authPath + '/complete', { callback_url: callbackUrl })).data
}
export async function bindOneBoundAuthorization(session: string): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.post(authPath + '/onebound', { session })).data
}

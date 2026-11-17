import { apiClient } from '@/api/client'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const authPath = '/api/alibaba/authorization'
export type AlibabaProvider = BackendAlibabaAuthorizationStatus['application_type']

export async function fetchAlibabaAuthorization(provider: AlibabaProvider): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.get(authPath, { params: { provider } })).data
}
export async function startAlibabaAuthorization(provider: AlibabaProvider): Promise<{ ok: boolean; url: string }> {
  return (await apiClient.post(authPath + '/start', { provider })).data
}
export async function completeAlibabaAuthorization(callbackUrl: string, provider: AlibabaProvider): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.post(authPath + '/complete', { callback_url: callbackUrl, provider })).data
}
export async function bindOneBoundBuyerToken(accessToken: string): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.post(authPath + '/onebound', { access_token: accessToken })).data
}
export async function bindSelfUseToken(accessToken: string): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.post(authPath + '/self-use-token', { access_token: accessToken })).data
}

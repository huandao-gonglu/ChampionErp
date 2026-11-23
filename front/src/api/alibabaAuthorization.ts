import { apiClient } from '@/api/client'
import type { BackendAlibabaAuthorizationStatus, BackendOneBoundQuotaView } from '@/types/workflow.generated'

const authPath = '/api/alibaba/authorization'
export type AlibabaProvider = BackendAlibabaAuthorizationStatus['application_type']
export type OneBoundQuotaView = BackendOneBoundQuotaView

export async function fetchAlibabaAuthorization(provider: AlibabaProvider): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.get(authPath, { params: { provider } })).data
}
export async function startAlibabaAuthorization(provider: AlibabaProvider): Promise<{ ok: boolean; url: string }> {
  return (await apiClient.post(authPath + '/start', { provider })).data
}
export async function completeAlibabaAuthorization(callbackUrl: string, provider: AlibabaProvider): Promise<BackendAlibabaAuthorizationStatus> {
  if (provider === 'onebound') {
    return (await apiClient.post(authPath + '/onebound/complete', { callback_url: callbackUrl })).data
  }
  return (await apiClient.post(authPath + '/complete', { callback_url: callbackUrl, provider })).data
}
export async function bindSelfUseToken(accessToken: string): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.post(authPath + '/self-use-token', { access_token: accessToken })).data
}
export async function fetchOneBoundQuota(): Promise<OneBoundQuotaView> {
  return (await apiClient.get('/api/business-queries/alibaba.onebound_quota')).data
}
export async function restoreOneBoundKey(): Promise<{ ok: boolean; restored: boolean; quota: OneBoundQuotaView }> {
  return (await apiClient.post('/api/alibaba/onebound/key/restore', {})).data
}

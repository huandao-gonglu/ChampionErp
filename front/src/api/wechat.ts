import { apiClient } from './client'
import type { BackendWechatChannelStatus } from '@/types/workflow.generated'

export type WechatForwardType = BackendWechatChannelStatus['forward_types'][number]

export async function fetchWechatStatus(signal?: AbortSignal): Promise<BackendWechatChannelStatus> {
  return (await apiClient.get<BackendWechatChannelStatus>('/api/wechat/status', { signal })).data
}
export async function startWechatLogin(): Promise<BackendWechatChannelStatus> {
  return (await apiClient.post<BackendWechatChannelStatus>('/api/wechat/login', {})).data
}
export async function verifyWechatLogin(code: string): Promise<BackendWechatChannelStatus> {
  return (await apiClient.post<BackendWechatChannelStatus>('/api/wechat/verify', { code })).data
}
export async function unbindWechat(): Promise<BackendWechatChannelStatus> {
  return (await apiClient.post<BackendWechatChannelStatus>('/api/wechat/unbind', {})).data
}
export async function saveWechatForwardTypes(forwardTypes: WechatForwardType[]): Promise<BackendWechatChannelStatus> {
  return (await apiClient.post<BackendWechatChannelStatus>('/api/wechat/settings', { forward_types: forwardTypes })).data
}

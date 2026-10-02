import { apiClient } from './client'
import type { ImageHostingConfig, ImageHostingInput, ImageHostingTestResult } from '@/types/imageHosting'

interface ConfigResponse { ok: boolean; image_hosting: ImageHostingConfig; id?: string }
export async function loadImageHosting() {
  return (await apiClient.get<ConfigResponse>('/api/image-hosting')).data.image_hosting
}
export async function saveImageHosting(profile: ImageHostingInput) {
  return (await apiClient.post<ConfigResponse>('/api/image-hosting/save', { profile })).data
}
export async function setImageHostingDefault(id: string) {
  return (await apiClient.post<ConfigResponse>('/api/image-hosting/default', { id })).data.image_hosting
}
export async function deleteImageHosting(id: string) {
  return (await apiClient.post<ConfigResponse>('/api/image-hosting/delete', { id })).data.image_hosting
}
export async function testImageHosting(profile: ImageHostingInput) {
  return (await apiClient.post<{ ok: boolean; result: ImageHostingTestResult }>('/api/image-hosting/test', { profile })).data.result
}

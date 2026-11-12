import { apiClient } from './client'
import type { BackendSystemSettings, BackendWatermarkSettings, BackendWatermarkPreview } from '@/types/workflow.generated'

export async function fetchSystemSettings(): Promise<BackendSystemSettings> {
  return (await apiClient.get<BackendSystemSettings>('/api/system-settings')).data
}

export async function saveSystemSettings(settings: Partial<BackendSystemSettings>): Promise<BackendSystemSettings> {
  return (await apiClient.post<BackendSystemSettings>('/api/system-settings', settings)).data
}

export type WatermarkPreview = BackendWatermarkPreview

export async function previewWatermark(watermark: BackendWatermarkSettings, signal?: AbortSignal): Promise<WatermarkPreview> {
  return (await apiClient.post<WatermarkPreview>('/api/system-settings/watermark-preview', { watermark }, { signal })).data
}

export async function fetchSyncPolicies(): Promise<import('@/types/workflow.generated').BackendSyncPolicyCatalog> {
  return (await apiClient.get<import('@/types/workflow.generated').BackendSyncPolicyCatalog>('/api/business-queries/settings.sync_policies')).data
}

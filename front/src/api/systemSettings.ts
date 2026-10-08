import { apiClient } from './client'
import type { BackendSystemSettings } from '@/types/workflow.generated'

export async function fetchSystemSettings(): Promise<BackendSystemSettings> {
  return (await apiClient.get<BackendSystemSettings>('/api/system-settings')).data
}

export async function saveSystemSettings(settings: BackendSystemSettings): Promise<BackendSystemSettings> {
  return (await apiClient.post<BackendSystemSettings>('/api/system-settings', settings)).data
}

import { apiClient } from './client'

import type { BackendManagedSettings, BackendPublicEnterpriseApplication, BackendTariffSettings, BackendTariffDiff, BackendTariffRulesResponse } from '@/types/workflow.generated'

export type ManagedSettings = BackendManagedSettings
export type ApplicationSettings = BackendPublicEnterpriseApplication
export type TariffSettings = BackendTariffSettings
export type TariffDiff = BackendTariffDiff
export type TariffRules = BackendTariffRulesResponse

export interface TariffImport {
  platform: TariffSettings['platform']
  scope: 'personal' | 'shared'
  filename: string
  content_base64: string
  effective_date?: string
  expected_version: string
  preview: boolean
}
export async function fetchManagedSettings(): Promise<ManagedSettings> {
  return (await apiClient.get('/api/admin/settings')).data
}
export async function saveManagedSettings(settings: Omit<ManagedSettings, 'ok' | 'alibaba' | 'alibaba_crossborder' | 'onebound' | 'crossborderbus_secret_configured' | 'gemini_canvas_url'> & {
  alibaba: Omit<ApplicationSettings, 'secret_configured'> & { app_secret?: string }
  alibaba_crossborder: Omit<ApplicationSettings, 'secret_configured'> & { app_secret?: string }
  onebound?: { api_key: string; api_secret?: string; initial_grant?: number }
  crossborderbus_client_secret?: string
  gemini_canvas_url?: string
}): Promise<ManagedSettings> {
  return (await apiClient.post('/api/admin/settings', settings)).data
}
export async function fetchTariffSettings(): Promise<TariffSettings[]> {
  return (await apiClient.get('/api/shipping/tariffs')).data.items
}
export async function fetchTariffRules(platform: TariffSettings['platform'], scope: 'effective' | 'shared' = 'effective'): Promise<TariffRules> {
  return (await apiClient.get('/api/shipping/tariffs/rules', { params: { platform, scope } })).data
}
export async function importTariffFile(body: TariffImport): Promise<{ items: TariffSettings[]; diff: TariffDiff }> {
  return (await apiClient.post('/api/shipping/tariffs/import', body)).data
}
export async function upgradeTariff(item: TariffSettings): Promise<TariffSettings[]> {
  return (await apiClient.post('/api/shipping/tariffs/upgrade', {
    platform: item.platform, shared_version: item.shared.version, expected_version: item.version,
  })).data.items
}
export function readTariffFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.onerror = () => reject(new Error('无法读取运费表'))
    reader.readAsDataURL(file)
  })
}

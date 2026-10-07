import { apiClient } from '@/api/client'
import type { BackendOzonExportDownload, BackendOzonExportPreview } from '@/types/workflow.generated'

export type OzonExportPreview = BackendOzonExportPreview
export type OzonExportRow = OzonExportPreview['rows'][number]
export interface OzonExportRequest {
  account_id: string
  listing_ids: string[]
  template_name: string
  template_base64: string
  overrides: Record<string, Record<string, string>>
  preview_fingerprint?: string
}
function checked<T extends { ok: boolean }>(data: T & { error?: string }): T {
  if (!data.ok) throw new Error(data.error || 'Ozon 模板导出失败')
  return data
}
export async function previewOzonExport(body: OzonExportRequest): Promise<OzonExportPreview> {
  return checked((await apiClient.post<OzonExportPreview>('/api/online-products/ozon-export/preview', body)).data)
}
export async function generateOzonExport(body: OzonExportRequest): Promise<BackendOzonExportDownload> {
  return checked((await apiClient.post<BackendOzonExportDownload>('/api/online-products/ozon-export/download', body)).data)
}

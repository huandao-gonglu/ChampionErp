import { apiClient } from '@/api/client'
import type { BackendAppStateResponse } from '@/types/workflow.generated'
import type { AppStateResponse } from './normalizers'
import {
  asRecord,
  ensureOk,
  getNumber,
  getString,
  normalizeBackendProduct,
  normalizeMercadoLibreAuthChecklist,
  normalizeMarketplaceOptions,
} from './normalizers'

type WorkflowSettings = Omit<AppStateResponse, 'product' | 'imagePool'>
export function fetchState(): Promise<AppStateResponse>
export function fetchState(settingsOnly: boolean): Promise<WorkflowSettings | AppStateResponse>
export async function fetchState(settingsOnly = false): Promise<WorkflowSettings | AppStateResponse> {
  const response = await apiClient.get<BackendAppStateResponse>(settingsOnly ? '/api/state?view=settings' : '/api/state')
  const data = asRecord(response.data)
  ensureOk(data, '读取状态失败')
  const schemaVersion = getNumber(data, ['schemaVersion'])
  if (schemaVersion !== 1) {
    throw new Error(`不支持的后端状态契约版本：${schemaVersion || '未声明'}`)
  }
  const settings = {
    schemaVersion,
    appConfig: asRecord(data.appConfig),
    storeConfig: asRecord(data.storeConfig),
    storeAuthSummary: asRecord(data.storeAuthSummary),
    mercadolibreAuthChecklist: data.mercadolibreAuthChecklist
      ? normalizeMercadoLibreAuthChecklist(data.mercadolibreAuthChecklist)
      : null,
    outputDir: getString(data, ['outputDir']),
    platformOptions: normalizeMarketplaceOptions(data.platformOptions),
  }
  if (settingsOnly) return settings
  const product = normalizeBackendProduct(data.product, data.imagePool)
  return { ...settings, product, imagePool: product.source.imagePool }
}

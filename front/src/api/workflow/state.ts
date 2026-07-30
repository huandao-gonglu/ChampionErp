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

export async function fetchState(): Promise<AppStateResponse> {
  const response = await apiClient.get<BackendAppStateResponse>('/api/state')
  const data = asRecord(response.data)
  ensureOk(data, '读取状态失败')
  const schemaVersion = getNumber(data, ['schemaVersion'])
  if (schemaVersion !== 1) {
    throw new Error(`不支持的后端状态契约版本：${schemaVersion || '未声明'}`)
  }
  const product = normalizeBackendProduct(data.product, data.imagePool)
  return {
    schemaVersion,
    product,
    imagePool: product.source.imagePool,
    appConfig: asRecord(data.appConfig),
    storeConfig: asRecord(data.storeConfig),
    storeAuthSummary: asRecord(data.storeAuthSummary),
    mercadolibreAuthChecklist: data.mercadolibreAuthChecklist
      ? normalizeMercadoLibreAuthChecklist(data.mercadolibreAuthChecklist)
      : null,
    outputDir: getString(data, ['outputDir']),
    platformOptions: normalizeMarketplaceOptions(data.platformOptions),
  }
}

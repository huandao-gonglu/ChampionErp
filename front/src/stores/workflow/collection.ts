import { ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { createDefaultCollectDiagnostics, createDefaultCollectForm } from '@/constants/initialState'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import type { BrowserDebugStatus, CollectBatchRow, CollectDiagnostics, CollectForm, UnknownRecord } from '@/types/workflow'
import { COLLECT_QUEUE_STORAGE_KEY, restoreCollectQueue } from '@/utils/collectQueue'

export const useWorkflowCollectionStore = defineStore('workflow-collection', () => {
  const collectForm = ref<CollectForm>(createDefaultCollectForm())
  const collectDiagnostics = ref<CollectDiagnostics>(createDefaultCollectDiagnostics())
  const collectBatchRows = ref<CollectBatchRow[]>([])
  const browserDebugStatus = ref<BrowserDebugStatus | null>(null)

  try { collectBatchRows.value = restoreCollectQueue(localStorage.getItem(COLLECT_QUEUE_STORAGE_KEY)) } catch { /* 存储不可用时仍可在当前页面管理列表。 */ }
  watch(collectBatchRows, (rows) => {
    try {
      // 商品内容由后端保存，本地仅保留列表及可见结果，不保存凭据。
      localStorage.setItem(COLLECT_QUEUE_STORAGE_KEY, JSON.stringify(rows.map((row) => ({ ...row, product: undefined }))))
    } catch { /* 浏览器禁用存储或空间不足时不阻塞采集。 */ }
  }, { deep: true, flush: 'sync' })

  function fillFormFromState(nextAppConfig: UnknownRecord, outputDir = '') {
    const product = useWorkflowCatalogStore().product
    const api1688 = nextAppConfig['1688_api'] && typeof nextAppConfig['1688_api'] === 'object' && !Array.isArray(nextAppConfig['1688_api'])
      ? nextAppConfig['1688_api'] as UnknownRecord
      : {}
    collectForm.value.alibabaApiBaseUrl = String(api1688.base_url || collectForm.value.alibabaApiBaseUrl || '')
    collectForm.value.alibabaApiMethod = String(api1688.method || collectForm.value.alibabaApiMethod || '')
    collectForm.value.alibabaApiVersion = String(api1688.api_version || collectForm.value.alibabaApiVersion || '')
    collectForm.value.alibabaApiTimeoutSeconds = String(api1688.timeout_seconds || collectForm.value.alibabaApiTimeoutSeconds || '')
    collectForm.value.autoAiRecognition = String(nextAppConfig.auto_ai_recognition ?? '1') !== '0'
    collectForm.value.outputDir = String(nextAppConfig.collect_output_dir || outputDir || collectForm.value.outputDir || '')
    collectForm.value.productUrl = product.source.sourceUrl || collectForm.value.productUrl
    collectForm.value.platform = product.source.sourcePlatform || collectForm.value.platform || '1688'
    collectForm.value.manualTitle = product.source.title || product.name || ''
    collectForm.value.manualPrice = product.source.price || ''
    collectForm.value.manualDescription = product.source.description || ''
    collectForm.value.manualWeight = product.source.weightKg || ''
    const dims = product.source.dimensions
    collectForm.value.manualDimensions = [dims.lengthCm, dims.widthCm, dims.heightCm].filter(Boolean).join(' x ')
  }

  return {
    collectForm,
    collectDiagnostics,
    collectBatchRows,
    browserDebugStatus,
    fillFormFromState,
  }
})

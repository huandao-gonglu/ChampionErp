import { ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { createDefaultCollectDiagnostics, createDefaultCollectForm } from '@/constants/initialState'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import type { CollectBatchRow, CollectDiagnostics, CollectForm, UnknownRecord } from '@/types/workflow'
import { COLLECT_QUEUE_STORAGE_KEY, restoreCollectQueue } from '@/utils/collectQueue'
import { businessStorageKey } from '@/utils/accountTransport'

export const useWorkflowCollectionStore = defineStore('workflow-collection', () => {
  const storageKey = businessStorageKey(COLLECT_QUEUE_STORAGE_KEY)
  const collectForm = ref<CollectForm>(createDefaultCollectForm())
  const collectDiagnostics = ref<CollectDiagnostics>(createDefaultCollectDiagnostics())
  const collectBatchRows = ref<CollectBatchRow[]>([])

  try { collectBatchRows.value = restoreCollectQueue(localStorage.getItem(storageKey)) } catch { /* 存储不可用时仍可在当前页面管理列表。 */ }
  watch(collectBatchRows, (rows) => {
    try {
      // 商品内容由后端保存，本地仅保留列表及可见结果，不保存凭据。
      localStorage.setItem(storageKey, JSON.stringify(rows.map((row) => ({ ...row, product: undefined }))))
    } catch { /* 浏览器禁用存储或空间不足时不阻塞采集。 */ }
  }, { deep: true, flush: 'sync' })

  function fillFormFromState(nextAppConfig: UnknownRecord, outputDir = '') {
    const product = useWorkflowCatalogStore().product
    collectForm.value.outputDir = String(nextAppConfig.collect_output_dir || outputDir || collectForm.value.outputDir || '')
    collectForm.value.productUrl = product.source.sourceUrl || collectForm.value.productUrl
    collectForm.value.platform = collectForm.value.platform || 'unknown'
  }

  return {
    collectForm,
    collectDiagnostics,
    collectBatchRows,
    fillFormFromState,
  }
})

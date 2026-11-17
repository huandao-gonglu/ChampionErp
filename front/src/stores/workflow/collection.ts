import { ref } from 'vue'
import { defineStore } from 'pinia'
import { createDefaultCollectDiagnostics, createDefaultCollectForm } from '@/constants/initialState'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import type { CollectDiagnostics, CollectForm, UnknownRecord } from '@/types/workflow'
import { COLLECT_QUEUE_STORAGE_KEY, restoreCollectQueue } from '@/utils/collectQueue'
import { listLinks, manageLinks, runLink, verifyLink, type CollectionLink, type LinkTarget } from '@/api/workflow/collectionLinks'
import { businessStorageKey } from '@/utils/accountTransport'

export const useWorkflowCollectionStore = defineStore('workflow-collection', () => {
  const collectForm = ref<CollectForm>(createDefaultCollectForm())
  const collectDiagnostics = ref<CollectDiagnostics>(createDefaultCollectDiagnostics())
  const links = ref<CollectionLink[]>([])
  const running = ref(false)
  const waitingId = ref('')
  const syncError = ref('')
  const runError = ref('')
  const ready = ref(false)
  let sequence = 0
  let migration: Promise<void> | undefined
  let controller: AbortController | undefined

  async function importPreviousQueue() {
    const storageKey = businessStorageKey(COLLECT_QUEUE_STORAGE_KEY)
    let saved: string | null
    try { saved = localStorage.getItem(storageKey) } catch { return }
    if (!saved) return
    const items = restoreCollectQueue(saved)
    if (!items.length) return
    await manageLinks({ action: 'import', items })
    if (localStorage.getItem(storageKey) === saved) localStorage.removeItem(storageKey)
  }

  async function refreshLinks() {
    const current = ++sequence
    try {
      migration ??= importPreviousQueue().catch(error => { migration = undefined; throw error })
      let importError = ''
      try { await migration } catch (error) {
        importError = '旧队列尚未导入：' + (error instanceof Error ? error.message : '请稍后刷新。')
      }
      const items = await listLinks()
      if (current === sequence) {
        links.value = items
        syncError.value = importError
        ready.value = true
      }
      return items
    } catch (error) {
      if (current === sequence) syncError.value = error instanceof Error ? error.message : '同步采集列表失败。'
      throw error
    }
  }

  async function runLinks(targets: LinkTarget[]) {
    if (running.value || !targets?.length) return
    running.value = true
    runError.value = ''
    controller = new AbortController()
    const { signal } = controller
    try {
      const baseline = await refreshLinks()
      for (const target of targets) {
        let row = baseline.find(item => item.id === target.id)
        if (!row || row.revision !== target.revision || row.status === 'running') {
          runError.value = '已跳过在其他页面发生变更的链接。'
          continue
        }
        do {
          if (row.status === 'waiting_verification') {
            waitingId.value = row.id
            let state
            do {
              await new Promise(resolve => setTimeout(resolve, 1500))
              if (signal.aborted) return
              state = await verifyLink(row, signal)
            } while (state === 'waiting_verification' || state === 'loading')
            waitingId.value = ''
            if (state === 'unavailable') break
          }
          if (signal.aborted) return
          row = await runLink(row)
          // 同一版本只投递一次；后续验证使用后台刚刚保存的版本。
          ++sequence
          const index = links.value.findIndex(item => item.id === target.id)
          if (index !== -1 && links.value[index].revision <= row.revision) links.value[index] = row
        } while (row.status === 'waiting_verification')
      }
    } catch (error) {
      if (!signal.aborted) runError.value = error instanceof Error ? error.message : '采集未完成。'
    } finally {
      waitingId.value = ''
      controller = undefined
      running.value = false
      await refreshLinks().catch(() => undefined)
    }
  }

  function cancelWait() { if (waitingId.value) controller?.abort() }

  function fillFormFromState(nextAppConfig: UnknownRecord, outputDir = '') {
    const product = useWorkflowCatalogStore().product
    collectForm.value.outputDir = String(nextAppConfig.collect_output_dir || outputDir || collectForm.value.outputDir || '')
    collectForm.value.productUrl = product.source.sourceUrl || collectForm.value.productUrl
    collectForm.value.platform = collectForm.value.platform || 'unknown'
  }

  return {
    collectForm,
    collectDiagnostics,
    links, ready, running, waitingId, runError, syncError, refreshLinks, runLinks, cancelWait,
    fillFormFromState,
  }
})

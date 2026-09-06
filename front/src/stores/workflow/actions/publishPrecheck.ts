import { enqueuePublish as enqueuePublishApi, previewPublishPayload, publishPrecheck } from '@/api/workflow/publishing'
import { normalizePublishPrecheck } from '@/api/workflow/normalizers'
import { fetchDraftsIndex } from '@/api/workflow/catalog'
import type { PublishPayloadSummary, UnknownRecord } from '@/types/workflow'
import {
  isMercadoLibreCbtTarget, MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE,
  mercadoLibreHasFullyManagedBinding, mercadoLibreListingModel, mercadoLibreListingModelError,
} from '@/utils/mercadolibreGlobalSelling'
import type { WorkflowRuntime } from '../orchestration/runtime'

export type WorkflowPublishPrecheckActionsPort = Pick<WorkflowRuntime,
  | 'currentDraft' | 'currentDraftProductContext' | 'selectedPublishTarget'
  | 'precheck' | 'precheckResults' | 'payloadPreview' | 'publishJob' | 'publishJobStatus'
  | 'selectedPublishJobId' | 'draftsIndex' | 'storeConfig' | 'loading' | 'setError' | 'addLog'
  | 'persistCurrentDraftForPublish' | 'syncActivePublishTarget' | 'applyMutationIndexes'
  | 'pricingTargetKey' | 'currentStage' | 'refreshPublishJobs'
> & { isCurrent?: () => boolean }

function normalizePayloadPreviewSummary(value: UnknownRecord): PublishPayloadSummary | null {
  if (!Object.keys(value).length) return null
  const text = (...keys: string[]) => {
    for (const key of keys) {
      const item = value[key]
      if (item !== undefined && item !== null && String(item).trim()) return String(item)
    }
    return ''
  }
  const imageCount = Number(value.image_count ?? value.imageCount ?? 0)
  return {
    productId: text('product_id', 'productId'),
    draftId: text('draft_id', 'draftId'),
    platform: text('platform'),
    site: text('site'),
    storeIdentity: text('store_identity', 'storeIdentity'),
    storeLabel: text('store_label', 'storeLabel'),
    skuItems: Array.isArray(value.sku_items) ? value.sku_items as UnknownRecord[] : [],
    groupingMode: text('grouping_mode'),
    title: text('title'),
    categoryId: text('category_id', 'categoryId'),
    listingCurrency: text('listing_currency', 'listingCurrency'),
    price: text('price'),
    stock: text('stock'),
    imageCount: Number.isFinite(imageCount) ? imageCount : 0,
  }
}

export function createWorkflowPublishPrecheckActions(runtime: WorkflowPublishPrecheckActionsPort) {
  const {
    currentDraft, currentDraftProductContext, selectedPublishTarget, precheck, precheckResults,
    payloadPreview, publishJob, publishJobStatus, selectedPublishJobId, draftsIndex, storeConfig,
    loading, setError, addLog, persistCurrentDraftForPublish, syncActivePublishTarget,
    applyMutationIndexes, pricingTargetKey, currentStage, refreshPublishJobs,
  } = runtime
  const isCurrent = () => runtime.isCurrent?.() ?? true

  function mercadoLibreCbtPublishBlocked(): boolean {
    if (!isMercadoLibreCbtTarget(selectedPublishTarget.value)) return false
    const listingModel = mercadoLibreListingModel(storeConfig.value)
    if (!listingModel) {
      setError(mercadoLibreListingModelError(storeConfig.value))
      return true
    }
    if (mercadoLibreHasFullyManagedBinding(storeConfig.value)) {
      setError(MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE)
      return true
    }
    return false
  }

  async function runPrecheck(options: { saveDraft?: boolean } = {}) {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择要预检的草稿。')
      return
    }
    if (mercadoLibreCbtPublishBlocked()) return
    loading.value = true
    setError('')
    precheck.value = null
    // 新一轮预检会重新保存和归一化草稿，之前的 Payload 指纹不再代表本轮数据。
    payloadPreview.value = null
    try {
      if (options.saveDraft !== false) await persistCurrentDraftForPublish()
      if (!isCurrent()) return
      const target = selectedPublishTarget.value
      const result = await publishPrecheck(currentDraft.value, target)
      if (!isCurrent()) return
      currentDraft.value = result.draft
      if (result.productContext) currentDraftProductContext.value = result.productContext
      syncActivePublishTarget(target)
      precheck.value = result.precheck
      precheckResults.value = result.platformResults
      applyMutationIndexes(result)
      if (result.precheck.ok) currentStage.value = 7
      addLog(result.precheck.ok ? '预检通过，商品可进入发布队列。' : `预检未通过：${result.precheck.errors.join('、')}`)
      return result.precheck
    } catch (exc) {
      if (isCurrent()) setError(exc instanceof Error ? exc.message : '上架预检失败')
    } finally {
      loading.value = false
    }
  }

  async function previewPayload(options: { saveDraft?: boolean } = {}) {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择要预检的草稿。')
      return
    }
    if (mercadoLibreCbtPublishBlocked()) return
    loading.value = true
    setError('')
    // 先撤销旧确认；如果本次准备或预览失败，不能继续提交上一次的指纹。
    payloadPreview.value = null
    try {
      if (options.saveDraft !== false) await persistCurrentDraftForPublish()
      if (!isCurrent()) return
      const target = selectedPublishTarget.value
      const result = await previewPublishPayload(currentDraft.value, target)
      if (!isCurrent()) return
      if (result.draft) {
        currentDraft.value = result.draft
        // 素材准备可能改变草稿；使用后端对最终 Payload 保存的新预检结果。
        const savedTarget = result.draft.targetSites.find((item) => (
          pricingTargetKey(item.platform, item.site) === pricingTargetKey(target.platform, target.site)
        ))
        const savedPrecheck = savedTarget?.lastPrecheck
        precheck.value = savedPrecheck && Object.keys(savedPrecheck).length
          ? normalizePublishPrecheck(savedPrecheck, {
            requireLayeredScopes: target.platform === 'mercadolibre',
            expectedMarkets: savedTarget?.sitesToSell,
          })
          : null
      }
      if (result.productContext) currentDraftProductContext.value = result.productContext
      syncActivePublishTarget(target)
      applyMutationIndexes(result)
      payloadPreview.value = {
        platform: result.platform,
        site: result.site,
        targetKey: pricingTargetKey(result.platform, result.site),
        status: result.status,
        path: result.path,
        payload: result.payload,
        warning: result.warning,
        validationDigest: result.validationDigest,
        summary: normalizePayloadPreviewSummary(result.summary),
        warnings: result.warnings,
      }
      addLog(`Payload 已生成：${result.path || result.status}`)
      return payloadPreview.value
    } catch (exc) {
      if (isCurrent()) setError(exc instanceof Error ? exc.message : '生成 Payload 失败')
    } finally {
      loading.value = false
    }
  }

  async function enqueuePublish() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择要发布的草稿。')
      return
    }
    if (mercadoLibreCbtPublishBlocked()) return
    const preview = payloadPreview.value
    const target = selectedPublishTarget.value
    if (!preview?.validationDigest || preview.targetKey !== pricingTargetKey(target.platform, target.site)) {
      setError('请先生成当前目标站点的 Payload 预览并确认摘要后，再加入发布队列。')
      return
    }
    loading.value = true
    setError('')
    try {
      // Payload 预览已经保存了草稿并生成确认指纹。确认之后不能再执行写入，
      // 否则保存归一化可能改变发布事实，却仍提交旧 validationDigest。
      publishJobStatus.value = null
      const job = await enqueuePublishApi(currentDraft.value, target, preview.validationDigest)
      if (!isCurrent()) return
      publishJob.value = job
      selectedPublishJobId.value = publishJob.value.jobId
      currentStage.value = 8
      addLog(`发布任务已入队：${job.jobId}`)
      // 队列已接受提交；列表刷新失败不能把成功入队误报为可重试的失败。
      try {
        await refreshPublishJobs({ quiet: true })
        const updatedIndex = await fetchDraftsIndex()
        if (isCurrent()) draftsIndex.value = updatedIndex
      } catch {
        addLog('发布任务已入队，列表暂未刷新，请在发布队列查看。')
      }
      return job
    } catch (exc) {
      // 入队失败后强制重新预览，避免重复提交已经被后端判定失效的确认指纹。
      payloadPreview.value = null
      if (isCurrent()) setError(exc instanceof Error ? exc.message : '发布入队失败')
    } finally {
      loading.value = false
    }
  }

  return { runPrecheck, previewPayload, enqueuePublish }
}

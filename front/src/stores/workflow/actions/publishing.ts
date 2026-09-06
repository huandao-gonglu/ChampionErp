import { createWorkflowCategoryActions } from './category'
import { createWorkflowPublishPrecheckActions } from './publishPrecheck'
import {
  fetchPublishLogs,
  publishProductDirect,
} from '@/api/workflow/publishing'
import { fetchDraftsIndex } from '@/api/workflow/catalog'
import { marketplaces } from '@/constants/initialState'
import { isMercadoLibrePlatform } from '@/utils/draftTargetOptions'
import type {
  Marketplace,
  MarketplaceTargetSite,
} from '@/types/workflow'
import {
  publishJobMatchesProgressContext,
  workflowProgressDraft,
  type WorkflowRuntime,
} from '../orchestration/runtime'

type WorkflowPublishingActionsPort = Pick<
  WorkflowRuntime,
  | 'product'
  | 'draftsIndex'
  | 'currentDraft'
  | 'currentDraftProductContext'
  | 'category'
  | 'categoryQuery'
  | 'categoryResults'
  | 'categoryRecommendations'
  | 'categoryAutoMatching'
  | 'categoryAutoMatchMessage'
  | 'categoryAutoMatchCurrent'
  | 'categoryAutoMatchTotal'
  | 'categoryAutoMatchProductName'
  | 'categoryAttributeTranslations'
  | 'categoryAttributeTranslationsSource'
  | 'categoryAttributeTranslating'
  | 'categoryAttributeLoading'
  | 'categoryAttributeError'
  | 'categoryResultTranslations'
  | 'categoryResultTranslationsSource'
  | 'categoryResultTranslating'
  | 'categoryPrecheck'
  | 'precheck'
  | 'precheckResults'
  | 'payloadPreview'
  | 'publishJob'
  | 'publishJobStatus'
  | 'selectedPublishJobId'
  | 'publishLogs'
  | 'activeMarketplace'
  | 'platformOptions'
  | 'publishResult'
  | 'activePublishTargetKey'
  | 'storeConfig'
  | 'loading'
  | 'addLog'
  | 'setError'
  | 'requestSequence'
  | 'currentStage'
  | 'currentPublishTargets'
  | 'selectedPublishTarget'
  | 'activeMarketplaceSite'
  | 'targetSiteKey'
  | 'applyCategoryRecommendationForTarget'
  | 'setCategoryRecommendation'
  | 'persistActiveTargetListingFields'
  | 'invalidateCategoryAttributeLoad'
  | 'applyTargetListingToDraft'
  | 'pricingTargetKey'
  | 'syncActivePublishTarget'
  | 'applyMutationIndexes'
  | 'restorePrecheckFromProduct'
  | 'restoreCategoryFromProduct'
  | 'persistCurrentDraftForPublish'
  | 'refreshPublishJobs'
>

export function createWorkflowPublishingActions(runtime: WorkflowPublishingActionsPort) {
  const {
    product, draftsIndex, currentDraft, categoryAttributeTranslations,
    categoryAttributeTranslationsSource, categoryResultTranslations, categoryResultTranslationsSource, categoryPrecheck, precheck,
    payloadPreview, publishJob, publishJobStatus,
    publishLogs, activeMarketplace, platformOptions, publishResult, activePublishTargetKey,
    loading, addLog, setError,
    currentPublishTargets, activeMarketplaceSite, persistActiveTargetListingFields, invalidateCategoryAttributeLoad,
    applyTargetListingToDraft, pricingTargetKey, applyMutationIndexes, restorePrecheckFromProduct,
    restoreCategoryFromProduct
  } = runtime

  function selectPublishTarget(target: MarketplaceTargetSite) {
    const targets = currentPublishTargets.value
    const selected = targets.find((item) => pricingTargetKey(item.platform, item.site) === pricingTargetKey(target.platform, target.site))
    if (!selected) {
      setError('这个站点不属于当前草稿的目标市场。')
      return
    }
    persistActiveTargetListingFields(categoryPrecheck.value ? { categoryPrecheck: categoryPrecheck.value.raw || categoryPrecheck.value } : {})
    invalidateCategoryAttributeLoad()
    activePublishTargetKey.value = pricingTargetKey(selected.platform, selected.site)
    activeMarketplace.value = selected.platform
    precheck.value = null
    payloadPreview.value = null
    applyTargetListingToDraft(selected)
  }
  async function publishDirect() {
    if (activeMarketplace.value === 'mercadolibre') {
      setError('Mercado Libre 仅支持通过发布队列提交。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await publishProductDirect(product.value, activeMarketplace.value)
      publishResult.value = result.raw
      if (result.product) product.value = result.product
      applyMutationIndexes(result)
      draftsIndex.value = result.draftsIndex?.length ? result.draftsIndex : await fetchDraftsIndex()
      publishLogs.value = await fetchPublishLogs()
      addLog(`直接发布返回：${result.status || (result.ok ? 'success' : 'failed')} ${result.message || result.error || ''}`)
      if (!result.ok && result.error) setError(result.error)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '直接发布失败')
    } finally {
      loading.value = false
    }
  }

  function setMarketplace(value: Marketplace) {
    if (marketplaces.includes(value)) {
      invalidateCategoryAttributeLoad()
      activeMarketplace.value = value
      setMarketplaceSite(activeMarketplaceSite())
      categoryAttributeTranslations.value = {}
      categoryAttributeTranslationsSource.value = ''
      categoryResultTranslations.value = {}
      categoryResultTranslationsSource.value = ''
      restoreCategoryFromProduct()
      restorePrecheckFromProduct()
      const progressDraft = workflowProgressDraft(product.value, currentDraft.value, value)
      if (
        publishJob.value
        && !publishJobMatchesProgressContext(
          publishJob.value,
          value,
          progressDraft,
          activePublishTargetKey.value,
        )
      ) {
        publishJob.value = null
        publishJobStatus.value = null
      }
    }
  }

  function setMarketplaceSite(site: string) {
    const draft = product.value.drafts[activeMarketplace.value]
    const platform = platformOptions.value.find((option) => option.key === activeMarketplace.value)
    const selected = isMercadoLibrePlatform(activeMarketplace.value)
      ? platform?.sites.find((item) => item.code.toUpperCase() === 'CBT')
      : platform?.sites.find((item) => item.code.toLowerCase() === String(site || '').trim().toLowerCase())
    if (!draft || !selected) return
    draft.site = selected.code
    if (!isMercadoLibrePlatform(activeMarketplace.value)) draft.language = selected.language
  }

  return {
    ...createWorkflowCategoryActions(runtime),
    ...createWorkflowPublishPrecheckActions(runtime),
    publishDirect, setMarketplace, setMarketplaceSite, selectPublishTarget,
  }
}

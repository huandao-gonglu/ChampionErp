import { ref } from 'vue'
import { defineStore } from 'pinia'
import { matchCategories } from '@/api/workflow/publishing'
import { withAiConversation } from '@/services/withAiConversation'
import { draftContextTitle } from '@/utils/draftDisplayTitle'
import { createWorkflowCatalogActions } from './workflow/actions/catalog'
import { createWorkflowCollectionActions } from './workflow/actions/collection'
import { createWorkflowPricingActions } from './workflow/actions/pricing'
import { createWorkflowPublishingActions } from './workflow/actions/publishing'
import { createWorkflowRuntime } from './workflow/orchestration/runtime'
import { createDraftPublishBatch } from './workflow/orchestration/publishBatch'
import { createDraftTargetEditors } from './workflow/orchestration/targetEditors'

export const useWorkflowStore = defineStore('workflow', () => {
  const runtime = createWorkflowRuntime()
  const targetEditors = createDraftTargetEditors(runtime)
  const publishBatch = createDraftPublishBatch(runtime, targetEditors)
  const batchCategoryMatching = ref(false)
  const combined = {
    ...runtime,
    ...createWorkflowCollectionActions(runtime),
    ...createWorkflowCatalogActions(runtime),
    ...createWorkflowPricingActions(runtime),
    ...createWorkflowPublishingActions(runtime),
  }

  /** 一键 1+N 并行匹配当前草稿的全部目标平台类目，并分发到各平台编辑卡片。 */
  async function autoMatchAllTargets() {
    const editors = targetEditors.value
    if (!runtime.currentDraft.value.draftId || !editors.length || batchCategoryMatching.value) return false
    const targets = editors.map((editor) => editor.target)
    batchCategoryMatching.value = true
    runtime.setError('')
    const productName = draftContextTitle(runtime.currentDraftProductContext.value, runtime.currentDraft.value.draftId)
    for (const editor of editors) {
      editor.state.busy = true
      editor.state.categoryAutoMatching = true
      editor.state.categoryAutoMatchProductName = productName
      editor.state.categoryAutoMatchMessage = `正在 1+N 并行匹配 ${targets.length} 个平台类目…`
      editor.state.error = ''
    }
    try {
      await runtime.persistCurrentDraftForPublish()
      const results = await withAiConversation('一键 AI 匹配全部平台类目', id => matchCategories(runtime.currentDraft.value, targets, id))
      let candidateTargetCount = 0
      let completedCount = 0
      editors.forEach((editor, index) => {
        const result = results[index]
        if (!result || !editor.attached()) return
        const errorMessage = result.status !== 'completed'
          ? result.failure?.message || 'AI 尚未确认合适类目，请核对候选。'
          : ''
        editor.state.categoryRecommendations[editor.key] = {
          query: result.query,
          results: result.candidates,
          error: errorMessage,
        }
        editor.state.categoryQuery = result.query
        editor.state.categoryResults = result.candidates
        editor.state.categoryResultTranslations = {}
        editor.state.categoryResultTranslationsSource = ''
        if (result.candidates.length) candidateTargetCount += 1
        if (result.status === 'completed') completedCount += 1
        void editor.actions.localizeCategoryResultsInBackground(result.candidates, editor.target)
      })
      runtime.addLog(`全平台类目匹配完成：${candidateTargetCount}/${targets.length} 个目标返回候选，${completedCount} 个给出已验证首选。`)
      return candidateTargetCount > 0
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '全平台类目匹配失败'
      runtime.setError(message)
      for (const editor of editors) {
        if (editor.attached()) editor.state.error = message
      }
      return false
    } finally {
      for (const editor of editors) {
        editor.state.categoryAutoMatchMessage = ''
        editor.state.categoryAutoMatching = false
        editor.state.busy = false
      }
      batchCategoryMatching.value = false
    }
  }

  async function hydrateTab(key: string) {
    if (key === 'dashboard') {
      await combined.refreshProductsIndex()
      return
    }
    if (key === 'library') {
      await combined.refreshProductsIndex('list')
      return
    }
    if (key === 'drafts') {
      await combined.refreshDraftsIndex()
      return
    }
    if (key === 'auth') await combined.loadAiConfig()
  }

  return {
    targetEditors,
    publishBatch,
    batchCategoryMatching,
    autoMatchAllTargets,
    product: combined.product,
    collectForm: combined.collectForm,
    collectDiagnostics: combined.collectDiagnostics,
    productsIndex: combined.productsIndex,
    draftsIndex: combined.draftsIndex,
    selectedProductIds: combined.selectedProductIds,
    selectedProducts: combined.selectedProducts,
    pricingInput: combined.pricingInput,
    pricingResult: combined.pricingResult,
    category: combined.category,
    categoryQuery: combined.categoryQuery,
    categoryResults: combined.categoryResults,
    categoryAutoMatching: combined.categoryAutoMatching,
    categoryAutoMatchMessage: combined.categoryAutoMatchMessage,
    categoryAutoMatchCurrent: combined.categoryAutoMatchCurrent,
    categoryAutoMatchTotal: combined.categoryAutoMatchTotal,
    categoryAutoMatchProductName: combined.categoryAutoMatchProductName,
    categoryAutoMatchTargetError: combined.categoryAutoMatchTargetError,
    categoryAttributeTranslations: combined.categoryAttributeTranslations,
    categoryAttributeTranslationsSource: combined.categoryAttributeTranslationsSource,
    categoryAttributeTranslating: combined.categoryAttributeTranslating,
    categoryAttributeLoading: combined.categoryAttributeLoading,
    categoryAttributeError: combined.categoryAttributeError,
    categoryResultTranslations: combined.categoryResultTranslations,
    categoryResultTranslationsSource: combined.categoryResultTranslationsSource,
    categoryResultTranslating: combined.categoryResultTranslating,
    categoryPrecheck: combined.categoryPrecheck,
    precheck: combined.precheck,
    precheckResults: combined.precheckResults,
    payloadPreview: combined.payloadPreview,
    copyGenerating: combined.copyGenerating,
    publishJob: combined.publishJob,
    activeMarketplace: combined.activeMarketplace,
    platformOptions: combined.platformOptions,
    logs: combined.logs,
    appConfig: combined.appConfig,
    aiConfig: combined.aiConfig,
    storeConfig: combined.storeConfig,
    storeAuthSummary: combined.storeAuthSummary,
    mercadolibreAuthChecklist: combined.mercadolibreAuthChecklist,
    lastAuthResult: combined.lastAuthResult,
    authLink: combined.authLink,
    publishResult: combined.publishResult,
    imagePrompt: combined.imagePrompt,
    currentStage: combined.currentStage,
    loading: combined.loading,
    error: combined.error,
    currentDraft: combined.currentDraft,
    currentDraftProductContext: combined.currentDraftProductContext,
    activePublishTargetKey: combined.activePublishTargetKey,
    currentPublishTargets: combined.currentPublishTargets,
    selectedPublishTarget: combined.selectedPublishTarget,
    draft: combined.draft,
    imagePool: combined.imagePool,
    selectedImages: combined.selectedImages,
    workflowSteps: combined.workflowSteps,
    progressPercent: combined.progressPercent,
    hydrateTab,
    loadState: combined.loadState,
    resetForm: combined.resetForm,
    collectProduct: combined.collectProduct,
    cancelCollectionVerification: combined.cancelCollectionVerification,
    clearCollectedProduct: combined.clearCollectedProduct,
    saveCollectSettings: combined.saveCollectSettings,
    productsRefreshing: combined.productsRefreshing,
    draftsRefreshing: combined.draftsRefreshing,
    refreshProductsIndex: combined.refreshProductsIndex,
    refreshDraftsIndex: combined.refreshDraftsIndex,
    loadProduct: combined.loadProduct,
    loadDraft: combined.loadDraft,
    acceptLoadedDraft: combined.acceptLoadedDraft,
    loadDraftForPricing: combined.loadDraftForPricing,
    duplicateDraft: combined.duplicateDraft,
    updateDraftTargets: combined.updateDraftTargets,
    updateDraftSitesToSell: combined.updateDraftSitesToSell,
    updateDraftLanguage: combined.updateDraftLanguage,
    deleteDraft: combined.deleteDraft,
    deleteDrafts: combined.deleteDrafts,
    deleteProduct: combined.deleteProduct,
    deleteSelectedProducts: combined.deleteSelectedProducts,
    exportSelectedProducts: combined.exportSelectedProducts,
    importProductsFromJson: combined.importProductsFromJson,
    exportSelectedDrafts: combined.exportSelectedDrafts,
    importDraftsFromZip: combined.importDraftsFromZip,
    toggleProductSelection: combined.toggleProductSelection,
    selectAllProducts: combined.selectAllProducts,
    claimProductsToDrafts: combined.claimProductsToDrafts,
    generateCopyForSelectedProducts: combined.generateCopyForSelectedProducts,
    enqueueSelectedProducts: combined.enqueueSelectedProducts,
    uploadReferenceImages: combined.uploadReferenceImages,
    clearSourceImages: combined.clearSourceImages,
    saveCurrentImagePool: combined.saveCurrentImagePool,
    setMainImage: combined.setMainImage,
    deleteImages: combined.deleteImages,
    editImagesWithPrompt: combined.editImagesWithPrompt,
    saveCurrentProduct: combined.saveCurrentProduct,
    saveCurrentDraft: combined.saveCurrentDraft,
    assignUpc: combined.assignUpc,
    generateCopy: combined.generateCopy,
    generateImagePromptPack: combined.generateImagePromptPack,
    translateImages: combined.translateImages,
    calculatePrice: combined.calculatePrice,
    restorePricingPreview: combined.restorePricingPreview,
    applyPrice: combined.applyPrice,
    searchCategory: combined.searchCategory,
    suggestCategoryByAi: combined.suggestCategoryByAi,
    autoSuggestCategoriesForDraft: combined.autoSuggestCategoriesForDraft,
    selectCategory: combined.selectCategory,
    loadCategoryAttributes: combined.loadCategoryAttributes,
    invalidatePublishValidation: combined.invalidatePublishValidation,
    invalidateCategoryPrecheck: combined.invalidateCategoryPrecheck,
    runCategoryOnlyPrecheck: combined.runCategoryOnlyPrecheck,
    runPrecheck: combined.runPrecheck,
    previewPayload: combined.previewPayload,
    enqueuePublish: combined.enqueuePublish,
    loadAiConfig: combined.loadAiConfig,
    saveAiSettings: combined.saveAiSettings,
    testAiSettings: combined.testAiSettings,
    testPlatformApiConfig: combined.testPlatformApiConfig,
    saveStoreConfig: combined.saveStoreConfig,
    saveStoreCurrency: combined.saveStoreCurrency,
    testAuth: combined.testAuth,
    loadMercadoLibreChecklist: combined.loadMercadoLibreChecklist,
    generateMercadoLibreAuthLink: combined.generateMercadoLibreAuthLink,
    openMercadoLibreAuth: combined.openMercadoLibreAuth,
    refreshMercadoLibreAuthToken: combined.refreshMercadoLibreAuthToken,
    runMercadoLibreAuthTest: combined.runMercadoLibreAuthTest,
    exchangeMlCode: combined.exchangeMlCode,
    clearPlatformAuth: combined.clearPlatformAuth,
    setMarketplace: combined.setMarketplace,
    setMarketplaceSite: combined.setMarketplaceSite,
    selectPublishTarget: combined.selectPublishTarget,
  }
})

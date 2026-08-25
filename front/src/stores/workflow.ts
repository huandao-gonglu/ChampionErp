import { defineStore } from 'pinia'
import { createWorkflowCatalogActions } from './workflow/actions/catalog'
import { createWorkflowCollectionActions } from './workflow/actions/collection'
import { createWorkflowPricingActions } from './workflow/actions/pricing'
import { createWorkflowPublishingActions } from './workflow/actions/publishing'
import { createWorkflowRuntime } from './workflow/orchestration/runtime'

export const useWorkflowStore = defineStore('workflow', () => {
  const runtime = createWorkflowRuntime()
  const combined = {
    ...runtime,
    ...createWorkflowCollectionActions(runtime),
    ...createWorkflowCatalogActions(runtime),
    ...createWorkflowPricingActions(runtime),
    ...createWorkflowPublishingActions(runtime),
  }

  async function hydrateTab(key: string) {
    if (key === 'dashboard') {
      await Promise.all([
        combined.refreshProductsIndex(),
        combined.refreshPublishLogs(),
      ])
      if (combined.mercadolibreAuthChecklist.value?.tokenReady) {
        await Promise.all([
          combined.refreshMercadoLibreOrders(),
          combined.refreshMercadoLibreRemoteItems(),
        ])
      }
      return
    }
    if (key === 'pending' || key === 'library') {
      await combined.refreshProductsIndex()
      return
    }
    if (key === 'drafts') {
      await combined.refreshDraftsIndex()
      return
    }
    if (key === 'publish') {
      await combined.refreshPublishJobs()
      return
    }
    if (key === 'logs') {
      await combined.refreshPublishLogs()
      return
    }
    if (key === 'mlItems') {
      await combined.refreshMercadoLibreRemoteItems()
      return
    }
    if (key === 'auth') await combined.loadAiConfig()
  }

  return {
    product: combined.product,
    collectForm: combined.collectForm,
    collectDiagnostics: combined.collectDiagnostics,
    collectBatchRows: combined.collectBatchRows,
    browserDebugStatus: combined.browserDebugStatus,
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
    publishJobStatus: combined.publishJobStatus,
    publishJobs: combined.publishJobs,
    selectedPublishJobId: combined.selectedPublishJobId,
    publishJobsNextCursor: combined.publishJobsNextCursor,
    publishJobsLoading: combined.publishJobsLoading,
    publishJobsLastUpdated: combined.publishJobsLastUpdated,
    publishLogs: combined.publishLogs,
    mercadoLibreOrders: combined.mercadoLibreOrders,
    mercadoLibreOrderNotifications: combined.mercadoLibreOrderNotifications,
    mercadoLibreOrdersTotal: combined.mercadoLibreOrdersTotal,
    mercadoLibreOrdersCheckedAt: combined.mercadoLibreOrdersCheckedAt,
    mercadoLibreRemoteItems: combined.mercadoLibreRemoteItems,
    mercadoLibreRemoteStatus: combined.mercadoLibreRemoteStatus,
    mercadoLibreRemotePage: combined.mercadoLibreRemotePage,
    mercadoLibreRemotePerPage: combined.mercadoLibreRemotePerPage,
    mercadoLibreRemoteTotal: combined.mercadoLibreRemoteTotal,
    mercadoLibreRemoteTotalPages: combined.mercadoLibreRemoteTotalPages,
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
    collectBatch: combined.collectBatch,
    collectFromBrowserTab: combined.collectFromBrowserTab,
    open1688Browser: combined.open1688Browser,
    checkBrowserDebugStatus: combined.checkBrowserDebugStatus,
    openDebugProfile: combined.openDebugProfile,
    importManual: combined.importManual,
    previewClean1688Text: combined.previewClean1688Text,
    clearCollectedProduct: combined.clearCollectedProduct,
    saveCollectSettings: combined.saveCollectSettings,
    refreshProductsIndex: combined.refreshProductsIndex,
    refreshDraftsIndex: combined.refreshDraftsIndex,
    loadProduct: combined.loadProduct,
    loadDraft: combined.loadDraft,
    loadDraftForPricing: combined.loadDraftForPricing,
    updateDraftTargets: combined.updateDraftTargets,
    updateDraftSitesToSell: combined.updateDraftSitesToSell,
    updateDraftLanguage: combined.updateDraftLanguage,
    deleteDraft: combined.deleteDraft,
    deleteDrafts: combined.deleteDrafts,
    deleteProduct: combined.deleteProduct,
    deleteSelectedProducts: combined.deleteSelectedProducts,
    toggleProductSelection: combined.toggleProductSelection,
    selectAllProducts: combined.selectAllProducts,
    setClaimPlatforms: combined.setClaimPlatforms,
    claimSelectedProducts: combined.claimSelectedProducts,
    claimCurrentProduct: combined.claimCurrentProduct,
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
    applyPrice: combined.applyPrice,
    searchCategory: combined.searchCategory,
    suggestCategoryByAi: combined.suggestCategoryByAi,
    autoSuggestCategoriesForDraft: combined.autoSuggestCategoriesForDraft,
    selectCategory: combined.selectCategory,
    loadCategoryAttributes: combined.loadCategoryAttributes,
    translateCategoryAttributes: combined.translateCategoryAttributes,
    translateCategoryResults: combined.translateCategoryResults,
    fillAttributesByAi: combined.fillAttributesByAi,
    invalidateCategoryPrecheck: combined.invalidateCategoryPrecheck,
    runCategoryOnlyPrecheck: combined.runCategoryOnlyPrecheck,
    runPrecheck: combined.runPrecheck,
    previewPayload: combined.previewPayload,
    enqueuePublish: combined.enqueuePublish,
    publishDirect: combined.publishDirect,
    confirmRealPublish: combined.confirmRealPublish,
    refreshPublishJob: combined.refreshPublishJob,
    refreshPublishJobs: combined.refreshPublishJobs,
    loadMorePublishJobs: combined.loadMorePublishJobs,
    selectPublishJob: combined.selectPublishJob,
    refreshPublishLogs: combined.refreshPublishLogs,
    refreshMercadoLibreOrders: combined.refreshMercadoLibreOrders,
    refreshMercadoLibreRemoteItems: combined.refreshMercadoLibreRemoteItems,
    closeMercadoLibreRemoteItem: combined.closeMercadoLibreRemoteItem,
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

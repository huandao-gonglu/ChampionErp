import { computed, reactive, toRefs, watch } from 'vue'
import { createWorkflowCategoryActions, type WorkflowCategoryActionsPort } from '../actions/category'
import { createWorkflowPublishPrecheckActions, type WorkflowPublishPrecheckActionsPort } from '../actions/publishPrecheck'
import { normalizePublishPrecheck } from '@/api/workflow/normalizers'
import type { WorkflowRuntime } from './runtime'
import type { CategoryAttributeTranslations, CategoryPrecheckResult, CategoryResultTranslations, CategorySearchResult, CategorySelection, DraftDetail, DraftPublishOperation, MarketplaceTargetSite, PayloadPreviewState, PublishPrecheck, UnknownRecord } from '@/types/workflow'

const listingDefaults = {
  categoryId: '', descriptionCategoryId: '', categoryPath: '', attributes: {},
  validationErrors: [], publishStatus: '', status: 'pending', lastPrecheck: {}, lastPrecheckTarget: {},
} satisfies Partial<DraftDetail>

/** 每个目标持有独立的界面状态；草稿字段直接投影到该目标，保存仍经过统一草稿入口。 */
export function createDraftTargetEditors(runtime: WorkflowRuntime) {
  const editors = new Map<string, ReturnType<typeof createEditor>>()

  function createEditor(identity: MarketplaceTargetSite) {
    let disposed = false
    const draftId = runtime.currentDraft.value.draftId
    const key = runtime.targetSiteKey(identity)
    const state = reactive({
      category: null as CategorySelection | null,
      categoryQuery: '', categoryResults: [] as CategorySearchResult[],
      categoryRecommendations: {} as Record<string, { query: string; results: CategorySearchResult[]; error: string }>,
      categoryAutoMatching: false, categoryAutoMatchMessage: '', categoryAutoMatchCurrent: 0,
      categoryAutoMatchTotal: 0, categoryAutoMatchProductName: '',
      categoryAttributeTranslations: {} as CategoryAttributeTranslations,
      categoryAttributeTranslationsSource: '', categoryAttributeTranslating: false,
      categoryAttributeLoading: false, categoryAttributeError: '',
      categoryResultTranslations: {} as CategoryResultTranslations,
      categoryResultTranslationsSource: '', categoryResultTranslating: false,
      categoryPrecheck: null as CategoryPrecheckResult | null,
      precheck: null as PublishPrecheck | null, precheckResults: {} as UnknownRecord,
      payloadPreview: null as PayloadPreviewState | null,
      publishFailure: null as { operation: DraftPublishOperation; message: string } | null,
      queuedPublishJobId: '',
      loading: false, error: '', busy: false,
    })
    const requestSequence = { ...runtime.requestSequence }
    const attached = () => !disposed && runtime.currentDraft.value.draftId === draftId
      && runtime.currentDraft.value.targetSites.some((item) => runtime.targetSiteKey(item) === key)
    function targetRecord() {
      if (!attached()) throw new Error('草稿或目标市场已改变，请在当前草稿中重新操作。')
      return runtime.currentDraft.value.targetSites.find((item) => runtime.targetSiteKey(item) === key)!
    }
    const target = computed(() => targetRecord())
    const isPublishTarget = () => runtime.targetSiteKey(runtime.selectedPublishTarget.value) === key

    function syncPublishProjection() {
      if (attached() && isPublishTarget()) {
        const record = targetRecord()
        Object.assign(runtime.currentDraft.value, Object.fromEntries(
          Object.entries(listingDefaults).map(([field, fallback]) => [field, record[field as keyof MarketplaceTargetSite] ?? structuredClone(fallback)]),
        ))
        runtime.categoryPrecheck.value = state.categoryPrecheck
        runtime.precheck.value = state.precheck
        runtime.payloadPreview.value = state.payloadPreview
      }
    }

    // 只投影明确列出的平台字段；标题、品牌、包装尺寸等仍引用同一份共享草稿。
    const draft = computed<DraftDetail>({
      get: () => new Proxy(runtime.currentDraft.value, {
        get(root, field, receiver) {
          const record = targetRecord()
          if (field === 'platform' || field === 'site') return record[field]
          if (Object.hasOwn(listingDefaults, field)) {
            const name = field as keyof typeof listingDefaults
            if (record[name] === undefined) Object.assign(record, { [name]: structuredClone(listingDefaults[name]) })
            return record[name]
          }
          return Reflect.get(root, field, receiver)
        },
        set(root, field, value) {
          if (Object.hasOwn(listingDefaults, field)) {
            Object.assign(targetRecord(), { [field]: value })
            if (isPublishTarget()) Reflect.set(root, field, value)
            return true
          }
          return Reflect.set(root, field, value)
        },
      }),
      set: (value) => {
        if (!attached() || value.draftId !== draftId) return
        const selected = runtime.selectedPublishTarget.value
        runtime.currentDraft.value = value
        runtime.syncActivePublishTarget(selected)
      },
    })
    state.categoryPrecheck = runtime.categoryPrecheckFromTarget(target.value.categoryPrecheck)
    if (target.value.categoryId) {
      state.category = {
        platform: identity.platform, categoryId: target.value.categoryId,
        categoryPath: target.value.categoryPath || '', requiredAttributes: [], optionalAttributes: [],
      }
    }
    const port: WorkflowCategoryActionsPort & WorkflowPublishPrecheckActionsPort = {
      ...runtime,
      ...toRefs(state),
      isCurrent: attached,
      currentDraft: draft,
      currentDraftProductContext: computed({
        get: () => runtime.currentDraftProductContext.value,
        set: (value) => { if (attached()) runtime.currentDraftProductContext.value = value },
      }),
      currentPublishTargets: computed(() => [target.value]),
      selectedPublishTarget: target,
      requestSequence,
      setError: (message) => { state.error = message; if (message && attached()) runtime.setError(message) },
      applyCategoryRecommendationForTarget: () => {
        const recommendation = state.categoryRecommendations[key]
        state.categoryQuery = recommendation?.query || ''
        state.categoryResults = recommendation?.results || []
      },
      setCategoryRecommendation: (_, query, results, error = '') => {
        state.categoryRecommendations[key] = { query, results, error }
        state.categoryQuery = query
        state.categoryResults = results
      },
      invalidateCategoryAttributeLoad: () => {
        requestSequence.categoryAttributeLoad += 1
        state.categoryAttributeLoading = false
        state.categoryAttributeError = ''
      },
      persistActiveTargetListingFields: (extra = {}) => {
        Object.assign(targetRecord(), extra)
        syncPublishProjection()
      },
      syncActivePublishTarget: () => syncPublishProjection(),
      persistCurrentDraftForPublish: async () => {
        targetRecord()
        syncPublishProjection()
        await runtime.persistCurrentDraftForPublish()
        return draft.value
      },
    }
    const actions = {
      ...createWorkflowCategoryActions(port),
      ...createWorkflowPublishPrecheckActions(port),
    }
    const savedPrecheck = target.value.lastPrecheck
    if (savedPrecheck && Object.keys(savedPrecheck).length) {
      state.precheck = normalizePublishPrecheck(savedPrecheck, {
        requireLayeredScopes: target.value.platform === 'mercadolibre',
        expectedMarkets: target.value.sitesToSell,
      })
    }
    // 结果只覆盖检查时的数据。平台资料变更清除本区结果，共享资料变更会清除各区结果。
    const stopWatching = watch(() => {
      if (!attached()) return ''
      const value = draft.value
      return JSON.stringify({
        title: value.title, globalTitle: value.globalTitle, description: value.description,
        brand: value.brand, model: value.model, bullets: value.bullets, language: value.language,
        skuItems: value.skuItems, grouping: value.grouping, images: value.images, pricing: value.pricing,
        sku: value.sku, stock: value.stock, upc: value.upc, packageDimensions: value.packageDimensions,
        saleTerms: value.saleTerms, allowGtinExemption: value.allowGtinExemption,
        categoryId: value.categoryId, descriptionCategoryId: value.descriptionCategoryId,
        categoryPath: value.categoryPath, attributes: value.attributes,
        listingCurrency: target.value.listingCurrency, currencyFingerprint: target.value.currencyFingerprint,
        sitesToSell: target.value.sitesToSell,
      })
    }, () => {
      state.precheck = null
      state.payloadPreview = null
    }, { flush: 'sync' })
    async function execute<T>(operation: () => T | Promise<T>) {
      if (!attached()) return
      state.busy = true
      try {
        return await operation()
      } catch (error) {
        if (attached()) port.setError(error instanceof Error ? error.message : '目标市场操作失败。')
      } finally {
        syncPublishProjection()
        state.busy = false
      }
    }
    async function run<T>(operation: () => T | Promise<T>) {
      if (runtime.loading.value || !attached()) return
      runtime.loading.value = true
      try {
        return await execute(operation)
      } finally {
        runtime.loading.value = false
      }
    }
    return reactive({ key, draft, target, state, actions, run, execute, attached, dispose: () => { disposed = true; stopWatching() } })
  }

  watch(() => runtime.currentDraft.value.draftId, () => {
    editors.forEach((editor) => editor.dispose())
    editors.clear()
  }, { flush: 'sync' })
  return computed(() => {
    const targets = runtime.currentPublishTargets.value
    const keys = new Set(targets.map(runtime.targetSiteKey))
    for (const [key, editor] of editors) {
      if (!keys.has(key)) { editor.dispose(); editors.delete(key) }
    }
    return targets.map((target) => {
      const key = runtime.targetSiteKey(target)
      if (!editors.has(key)) editors.set(key, createEditor(target))
      return editors.get(key)!
    })
  })
}

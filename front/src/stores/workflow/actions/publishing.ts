import {
  confirmMercadoLibreRealPublish,
  enqueuePublish as enqueuePublishApi,
  fetchCategoryAttrs,
  fetchPublishLogs,
  fillCategoryAttributes,
  matchCategory,
  previewPublishPayload,
  publishProductDirect,
  publishPrecheck,
  runCategoryPrecheck,
  searchCategories,
} from '@/api/workflow/publishing'
import { translateText, type TextTranslationMap } from '@/api/workflow/translation'
import { fetchDraftsIndex } from '@/api/workflow/catalog'
import { marketplaces } from '@/constants/initialState'
import type {
  CategoryAttributeTranslations,
  CategoryResultTranslations,
  CategorySearchResult,
  CategorySelection,
  Marketplace,
  MarketplaceTargetSite,
  UnknownRecord,
} from '@/types/workflow'
import {
  categoryAttributeSchemaFromSelection,
  publishJobMatchesProgressContext,
  workflowProgressDraft,
  type WorkflowRuntime,
} from '../orchestration/runtime'

type AttributeTranslationSlot = {
  attributeId: string
  field: 'label' | 'help' | 'option'
  option?: string
}

function categoryResultTranslationContent(results: CategorySearchResult[]) {
  const content: TextTranslationMap = {}
  const categoryIdsByKey = new Map<string, string>()
  results.forEach((item, index) => {
    const text = String(item.path || item.name || '').trim()
    if (!item.id || !text) return
    const key = `category.${index}.path`
    content[key] = text
    categoryIdsByKey.set(key, item.id)
  })
  return { content, categoryIdsByKey }
}

function categoryAttributeTranslationContent(selection: CategorySelection) {
  const content: TextTranslationMap = {}
  const slots = new Map<string, AttributeTranslationSlot>()
  const attributes = [...selection.requiredAttributes, ...selection.optionalAttributes]
  attributes.forEach((attribute, attributeIndex) => {
    const label = String(attribute.name || '').trim()
    if (label) {
      const key = `attribute.${attributeIndex}.label`
      content[key] = label
      slots.set(key, { attributeId: attribute.id, field: 'label' })
    }
    const description = String(attribute.description || '').trim()
    if (description) {
      const key = `attribute.${attributeIndex}.description`
      content[key] = description
      slots.set(key, { attributeId: attribute.id, field: 'help' })
    }
    for (const [optionIndex, option] of (attribute.options || []).entries()) {
      const text = String(option || '').trim()
      if (!text) continue
      const key = `attribute.${attributeIndex}.option.${optionIndex}`
      content[key] = text
      slots.set(key, { attributeId: attribute.id, field: 'option', option: text })
    }
  })
  return { content, slots }
}

function attributeTranslationsFromText(
  selection: CategorySelection,
  translated: TextTranslationMap,
  slots: Map<string, AttributeTranslationSlot>,
): CategoryAttributeTranslations {
  const translations: CategoryAttributeTranslations = Object.fromEntries(
    [...selection.requiredAttributes, ...selection.optionalAttributes].map((attribute) => [
      attribute.id,
      { label: '', help: '', values: {} },
    ]),
  )
  for (const [key, text] of Object.entries(translated)) {
    const slot = slots.get(key)
    if (!slot || !translations[slot.attributeId]) continue
    if (slot.field === 'option' && slot.option) {
      translations[slot.attributeId].values[slot.option] = text
    } else if (slot.field === 'label') {
      translations[slot.attributeId].label = text
    } else {
      translations[slot.attributeId].help = text
    }
  }
  return translations
}

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
  | 'publishLogs'
  | 'activeMarketplace'
  | 'platformOptions'
  | 'publishResult'
  | 'activePublishTargetKey'
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
>

export function createWorkflowPublishingActions(runtime: WorkflowPublishingActionsPort) {
  const {
    product, draftsIndex, currentDraft, currentDraftProductContext, category,
    categoryQuery, categoryResults, categoryRecommendations, categoryAutoMatching, categoryAutoMatchMessage,
    categoryAutoMatchCurrent, categoryAutoMatchTotal, categoryAutoMatchProductName, categoryAttributeTranslations,
    categoryAttributeTranslationsSource, categoryAttributeTranslating, categoryAttributeLoading, categoryAttributeError, categoryResultTranslations,
    categoryResultTranslationsSource, categoryResultTranslating, categoryPrecheck, precheck, precheckResults,
    payloadPreview, publishJob, publishJobStatus, publishLogs, activeMarketplace, platformOptions,
    publishResult, activePublishTargetKey, loading, addLog, setError,
    requestSequence, currentStage, currentPublishTargets, selectedPublishTarget, activeMarketplaceSite,
    targetSiteKey, applyCategoryRecommendationForTarget, setCategoryRecommendation, persistActiveTargetListingFields, invalidateCategoryAttributeLoad,
    applyTargetListingToDraft, pricingTargetKey, syncActivePublishTarget, applyMutationIndexes, restorePrecheckFromProduct,
    restoreCategoryFromProduct, persistCurrentDraftForPublish,
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
  async function searchCategory() {
    if (!categoryQuery.value.trim()) {
      setError('请输入类目搜索关键词。')
      return
    }
    const target = selectedPublishTarget.value
    if (!currentDraft.value.draftId || !target.platform || !target.site) {
      setError('请先从草稿箱选择要预检的草稿目标。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await searchCategories(target.platform, categoryQuery.value, target.site)
      setCategoryRecommendation(target, categoryQuery.value, result.results)
      categoryResultTranslations.value = {}
      categoryResultTranslationsSource.value = ''
      addLog(`类目搜索完成：${result.results.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '类目搜索失败')
    } finally {
      loading.value = false
    }
  }

  async function suggestCategoryByAi() {
    await autoSuggestCategoriesForDraft()
  }

  async function autoMatchCategories(targets: MarketplaceTargetSite[]) {
    categoryAutoMatchProductName.value = currentDraftProductContext.value.title || currentDraft.value.title
    const recommendations: Record<string, { query: string; results: CategorySearchResult[]; error: string }> = {}
    let candidateTargetCount = 0
    let completedCount = 0
    for (const [index, target] of targets.entries()) {
      categoryAutoMatchMessage.value = `正在为 ${target.platform.toUpperCase()} ${target.site} 运行类目能力（${index + 1}/${targets.length}）…`
      try {
        const result = await matchCategory(currentDraft.value, target)
        recommendations[targetSiteKey(target)] = {
          query: result.query,
          results: result.candidates,
          error: result.status === 'failed' ? result.failure?.message || '类目匹配失败' : '',
        }
        if (result.candidates.length) candidateTargetCount += 1
        if (result.status === 'completed') completedCount += 1
      } catch (exc) {
        recommendations[targetSiteKey(target)] = {
          query: '',
          results: [],
          error: exc instanceof Error ? exc.message : '类目匹配失败',
        }
      }
      categoryAutoMatchCurrent.value = index + 1
    }
    categoryRecommendations.value = recommendations
    applyCategoryRecommendationForTarget(selectedPublishTarget.value)
    categoryResultTranslations.value = {}
    categoryResultTranslationsSource.value = ''
    addLog(`类目匹配已完成：${candidateTargetCount}/${targets.length} 个目标站点返回候选，${completedCount} 个给出已验证首选；仍需人工点击候选确认。`)
    if (!candidateTargetCount) setError('没有找到可用类目候选，请调整商品信息或手动搜索。')
    return candidateTargetCount > 0
  }

  function clearCurrentCategoryDependentFields() {
    currentDraft.value.descriptionCategoryId = ''
    currentDraft.value.attributes = {}
    currentDraft.value.validationErrors = []
    currentDraft.value.lastPrecheck = {}
    currentDraft.value.lastPrecheckTarget = {}
    currentDraft.value.publishStatus = ''
    currentDraft.value.status = 'category_ready'
    category.value = null
    categoryAttributeError.value = ''
    categoryPrecheck.value = null
    precheck.value = null
    precheckResults.value = {}
    payloadPreview.value = null
    persistActiveTargetListingFields({
      descriptionCategoryId: '',
      categoryPrecheck: {},
      lastPrecheck: {},
      lastPrecheckTarget: {},
    })
  }

  function invalidateCategoryPrecheck() {
    const categoryChanged = String(selectedPublishTarget.value.categoryId || '').trim()
      !== currentDraft.value.categoryId.trim()
    if (categoryChanged) {
      currentDraft.value.descriptionCategoryId = ''
    }
    categoryPrecheck.value = null
    precheck.value = null
    precheckResults.value = {}
    payloadPreview.value = null
    currentDraft.value.lastPrecheck = {}
    currentDraft.value.lastPrecheckTarget = {}
    persistActiveTargetListingFields({
      ...(categoryChanged ? { descriptionCategoryId: '' } : {}),
      categoryPrecheck: {},
      lastPrecheck: {},
      lastPrecheckTarget: {},
    })
  }

  async function autoSuggestCategoriesForDraft() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择要匹配类目的草稿。')
      return false
    }
    const initialTargets = currentPublishTargets.value
    if (!initialTargets.length) {
      setError('当前草稿没有可匹配类目的目标站点。')
      return false
    }
    categoryAutoMatching.value = true
    categoryAutoMatchMessage.value = '正在运行受控类目匹配能力…'
    categoryAutoMatchCurrent.value = 0
    categoryAutoMatchTotal.value = initialTargets.length
    categoryAutoMatchProductName.value = ''
    categoryRecommendations.value = {}
    loading.value = true
    setError('')
    try {
      await persistCurrentDraftForPublish()
      return await autoMatchCategories(currentPublishTargets.value)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '匹配类目失败')
      return false
    } finally {
      categoryAutoMatchMessage.value = ''
      categoryAutoMatching.value = false
      loading.value = false
    }
  }

  async function selectCategory(item: CategorySearchResult) {
    const target = { ...selectedPublishTarget.value }
    const previousCategoryId = String(target.categoryId || '').trim()
    const categoryId = String(item.raw.type_id || item.id).trim()
    if (!categoryId) {
      setError('所选类目缺少类目 ID。')
      return
    }
    const categoryChanged = previousCategoryId !== categoryId
    invalidateCategoryAttributeLoad()
    categoryAttributeLoading.value = true
    currentDraft.value.categoryId = categoryId
    currentDraft.value.categoryPath = item.path || item.name
    if (categoryChanged) {
      clearCurrentCategoryDependentFields()
    }
    currentDraft.value.descriptionCategoryId = target.platform === 'ozon'
      ? String(item.raw.description_category_id || '')
      : ''
    categoryAttributeTranslations.value = {}
    categoryAttributeTranslationsSource.value = ''
    if (categoryChanged) {
      persistActiveTargetListingFields({
        descriptionCategoryId: currentDraft.value.descriptionCategoryId,
        categoryAttributeSchema: null,
      })
    }
    loading.value = true
    setError('')
    try {
      await persistCurrentDraftForPublish()
      addLog(`类目已保存：${categoryId}`)
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '保存类目失败'
      categoryAttributeError.value = message
      categoryAttributeLoading.value = false
      setError(message)
      return
    } finally {
      loading.value = false
    }
    await loadCategoryAttributes(item.raw)
  }

  async function loadCategoryAttributes(categoryRecord?: UnknownRecord) {
    const target = { ...selectedPublishTarget.value }
    const categoryId = currentDraft.value.categoryId.trim()
    if (!categoryId) {
      const message = '请先填写或选择类目 ID。'
      categoryAttributeLoading.value = false
      categoryAttributeError.value = message
      setError(message)
      return
    }
    if (!target.platform) {
      const message = '当前草稿没有可读取类目属性的目标站点。'
      categoryAttributeLoading.value = false
      categoryAttributeError.value = message
      setError(message)
      return
    }
    const requestId = ++requestSequence.categoryAttributeLoad
    const requestTargetKey = targetSiteKey(target)
    const requestIsCurrent = () => (
      requestId === requestSequence.categoryAttributeLoad
      && requestTargetKey === targetSiteKey(selectedPublishTarget.value)
      && categoryId === currentDraft.value.categoryId.trim()
    )
    categoryAttributeLoading.value = true
    categoryAttributeError.value = ''
    loading.value = true
    setError('')
    try {
      const matchingLoadedRecord = category.value?.categoryId === categoryId && category.value.platform === target.platform
        ? category.value.raw
        : undefined
      const loadedCategory = await fetchCategoryAttrs(target.platform, categoryId, target.site, categoryRecord || matchingLoadedRecord)
      if (!requestIsCurrent()) return
      if (String(target.categoryId || '').trim() !== categoryId) {
        clearCurrentCategoryDependentFields()
      }
      if (loadedCategory.categoryPath) {
        currentDraft.value.categoryPath = loadedCategory.categoryPath
      }
      currentDraft.value.descriptionCategoryId = target.platform === 'ozon'
        ? String(loadedCategory.raw?.description_category_id || '')
        : ''
      if (target.platform === 'ozon' && !currentDraft.value.descriptionCategoryId) {
        throw new Error('Ozon 实时类目缺少 description_category_id')
      }
      category.value = loadedCategory
      categoryAttributeError.value = ''
      persistActiveTargetListingFields({
        descriptionCategoryId: currentDraft.value.descriptionCategoryId,
        categoryAttributeSchema: categoryAttributeSchemaFromSelection(loadedCategory, target),
      })
      await persistCurrentDraftForPublish()
      categoryAttributeTranslations.value = {}
      categoryAttributeTranslationsSource.value = ''
      currentStage.value = 6
      addLog(`已读取并保存类目属性：${categoryId}`)
    } catch (exc) {
      if (!requestIsCurrent()) return
      const message = exc instanceof Error ? exc.message : '读取或保存类目属性失败'
      categoryAttributeError.value = message
      setError(message)
    } finally {
      if (requestId === requestSequence.categoryAttributeLoad) {
        categoryAttributeLoading.value = false
        loading.value = false
      }
    }
  }

  async function translateCategoryAttributes() {
    const categoryId = currentDraft.value.categoryId.trim()
    if (!categoryId) {
      setError('请先选择或填写类目 ID。')
      return
    }
    const categoryForTranslation = category.value
    if (!categoryForTranslation || categoryForTranslation.categoryId !== categoryId) {
      setError('请先加载平台属性定义，再翻译属性文本。')
      return
    }
    const requestId = ++requestSequence.categoryAttributeTranslation
    loading.value = true
    categoryAttributeTranslating.value = true
    setError('')
    try {
      const { content, slots } = categoryAttributeTranslationContent(categoryForTranslation)
      const translated = await translateText('zh-CN', content)
      if (requestId !== requestSequence.categoryAttributeTranslation) return
      categoryAttributeTranslations.value = attributeTranslationsFromText(categoryForTranslation, translated, slots)
      categoryAttributeTranslationsSource.value = 'ai'
      addLog(`属性翻译已加载：${Object.keys(translated).length} 段文本（AI）。`)
    } catch (exc) {
      if (requestId === requestSequence.categoryAttributeTranslation) setError(exc instanceof Error ? exc.message : '翻译类目属性失败')
    } finally {
      if (requestId === requestSequence.categoryAttributeTranslation) {
        categoryAttributeTranslating.value = false
        loading.value = false
      }
    }
  }

  async function translateCategoryResults() {
    if (!categoryResults.value.length) return
    const requestId = ++requestSequence.categoryResultTranslation
    const results = categoryResults.value
    categoryResultTranslating.value = true
    try {
      const { content, categoryIdsByKey } = categoryResultTranslationContent(results)
      const translated = await translateText('zh-CN', content)
      if (requestId !== requestSequence.categoryResultTranslation) return
      categoryResultTranslations.value = Object.fromEntries(
        Object.entries(translated)
          .map(([key, text]) => [categoryIdsByKey.get(key) || '', text])
          .filter(([categoryKey]) => categoryKey),
      ) as CategoryResultTranslations
      categoryResultTranslationsSource.value = 'ai'
      addLog(`候选类目翻译已加载：${Object.keys(translated).length} 项（AI）。`)
    } catch (exc) {
      if (requestId === requestSequence.categoryResultTranslation) setError(exc instanceof Error ? exc.message : '翻译候选类目失败')
    } finally {
      if (requestId === requestSequence.categoryResultTranslation) categoryResultTranslating.value = false
    }
  }

  async function fillAttributesByAi() {
    const categoryId = currentDraft.value.categoryId.trim()
    if (!categoryId) {
      setError('请先选择类目。')
      return
    }
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择一个草稿再填充属性。')
      return
    }
    loading.value = true
    setError('')
    try {
      await persistCurrentDraftForPublish()
      const target = selectedPublishTarget.value
      if (!category.value || category.value.categoryId !== categoryId || category.value.platform !== target.platform) {
        category.value = await fetchCategoryAttrs(target.platform, categoryId, target.site)
      }
      const before = { ...currentDraft.value.attributes }
      const result = await fillCategoryAttributes(currentDraft.value, target, categoryId, category.value)
      currentDraft.value = result.draft
      currentDraftProductContext.value = result.productContext
      syncActivePublishTarget(target)
      applyMutationIndexes(result)
      const after = currentDraft.value.attributes
      const filledCount = Object.keys(after).filter((key) => String(after[key] || '').trim() && String(before[key] || '').trim() !== String(after[key] || '').trim()).length
      const source = result.raw?.fill_source === 'ai_model' ? 'AI 模型' : '规则'
      addLog(`属性已填充：${source} 新增/更新 ${filledCount} 项，需要复核 ${result.needReview.length} 项。`)
      if (result.warning) addLog(result.warning)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'AI 填充属性失败')
    } finally {
      loading.value = false
    }
  }

  async function runCategoryOnlyPrecheck() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择要预检的草稿。')
      return
    }
    const categoryId = currentDraft.value.categoryId.trim()
    if (!categoryId) {
      setError('请先选择或填写类目 ID。')
      return
    }
    loading.value = true
    setError('')
    try {
      await persistCurrentDraftForPublish()
      categoryPrecheck.value = await runCategoryPrecheck(currentDraft.value, selectedPublishTarget.value, categoryId)
      persistActiveTargetListingFields({ categoryPrecheck: categoryPrecheck.value.raw || categoryPrecheck.value })
      addLog(categoryPrecheck.value.ok ? '类目预检通过。' : `类目预检发现缺项：${categoryPrecheck.value.missingFields.join('、') || categoryPrecheck.value.errors.join('、')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '类目预检失败')
    } finally {
      loading.value = false
    }
  }

  async function runPrecheck() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择要预检的草稿。')
      return
    }
    loading.value = true
    setError('')
    try {
      await persistCurrentDraftForPublish()
      const target = selectedPublishTarget.value
      const result = await publishPrecheck(currentDraft.value, target)
      currentDraft.value = result.draft
      if (result.productContext) currentDraftProductContext.value = result.productContext
      syncActivePublishTarget(target)
      precheck.value = result.precheck
      precheckResults.value = result.platformResults
      applyMutationIndexes(result)
      if (result.precheck.ok) currentStage.value = 7
      addLog(result.precheck.ok ? '预检通过，商品可进入发布队列。' : `预检未通过：${result.precheck.errors.join('、')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '上架预检失败')
    } finally {
      loading.value = false
    }
  }

  async function previewPayload() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择要预检的草稿。')
      return
    }
    loading.value = true
    setError('')
    try {
      await persistCurrentDraftForPublish()
      const target = selectedPublishTarget.value
      const result = await previewPublishPayload(currentDraft.value, target)
      if (result.draft) currentDraft.value = result.draft
      if (result.productContext) currentDraftProductContext.value = result.productContext
      syncActivePublishTarget(target)
      applyMutationIndexes(result)
      payloadPreview.value = result.payload
      addLog(`Payload 已生成：${result.path || result.status}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '生成 Payload 失败')
    } finally {
      loading.value = false
    }
  }

  async function enqueuePublish() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择要发布的草稿。')
      return
    }
    loading.value = true
    setError('')
    try {
      await persistCurrentDraftForPublish()
      publishJob.value = await enqueuePublishApi(currentDraft.value, selectedPublishTarget.value)
      draftsIndex.value = await fetchDraftsIndex()
      currentStage.value = 8
      addLog(`发布任务已入队：${publishJob.value.jobId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '发布入队失败')
    } finally {
      loading.value = false
    }
  }

  async function publishDirect() {
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

  async function confirmRealPublish() {
    loading.value = true
    setError('')
    try {
      const result = await confirmMercadoLibreRealPublish(product.value, true)
      publishResult.value = result.raw
      if (result.product) product.value = result.product
      applyMutationIndexes(result)
      draftsIndex.value = result.draftsIndex?.length ? result.draftsIndex : await fetchDraftsIndex()
      publishLogs.value = await fetchPublishLogs()
      addLog(`Mercado Libre 真实发布返回：${result.status || (result.ok ? 'success' : 'failed')} ${result.message || result.error || ''}`)
      if (!result.ok && result.error) setError(result.error)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Mercado Libre 真实发布失败')
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
    const selected = platformOptions.value
      .find((option) => option.key === activeMarketplace.value)
      ?.sites.find((item) => item.code.toLowerCase() === String(site || '').trim().toLowerCase())
    if (!draft || !selected) return
    draft.site = selected.code
    draft.language = selected.language
    draft.currency = selected.currency
  }

  return {
    searchCategory, suggestCategoryByAi, autoSuggestCategoriesForDraft, selectCategory, loadCategoryAttributes, translateCategoryAttributes,
    translateCategoryResults, fillAttributesByAi, invalidateCategoryPrecheck, runCategoryOnlyPrecheck, runPrecheck, previewPayload,
    enqueuePublish, publishDirect, confirmRealPublish, setMarketplace, setMarketplaceSite, selectPublishTarget,
  }
}

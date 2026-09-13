import { fetchCategoryAttrs, matchCategory, runCategoryPrecheck, searchCategories } from '@/api/workflow/publishing'
import { translateText, type TextTranslationMap } from '@/api/workflow/translation'
import { withAiForeground } from '@/services/withAiForeground'
import { useAiWorkDisplayStore } from '@/stores/aiWorkDisplay'
import type { CategoryAttributeTranslations, CategoryResultTranslations, CategorySearchResult, CategorySelection, MarketplaceTargetSite } from '@/types/workflow'
import type { WorkflowRuntime } from '../orchestration/runtime'

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

export type WorkflowCategoryActionsPort = Pick<WorkflowRuntime,
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
  | 'loading'
  | 'addLog'
  | 'setError'
  | 'requestSequence'
  | 'currentStage'
  | 'currentPublishTargets'
  | 'selectedPublishTarget'
  | 'targetSiteKey'
  | 'applyCategoryRecommendationForTarget'
  | 'setCategoryRecommendation'
  | 'persistActiveTargetListingFields'
  | 'invalidateCategoryAttributeLoad'
  | 'syncActivePublishTarget'
  | 'applyMutationIndexes'
  | 'persistCurrentDraftForPublish'
> & { isCurrent?: () => boolean }

/** 类目业务动作复用显式注入的草稿与平台状态，不读取全局当前平台。 */
export function createWorkflowCategoryActions(runtime: WorkflowCategoryActionsPort) {
  const isCurrent = runtime.isCurrent || (() => true)
  const {
    currentDraft, currentDraftProductContext, category, categoryQuery,
    categoryResults, categoryRecommendations, categoryAutoMatching, categoryAutoMatchMessage,
    categoryAutoMatchCurrent, categoryAutoMatchTotal, categoryAutoMatchProductName, categoryAttributeTranslations,
    categoryAttributeTranslationsSource, categoryAttributeTranslating, categoryAttributeLoading, categoryAttributeError,
    categoryResultTranslations, categoryResultTranslationsSource, categoryResultTranslating, categoryPrecheck,
    precheck, precheckResults, payloadPreview, loading,
    addLog, setError, requestSequence, currentStage,
    currentPublishTargets, selectedPublishTarget, targetSiteKey, applyCategoryRecommendationForTarget,
    setCategoryRecommendation, persistActiveTargetListingFields, invalidateCategoryAttributeLoad, syncActivePublishTarget,
    applyMutationIndexes, persistCurrentDraftForPublish
  } = runtime

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
          error: result.status !== 'completed' ? result.failure?.message || 'AI 尚未确认合适类目，请核对候选。' : '',
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
    const key = targetSiteKey(selectedPublishTarget.value)
    for (const row of currentDraft.value.skuItems) delete row.attributes_by_target[key]
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

  function clearPublishValidation(extra: Partial<MarketplaceTargetSite> = {}) {
    precheck.value = null
    precheckResults.value = {}
    payloadPreview.value = null
    currentDraft.value.lastPrecheck = {}
    currentDraft.value.lastPrecheckTarget = {}
    currentDraft.value.publishStatus = ''
    currentDraft.value.status = currentDraft.value.categoryId ? 'category_ready' : 'pending'
    persistActiveTargetListingFields({
      lastPrecheck: {},
      lastPrecheckTarget: {},
      publishStatus: '',
      status: currentDraft.value.status,
      ...extra,
    })
  }

  function invalidatePublishValidation() {
    clearPublishValidation()
  }

  function invalidateCategoryPrecheck() {
    const categoryChanged = String(category.value?.categoryId || selectedPublishTarget.value.categoryId || '').trim()
      !== currentDraft.value.categoryId.trim()
    if (categoryChanged) {
      currentDraft.value.descriptionCategoryId = ''
    }
    categoryPrecheck.value = null
    clearPublishValidation({
      ...(categoryChanged ? { descriptionCategoryId: '' } : {}),
      categoryPrecheck: {},
    })
  }

  async function autoSuggestCategoriesForDraft() {
    if (useAiWorkDisplayStore().foregroundOccupied) {
      setError('已有前台 AI 任务运行，请等待完成后再试。')
      return false
    }
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
    const categoryId = String(item.id).trim()
    if (item.raw.type_id && String(item.raw.type_id).trim() !== categoryId) {
      setError('类目编号与平台商品类型不一致，请重新搜索后选择。')
      return
    }
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
    await loadCategoryAttributes()
  }

  async function loadCategoryAttributes() {
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
      isCurrent() && requestId === requestSequence.categoryAttributeLoad
      && requestTargetKey === targetSiteKey(selectedPublishTarget.value)
      && categoryId === currentDraft.value.categoryId.trim()
    )
    categoryAttributeLoading.value = true
    categoryAttributeError.value = ''
    loading.value = true
    setError('')
    try {
      const loadedCategory = await fetchCategoryAttrs(target.platform, categoryId, target.site)
      if (!requestIsCurrent()) return
      if (String(category.value?.categoryId || target.categoryId || '').trim() !== categoryId) {
        clearCurrentCategoryDependentFields()
      }
      if (loadedCategory.categoryPath) {
        currentDraft.value.categoryPath = loadedCategory.categoryPath
      }
      if (target.platform === 'ozon') {
        // 新版分页属性页不再回传类目记录：实时响应带 description_category_id 时优先采用，
        // 否则沿用草稿/目标站点已持久化的类目身份。
        const liveDescriptionCategoryId = String(loadedCategory.raw?.description_category_id ?? '').trim()
        if (liveDescriptionCategoryId) {
          currentDraft.value.descriptionCategoryId = liveDescriptionCategoryId
        }
        if (!currentDraft.value.descriptionCategoryId) {
          throw new Error('Ozon 实时类目缺少 description_category_id')
        }
      } else {
        currentDraft.value.descriptionCategoryId = ''
      }
      // 属性定义仅保存在编辑态 category ref（瞬时），持久化只写类目身份字段。
      category.value = loadedCategory
      categoryAttributeError.value = ''
      persistActiveTargetListingFields({
        descriptionCategoryId: currentDraft.value.descriptionCategoryId,
      })
      await persistCurrentDraftForPublish()
      categoryAttributeTranslations.value = {}
      categoryAttributeTranslationsSource.value = ''
      currentStage.value = 6
      addLog(`已读取类目属性定义并保存类目：${categoryId}`)
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
      const translated = await withAiForeground(
        {
          displayTitle: '翻译平台属性',
          initialUserMessage: `将类目 ${categoryId} 的平台属性名称、说明和选项翻译为中文。`,
        },
        ({ presentationId }) => translateText('zh-CN', content, { presentationId }),
      )
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
      const translated = await withAiForeground(
        {
          displayTitle: '翻译候选类目',
          initialUserMessage: `将当前 ${results.length} 个候选类目翻译为中文。`,
        },
        ({ presentationId }) => translateText('zh-CN', content, { presentationId }),
      )
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

  return { searchCategory, suggestCategoryByAi, autoSuggestCategoriesForDraft, selectCategory, loadCategoryAttributes, translateCategoryAttributes, translateCategoryResults, invalidatePublishValidation, invalidateCategoryPrecheck, runCategoryOnlyPrecheck }
}

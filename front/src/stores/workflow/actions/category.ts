import { draftContextTitle } from '@/utils/draftDisplayTitle'
import { fetchCategoryAttrs, matchCategories, runCategoryPrecheck, searchCategories } from '@/api/workflow/publishing'
import { isNonTranslatableText, translateTextSilent, type TextTranslationMap } from '@/api/workflow/translation'
import { withAiConversation } from '@/services/withAiConversation'
import type {
  CategoryAttributeTranslations,
  CategoryResultTranslations,
  CategorySearchResult,
  CategorySelection,
  MarketplaceTargetSite,
} from '@/types/workflow'
import type { WorkflowRuntime } from '../orchestration/runtime'

type AttributeTranslationSlot = {
  attributeId: string
  field: 'label' | 'help' | 'option'
  option?: string
}

const MAX_BACKGROUND_LOCALIZED_RESULTS = 25

function splitPathSegments(text: string): string[] {
  return String(text || '')
    .split(/\s*\/\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
}

/**
 * 按路径节点段去重拆分待补翻类目候选：
 * - 共享上级目录（如“建筑与维修 / 工具 / 手动工具”）仅发送 1 次并独立入库 L2 词典；
 * - 若 L2 已部分拼装出中文上级，仅补翻尚未翻译的叶子段，大幅降低 Token 与耗时。
 */
function pendingCategoryResultLocalizationContent(results: CategorySearchResult[]) {
  const content: TextTranslationMap = {}
  const keyBySegment = new Map<string, string>()
  const itemSegments = new Map<string, string[]>()

  for (const item of results.slice(0, MAX_BACKGROUND_LOCALIZED_RESULTS)) {
    if (!item.id) continue
    const baseText = String(item.pathZh || item.path || item.nameZh || item.name || '').trim()
    if (!baseText) continue
    const segments = splitPathSegments(baseText)
    if (!segments.length) continue
    const missing = segments.filter((seg) => !isNonTranslatableText(seg))
    if (!missing.length) continue
    itemSegments.set(item.id, segments)
    for (const seg of missing) {
      if (!keyBySegment.has(seg)) {
        const key = `seg.${keyBySegment.size}`
        keyBySegment.set(seg, key)
        content[key] = seg
      }
    }
  }
  return { content, keyBySegment, itemSegments }
}

function pendingCategoryAttributeLocalizationContent(selection: CategorySelection) {
  const content: TextTranslationMap = {}
  const slots = new Map<string, AttributeTranslationSlot>()
  const attributes = [...selection.requiredAttributes, ...selection.optionalAttributes]
  attributes.forEach((attribute, attributeIndex) => {
    const label = String(attribute.name || '').trim()
    if (label && !attribute.nameZh && !isNonTranslatableText(label)) {
      const key = `attribute.${attributeIndex}.label`
      content[key] = label
      slots.set(key, { attributeId: attribute.id, field: 'label' })
    }
    const description = String(attribute.description || '').trim()
    if (description && !attribute.descriptionZh && !isNonTranslatableText(description)) {
      const key = `attribute.${attributeIndex}.description`
      content[key] = description
      slots.set(key, { attributeId: attribute.id, field: 'help' })
    }
    // 大字典属性或专有名词属性（品牌/型号）跳过整表预览翻译，改为打开下拉时按当前页懒本地化。
    if (!attribute.skipValueLocalization && !attribute.hasMoreValues) {
      for (const [optionIndex, option] of (attribute.options || []).entries()) {
        const text = String(option || '').trim()
        if (!text || attribute.optionLabelsZh?.[text] || isNonTranslatableText(text)) continue
        const key = `attribute.${attributeIndex}.option.${optionIndex}`
        content[key] = text
        slots.set(key, { attributeId: attribute.id, field: 'option', option: text })
      }
    }
  })
  return { content, slots }
}

function mergeAttributeTranslations(
  existing: CategoryAttributeTranslations,
  selection: CategorySelection,
  translated: TextTranslationMap,
  slots: Map<string, AttributeTranslationSlot>,
): CategoryAttributeTranslations {
  const next: CategoryAttributeTranslations = { ...existing }
  for (const attribute of [...selection.requiredAttributes, ...selection.optionalAttributes]) {
    if (!next[attribute.id]) {
      next[attribute.id] = { label: '', help: '', values: {} }
    } else {
      next[attribute.id] = {
        ...next[attribute.id],
        values: { ...next[attribute.id].values },
      }
    }
  }
  for (const [key, text] of Object.entries(translated)) {
    const slot = slots.get(key)
    if (!slot || !next[slot.attributeId]) continue
    if (slot.field === 'option' && slot.option) {
      next[slot.attributeId].values[slot.option] = text
    } else if (slot.field === 'label') {
      next[slot.attributeId].label = text
    } else {
      next[slot.attributeId].help = text
    }
  }
  return next
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

export interface LoadCategoryAttributesOptions {
  forceRefresh?: boolean
  auto?: boolean
}

/** 类目业务动作复用显式注入的草稿与平台状态，自动执行 L3 非阻塞静默中文补翻。 */
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
    applyMutationIndexes, persistCurrentDraftForPublish,
  } = runtime

  async function localizeCategoryResultsInBackground(
    results: CategorySearchResult[],
    target: MarketplaceTargetSite,
  ) {
    if (!results.length) return
    const { content, keyBySegment, itemSegments } = pendingCategoryResultLocalizationContent(results)
    if (!Object.keys(content).length) return
    const requestId = ++requestSequence.categoryResultTranslation
    categoryResultTranslating.value = true
    try {
      const response = await translateTextSilent('zh-CN', content, {
        domain: target.platform,
        scope: {
          draft_id: currentDraft.value.draftId,
          platform: target.platform,
          site: target.site,
        },
      })
      if (!isCurrent() || requestId !== requestSequence.categoryResultTranslation) return
      const translatedBySegment = new Map<string, string>()
      for (const [seg, key] of keyBySegment.entries()) {
        const translated = response.translations[key]
        if (translated) translatedBySegment.set(seg, translated)
      }
      if (translatedBySegment.size) {
        const mapped: CategoryResultTranslations = { ...categoryResultTranslations.value }
        for (const [cid, segments] of itemSegments.entries()) {
          let changed = false
          const assembled = segments.map((seg) => {
            const zh = translatedBySegment.get(seg)
            if (zh) {
              changed = true
              return zh
            }
            return seg
          })
          if (changed) mapped[cid] = assembled.join(' / ')
        }
        categoryResultTranslations.value = mapped
      }
      categoryResultTranslationsSource.value = response.source
    } finally {
      if (requestId === requestSequence.categoryResultTranslation) {
        categoryResultTranslating.value = false
      }
    }
  }

  async function localizeCategoryAttributesInBackground(
    selection: CategorySelection,
    target: MarketplaceTargetSite,
  ) {
    const { content, slots } = pendingCategoryAttributeLocalizationContent(selection)
    if (!Object.keys(content).length) return
    const requestId = ++requestSequence.categoryAttributeTranslation
    categoryAttributeTranslating.value = true
    try {
      const response = await translateTextSilent('zh-CN', content, {
        domain: target.platform,
        scope: {
          draft_id: currentDraft.value.draftId,
          platform: target.platform,
          site: target.site,
          category_id: selection.categoryId,
        },
      })
      if (!isCurrent() || requestId !== requestSequence.categoryAttributeTranslation) return
      if (category.value?.categoryId !== selection.categoryId) return
      if (Object.keys(response.translations).length) {
        categoryAttributeTranslations.value = mergeAttributeTranslations(
          categoryAttributeTranslations.value,
          selection,
          response.translations,
          slots,
        )
        categoryAttributeTranslationsSource.value = response.source
      }
    } finally {
      if (requestId === requestSequence.categoryAttributeTranslation) {
        categoryAttributeTranslating.value = false
      }
    }
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
      setCategoryRecommendation(target, categoryQuery.value, result.results, '', result.translatedQuery || '')
      categoryResultTranslations.value = {}
      categoryResultTranslationsSource.value = ''
      addLog(
        result.translatedQuery
          ? `类目搜索完成：${result.results.length} 条（已按「${result.translatedQuery}」检索）。`
          : `类目搜索完成：${result.results.length} 条。`,
      )
      void localizeCategoryResultsInBackground(result.results, target)
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
    categoryAutoMatchProductName.value = draftContextTitle(currentDraftProductContext.value, currentDraft.value.draftId)
    const recommendations: Record<string, { query: string; results: CategorySearchResult[]; error: string }> = {}
    let candidateTargetCount = 0
    let completedCount = 0
    categoryAutoMatchMessage.value = `正在匹配 ${targets.length} 个目标站点的类目…`
    const results = await withAiConversation('AI 匹配类目', id => matchCategories(currentDraft.value, targets, id))
    targets.forEach((target, index) => {
      const result = results[index]
      recommendations[targetSiteKey(target)] = {
        query: result.query,
        results: result.candidates,
        error: result.status !== 'completed' ? result.failure?.message || 'AI 尚未确认合适类目，请核对候选。' : '',
      }
      if (result.candidates.length) candidateTargetCount += 1
      if (result.status === 'completed') completedCount += 1
    })
    categoryAutoMatchCurrent.value = targets.length
    categoryRecommendations.value = recommendations
    applyCategoryRecommendationForTarget(selectedPublishTarget.value)
    categoryResultTranslations.value = {}
    categoryResultTranslationsSource.value = ''
    addLog(`类目匹配已完成：${candidateTargetCount}/${targets.length} 个目标站点返回候选，${completedCount} 个给出已验证首选；仍需人工点击候选确认。`)
    if (!candidateTargetCount) setError('没有找到可用类目候选，请调整商品信息或手动搜索。')
    void localizeCategoryResultsInBackground(categoryResults.value, selectedPublishTarget.value)
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
    categoryAutoMatchMessage.value = '正在运行 1+N 多模态与单轮精排类目匹配…'
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
    if (item.forbidden || item.raw.forbidden) {
      setError(String(item.forbiddenReason || item.raw.forbidden_reason || '该类目在禁售名单中，不能选择。'))
      return
    }
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
    await loadCategoryAttributes({ auto: true })
  }

  async function loadCategoryAttributes(options: LoadCategoryAttributesOptions = {}) {
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
      const loadedCategory = options.forceRefresh
        ? await fetchCategoryAttrs(target.platform, categoryId, target.site, { forceRefresh: true })
        : await fetchCategoryAttrs(target.platform, categoryId, target.site)
      if (!requestIsCurrent()) return
      const previousPath = String(currentDraft.value.categoryPath || '').trim()
      const previousDescId = String(currentDraft.value.descriptionCategoryId || '').trim()
      const categoryIdentityChanged = String(category.value?.categoryId || target.categoryId || '').trim() !== categoryId
      if (categoryIdentityChanged) {
        clearCurrentCategoryDependentFields()
      }
      if (loadedCategory.categoryPath) {
        currentDraft.value.categoryPath = loadedCategory.categoryPath
      }
      if (target.platform === 'ozon') {
        const liveDescriptionCategoryId = String(
          loadedCategory.descriptionCategoryId
          ?? loadedCategory.raw?.description_category_id
          ?? '',
        ).trim()
        if (liveDescriptionCategoryId) {
          currentDraft.value.descriptionCategoryId = liveDescriptionCategoryId
        }
        if (!currentDraft.value.descriptionCategoryId) {
          throw new Error('Ozon 实时类目缺少 description_category_id')
        }
      } else {
        currentDraft.value.descriptionCategoryId = ''
      }
      category.value = loadedCategory
      categoryAttributeError.value = ''
      const metadataChanged = (
        categoryIdentityChanged
        || currentDraft.value.categoryPath.trim() !== previousPath
        || currentDraft.value.descriptionCategoryId.trim() !== previousDescId
      )
      if (metadataChanged || !options.auto) {
        persistActiveTargetListingFields({
          descriptionCategoryId: currentDraft.value.descriptionCategoryId,
        })
        await persistCurrentDraftForPublish()
      }
      categoryAttributeTranslations.value = {}
      categoryAttributeTranslationsSource.value = ''
      currentStage.value = 6
      addLog(`已读取类目属性定义：${categoryId}`)
      void localizeCategoryAttributesInBackground(loadedCategory, target)
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
      if (!isCurrent()) return
      const draftId = currentDraft.value.draftId
      const target = selectedPublishTarget.value
      const result = await runCategoryPrecheck(currentDraft.value, target, categoryId)
      if (!isCurrent() || currentDraft.value.draftId !== draftId) return
      currentDraft.value = result.draft
      currentDraftProductContext.value = result.productContext
      syncActivePublishTarget(target)
      applyMutationIndexes(result)
      categoryPrecheck.value = result
      persistActiveTargetListingFields({ categoryPrecheck: categoryPrecheck.value.raw || categoryPrecheck.value })
      addLog(categoryPrecheck.value.ok ? '类目预检通过。' : `类目预检发现缺项：${categoryPrecheck.value.missingFields.join('、') || categoryPrecheck.value.errors.join('、')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '类目预检失败')
    } finally {
      loading.value = false
    }
  }

  return {
    searchCategory,
    suggestCategoryByAi,
    autoSuggestCategoriesForDraft,
    selectCategory,
    loadCategoryAttributes,
    localizeCategoryResultsInBackground,
    localizeCategoryAttributesInBackground,
    invalidatePublishValidation,
    invalidateCategoryPrecheck,
    runCategoryOnlyPrecheck,
  }
}

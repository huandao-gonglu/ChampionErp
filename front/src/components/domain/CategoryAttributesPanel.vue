<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { ComponentPublicInstance } from 'vue'
import CategoryPrecheckPanel from '@/components/domain/CategoryPrecheckPanel.vue'
import { fetchCategoryAttributeValues } from '@/api/workflow/publishing'
import { isCategoryDictionaryAttribute } from '@/api/workflow/normalizers'
import type { CategoryAttributeDefinition, CategoryAttributeOption, CategoryAttributeTranslations, CategoryDictionaryValue, CategoryPrecheckResult, CategoryResultTranslations, CategorySearchResult, CategorySelection, DraftDetail, DraftProductContext, MarketplaceOption, MarketplaceTargetSite, PrecheckIssue, PublishPrecheck, UnknownRecord } from '@/types/workflow'

const props = withDefaults(defineProps<{
  draft: DraftDetail
  productContext: DraftProductContext
  publishTargets: MarketplaceTargetSite[]
  selectedPublishTarget: MarketplaceTargetSite
  platformOptions: MarketplaceOption[]
  category: CategorySelection | null
  categoryQuery: string
  categoryResults: CategorySearchResult[]
  categoryAutoMatchProductName?: string
  categoryAutoMatchTargetError?: string
  categoryAttributeTranslations: CategoryAttributeTranslations
  categoryAttributeTranslationsSource: string
  categoryAttributeTranslating: boolean
  categoryAttributeLoading?: boolean
  categoryAttributeError?: string
  categoryResultTranslations: CategoryResultTranslations
  categoryResultTranslationsSource: string
  categoryResultTranslating: boolean
  categoryPrecheck: CategoryPrecheckResult | null
  precheck: PublishPrecheck | null
  loading: boolean
}>(), {
  categoryAutoMatchProductName: '',
  categoryAutoMatchTargetError: '',
  categoryAttributeLoading: false,
  categoryAttributeError: '',
})

const emit = defineEmits<{
  updateCategoryQuery: [value: string]
  selectPublishTarget: [value: MarketplaceTargetSite]
  searchCategory: []
  suggestCategory: []
  selectCategory: [item: CategorySearchResult]
  applyCategory: []
  translateCategoryResults: []
  translateCategoryAttributes: []
  fillAttributes: []
  invalidateCategoryPrecheck: []
  categoryPrecheck: []
}>()

const selectedTargetKey = computed(() => targetKey(props.selectedPublishTarget))
const targetOptions = computed(() => props.publishTargets.map((target) => ({
  ...target,
  key: targetKey(target),
  label: targetLabel(target),
})))

const showRequiredAttributes = ref(false)
const showOptionalAttributes = ref(false)
const attributeInputRefs = ref<Record<string, HTMLInputElement | HTMLSelectElement | null>>({})
interface DictionaryFieldState {
  query: string
  loadedQuery: string | null
  options: CategoryAttributeOption[]
  loading: boolean
  loadingMore: boolean
  error: string
  open: boolean
  requestId: number
  nextCursor: string
  hasMore: boolean
}
const dictionaryFieldStates = ref<Record<string, DictionaryFieldState>>({})
const dictionarySearchTimers = new Map<string, ReturnType<typeof setTimeout>>()

const hasCurrentDraft = computed(() => Boolean(props.draft.draftId))
const currentDraftTitle = computed(() => props.draft.title || props.productContext.title || props.productContext.sourceTitle || props.draft.draftId || '尚未选择草稿')

const activeDraft = computed(() => {
  const draft = props.draft
  if (!Array.isArray(draft.validationErrors)) {
    draft.validationErrors = []
  }
  return draft
})
const hasSelectedCategory = computed(() => Boolean(activeDraft.value.categoryId.trim()))
const hasLoadedCategoryDefinition = computed(() => Boolean(
  props.category
  && props.category.categoryId === activeDraft.value.categoryId.trim()
  && props.category.platform === props.selectedPublishTarget.platform
  && props.category.fetchedAt,
))
const categoryAttributeState = computed<'empty' | 'loading' | 'ready' | 'error'>(() => {
  if (!hasSelectedCategory.value) return 'empty'
  if (props.categoryAttributeLoading) return 'loading'
  if (hasLoadedCategoryDefinition.value) return 'ready'
  return 'error'
})
const categoryAttributeErrorMessage = computed(() => props.categoryAttributeError || '平台属性定义尚未加载，请重新加载后再编辑属性。')

const blockingIssues = computed(() => props.precheck?.errorItems || [])
const warningIssues = computed(() => props.precheck?.warningItems || [])
const pendingReviewAttributeIds = computed(() => {
  const ids = new Set<string>()
  const requiredIds = new Set(
    (props.category?.requiredAttributes || [])
      .map((attr) => attr.id)
      .filter(Boolean),
  )
  for (const item of activeDraft.value.validationErrors) {
    collectReviewAttributeId(item, ids)
  }
  for (const issue of [...blockingIssues.value, ...warningIssues.value]) {
    collectReviewAttributeId(issue, ids)
  }
  return [...ids].filter((attrId) => requiredIds.has(attrId)).sort()
})
const attributeFields = computed(() => {
  const fields = new Map<string, CategoryAttributeDefinition>()
  for (const attr of props.category?.requiredAttributes || []) {
    if (attr.id) fields.set(attr.id, { ...attr, name: attr.name || attr.id, options: attr.options || [] })
  }
  for (const attr of props.category?.optionalAttributes || []) {
    if (attr.id && !fields.has(attr.id)) fields.set(attr.id, { ...attr, name: attr.name || attr.id, required: false, options: attr.options || [] })
  }
  return [...fields.values()]
})
const requiredAttributeFields = computed(() => attributeFields.value.filter((attr) => attr.required))
const optionalAttributeFields = computed(() => attributeFields.value.filter((attr) => !attr.required))
const attributeFieldById = computed(() => new Map(attributeFields.value.map((attr) => [attr.id, attr])))
const translationCount = computed(() => Object.values(props.categoryAttributeTranslations || {}).filter((item) => item.label).length)
const translationSourceLabel = computed(() => props.categoryAttributeTranslationsSource === 'cache' ? '缓存' : props.categoryAttributeTranslationsSource === 'ai' ? 'AI' : '')
const showAttributeTranslationProgress = computed(() => props.categoryAttributeTranslating)
const categoryResultTranslationCount = computed(() => Object.values(props.categoryResultTranslations || {}).filter(Boolean).length)
const showCategoryResultTranslationProgress = computed(() => props.categoryResultTranslating)

function attributeTranslation(attrId: string) {
  return props.categoryAttributeTranslations?.[attrId] || null
}

function attributeLabel(attr: { id: string; name: string }) {
  const translation = attributeTranslation(attr.id)
  return translation?.label || attr.name || attr.id
}

function attributeOriginalLabel(attr: { id: string; name: string }) {
  return [attr.name || attr.id, attr.id].filter((item, index, items) => item && items.indexOf(item) === index).join(' · ')
}

function attributeOptionLabel(attrId: string, option: string) {
  const translation = attributeTranslation(attrId)
  const translated = translation?.values?.[option]
  return translated ? `${translated} / ${option}` : option
}

function attributePlaceholder(attr: CategoryAttributeDefinition) {
  if (isCategoryDictionaryAttribute(attr.dictionaryId, attr.isDictionary)) return '请选择平台允许的值'
  return attr.options?.length ? '请选择平台允许的选项' : '请输入属性值'
}

function selectedDictionaryValues(attrId: string): CategoryDictionaryValue[] {
  const raw = activeDraft.value.attributes[attrId]
  if (!raw || typeof raw === 'string' || !('values' in raw) || !Array.isArray(raw.values)) return []
  return raw.values
}

function selectedDictionarySummary(attr: CategoryAttributeDefinition) {
  const values = selectedDictionaryValues(attr.id)
  if (!values.length) return ''
  return attr.isCollection
    ? `已选 ${values.length} 项`
    : values[0]?.value || ''
}

function unitAttributeValue(attrId: string): { value: string; unit: string } {
  const raw = activeDraft.value.attributes[attrId]
  if (!raw || typeof raw === 'string') {
    return { value: typeof raw === 'string' ? raw : '', unit: '' }
  }
  if ('values' in raw) return { value: '', unit: '' }
  return { value: String(raw.value ?? ''), unit: String(raw.unit ?? '') }
}

function unitAttributeSelectedUnit(attr: CategoryAttributeDefinition) {
  const current = unitAttributeValue(attr.id).unit
  return current || attr.defaultUnit || attr.unitOptions?.[0] || ''
}

function setUnitAttributeValue(attr: CategoryAttributeDefinition, patch: { value?: string; unit?: string }) {
  const current = unitAttributeValue(attr.id)
  const value = (patch.value ?? current.value).trim()
  const unit = (patch.unit ?? unitAttributeSelectedUnit(attr)).trim()
  if (!value) {
    delete activeDraft.value.attributes[attr.id]
  } else {
    activeDraft.value.attributes[attr.id] = { value, unit }
  }
  emit('invalidateCategoryPrecheck')
}

function dictionaryState(attr: CategoryAttributeDefinition): DictionaryFieldState {
  const existing = dictionaryFieldStates.value[attr.id]
  if (existing) return existing
  const state: DictionaryFieldState = {
    query: '',
    loadedQuery: null,
    options: [],
    loading: false,
    loadingMore: false,
    error: '',
    open: false,
    requestId: 0,
    nextCursor: '',
    hasMore: false,
  }
  dictionaryFieldStates.value[attr.id] = state
  return state
}

function legacyDictionaryValue(attrId: string) {
  const value = activeDraft.value.attributes[attrId]
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function mergeDictionaryOptions(
  current: CategoryAttributeOption[],
  incoming: CategoryAttributeOption[],
) {
  const options = new Map(current.map((option) => [String(option.id), option]))
  for (const option of incoming) options.set(String(option.id), option)
  return [...options.values()]
}

async function loadDictionaryOptions(attr: CategoryAttributeDefinition, append = false) {
  const state = dictionaryState(attr)
  if (append && (!state.hasMore || !state.nextCursor || state.loading || state.loadingMore)) return
  const query = state.query.trim()
  const cursor = append ? state.nextCursor : ''
  const requestId = ++state.requestId
  state.loading = !append
  state.loadingMore = append
  state.error = ''
  if (!append) {
    state.options = []
    state.nextCursor = ''
    state.hasMore = false
  }
  try {
    const page = await fetchCategoryAttributeValues(
      props.selectedPublishTarget.platform,
      activeDraft.value.categoryId,
      attr.id,
      props.selectedPublishTarget.site,
      query,
      50,
      cursor,
    )
    if (requestId !== state.requestId) return
    state.options = append
      ? mergeDictionaryOptions(state.options, page.values)
      : page.values
    state.loadedQuery = query
    state.nextCursor = page.nextCursor
    state.hasMore = page.hasMore && Boolean(page.nextCursor)
  } catch (error) {
    if (requestId !== state.requestId) return
    if (!append) state.options = []
    state.error = error instanceof Error ? error.message : '读取平台枚举值失败'
  } finally {
    if (requestId === state.requestId) {
      state.loading = false
      state.loadingMore = false
    }
  }
}

function openDictionary(attr: CategoryAttributeDefinition) {
  const state = dictionaryState(attr)
  state.open = true
  if (
    state.loadedQuery !== state.query.trim()
    && !state.loading
    && !state.loadingMore
  ) void loadDictionaryOptions(attr)
}

function scheduleDictionarySearch(attr: CategoryAttributeDefinition, value: string) {
  const state = dictionaryState(attr)
  state.query = value
  state.open = true
  state.requestId += 1
  state.nextCursor = ''
  state.hasMore = false
  state.loadingMore = false
  state.error = ''
  const previous = dictionarySearchTimers.get(attr.id)
  if (previous) clearTimeout(previous)
  const query = value.trim()
  if (props.selectedPublishTarget.platform === 'ozon' && query.length === 1) {
    state.options = []
    state.loadedQuery = query
    state.loading = false
    state.error = 'Ozon 平台枚举搜索至少需要 2 个字符'
    return
  }
  dictionarySearchTimers.set(attr.id, setTimeout(() => {
    void loadDictionaryOptions(attr)
  }, 250))
}

function selectDictionaryOption(attr: CategoryAttributeDefinition, option: CategoryAttributeOption) {
  // 按字符串保存枚举值 ID：Yandex 等平台的 dictionary_value_id 可能超出
  // Number 安全整数范围，Number() 会造成精度丢失并选错枚举值。
  const dictionaryValueId = String(option.id ?? '').trim()
  if (!dictionaryValueId || dictionaryValueId === '0') return
  const existing = selectedDictionaryValues(attr.id)
  const selected = attr.isCollection
    ? [
      ...existing.filter((item) => String(item.dictionaryValueId) !== dictionaryValueId),
      { dictionaryValueId, value: option.value },
    ].slice(0, attr.maxValueCount && attr.maxValueCount > 0 ? attr.maxValueCount : undefined)
    : [{ dictionaryValueId, value: option.value }]
  activeDraft.value.attributes[attr.id] = { values: selected }
  const state = dictionaryState(attr)
  state.query = ''
  state.loadedQuery = null
  state.options = []
  state.nextCursor = ''
  state.hasMore = false
  state.open = Boolean(attr.isCollection)
  state.error = ''
  if (attr.isCollection) void loadDictionaryOptions(attr)
  emit('invalidateCategoryPrecheck')
}

function removeDictionaryOption(attr: CategoryAttributeDefinition, dictionaryValueId: string | number) {
  const removed = String(dictionaryValueId)
  const values = selectedDictionaryValues(attr.id).filter(
    (item) => String(item.dictionaryValueId) !== removed,
  )
  if (values.length) {
    activeDraft.value.attributes[attr.id] = { values }
  } else {
    delete activeDraft.value.attributes[attr.id]
  }
  emit('invalidateCategoryPrecheck')
}

function clearDictionaryValue(attr: CategoryAttributeDefinition) {
  delete activeDraft.value.attributes[attr.id]
  const state = dictionaryState(attr)
  state.query = ''
  state.loadedQuery = null
  state.options = []
  state.nextCursor = ''
  state.hasMore = false
  state.open = false
  emit('invalidateCategoryPrecheck')
}

function clearDictionarySearch(attr: CategoryAttributeDefinition) {
  const state = dictionaryState(attr)
  state.query = ''
  state.loadedQuery = null
  state.options = []
  state.nextCursor = ''
  state.hasMore = false
  state.error = ''
  state.open = true
  void loadDictionaryOptions(attr)
}

function loadMoreDictionaryOptions(attr: CategoryAttributeDefinition) {
  void loadDictionaryOptions(attr, true)
}

function closeDictionary(attr: CategoryAttributeDefinition) {
  setTimeout(() => {
    const state = dictionaryFieldStates.value[attr.id]
    const active = document.activeElement
    if (
      active instanceof HTMLElement
      && (active.dataset.attributeId === attr.id || active.dataset.dictionarySearchId === attr.id)
    ) return
    if (state) state.open = false
  }, 150)
}

function categoryResultTranslation(item: CategorySearchResult) {
  return props.categoryResultTranslations?.[item.id] || ''
}

function categoryResultTitle(item: CategorySearchResult) {
  return categoryResultTranslation(item) || item.name || item.id
}

function categoryResultSubtitle(item: CategorySearchResult) {
  return item.path || item.id
}

function isMissingAttribute(attrId: string) {
  const missing = [
    ...(props.categoryPrecheck?.missingFields || []),
    ...(props.categoryPrecheck?.errors || []),
    ...(props.precheck?.errorItems?.map((item) => item.field) || []),
  ]
  return missing.some((field) => field === attrId || field === `attributes.${attrId}` || field.endsWith(`.${attrId}`))
}

function reviewAttributeIdFromField(field: string) {
  const value = String(field || '').trim()
  if (!value || value === 'attributes') return ''
  return value.startsWith('attributes.') ? value.slice('attributes.'.length) : value
}

function collectReviewAttributeId(item: PrecheckIssue | UnknownRecord | string, ids: Set<string>) {
  if (typeof item === 'string') {
    const attrId = reviewAttributeIdFromField(item)
    if (attrId) ids.add(attrId)
    return
  }
  const record = item as UnknownRecord
  if (String(record.code || '') !== 'NEED_REVIEW_ATTRIBUTES') return
  const attrId = reviewAttributeIdFromField(String(record.field || ''))
  if (attrId) ids.add(attrId)
}

function setAttributeInputRef(attrId: string, el: Element | ComponentPublicInstance | null) {
  const node = el && '$el' in el ? el.$el : el
  attributeInputRefs.value[attrId] = node instanceof HTMLInputElement || node instanceof HTMLSelectElement ? node : null
}

function reviewAttributeLabel(attrId: string) {
  const attr = attributeFieldById.value.get(attrId)
  return attr ? attributeLabel(attr) : attrId
}

function reviewAttributeSection(attrId: string) {
  if (requiredAttributeFields.value.some((attr) => attr.id === attrId)) return 'required'
  if (optionalAttributeFields.value.some((attr) => attr.id === attrId)) return 'optional'
  return ''
}

async function focusAttribute(attrId: string) {
  const section = reviewAttributeSection(attrId)
  if (!section) return
  if (section === 'required') {
    showRequiredAttributes.value = true
  } else {
    showOptionalAttributes.value = true
  }
  await nextTick()
  const input = attributeInputRefs.value[attrId]
  input?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  input?.focus({ preventScroll: true })
  if (input instanceof HTMLInputElement) input.select()
}

watch(
  () => [selectedTargetKey.value, props.category?.categoryId || ''],
  () => {
    showRequiredAttributes.value = false
    showOptionalAttributes.value = false
    attributeInputRefs.value = {}
    for (const timer of dictionarySearchTimers.values()) clearTimeout(timer)
    dictionarySearchTimers.clear()
    dictionaryFieldStates.value = {}
  },
)

onBeforeUnmount(() => {
  for (const timer of dictionarySearchTimers.values()) clearTimeout(timer)
  dictionarySearchTimers.clear()
})

function targetKey(target: MarketplaceTargetSite) {
  return `${String(target.platform || '').trim().toLowerCase()}:${String(target.site || '').trim().toLowerCase()}`
}

function targetLabel(target: MarketplaceTargetSite) {
  if (!target.platform || !target.site) return '尚未选择目标站点'
  const platform = props.platformOptions.find((item) => item.key === target.platform)
  const site = platform?.sites.find((item) => item.code.toLowerCase() === String(target.site || '').toLowerCase())
  const platformLabel = platform?.label || target.platform || '目标平台'
  const siteLabel = site?.label || target.site || '默认站点'
  const language = target.language || site?.language || ''
  // 发布币种只展示店铺配置快照；未就绪统一提示待配置，不回退站点币种。
  const currency = target.listingCurrency || '店铺发布货币待配置'
  return `${platformLabel} - ${siteLabel}（${target.site || site?.code || '-'} / ${language || '-'} / ${currency || '-'}）`
}

function selectTargetByKey(value: string) {
  const target = props.publishTargets.find((item) => targetKey(item) === value)
  if (target) emit('selectPublishTarget', target)
}

</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <article class="mb-6 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="text-xs font-semibold text-accent-500 dark:text-accent-400">当前类目/属性草稿</p>
          <div class="mt-2 flex flex-wrap items-center gap-3">
            <img v-if="props.productContext.imagePool[0]?.previewUrl || props.productContext.imagePool[0]?.url" :src="props.productContext.imagePool[0]?.previewUrl || props.productContext.imagePool[0]?.url" class="size-14 rounded-lg object-cover" />
            <div class="min-w-0">
              <h3 class="truncate font-semibold text-accent-950 dark:text-white">{{ currentDraftTitle }}</h3>
              <div class="mt-1 flex flex-wrap gap-2 text-xs text-accent-500 dark:text-accent-400">
                <span>{{ activeDraft.sku || props.productContext.sku || '无 SKU' }}</span>
                <span>{{ props.productContext.sourcePlatform || '来源未记录' }}</span>
                <span>{{ activeDraft.status || 'pending' }}</span>
                <span>{{ targetLabel(props.selectedPublishTarget) }}</span>
              </div>
            </div>
          </div>
          <p v-if="!hasCurrentDraft" class="mt-3 text-sm text-amber-700">请先从草稿箱选择草稿，再编辑目标站点的类目/属性。</p>
        </div>
        <div class="flex w-full flex-wrap gap-2 lg:w-auto lg:min-w-[28rem]">
          <div class="min-w-0 flex-1 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm text-accent-700 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-200">
            <div class="text-xs font-semibold text-accent-500 dark:text-accent-400">来源商品</div>
            <div class="mt-1 truncate">{{ props.productContext.sourceTitle || props.productContext.title || props.productContext.productId || '未记录来源商品' }}</div>
          </div>
          <div class="min-w-0 flex-1 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm text-accent-700 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-200">
            <div class="text-xs font-semibold text-accent-500 dark:text-accent-400">草稿 ID</div>
            <div class="mt-1 truncate font-mono">{{ activeDraft.draftId || '-' }}</div>
          </div>
        </div>
      </div>
    </article>

    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">类目/属性</h2>
        <p class="muted mt-1">在当前草稿的目标站点之间切换，并分别维护平台类目和必填属性。</p>
        <p v-if="props.categoryAutoMatchProductName" class="mt-1 text-xs font-semibold text-brand-700 dark:text-brand-300">AI 识别商品主体：{{ props.categoryAutoMatchProductName }}。请逐站点检查候选类目后再确认。</p>
        <p v-if="showCategoryResultTranslationProgress || showAttributeTranslationProgress" class="mt-1 text-xs text-brand-700 dark:text-brand-300">正在调用 AI 模型翻译文本...</p>
        <p v-else-if="categoryResultTranslationCount || translationCount" class="mt-1 text-xs text-accent-500 dark:text-accent-400">已翻译候选类目 {{ categoryResultTranslationCount }} 项 / 属性 {{ translationCount }} 项</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <select :value="selectedTargetKey" class="input w-80 max-w-full" :disabled="props.loading || targetOptions.length <= 1" @change="selectTargetByKey(($event.target as HTMLSelectElement).value)">
          <option v-for="target in targetOptions" :key="target.key" :value="target.key">{{ target.label }}</option>
        </select>
      </div>
    </div>
    <div v-if="showCategoryResultTranslationProgress || showAttributeTranslationProgress" class="mt-3 h-2 overflow-hidden rounded-full bg-accent-200 dark:bg-dark-800">
      <div class="h-full w-2/3 animate-pulse rounded-full bg-brand-500" />
    </div>

    <div class="mt-5 grid min-w-0 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <article class="min-w-0 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">类目候选与手动搜索</h3>
            <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">可手动输入关键词搜索当前目标站点，或按需使用 AI 识别商品主体并生成候选。</p>
          </div>
          <button class="btn btn-outline" :disabled="props.loading || props.categoryResultTranslating || !props.categoryResults.length" @click="emit('translateCategoryResults')">翻译候选类目</button>
        </div>
        <p class="mt-3 text-xs text-accent-500 dark:text-accent-400">{{ props.loading ? '正在请求当前平台实时类目接口...' : '候选类目来自当前平台实时搜索；选中类目后再读取并保存平台属性定义。' }}</p>
        <p v-if="props.categoryAutoMatchTargetError" class="mt-2 text-xs text-amber-700 dark:text-amber-300">本目标站点自动匹配未完成：{{ props.categoryAutoMatchTargetError }}</p>
        <div class="mt-4 flex gap-2">
          <input :value="props.categoryQuery" class="input" placeholder="类目关键词" @input="emit('updateCategoryQuery', ($event.target as HTMLInputElement).value)" @keyup.enter="emit('searchCategory')" />
          <button class="btn btn-outline shrink-0" :disabled="props.loading || !hasCurrentDraft" @click="emit('suggestCategory')">AI 匹配类目</button>
          <button class="btn btn-primary shrink-0" :disabled="props.loading || !hasCurrentDraft" @click="emit('searchCategory')">搜索</button>
        </div>
        <div class="mt-4 space-y-2">
          <button v-for="item in props.categoryResults" :key="item.id" class="w-full rounded-lg border border-accent-200 bg-white p-3 text-left hover:border-brand-300 hover:bg-brand-50 dark:border-dark-700 dark:bg-dark-900 dark:hover:border-primary-500/60 dark:hover:bg-dark-800" @click="emit('selectCategory', item)">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="font-semibold text-accent-950 dark:text-white">{{ categoryResultTitle(item) }}</div>
              <span v-if="item.raw.score" class="badge-info">AI {{ item.raw.score }}</span>
            </div>
            <div class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ categoryResultSubtitle(item) }}</div>
            <div v-if="item.raw.site || item.raw.source" class="mt-1 text-xs text-accent-400 dark:text-accent-500">{{ item.raw.site || '' }}{{ item.raw.source ? ` / ${item.raw.source}` : '' }}</div>
          </button>
          <div v-if="!props.categoryResults.length" class="rounded-lg border border-dashed border-accent-300 bg-white p-5 text-center text-sm text-accent-500 dark:border-dark-600 dark:bg-dark-900 dark:text-accent-300">暂无搜索结果。</div>
        </div>
      </article>

      <article class="min-w-0 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">当前类目 / 平台属性</h3>
            <p v-if="translationCount" class="mt-1 text-xs text-accent-500 dark:text-accent-400">属性翻译：{{ translationCount }} 项{{ translationSourceLabel ? ` / ${translationSourceLabel}` : '' }}</p>
          </div>
        </div>
        <label class="mt-4 block">
          <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">类目 ID</span>
          <input v-model="activeDraft.categoryId" class="input mt-1" placeholder="例如 MLM12345" @input="emit('invalidateCategoryPrecheck')" />
        </label>
        <label class="mt-3 block">
          <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">类目路径</span>
          <input v-model="activeDraft.categoryPath" class="input mt-1" />
        </label>
        <div class="mt-4 flex flex-wrap gap-2">
          <button class="btn btn-outline" :disabled="props.loading || props.categoryAttributeLoading || !hasCurrentDraft || !hasSelectedCategory" @click="emit('applyCategory')">刷新平台属性</button>
          <button class="btn btn-outline" :disabled="props.loading || props.categoryAttributeTranslating || categoryAttributeState !== 'ready'" @click="emit('translateCategoryAttributes')">翻译平台属性</button>
          <button class="btn btn-primary" :disabled="props.loading || categoryAttributeState !== 'ready'" @click="emit('fillAttributes')">AI 填充属性</button>
          <button class="btn btn-outline" :disabled="props.loading || categoryAttributeState !== 'ready'" @click="emit('categoryPrecheck')">类目预检</button>
        </div>

        <div v-if="categoryAttributeState === 'empty'" class="mt-4 rounded-lg border border-dashed border-accent-300 bg-white p-4 text-sm text-accent-500 dark:border-dark-600 dark:bg-dark-900 dark:text-accent-300">
          请先从左侧搜索结果中选择类目。
        </div>
        <div v-else-if="categoryAttributeState === 'loading'" class="mt-4 rounded-lg border border-brand-200 bg-brand-50 p-4 text-sm text-brand-700 dark:border-primary-500/40 dark:bg-primary-950/20 dark:text-brand-300">
          正在读取并保存平台属性定义...
        </div>
        <div v-else-if="categoryAttributeState === 'error'" class="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-300">
          <div class="font-semibold">平台属性定义未加载</div>
          <p class="mt-1">{{ categoryAttributeErrorMessage }}</p>
          <button class="btn btn-outline mt-3" :disabled="props.loading || props.categoryAttributeLoading" type="button" @click="emit('applyCategory')">重新加载</button>
        </div>
        <div v-else class="mt-4 flex flex-wrap gap-2">
          <span class="badge-muted">必填属性 {{ requiredAttributeFields.length }} 个</span>
          <span class="badge-muted">可选属性 {{ optionalAttributeFields.length }} 个</span>
        </div>

        <div v-if="categoryAttributeState === 'ready' && pendingReviewAttributeIds.length" class="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-800 ring-1 ring-amber-200 dark:bg-amber-950/20 dark:text-amber-300 dark:ring-amber-900/60">
          <div class="font-semibold">待复核属性</div>
          <div class="mt-2 flex flex-wrap gap-2">
            <button
              v-for="attrId in pendingReviewAttributeIds"
              :key="attrId"
              class="rounded-full bg-white px-2.5 py-1 text-xs text-amber-800 ring-1 ring-amber-200 transition hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-400 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-dark-900 dark:text-amber-300 dark:ring-amber-900/60"
              type="button"
              :disabled="!reviewAttributeSection(attrId)"
              :title="reviewAttributeSection(attrId) ? `定位到${reviewAttributeLabel(attrId)}` : '当前平台属性定义中不存在该属性'"
              @click="focusAttribute(attrId)"
            >
              {{ reviewAttributeLabel(attrId) }}
            </button>
          </div>
        </div>

        <div v-if="categoryAttributeState === 'ready' && requiredAttributeFields.length" class="mt-4 rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
          <button class="flex w-full items-center justify-between text-left text-sm font-semibold text-accent-700 dark:text-accent-200" type="button" @click="showRequiredAttributes = !showRequiredAttributes">
            <span>必填属性 {{ requiredAttributeFields.length }} 个</span>
            <span>{{ showRequiredAttributes ? '收起' : '展开' }}</span>
          </button>
          <div v-if="showRequiredAttributes" class="mt-3 grid gap-2" data-testid="required-attribute-fields">
            <label v-for="attr in requiredAttributeFields" :key="attr.id" class="block">
              <span class="text-xs font-semibold" :class="isMissingAttribute(attr.id) ? 'text-rose-700' : 'text-slate-500'">* {{ attributeLabel(attr) }}</span>
              <span v-if="attributeTranslation(attr.id)" class="mt-0.5 block text-[11px] text-slate-400">{{ attributeOriginalLabel(attr) }}</span>
              <span v-if="attributeTranslation(attr.id)?.help" class="mt-0.5 block text-[11px] text-slate-500">{{ attributeTranslation(attr.id)?.help }}</span>
              <span v-if="pendingReviewAttributeIds.includes(attr.id)" class="mt-0.5 block text-[11px] text-amber-600">AI 暂无法从商品信息判断，请人工确认。</span>
              <div v-if="isCategoryDictionaryAttribute(attr.dictionaryId, attr.isDictionary)" class="relative mt-1">
                <div v-if="attr.isCollection && selectedDictionaryValues(attr.id).length" class="mb-2 flex flex-wrap gap-2">
                  <span v-for="item in selectedDictionaryValues(attr.id)" :key="item.dictionaryValueId" class="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs text-brand-800 ring-1 ring-brand-200 dark:bg-brand-950/30 dark:text-brand-200 dark:ring-brand-900/60">
                    {{ item.value }}
                    <button type="button" aria-label="移除选项" @click="removeDictionaryOption(attr, item.dictionaryValueId)">×</button>
                  </span>
                </div>
                <div class="flex gap-2">
                  <input
                    :ref="(el) => setAttributeInputRef(attr.id, el)"
                    :value="selectedDictionarySummary(attr)"
                    class="input"
                    :class="isMissingAttribute(attr.id) ? 'border-rose-300 bg-rose-50' : ''"
                    :data-attribute-id="attr.id"
                    :placeholder="attributePlaceholder(attr)"
                    readonly
                    @focus="openDictionary(attr)"
                    @blur="closeDictionary(attr)"
                    @click="openDictionary(attr)"
                  />
                  <button v-if="selectedDictionaryValues(attr.id).length || legacyDictionaryValue(attr.id)" class="btn btn-outline shrink-0" type="button" @click="clearDictionaryValue(attr)">清除已选</button>
                </div>
                <div v-if="dictionaryState(attr).open" class="absolute z-30 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-accent-200 bg-white p-1 shadow-xl dark:border-dark-700 dark:bg-dark-900">
                  <div class="sticky top-0 flex gap-2 bg-white p-2 dark:bg-dark-900">
                    <input
                      :value="dictionaryState(attr).query"
                      class="input"
                      :data-dictionary-search-id="attr.id"
                      placeholder="仅搜索平台选项，不会作为属性值保存"
                      autocomplete="off"
                      @focus="openDictionary(attr)"
                      @blur="closeDictionary(attr)"
                      @input="scheduleDictionarySearch(attr, ($event.target as HTMLInputElement).value)"
                    />
                    <button v-if="dictionaryState(attr).query" class="btn btn-outline shrink-0" type="button" @mousedown.prevent @click="clearDictionarySearch(attr)">清除搜索</button>
                  </div>
                  <div v-if="dictionaryState(attr).loading" class="p-3 text-xs text-accent-500">正在读取平台选项…</div>
                  <div v-else-if="dictionaryState(attr).error" class="p-3 text-xs text-rose-700">{{ dictionaryState(attr).error }}</div>
                  <button
                    v-for="option in dictionaryState(attr).options"
                    :key="option.id"
                    class="block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-brand-50 dark:hover:bg-dark-800"
                    type="button"
                    @mousedown.prevent
                    @click="selectDictionaryOption(attr, option)"
                  >
                    <span class="block font-medium text-accent-900 dark:text-white">{{ option.value }}</span>
                    <span v-if="option.info" class="mt-0.5 block text-xs text-accent-500">{{ option.info }}</span>
                  </button>
                  <button
                    v-if="dictionaryState(attr).hasMore"
                    class="mt-1 block w-full rounded-md border border-dashed border-accent-300 px-3 py-2 text-center text-xs font-semibold text-brand-700 hover:bg-brand-50 disabled:cursor-wait disabled:opacity-60 dark:border-dark-600 dark:text-brand-300 dark:hover:bg-dark-800"
                    type="button"
                    :disabled="dictionaryState(attr).loadingMore"
                    @mousedown.prevent
                    @click="loadMoreDictionaryOptions(attr)"
                  >
                    {{ dictionaryState(attr).loadingMore ? '正在加载更多…' : '加载更多平台选项' }}
                  </button>
                  <div v-if="!dictionaryState(attr).loading && !dictionaryState(attr).error && !dictionaryState(attr).options.length" class="p-3 text-xs text-accent-500">没有匹配的平台选项，请更换关键词或检查类目。</div>
                </div>
                <p v-if="legacyDictionaryValue(attr.id)" class="mt-1 text-xs text-rose-700">旧值“{{ legacyDictionaryValue(attr.id) }}”不是平台选项，请重新选择。</p>
                <p v-else class="mt-1 text-xs text-accent-500">平台枚举字段，只会保存从列表选中的值。</p>
              </div>
              <div v-else-if="attr.unitOptions?.length" class="mt-1 flex gap-2">
                <input
                  :ref="(el) => setAttributeInputRef(attr.id, el)"
                  :value="unitAttributeValue(attr.id).value"
                  class="input"
                  :class="isMissingAttribute(attr.id) ? 'border-rose-300 bg-rose-50' : ''"
                  :data-attribute-id="attr.id"
                  :placeholder="attributePlaceholder(attr)"
                  @input="setUnitAttributeValue(attr, { value: ($event.target as HTMLInputElement).value })"
                />
                <select
                  :value="unitAttributeSelectedUnit(attr)"
                  class="input w-28 shrink-0"
                  :aria-label="`单位（${attributeLabel(attr)}）`"
                  @change="setUnitAttributeValue(attr, { unit: ($event.target as HTMLSelectElement).value })"
                >
                  <option v-for="unitOption in attr.unitOptions" :key="unitOption" :value="unitOption">{{ unitOption }}</option>
                </select>
              </div>
              <select
                v-else-if="attr.options?.length"
                :ref="(el) => setAttributeInputRef(attr.id, el)"
                v-model="activeDraft.attributes[attr.id]"
                class="input mt-1"
                :class="isMissingAttribute(attr.id) ? 'border-rose-300 bg-rose-50' : ''"
                :data-attribute-id="attr.id"
                @change="emit('invalidateCategoryPrecheck')"
              >
                <option value="">{{ attributePlaceholder(attr) }}</option>
                <option v-for="option in attr.options" :key="option" :value="option">{{ attributeOptionLabel(attr.id, option) }}</option>
              </select>
              <input
                v-else
                :ref="(el) => setAttributeInputRef(attr.id, el)"
                v-model="activeDraft.attributes[attr.id]"
                class="input mt-1"
                :class="isMissingAttribute(attr.id) ? 'border-rose-300 bg-rose-50' : ''"
                :data-attribute-id="attr.id"
                :placeholder="attributePlaceholder(attr)"
                @input="emit('invalidateCategoryPrecheck')"
              />
            </label>
          </div>
        </div>
        <div v-else-if="categoryAttributeState === 'ready'" class="mt-4 rounded-lg border border-accent-200 bg-white p-3 text-sm text-accent-500 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-300">
          当前类目没有必填属性。
        </div>

        <div v-if="categoryAttributeState === 'ready' && optionalAttributeFields.length" class="mt-4 rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
          <button class="flex w-full items-center justify-between text-left text-sm font-semibold text-accent-700 dark:text-accent-200" type="button" @click="showOptionalAttributes = !showOptionalAttributes">
            <span>可选属性 {{ optionalAttributeFields.length }} 个</span>
            <span>{{ showOptionalAttributes ? '收起' : '展开' }}</span>
          </button>
          <div v-if="showOptionalAttributes" class="mt-3 grid gap-2" data-testid="optional-attribute-fields">
            <label v-for="attr in optionalAttributeFields" :key="attr.id" class="block">
              <span class="text-xs font-semibold text-slate-500">{{ attributeLabel(attr) }}</span>
              <span v-if="attributeTranslation(attr.id)" class="mt-0.5 block text-[11px] text-slate-400">{{ attributeOriginalLabel(attr) }}</span>
              <span v-if="attributeTranslation(attr.id)?.help" class="mt-0.5 block text-[11px] text-slate-500">{{ attributeTranslation(attr.id)?.help }}</span>
              <div v-if="isCategoryDictionaryAttribute(attr.dictionaryId, attr.isDictionary)" class="relative mt-1">
                <div v-if="attr.isCollection && selectedDictionaryValues(attr.id).length" class="mb-2 flex flex-wrap gap-2">
                  <span v-for="item in selectedDictionaryValues(attr.id)" :key="item.dictionaryValueId" class="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs text-brand-800 ring-1 ring-brand-200 dark:bg-brand-950/30 dark:text-brand-200 dark:ring-brand-900/60">
                    {{ item.value }}
                    <button type="button" aria-label="移除选项" @click="removeDictionaryOption(attr, item.dictionaryValueId)">×</button>
                  </span>
                </div>
                <div class="flex gap-2">
                  <input
                    :ref="(el) => setAttributeInputRef(attr.id, el)"
                    :value="selectedDictionarySummary(attr)"
                    class="input"
                    :data-attribute-id="attr.id"
                    :placeholder="attributePlaceholder(attr)"
                    readonly
                    @focus="openDictionary(attr)"
                    @blur="closeDictionary(attr)"
                    @click="openDictionary(attr)"
                  />
                  <button v-if="selectedDictionaryValues(attr.id).length || legacyDictionaryValue(attr.id)" class="btn btn-outline shrink-0" type="button" @click="clearDictionaryValue(attr)">清除已选</button>
                </div>
                <div v-if="dictionaryState(attr).open" class="absolute z-30 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-accent-200 bg-white p-1 shadow-xl dark:border-dark-700 dark:bg-dark-900">
                  <div class="sticky top-0 flex gap-2 bg-white p-2 dark:bg-dark-900">
                    <input
                      :value="dictionaryState(attr).query"
                      class="input"
                      :data-dictionary-search-id="attr.id"
                      placeholder="仅搜索平台选项，不会作为属性值保存"
                      autocomplete="off"
                      @focus="openDictionary(attr)"
                      @blur="closeDictionary(attr)"
                      @input="scheduleDictionarySearch(attr, ($event.target as HTMLInputElement).value)"
                    />
                    <button v-if="dictionaryState(attr).query" class="btn btn-outline shrink-0" type="button" @mousedown.prevent @click="clearDictionarySearch(attr)">清除搜索</button>
                  </div>
                  <div v-if="dictionaryState(attr).loading" class="p-3 text-xs text-accent-500">正在读取平台选项…</div>
                  <div v-else-if="dictionaryState(attr).error" class="p-3 text-xs text-rose-700">{{ dictionaryState(attr).error }}</div>
                  <button
                    v-for="option in dictionaryState(attr).options"
                    :key="option.id"
                    class="block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-brand-50 dark:hover:bg-dark-800"
                    type="button"
                    @mousedown.prevent
                    @click="selectDictionaryOption(attr, option)"
                  >
                    <span class="block font-medium text-accent-900 dark:text-white">{{ option.value }}</span>
                    <span v-if="option.info" class="mt-0.5 block text-xs text-accent-500">{{ option.info }}</span>
                  </button>
                  <button
                    v-if="dictionaryState(attr).hasMore"
                    class="mt-1 block w-full rounded-md border border-dashed border-accent-300 px-3 py-2 text-center text-xs font-semibold text-brand-700 hover:bg-brand-50 disabled:cursor-wait disabled:opacity-60 dark:border-dark-600 dark:text-brand-300 dark:hover:bg-dark-800"
                    type="button"
                    :disabled="dictionaryState(attr).loadingMore"
                    @mousedown.prevent
                    @click="loadMoreDictionaryOptions(attr)"
                  >
                    {{ dictionaryState(attr).loadingMore ? '正在加载更多…' : '加载更多平台选项' }}
                  </button>
                  <div v-if="!dictionaryState(attr).loading && !dictionaryState(attr).error && !dictionaryState(attr).options.length" class="p-3 text-xs text-accent-500">没有匹配的平台选项，请更换关键词或检查类目。</div>
                </div>
                <p v-if="legacyDictionaryValue(attr.id)" class="mt-1 text-xs text-rose-700">旧值“{{ legacyDictionaryValue(attr.id) }}”不是平台选项，请重新选择。</p>
                <p v-else class="mt-1 text-xs text-accent-500">平台枚举字段，只会保存从列表选中的值。</p>
              </div>
              <div v-else-if="attr.unitOptions?.length" class="mt-1 flex gap-2">
                <input
                  :ref="(el) => setAttributeInputRef(attr.id, el)"
                  :value="unitAttributeValue(attr.id).value"
                  class="input"
                  :data-attribute-id="attr.id"
                  :placeholder="attributePlaceholder(attr)"
                  @input="setUnitAttributeValue(attr, { value: ($event.target as HTMLInputElement).value })"
                />
                <select
                  :value="unitAttributeSelectedUnit(attr)"
                  class="input w-28 shrink-0"
                  :aria-label="`单位（${attributeLabel(attr)}）`"
                  @change="setUnitAttributeValue(attr, { unit: ($event.target as HTMLSelectElement).value })"
                >
                  <option v-for="unitOption in attr.unitOptions" :key="unitOption" :value="unitOption">{{ unitOption }}</option>
                </select>
              </div>
              <select v-else-if="attr.options?.length" :ref="(el) => setAttributeInputRef(attr.id, el)" v-model="activeDraft.attributes[attr.id]" class="input mt-1" :data-attribute-id="attr.id" @change="emit('invalidateCategoryPrecheck')">
                <option value="">{{ attributePlaceholder(attr) }}</option>
                <option v-for="option in attr.options" :key="option" :value="option">{{ attributeOptionLabel(attr.id, option) }}</option>
              </select>
              <input v-else :ref="(el) => setAttributeInputRef(attr.id, el)" v-model="activeDraft.attributes[attr.id]" class="input mt-1" :data-attribute-id="attr.id" :placeholder="attributePlaceholder(attr)" @input="emit('invalidateCategoryPrecheck')" />
            </label>
          </div>
        </div>
        <CategoryPrecheckPanel :result="props.categoryPrecheck" @locate-attribute="focusAttribute" />
      </article>
    </div>
  </section>
</template>

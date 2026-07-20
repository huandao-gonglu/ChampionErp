<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { ComponentPublicInstance } from 'vue'
import type { CategoryAttributeTranslations, CategoryPrecheckResult, CategoryResultTranslations, CategorySearchResult, CategorySelection, DraftDetail, DraftProductContext, MarketplaceOption, MarketplaceTargetSite, PrecheckIssue, PublishPrecheck, UnknownRecord } from '@/types/workflow'

const props = withDefaults(defineProps<{
  mode?: 'publish' | 'category'
  draft: DraftDetail
  productContext: DraftProductContext
  publishTargets: MarketplaceTargetSite[]
  selectedPublishTarget: MarketplaceTargetSite
  platformOptions: MarketplaceOption[]
  category: CategorySelection | null
  categoryQuery: string
  categoryResults: CategorySearchResult[]
  categoryAttributeTranslationEnabled: boolean
  categoryAttributeTranslations: CategoryAttributeTranslations
  categoryAttributeTranslationsSource: string
  categoryAttributeTranslating: boolean
  categoryResultTranslations: CategoryResultTranslations
  categoryResultTranslationsSource: string
  categoryResultTranslating: boolean
  categoryPrecheck: CategoryPrecheckResult | null
  precheck: PublishPrecheck | null
  payloadPreview: UnknownRecord | null
  loading: boolean
}>(), {
  mode: 'publish',
})

const emit = defineEmits<{
  updateCategoryQuery: [value: string]
  selectPublishTarget: [value: MarketplaceTargetSite]
  searchCategory: []
  suggestCategory: []
  selectCategory: [item: CategorySearchResult]
  applyCategory: []
  setTranslateAttributesEnabled: [value: boolean]
  fillAttributes: []
  categoryPrecheck: []
  precheck: []
  previewPayload: []
  publish: []
}>()

const selectedTargetKey = computed(() => targetKey(props.selectedPublishTarget))
const targetOptions = computed(() => props.publishTargets.map((target) => ({
  ...target,
  key: targetKey(target),
  label: targetLabel(target),
})))

const showOptionalAttributes = ref(false)
const attributeInputRefs = ref<Record<string, HTMLInputElement | HTMLSelectElement | null>>({})
type WarrantyType = 'none' | 'seller' | 'factory'
type WarrantyUnit = 'months' | 'years'

const warrantyTypeOptions: Array<{ value: WarrantyType; label: string }> = [
  { value: 'none', label: '无保修' },
  { value: 'seller', label: '卖家保修' },
  { value: 'factory', label: '厂家保修' },
]
const warrantyUnitOptions: Array<{ value: WarrantyUnit; label: string }> = [
  { value: 'months', label: '个月' },
  { value: 'years', label: '年' },
]

const hasCurrentDraft = computed(() => Boolean(props.draft.draftId))
const isCategoryMode = computed(() => props.mode === 'category')

const currentDraftTitle = computed(() => props.draft.title || props.productContext.title || props.productContext.sourceTitle || props.draft.draftId || '尚未选择草稿')

const activeDraft = computed(() => {
  const draft = props.draft
  if (!draft.packageDimensions) {
    draft.packageDimensions = { lengthCm: '', widthCm: '', heightCm: '', weightKg: '' }
  }
  if (!Array.isArray(draft.saleTerms)) {
    draft.saleTerms = []
  }
  if (typeof draft.upc !== 'string') {
    draft.upc = ''
  }
  if (typeof draft.allowGtinExemption !== 'boolean') {
    draft.allowGtinExemption = false
  }
  if (!Array.isArray(draft.validationErrors)) {
    draft.validationErrors = []
  }
  return draft
})
const blockingIssues = computed(() => props.precheck?.errorItems || [])
const warningIssues = computed(() => props.precheck?.warningItems || [])
const pendingReviewAttributeIds = computed(() => {
  const ids = new Set<string>()
  for (const item of activeDraft.value.validationErrors) {
    collectReviewAttributeId(item, ids)
  }
  for (const issue of [...blockingIssues.value, ...warningIssues.value]) {
    collectReviewAttributeId(issue, ids)
  }
  return [...ids].sort()
})
const canQueuePublish = computed(() => Boolean(hasCurrentDraft.value && (props.precheck?.ok || activeDraft.value.status === 'ready_to_publish')))
const publishReadiness = computed(() => {
  if (!props.precheck && activeDraft.value.status === 'ready_to_publish') return '已保存为校验通过，可以加入发布队列。'
  if (!props.precheck) return '点击上架预检后，这里会变成可处理清单。'
  if (props.precheck.ok) return '预检通过，可以加入发布队列。'
  return `还剩 ${blockingIssues.value.length} 个阻断项，先在本页补齐能直接处理的字段。`
})
const attributeFields = computed(() => {
  const draftAttrs = activeDraft.value.attributes
  const fields = new Map<string, { id: string; name: string; required: boolean; options: string[] }>()
  for (const attr of props.category?.requiredAttributes || []) {
    if (attr.id) fields.set(attr.id, { id: attr.id, name: attr.name || attr.id, required: attr.required, options: attr.options || [] })
  }
  for (const attr of props.category?.optionalAttributes || []) {
    if (attr.id && !fields.has(attr.id)) fields.set(attr.id, { id: attr.id, name: attr.name || attr.id, required: false, options: attr.options || [] })
  }
  for (const key of Object.keys(draftAttrs)) {
    if (!fields.has(key)) fields.set(key, { id: key, name: key, required: false, options: [] })
  }
  return [...fields.values()]
})
const requiredAttributeFields = computed(() => attributeFields.value.filter((attr) => attr.required || isMissingAttribute(attr.id)))
const optionalAttributeFields = computed(() => attributeFields.value.filter((attr) => !attr.required && !isMissingAttribute(attr.id)))
const selectedWarrantyType = computed<WarrantyType>({
  get() {
    const typeTerm = activeDraft.value.saleTerms.find((term) => String(term.id || '') === 'WARRANTY_TYPE')
    const value = String(typeTerm?.value_id || typeTerm?.value_name || '').toLowerCase()
    if (value.includes('2230280') || value.includes('seller') || value.includes('vendedor')) return 'seller'
    if (value.includes('2230279') || value.includes('factory') || value.includes('fábrica') || value.includes('fabrica')) return 'factory'
    return 'none'
  },
  set(value) {
    applyWarrantyTerms(value, warrantyDurationValue.value, warrantyDurationUnit.value)
  },
})
const warrantyDurationValue = computed<string>({
  get() {
    const timeTerm = activeDraft.value.saleTerms.find((term) => String(term.id || '') === 'WARRANTY_TIME')
    const struct = timeTerm?.value_struct && typeof timeTerm.value_struct === 'object' ? timeTerm.value_struct as UnknownRecord : {}
    const number = struct.number ?? String(timeTerm?.value_name || '').match(/\d+(?:[,.]\d+)?/)?.[0] ?? '3'
    return String(number || '3')
  },
  set(value) {
    applyWarrantyTerms(selectedWarrantyType.value, value, warrantyDurationUnit.value)
  },
})
const warrantyDurationUnit = computed<WarrantyUnit>({
  get() {
    const timeTerm = activeDraft.value.saleTerms.find((term) => String(term.id || '') === 'WARRANTY_TIME')
    const struct = timeTerm?.value_struct && typeof timeTerm.value_struct === 'object' ? timeTerm.value_struct as UnknownRecord : {}
    const unit = String(struct.unit || timeTerm?.value_name || '').toLowerCase()
    return unit.includes('year') || unit.includes('año') || unit.includes('ano') ? 'years' : 'months'
  },
  set(value) {
    applyWarrantyTerms(selectedWarrantyType.value, warrantyDurationValue.value, value)
  },
})
const warrantySummary = computed(() => activeDraft.value.saleTerms.length ? `已配置 ${activeDraft.value.saleTerms.length} 条` : '尚未配置 warranty / sale_terms')
const translateAttributesEnabled = computed({
  get: () => props.categoryAttributeTranslationEnabled,
  set: (value: boolean) => emit('setTranslateAttributesEnabled', value),
})
const translationCount = computed(() => Object.values(props.categoryAttributeTranslations || {}).filter((item) => item.label).length)
const translationSourceLabel = computed(() => props.categoryAttributeTranslationsSource === 'cache' ? '缓存' : props.categoryAttributeTranslationsSource === 'ai' ? 'AI' : '')
const showAttributeTranslationProgress = computed(() => translateAttributesEnabled.value && props.categoryAttributeTranslating)
const categoryResultTranslationCount = computed(() => Object.values(props.categoryResultTranslations || {}).filter(Boolean).length)
const showCategoryResultTranslationProgress = computed(() => translateAttributesEnabled.value && props.categoryResultTranslating)

function attributeTranslation(attrId: string) {
  return props.categoryAttributeTranslations?.[attrId] || null
}

function attributeLabel(attr: { id: string; name: string }) {
  const translation = translateAttributesEnabled.value ? attributeTranslation(attr.id) : null
  return translation?.label || attr.name || attr.id
}

function attributeOriginalLabel(attr: { id: string; name: string }) {
  return [attr.name || attr.id, attr.id].filter((item, index, items) => item && items.indexOf(item) === index).join(' · ')
}

function attributeOptionLabel(attrId: string, option: string) {
  const translation = translateAttributesEnabled.value ? attributeTranslation(attrId) : null
  const translated = translation?.values?.[option]
  return translated ? `${translated} / ${option}` : option
}

function attributePlaceholder(attr: { id: string; options?: string[] }) {
  return attr.options?.length ? '请选择平台允许的选项' : '请输入属性值'
}

function categoryResultTranslation(item: CategorySearchResult) {
  return props.categoryResultTranslations?.[item.id] || ''
}

function categoryResultTitle(item: CategorySearchResult) {
  return translateAttributesEnabled.value && categoryResultTranslation(item) ? categoryResultTranslation(item) : item.name || item.id
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

async function focusAttribute(attrId: string) {
  if (optionalAttributeFields.value.some((attr) => attr.id === attrId)) {
    showOptionalAttributes.value = true
  }
  await nextTick()
  const input = attributeInputRefs.value[attrId]
  input?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  input?.focus({ preventScroll: true })
  if (input instanceof HTMLInputElement) input.select()
}

function issueTitle(issue: PrecheckIssue) {
  return [issue.field, issue.message].filter(Boolean).join('：')
}

function hasIssue(field: string, code = '') {
  return blockingIssues.value.some((issue) => issue.field === field || issue.code === code || issue.field.startsWith(`${field}.`))
}

function generateSku() {
  const source = activeDraft.value.draftId || props.productContext.sourceUrl || props.productContext.title || activeDraft.value.title || Date.now().toString()
  const suffix = source.replace(/[^a-zA-Z0-9]+/g, '').slice(-8).toUpperCase() || Date.now().toString().slice(-6)
  const model = (activeDraft.value.attributes.MODEL || props.productContext.model || 'ML').replace(/[^a-zA-Z0-9]+/g, '').slice(0, 10).toUpperCase() || 'ML'
  const sku = `${model}-${suffix}`
  activeDraft.value.sku = sku
}

function useDefaultStock() {
  activeDraft.value.stock = activeDraft.value.stock || props.productContext.stock || '10'
}

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
  const currency = target.currency || site?.currency || ''
  return `${platformLabel} - ${siteLabel}（${target.site || site?.code || '-'} / ${language || '-'} / ${currency || '-'}）`
}

function selectTargetByKey(value: string) {
  const target = props.publishTargets.find((item) => targetKey(item) === value)
  if (target) emit('selectPublishTarget', target)
}

function applyWarrantyTerms(type: WarrantyType, durationValue = '3', unit: WarrantyUnit = 'months') {
  if (type === 'none') {
    activeDraft.value.saleTerms = [
      { id: 'WARRANTY_TYPE', value_id: '6150835', value_name: 'Sin garantía' },
    ]
    return
  }
  const number = Math.max(1, Number(String(durationValue || '').replace(',', '.')) || 3)
  const localUnit = unit === 'years' ? 'años' : 'meses'
  activeDraft.value.saleTerms = [
    {
      id: 'WARRANTY_TYPE',
      value_id: type === 'seller' ? '2230280' : '2230279',
      value_name: type === 'seller' ? 'Garantía del vendedor' : 'Garantía de fábrica',
    },
    {
      id: 'WARRANTY_TIME',
      value_name: `${number} ${localUnit}`,
      value_struct: { number, unit: localUnit },
    },
  ]
}

</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <article class="mb-6 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="text-xs font-semibold text-accent-500 dark:text-accent-400">{{ isCategoryMode ? '当前类目/属性草稿' : '当前预检草稿' }}</p>
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
        <h2 class="card-title">{{ isCategoryMode ? '类目/属性' : '类目 / 属性 / 发布预检' }}</h2>
        <p class="muted mt-1">{{ isCategoryMode ? '在当前草稿的目标站点之间切换，并分别维护平台类目和必填属性。' : '类目搜索、必填属性填充、发布前校验和 payload 预览。' }}</p>
        <p v-if="showCategoryResultTranslationProgress || showAttributeTranslationProgress" class="mt-1 text-xs text-brand-700 dark:text-brand-300">正在调用 AI 模型翻译类目/属性...</p>
        <p v-else-if="translateAttributesEnabled && (categoryResultTranslationCount || translationCount)" class="mt-1 text-xs text-accent-500 dark:text-accent-400">已翻译候选类目 {{ categoryResultTranslationCount }} 项 / 属性 {{ translationCount }} 项</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <label class="inline-flex items-center gap-2 rounded-full border border-accent-200 bg-white px-3 py-1.5 text-sm font-semibold text-accent-700 shadow-sm dark:border-dark-700 dark:bg-dark-900 dark:text-accent-200">
          <input v-model="translateAttributesEnabled" type="checkbox" class="size-4 rounded border-accent-300" :disabled="props.loading || !hasCurrentDraft" />
          翻译类目/属性
        </label>
        <select :value="selectedTargetKey" class="input w-80 max-w-full" :disabled="props.loading || targetOptions.length <= 1" @change="selectTargetByKey(($event.target as HTMLSelectElement).value)">
          <option v-for="target in targetOptions" :key="target.key" :value="target.key">{{ target.label }}</option>
        </select>
      </div>
    </div>
    <div v-if="showCategoryResultTranslationProgress || showAttributeTranslationProgress" class="mt-3 h-2 overflow-hidden rounded-full bg-accent-200 dark:bg-dark-800">
      <div class="h-full w-2/3 animate-pulse rounded-full bg-brand-500" />
    </div>

    <div class="mt-5 grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <article class="min-w-0 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">实时类目搜索</h3>
            <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">输入商品标题或关键词，实时匹配当前目标站点的 Mercado Libre 类目路径。</p>
          </div>
        </div>
        <p class="mt-3 text-xs text-accent-500 dark:text-accent-400">{{ props.loading ? '正在请求 Mercado Libre 实时类目接口...' : '候选类目来自 Mercado Libre 实时搜索；选中类目后再实时读取必填属性。' }}</p>
        <div class="mt-4 flex gap-2">
          <input :value="props.categoryQuery" class="input" placeholder="类目关键词" @input="emit('updateCategoryQuery', ($event.target as HTMLInputElement).value)" @keyup.enter="emit('searchCategory')" />
          <button class="btn btn-outline shrink-0" :disabled="props.loading || !hasCurrentDraft" @click="emit('suggestCategory')">匹配类目</button>
          <button class="btn btn-primary shrink-0" :disabled="props.loading || !hasCurrentDraft" @click="emit('searchCategory')">搜索</button>
        </div>
        <div class="mt-4 max-h-80 space-y-2 overflow-y-auto">
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
            <h3 class="font-semibold text-accent-950 dark:text-white">当前类目 / 必填属性</h3>
            <p v-if="translateAttributesEnabled && translationCount" class="mt-1 text-xs text-accent-500 dark:text-accent-400">属性翻译：{{ translationCount }} 项{{ translationSourceLabel ? ` / ${translationSourceLabel}` : '' }}</p>
          </div>
        </div>
        <label class="mt-4 block">
          <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">类目 ID</span>
          <input v-model="activeDraft.categoryId" class="input mt-1" placeholder="例如 MLM12345" />
        </label>
        <label class="mt-3 block">
          <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">类目路径</span>
          <input v-model="activeDraft.categoryPath" class="input mt-1" />
        </label>
        <div class="mt-4 flex flex-wrap gap-2">
          <button class="btn btn-outline" :disabled="props.loading || !hasCurrentDraft" @click="emit('applyCategory')">读取必填属性</button>
          <button class="btn btn-primary" :disabled="props.loading || !hasCurrentDraft" @click="emit('fillAttributes')">AI 填充属性</button>
          <button class="btn btn-outline" :disabled="props.loading || !hasCurrentDraft" @click="emit('categoryPrecheck')">类目预检</button>
        </div>
        <div v-if="pendingReviewAttributeIds.length" class="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-800 ring-1 ring-amber-200">
          <div class="font-semibold">待复核属性</div>
          <div class="mt-2 flex flex-wrap gap-2">
            <button
              v-for="attrId in pendingReviewAttributeIds"
              :key="attrId"
              class="rounded-full bg-white px-2.5 py-1 font-mono text-xs text-amber-800 ring-1 ring-amber-200 transition hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-400"
              type="button"
              @click="focusAttribute(attrId)"
            >
              {{ attrId }}
            </button>
          </div>
        </div>
        <div class="mt-4 flex flex-wrap gap-2">
          <span v-for="attr in requiredAttributeFields" :key="attr.id" class="badge-muted">{{ attributeLabel(attr) }}</span>
          <span v-if="!props.category?.requiredAttributes.length" class="badge-muted">待读取属性</span>
        </div>
        <div class="mt-4 grid gap-2">
          <label v-for="attr in requiredAttributeFields" :key="attr.id" class="block">
            <span class="text-xs font-semibold" :class="isMissingAttribute(attr.id) ? 'text-rose-700' : 'text-slate-500'">{{ attr.required ? '* ' : '' }}{{ attributeLabel(attr) }}</span>
            <span v-if="translateAttributesEnabled && attributeTranslation(attr.id)" class="mt-0.5 block text-[11px] text-slate-400">{{ attributeOriginalLabel(attr) }}</span>
            <span v-if="translateAttributesEnabled && attributeTranslation(attr.id)?.help" class="mt-0.5 block text-[11px] text-slate-500">{{ attributeTranslation(attr.id)?.help }}</span>
            <span v-if="pendingReviewAttributeIds.includes(attr.id)" class="mt-0.5 block text-[11px] text-amber-600">AI 暂无法从商品信息判断，请人工确认。</span>
            <select
              v-if="attr.options.length"
              :ref="(el) => setAttributeInputRef(attr.id, el)"
              v-model="activeDraft.attributes[attr.id]"
              class="input mt-1"
              :class="isMissingAttribute(attr.id) ? 'border-rose-300 bg-rose-50' : ''"
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
              :placeholder="attributePlaceholder(attr)"
            />
          </label>
        </div>
        <div v-if="optionalAttributeFields.length" class="mt-4 rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
          <button class="flex w-full items-center justify-between text-left text-sm font-semibold text-accent-700 dark:text-accent-200" type="button" @click="showOptionalAttributes = !showOptionalAttributes">
            <span>可选字段 {{ optionalAttributeFields.length }} 个</span>
            <span>{{ showOptionalAttributes ? '收起' : '展开' }}</span>
          </button>
          <div v-if="showOptionalAttributes" class="mt-3 grid gap-2">
            <label v-for="attr in optionalAttributeFields" :key="attr.id" class="block">
              <span class="text-xs font-semibold text-slate-500">{{ attributeLabel(attr) }}</span>
              <span v-if="translateAttributesEnabled && attributeTranslation(attr.id)" class="mt-0.5 block text-[11px] text-slate-400">{{ attributeOriginalLabel(attr) }}</span>
              <span v-if="translateAttributesEnabled && attributeTranslation(attr.id)?.help" class="mt-0.5 block text-[11px] text-slate-500">{{ attributeTranslation(attr.id)?.help }}</span>
              <select v-if="attr.options.length" :ref="(el) => setAttributeInputRef(attr.id, el)" v-model="activeDraft.attributes[attr.id]" class="input mt-1">
                <option value="">{{ attributePlaceholder(attr) }}</option>
                <option v-for="option in attr.options" :key="option" :value="option">{{ attributeOptionLabel(attr.id, option) }}</option>
              </select>
              <input v-else :ref="(el) => setAttributeInputRef(attr.id, el)" v-model="activeDraft.attributes[attr.id]" class="input mt-1" :placeholder="attributePlaceholder(attr)" />
            </label>
          </div>
        </div>
        <div v-if="props.categoryPrecheck" class="mt-4 rounded-2xl p-3 text-sm ring-1" :class="props.categoryPrecheck.ok ? 'bg-emerald-50 text-emerald-800 ring-emerald-200' : 'bg-amber-50 text-amber-800 ring-amber-200'">
          <div class="font-semibold">{{ props.categoryPrecheck.ok ? '类目预检通过' : '类目预检需处理' }}</div>
          <ul v-if="props.categoryPrecheck.missingFields.length || props.categoryPrecheck.errors.length" class="mt-2 list-inside list-disc">
            <li v-for="item in [...props.categoryPrecheck.missingFields, ...props.categoryPrecheck.errors]" :key="item">{{ item }}</li>
          </ul>
        </div>
      </article>
    </div>

    <div v-if="!isCategoryMode" class="mt-6 grid min-w-0 gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
      <article class="min-w-0 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">发布必填资料</h3>
            <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">{{ publishReadiness }}</p>
          </div>
          <span v-if="props.precheck" class="badge-muted">{{ blockingIssues.length }} 阻断 / {{ warningIssues.length }} 提醒</span>
        </div>

        <div class="mt-4 grid gap-3 md:grid-cols-2">
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('sku', 'SKU_MISSING') ? 'text-rose-700' : 'text-slate-500'">SKU</span>
            <div class="mt-1 flex gap-2">
              <input v-model="activeDraft.sku" class="input" :class="hasIssue('sku', 'SKU_MISSING') ? 'border-rose-300 bg-rose-50' : ''" />
              <button class="btn btn-outline shrink-0 px-3" type="button" @click="generateSku">生成</button>
            </div>
          </label>
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('stock', 'STOCK_MISSING') ? 'text-rose-700' : 'text-slate-500'">库存</span>
            <div class="mt-1 flex gap-2">
              <input v-model="activeDraft.stock" class="input" :class="hasIssue('stock', 'STOCK_MISSING') ? 'border-rose-300 bg-rose-50' : ''" />
              <button class="btn btn-outline shrink-0 px-3" type="button" @click="useDefaultStock">填 10</button>
            </div>
          </label>
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('upc', 'UPC_MISSING') ? 'text-rose-700' : 'text-slate-500'">UPC / GTIN</span>
            <input v-model="activeDraft.upc" class="input mt-1" :class="hasIssue('upc', 'UPC_MISSING') ? 'border-rose-300 bg-rose-50' : ''" />
          </label>
          <label class="mt-6 flex items-center gap-2 text-sm font-semibold text-accent-700 dark:text-accent-200">
            <input v-model="activeDraft.allowGtinExemption" type="checkbox" class="size-4 rounded border-accent-300" />
            允许无 UPC 豁免
          </label>
        </div>

        <div class="mt-4 grid gap-3 md:grid-cols-4">
          <label class="block">
            <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">长 cm</span>
            <input v-model="activeDraft.packageDimensions.lengthCm" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">宽 cm</span>
            <input v-model="activeDraft.packageDimensions.widthCm" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">高 cm</span>
            <input v-model="activeDraft.packageDimensions.heightCm" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">重量 kg</span>
            <input v-model="activeDraft.packageDimensions.weightKg" class="input mt-1" />
          </label>
        </div>

        <div class="mt-4 rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div class="text-sm font-semibold text-accent-950 dark:text-white">保修条款</div>
              <div class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ warrantySummary }}</div>
            </div>
          </div>
          <div class="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_7rem_8rem]">
            <label class="block">
              <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">保修类型</span>
              <select v-model="selectedWarrantyType" class="input mt-1">
                <option v-for="option in warrantyTypeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>
            <label class="block">
              <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">时长</span>
              <input v-model="warrantyDurationValue" class="input mt-1" :disabled="selectedWarrantyType === 'none'" inputmode="decimal" />
            </label>
            <label class="block">
              <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">单位</span>
              <select v-model="warrantyDurationUnit" class="input mt-1" :disabled="selectedWarrantyType === 'none'">
                <option v-for="option in warrantyUnitOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>
          </div>
        </div>
      </article>

      <article class="min-w-0 rounded-lg border p-4" :class="props.precheck?.ok ? 'border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10' : 'border-accent-200 bg-accent-50 dark:border-dark-700 dark:bg-dark-950/70'">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">预检结果</h3>
            <p class="mt-2 text-sm" :class="props.precheck?.ok ? 'text-emerald-700 dark:text-emerald-200' : 'text-accent-600 dark:text-accent-300'">{{ props.precheck ? (props.precheck.ok ? '预检通过，可以发布。' : '预检未通过。') : '尚未执行预检。' }}</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline" :disabled="props.loading || !hasCurrentDraft" @click="emit('precheck')">上架预检</button>
            <button class="btn btn-outline" :disabled="props.loading || !hasCurrentDraft" @click="emit('previewPayload')">Payload 预览</button>
            <button class="btn btn-primary" :disabled="props.loading || !canQueuePublish" @click="emit('publish')">确认加入队列</button>
          </div>
        </div>
        <ul v-if="props.precheck?.errorItems.length" class="mt-3 space-y-2 text-sm text-rose-700">
          <li v-for="issue in props.precheck.errorItems" :key="`${issue.code}-${issue.field}-${issue.message}`" class="rounded-lg bg-white/70 p-3 ring-1 ring-rose-100 dark:bg-rose-500/10 dark:ring-rose-500/20">
            <div class="font-semibold">{{ issueTitle(issue) }}</div>
            <div v-if="issue.nextAction" class="mt-1 text-rose-600">{{ issue.nextAction }}</div>
          </li>
        </ul>
        <ul v-else-if="props.precheck?.errors.length" class="mt-3 list-inside list-disc text-sm text-rose-700"><li v-for="err in props.precheck.errors" :key="err">{{ err }}</li></ul>
        <ul v-if="props.precheck?.warningItems.length" class="mt-3 space-y-2 text-sm text-amber-700">
          <li v-for="issue in props.precheck.warningItems" :key="`${issue.code}-${issue.field}-${issue.message}`" class="rounded-lg bg-white/70 p-3 ring-1 ring-amber-100 dark:bg-amber-500/10 dark:ring-amber-500/20">
            <div class="font-semibold">{{ issueTitle(issue) }}</div>
            <div v-if="issue.nextAction" class="mt-1 text-amber-600">{{ issue.nextAction }}</div>
          </li>
        </ul>
        <ul v-else-if="props.precheck?.warnings.length" class="mt-3 list-inside list-disc text-sm text-amber-700"><li v-for="warning in props.precheck.warnings" :key="warning">{{ warning }}</li></ul>
      </article>

      <article class="min-w-0 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
        <h3 class="font-semibold text-accent-950 dark:text-white">Payload 预览</h3>
        <pre class="mt-3 max-h-80 w-full max-w-full overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100">{{ props.payloadPreview ? JSON.stringify(props.payloadPreview, null, 2) : '尚未生成 payload。' }}</pre>
      </article>
    </div>
  </section>
</template>

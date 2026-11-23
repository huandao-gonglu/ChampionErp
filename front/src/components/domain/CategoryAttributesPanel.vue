<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { ComponentPublicInstance } from 'vue'
import {
  PhArrowRight,
  PhArrowsClockwise,
  PhCaretDown,
  PhCaretUp,
  PhCheck,
  PhCheckCircle,
  PhLightning,
  PhMagnifyingGlass,
  PhPencilSimple,
  PhShieldCheck,
  PhWarningCircle,
  PhX,
} from '@phosphor-icons/vue'
import { vDesktop } from '@/utils/desktopHandoffDirective'
import { modelOperationReason } from '@/utils/desktop'
import CategoryAttributeField from './CategoryAttributeField.vue'
import DraftBarcodeFields from './DraftBarcodeFields.vue'
import CategoryPrecheckPanel from '@/components/domain/CategoryPrecheckPanel.vue'
import { isMercadoLibreCbtTarget } from '@/utils/mercadolibreGlobalSelling'
import { isBarcodeIdentifier } from '@/utils/barcodePolicy'
import type {
  CategoryAttributeDefinition,
  CategoryAttributeTranslations,
  CategoryAttributeValue,
  CategoryPrecheckResult,
  CategoryResultTranslations,
  CategorySearchResult,
  CategorySelection,
  DraftDetail,
  DraftProductContext,
  MarketplaceOption,
  MarketplaceTargetSite,
  PrecheckIssue,
  PublishPrecheck,
  UnknownRecord,
} from '@/types/workflow'

const props = withDefaults(defineProps<{
  draft: DraftDetail
  productContext: DraftProductContext
  target: MarketplaceTargetSite
  platformOptions: MarketplaceOption[]
  category: CategorySelection | null
  categoryQuery: string
  categoryTranslatedQuery?: string
  categoryResults: CategorySearchResult[]
  skuScope?: boolean
  /** SKU 编辑模式下显式传入继承的公共属性，彻底消除 Proxy。 */
  inheritedAttributes?: Record<string, CategoryAttributeValue>
  /** SKU 编辑模式下显式传入当前 SKU 单独覆盖的属性切片。 */
  overrideAttributes?: Record<string, CategoryAttributeValue>
  categoryAutoMatchProductName?: string
  categoryAutoMatchTargetError?: string
  categoryAttributeTranslations?: CategoryAttributeTranslations
  categoryAttributeTranslationsSource?: string
  categoryAttributeTranslating?: boolean
  categoryAttributeLoading?: boolean
  categoryAttributeError?: string
  categoryResultTranslations?: CategoryResultTranslations
  categoryResultTranslationsSource?: string
  categoryResultTranslating?: boolean
  categoryPrecheck: CategoryPrecheckResult | null
  precheck: PublishPrecheck | null
  loading: boolean
}>(), {
  categoryAutoMatchProductName: '',
  categoryTranslatedQuery: '',
  skuScope: false,
  inheritedAttributes: undefined,
  overrideAttributes: undefined,
  categoryAutoMatchTargetError: '',
  categoryAttributeTranslations: () => ({}),
  categoryAttributeTranslationsSource: '',
  categoryAttributeTranslating: false,
  categoryAttributeLoading: false,
  categoryAttributeError: '',
  categoryResultTranslations: () => ({}),
  categoryResultTranslationsSource: '',
  categoryResultTranslating: false,
})

const emit = defineEmits<{
  updateCategoryQuery: [value: string]
  searchCategory: []
  suggestCategory: []
  selectCategory: [item: CategorySearchResult]
  applyCategory: [forceRefresh?: boolean]
  updateAttribute: [attrId: string, value: CategoryAttributeValue | undefined]
  clearAttributeOverride: [attrId: string]
  invalidateCategoryPrecheck: []
  categoryPrecheck: []
  openSkuTab: []
}>()

const targetIdentityKey = computed(() => targetKey(props.target))
const usesSharedMercadoLibreCbtCategory = computed(() => isMercadoLibreCbtTarget(props.target))
const sharedMercadoLibreMarketLabels = computed(() => {
  if (!usesSharedMercadoLibreCbtCategory.value) return []
  const platform = props.platformOptions.find((item) => item.key === 'mercadolibre')
  const seen = new Set<string>()
  return (props.target.sitesToSell || []).flatMap((market) => {
    const siteId = String(market.siteId || '').trim().toUpperCase()
    if (!siteId || siteId === 'CBT' || seen.has(siteId)) return []
    seen.add(siteId)
    const site = platform?.sites.find((item) => (
      item.code.toUpperCase() === siteId || item.key.toUpperCase() === siteId
    ))
    return [site ? `${site.label}（${siteId}）` : siteId]
  })
})
const sharedMercadoLibreMarketSummary = computed(() => (
  sharedMercadoLibreMarketLabels.value.length
    ? sharedMercadoLibreMarketLabels.value.join('、')
    : '当前 CBT 草稿的全部销售市场'
))

const hasCurrentDraft = computed(() => Boolean(props.draft.draftId))
const draftValidationErrors = computed(() => (
  Array.isArray(props.draft.validationErrors) ? props.draft.validationErrors : []
))
const hasSelectedCategory = computed(() => Boolean(String(props.draft.categoryId || '').trim()))
const hasLoadedCategoryDefinition = computed(() => Boolean(
  props.category
  && props.category.categoryId === String(props.draft.categoryId || '').trim()
  && props.category.platform === props.target.platform
  && props.category.fetchedAt,
))
const categoryAttributeState = computed<'empty' | 'idle' | 'loading' | 'ready' | 'error'>(() => {
  if (!hasSelectedCategory.value) return 'empty'
  if (props.categoryAttributeLoading) return 'loading'
  if (hasLoadedCategoryDefinition.value) return 'ready'
  return props.categoryAttributeError ? 'error' : 'idle'
})

// 方案 A 状态自适应：未选类目默认展开检索面板；已选类目默认收起，把 100% 宽度留给属性表单。
const pickerOpen = ref(Boolean(
  !hasSelectedCategory.value
  || props.categoryResults.length
  || props.categoryTranslatedQuery
  || props.categoryAutoMatchTargetError,
))
const manualEditOpen = ref(false)
const categorySearchInputRef = ref<HTMLInputElement | null>(null)
const showCategoryPicker = computed(() => !props.skuScope && (!hasSelectedCategory.value || pickerOpen.value))

// 当新候选/翻译词/报错返回时自动展开检索面板，便于立即核对与点选。
watch(
  () => [props.categoryResults, props.categoryTranslatedQuery, props.categoryAutoMatchTargetError] as const,
  ([results, translated, err], [prevResults, prevTranslated, prevErr]) => {
    if (
      (results !== prevResults && results.length > 0)
      || (translated && translated !== prevTranslated)
      || (err && err !== prevErr)
    ) {
      pickerOpen.value = true
    }
  },
)

async function toggleCategoryPicker() {
  pickerOpen.value = !pickerOpen.value
  if (pickerOpen.value) {
    await nextTick()
    categorySearchInputRef.value?.focus()
  }
}

function handleSelectCategory(item: CategorySearchResult) {
  if (isCategoryForbidden(item)) return
  emit('selectCategory', item)
  pickerOpen.value = false
  manualEditOpen.value = false
}

const effectiveAttributesMap = computed<Record<string, CategoryAttributeValue>>(() => {
  if (props.skuScope && (props.inheritedAttributes || props.overrideAttributes)) {
    return {
      ...(props.inheritedAttributes || {}),
      ...(props.overrideAttributes || {}),
    }
  }
  return props.draft.attributes || {}
})

function hasAttributeValue(value: CategoryAttributeValue | undefined): boolean {
  if (value == null) return false
  if (typeof value === 'string') return Boolean(value.trim())
  if ('values' in value) return Array.isArray(value.values) && value.values.length > 0
  return Boolean(String(value.value ?? '').trim())
}

function isAttributeOverridden(attrId: string): boolean {
  if (!props.skuScope || !props.overrideAttributes) return false
  return Object.prototype.hasOwnProperty.call(props.overrideAttributes, attrId)
}

function isAttributeInherited(attrId: string): boolean {
  if (!props.skuScope || isAttributeOverridden(attrId) || !props.inheritedAttributes) return false
  return hasAttributeValue(props.inheritedAttributes[attrId])
}

const filledAttributes = computed(() => {
  if (props.skuScope || categoryAttributeState.value === 'ready' || !hasSelectedCategory.value) return []
  if (props.category && props.category.categoryId !== String(props.draft.categoryId || '').trim()) return []
  return Object.entries(effectiveAttributesMap.value).flatMap(([id, value]) => {
    let text: string
    if (typeof value === 'string') text = value
    else if ('values' in value) text = value.values.map(option => option.value).join('、')
    else text = value.value.trim() ? [value.value, value.unit].filter(Boolean).join(' ') : ''
    return text.trim() ? [{ id, text }] : []
  })
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

function isManagedAttribute(attr: CategoryAttributeDefinition): boolean {
  return attr.managedBy === 'listing_grouping' || attr.managedBy === 'draft_derived'
}

const systemGroupingAttributes = computed(() => attributeFields.value.filter(attr => attr.managedBy === 'listing_grouping'))
const systemGroupingDisplay = computed(() => {
  if (props.draft.grouping.mode === 'separate') {
    return systemGroupingAttributes.value.some(attr => attr.required)
      ? '独立刊登：系统为每个 SKU 填写不同的分组值。'
      : '独立刊登：系统不提交分组名称。'
  }
  const name = (props.draft.grouping.name || props.draft.title).trim()
  return name ? `组合展示：${name}` : '尚未设置组合名称，请到 SKU 页填写平台组名。'
})
const skuVariantCount = computed(() => (
  props.skuScope || props.draft.skuItems.filter(row => row.selected).length < 2
    ? 0
    : attributeFields.value.filter(attr => attr.variationRole === 'variant').length
))
const belongsToSku = (attr: CategoryAttributeDefinition) => skuVariantCount.value > 0 && attr.variationRole === 'variant'

const requiredAttributeFields = computed(() => attributeFields.value.filter(
  (attr) => !(props.target.platform === 'mercadolibre' && attr.id === 'EMPTY_GTIN_REASON')
    && attr.required && !belongsToSku(attr) && !attr.readOnly && !isManagedAttribute(attr),
))
const optionalAttributeFields = computed(() => attributeFields.value.filter(
  (attr) => !(props.target.platform === 'mercadolibre' && attr.id === 'EMPTY_GTIN_REASON')
    && !isBarcodeIdentifier(attr.id) && !attr.required && !belongsToSku(attr) && !attr.readOnly && !isManagedAttribute(attr),
))
const attributeFieldById = computed(() => new Map(attributeFields.value.map((attr) => [attr.id, attr])))

// 聚焦优先：有必填时默认展开必填、折叠选填；当必填为 0 时自动展开可选属性，避免首屏留空。
const showRequiredAttributes = ref(true)
const showOptionalAttributes = ref(props.skuScope || requiredAttributeFields.value.length === 0)
type AttributeFilterMode = 'all' | 'pending' | 'required' | 'optional'
const attributeFilter = ref<AttributeFilterMode>('all')
const attributeSearchQuery = ref('')
const attributeInputRefs = ref<Record<string, HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null>>({})

const blockingIssues = computed(() => props.precheck?.errorItems || [])
const warningIssues = computed(() => props.precheck?.warningItems || [])
const pendingReviewAttributeIds = computed(() => {
  const ids = new Set<string>()
  const requiredIds = new Set(requiredAttributeFields.value.map((attr) => attr.id))
  for (const item of draftValidationErrors.value) {
    collectReviewAttributeId(item, ids)
  }
  for (const issue of [...blockingIssues.value, ...warningIssues.value]) {
    collectReviewAttributeId(issue, ids)
  }
  return [...ids].filter((attrId) => requiredIds.has(attrId)).sort()
})

const completedRequiredCount = computed(() => (
  requiredAttributeFields.value.filter((attr) => hasAttributeValue(effectiveAttributesMap.value[attr.id])).length
))
const completedOptionalCount = computed(() => (
  optionalAttributeFields.value.filter((attr) => hasAttributeValue(effectiveAttributesMap.value[attr.id])).length
))
const pendingAttributeCount = computed(() => (
  [...requiredAttributeFields.value, ...optionalAttributeFields.value].filter((attr) => isPendingAttribute(attr)).length
))
const totalEditableAttributeCount = computed(() => (
  requiredAttributeFields.value.length + optionalAttributeFields.value.length
))

function matchesAttributeQuery(attr: CategoryAttributeDefinition): boolean {
  const needle = attributeSearchQuery.value.trim().toLocaleLowerCase()
  if (!needle) return true
  const zh = String(attr.nameZh || props.categoryAttributeTranslations?.[attr.id]?.label || '').toLocaleLowerCase()
  return attr.name.toLocaleLowerCase().includes(needle)
    || attr.id.toLocaleLowerCase().includes(needle)
    || zh.includes(needle)
}

function isPendingAttribute(attr: CategoryAttributeDefinition): boolean {
  return isMissingAttribute(attr.id)
    || pendingReviewAttributeIds.value.includes(attr.id)
    || (attr.required && !hasAttributeValue(effectiveAttributesMap.value[attr.id]))
}

const filteredRequiredFields = computed(() => {
  if (attributeFilter.value === 'optional') return []
  return requiredAttributeFields.value.filter((attr) => (
    matchesAttributeQuery(attr)
    && (attributeFilter.value !== 'pending' || isPendingAttribute(attr))
  ))
})

const filteredOptionalFields = computed(() => {
  if (attributeFilter.value === 'required') return []
  return optionalAttributeFields.value.filter((attr) => (
    matchesAttributeQuery(attr)
    && (attributeFilter.value !== 'pending' || isPendingAttribute(attr))
  ))
})

const cacheSourceBadge = computed(() => {
  const src = String(props.category?.cacheSource || '').trim()
  if (src === 'cache') return '本地缓存'
  if (src === 'stale') return '离线缓存'
  if (src === 'live') return '实时刷新'
  return ''
})

// 路径拆分：高亮叶子节点，中俄/中西双语完整呈现。
function splitPathDisplay(rawPath: string): { parent: string; leaf: string } {
  const normalized = String(rawPath || '').trim()
  if (!normalized) return { parent: '', leaf: '' }
  const parts = normalized.split(/\s*\/\s*/).filter(Boolean)
  if (parts.length <= 1) return { parent: '', leaf: normalized }
  return {
    parent: `${parts.slice(0, -1).join(' / ')} / `,
    leaf: parts[parts.length - 1],
  }
}

const originalCategoryPath = computed(() => (
  String(props.draft.categoryPath || props.category?.categoryPath || '').trim()
))
const primaryCategoryPath = computed(() => (
  String(props.category?.categoryPathZh || originalCategoryPath.value || '').trim()
))
const primaryCategoryParts = computed(() => splitPathDisplay(primaryCategoryPath.value))
const showOriginalPathSubline = computed(() => (
  Boolean(originalCategoryPath.value && originalCategoryPath.value !== primaryCategoryPath.value)
))

const targetPlatformInfo = computed(() => {
  const platform = props.platformOptions.find((item) => item.key === props.target.platform)
  const site = platform?.sites.find((item) => item.code.toLowerCase() === String(props.target.site || '').toLowerCase())
  const platformLabel = platform?.label || props.target.platform || '目标平台'
  const siteLabel = site?.label || props.target.site || ''
  const language = props.target.language || site?.language || ''
  const currency = props.target.listingCurrency || ''
  const metaParts = [props.target.site || site?.code, language, currency].filter(Boolean)
  return {
    title: siteLabel ? `${platformLabel} · ${siteLabel}` : platformLabel,
    meta: metaParts.join(' · '),
  }
})

function attributeTranslation(attrId: string) {
  return props.categoryAttributeTranslations?.[attrId] || null
}

function attributeDisplayLabel(attr: CategoryAttributeDefinition) {
  return attr.nameZh || attributeTranslation(attr.id)?.label || attr.name || attr.id
}

function categoryResultDisplay(item: CategorySearchResult) {
  const zhFull = String(props.categoryResultTranslations?.[item.id] || item.pathZh || item.nameZh || item.name || item.id).trim()
  const { parent, leaf } = splitPathDisplay(zhFull)
  return {
    leaf: leaf || zhFull,
    parent: parent.replace(/\s*\/\s*$/, ''),
    orig: String(item.path || item.name || item.id).trim(),
  }
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

function invalidateEditedAttribute(attrId: string) {
  if (Array.isArray(props.draft.validationErrors)) {
    props.draft.validationErrors = props.draft.validationErrors.filter((item) => {
      const ids = new Set<string>()
      collectReviewAttributeId(item, ids)
      return !ids.has(attrId)
    })
  }
  emit('invalidateCategoryPrecheck')
}

function handleAttributeUpdate(attrId: string, value: CategoryAttributeValue | undefined) {
  if (props.skuScope && (props.inheritedAttributes || props.overrideAttributes)) {
    emit('updateAttribute', attrId, value)
    return
  }
  if (!props.draft.attributes) props.draft.attributes = {}
  if (value === undefined) {
    delete props.draft.attributes[attrId]
  } else {
    props.draft.attributes[attrId] = value
  }
  invalidateEditedAttribute(attrId)
}

function handleClearOverride(attrId: string) {
  emit('clearAttributeOverride', attrId)
}

function registerAttributeInputRef(attrId: string, el: Element | ComponentPublicInstance | null) {
  const node = el && '$el' in el ? el.$el : el
  attributeInputRefs.value[attrId] = node instanceof HTMLInputElement || node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement ? node : null
}

function reviewAttributeLabel(attrId: string) {
  const attr = attributeFieldById.value.get(attrId)
  return attr ? attributeDisplayLabel(attr) : attrId
}

function reviewAttributeSection(attrId: string) {
  if (requiredAttributeFields.value.some((attr) => attr.id === attrId)) return 'required'
  if (optionalAttributeFields.value.some((attr) => attr.id === attrId)) return 'optional'
  return ''
}

async function focusAttribute(attrId: string) {
  const section = reviewAttributeSection(attrId)
  if (!section) return
  attributeFilter.value = 'all'
  attributeSearchQuery.value = ''
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

function resetAttributeFilters() {
  attributeFilter.value = 'all'
  attributeSearchQuery.value = ''
}

watch(
  () => [targetIdentityKey.value, props.category?.categoryId || '', props.category?.fetchedAt || ''],
  () => {
    showRequiredAttributes.value = true
    showOptionalAttributes.value = props.skuScope || requiredAttributeFields.value.length === 0
    attributeFilter.value = 'all'
    attributeSearchQuery.value = ''
    attributeInputRefs.value = {}
  },
)

watch(attributeSearchQuery, (query) => {
  if (query.trim()) {
    showRequiredAttributes.value = true
    showOptionalAttributes.value = true
  }
})

watch(attributeFilter, (mode) => {
  if (mode === 'optional') showOptionalAttributes.value = true
  if (mode === 'required' || mode === 'pending') showRequiredAttributes.value = true
})

function targetKey(target: MarketplaceTargetSite) {
  return `${String(target.platform || '').trim().toLowerCase()}:${String(target.site || '').trim().toLowerCase()}`
}

function isCategoryForbidden(item: CategorySearchResult) {
  return Boolean(item.forbidden || item.raw.forbidden)
}

function categoryForbiddenReason(item: CategorySearchResult) {
  return String(item.forbiddenReason || item.raw.forbidden_reason || '').trim()
}
</script>

<template>
  <section class="min-w-0 space-y-3.5">
    <!-- ========== 1. 紧凑类目状态条（常规模式） ========== -->
    <div
      v-if="!props.skuScope"
      class="rounded-2xl border border-accent-200/90 bg-accent-50/70 px-4 py-3 dark:border-dark-700 dark:bg-dark-950/60"
    >
      <div class="flex flex-wrap items-center justify-between gap-3">
        <!-- 左侧：平台上下文 + 双语类目路径 -->
        <div class="min-w-0 flex-1 space-y-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-xs font-semibold text-accent-800 dark:text-accent-100">
              {{ targetPlatformInfo.title }}
            </span>
            <span v-if="targetPlatformInfo.meta" class="text-[11px] text-accent-400 dark:text-accent-500">
              {{ targetPlatformInfo.meta }}
            </span>
            <span
              v-if="hasSelectedCategory"
              class="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30"
            >
              <PhCheck :size="11" weight="bold" aria-hidden="true" />已选类目
            </span>
            <span
              v-else
              class="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700 ring-1 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30"
            >
              待选类目
            </span>
            <span
              v-if="hasSelectedCategory"
              class="inline-flex items-center rounded-md border border-accent-200 bg-white px-1.5 py-0.5 font-mono text-[11px] text-accent-600 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-300"
              data-testid="selected-category-id"
            >
              ID {{ props.draft.categoryId }}
            </span>
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-accent-500 transition hover:bg-accent-200/60 hover:text-accent-800 dark:text-accent-400 dark:hover:bg-dark-800 dark:hover:text-accent-100"
              data-testid="toggle-manual-category-edit"
              @click="manualEditOpen = !manualEditOpen"
            >
              <PhPencilSimple :size="11" aria-hidden="true" />
              {{ manualEditOpen ? '收起改 ID' : (hasSelectedCategory ? '改 ID' : '手动填 ID') }}
            </button>
            <span
              v-if="cacheSourceBadge"
              class="rounded-full bg-accent-100 px-2 py-0.5 text-[10.5px] font-medium text-accent-500 dark:bg-dark-800 dark:text-accent-400"
            >
              {{ cacheSourceBadge }}
            </span>
            <span
              v-if="props.categoryAttributeTranslating || props.categoryResultTranslating"
              class="text-[11px] text-primary-600 dark:text-primary-400"
            >
              正在补全中文参考…
            </span>
          </div>

          <div v-if="hasSelectedCategory" class="space-y-0.5">
            <div class="text-sm font-semibold leading-snug text-accent-950 dark:text-white" data-testid="selected-category-path">
              <span v-if="primaryCategoryParts.parent" class="font-medium text-accent-700 dark:text-accent-200">{{ primaryCategoryParts.parent }}</span>
              <span class="text-primary-700 dark:text-primary-400">{{ primaryCategoryParts.leaf || props.draft.categoryId }}</span>
            </div>
            <p v-if="showOriginalPathSubline" class="text-xs text-accent-500 dark:text-accent-400">
              {{ originalCategoryPath }}
            </p>
          </div>
          <p v-else class="text-xs text-accent-500 dark:text-accent-400">
            {{ usesSharedMercadoLibreCbtCategory
              ? '请检索或使用 AI 匹配兼容全部已选销售市场的共享 CBT 类目。'
              : '请在下方输入中文、原文或 ID 搜索类目，或点击右侧「AI 匹配类目」。' }}
          </p>
        </div>

        <!-- 右侧：高频快捷动作 -->
        <div class="flex shrink-0 flex-wrap items-center gap-1.5">
          <button
            v-if="hasSelectedCategory"
            type="button"
            class="btn btn-outline !rounded-lg !px-2.5 !py-1.5 text-xs"
            :class="pickerOpen ? '!border-primary-500/50 !bg-primary-50/70 !text-primary-700 dark:!bg-primary-950/30 dark:!text-primary-300' : ''"
            data-testid="toggle-category-picker"
            @click="toggleCategoryPicker"
          >
            <PhMagnifyingGlass :size="13" aria-hidden="true" />
            {{ pickerOpen ? '收起选类目' : '更换类目' }}
          </button>
          <button
            v-desktop="'category.product_match'"
            type="button"
            class="btn !rounded-lg !px-2.5 !py-1.5 text-xs"
            :class="hasSelectedCategory ? 'btn-outline' : 'btn-primary'"
            :disabled="!!modelOperationReason('category.product_match') || props.loading || !hasCurrentDraft"
            @click="emit('suggestCategory')"
          >
            <PhLightning :size="13" aria-hidden="true" />
            {{ hasSelectedCategory ? 'AI 重匹配' : 'AI 匹配类目' }}
          </button>
          <button
            v-if="hasSelectedCategory"
            type="button"
            class="btn btn-outline !rounded-lg !px-2.5 !py-1.5 text-xs"
            :disabled="props.loading || props.categoryAttributeLoading || !hasCurrentDraft"
            @click="emit('applyCategory', hasLoadedCategoryDefinition)"
          >
            <PhArrowsClockwise :size="13" :class="{ 'animate-spin': props.categoryAttributeLoading }" aria-hidden="true" />
            {{ hasLoadedCategoryDefinition ? '刷新属性' : '加载属性' }}
          </button>
          <button
            v-if="hasSelectedCategory"
            type="button"
            class="btn btn-outline !rounded-lg !px-2.5 !py-1.5 text-xs"
            :disabled="props.loading || props.category?.categoryId !== String(props.draft.categoryId || '').trim()"
            @click="emit('categoryPrecheck')"
          >
            <PhShieldCheck :size="13" aria-hidden="true" />
            类目预检
          </button>
        </div>
      </div>

      <p
        v-if="modelOperationReason('category.product_match')"
        role="status"
        class="mt-2 text-xs text-accent-500 dark:text-accent-400"
      >
        {{ modelOperationReason('category.product_match') }}
      </p>

      <!-- 按需展开的手动编辑类目 ID / 路径表单 -->
      <div
        v-if="manualEditOpen"
        data-testid="manual-category-edit-form"
        class="mt-3 grid gap-2.5 border-t border-accent-200/80 pt-3 dark:border-dark-700 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)_auto]"
      >
        <label class="block">
          <span class="text-[11px] font-semibold text-accent-500 dark:text-accent-400">
            {{ usesSharedMercadoLibreCbtCategory ? '共享 CBT 类目 ID' : '类目 ID' }}
          </span>
          <input
            v-model="props.draft.categoryId"
            class="input mt-1 !rounded-lg !py-1.5 text-xs"
            :placeholder="usesSharedMercadoLibreCbtCategory ? '请输入 CBT 类目 ID' : '例如 MLM12345'"
            @input="emit('invalidateCategoryPrecheck')"
          />
        </label>
        <label class="block">
          <span class="text-[11px] font-semibold text-accent-500 dark:text-accent-400">
            {{ usesSharedMercadoLibreCbtCategory ? '共享 CBT 类目路径' : '类目路径' }}
          </span>
          <input
            v-model="props.draft.categoryPath"
            class="input mt-1 !rounded-lg !py-1.5 text-xs"
          />
        </label>
        <div class="flex items-end gap-1.5">
          <button
            type="button"
            class="btn btn-primary !rounded-lg !px-3 !py-1.5 text-xs"
            :disabled="props.loading || props.categoryAttributeLoading || !hasCurrentDraft || !hasSelectedCategory"
            @click="emit('applyCategory', false); manualEditOpen = false"
          >
            读取该类目属性
          </button>
        </div>
      </div>
    </div>

    <!-- SKU 模式精简头部 -->
    <div
      v-else
      class="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-accent-200/80 bg-accent-50/60 px-3.5 py-2.5 dark:border-dark-700 dark:bg-dark-950/50"
    >
      <div class="min-w-0">
        <div class="text-xs font-semibold text-accent-900 dark:text-white">此 SKU 的平台差异属性</div>
        <p class="mt-0.5 truncate text-xs text-accent-500 dark:text-accent-400">
          {{ props.category?.categoryPathZh || props.draft.categoryPath }}（{{ props.draft.categoryId }}）
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <span v-if="props.categoryAttributeTranslating" class="text-[11px] text-primary-600 dark:text-primary-400">正在补全中文参考…</span>
        <button
          type="button"
          class="btn btn-outline !rounded-lg !px-2.5 !py-1.5 text-xs"
          :disabled="props.loading || props.categoryAttributeLoading || !hasCurrentDraft || !hasSelectedCategory"
          @click="emit('applyCategory', hasLoadedCategoryDefinition)"
        >
          <PhArrowsClockwise :size="13" :class="{ 'animate-spin': props.categoryAttributeLoading }" aria-hidden="true" />
          {{ hasLoadedCategoryDefinition ? '刷新平台属性' : '加载平台属性' }}
        </button>
      </div>
    </div>

    <!-- Mercado CBT 共享类目提醒 -->
    <div
      v-if="!props.skuScope && usesSharedMercadoLibreCbtCategory"
      data-testid="mercadolibre-shared-category-notice"
      class="rounded-xl border border-blue-200/90 bg-blue-50/80 px-3.5 py-2.5 text-xs text-blue-800 dark:border-blue-900/60 dark:bg-blue-950/30 dark:text-blue-200"
    >
      <span class="font-semibold">所有已选销售市场共用一个 CBT 类目</span>
      <span class="mx-1.5 opacity-50">·</span>
      <span>适用市场：{{ sharedMercadoLibreMarketSummary }}。Mercado Libre 会自动映射各市场的本地类目，不能为每个市场分别设置类目 ID。</span>
    </div>

    <!-- ========== 2. 按需展开的类目检索与候选面板（融入方案 B 卡片质感） ========== -->
    <div
      v-if="showCategoryPicker"
      data-testid="category-picker-panel"
      class="rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-900/80"
    >
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="text-sm font-semibold text-accent-950 dark:text-white">
            类目检索与候选
            <span v-if="props.categoryResults.length" class="ml-1 text-xs font-normal text-accent-400">({{ props.categoryResults.length }})</span>
          </h3>
          <span class="rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-medium text-primary-700 dark:bg-primary-950/40 dark:text-primary-300">
            支持中文 / 平台原文 / 类目 ID
          </span>
          <span
            v-if="props.categoryAutoMatchProductName"
            class="rounded-full bg-accent-100 px-2 py-0.5 text-[11px] text-accent-600 dark:bg-dark-800 dark:text-accent-300"
          >
            AI 识别主体：{{ props.categoryAutoMatchProductName }}
          </span>
        </div>
        <button
          v-if="hasSelectedCategory"
          type="button"
          class="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-accent-500 transition hover:bg-accent-100 hover:text-accent-800 dark:text-accent-400 dark:hover:bg-dark-800 dark:hover:text-white"
          @click="pickerOpen = false"
        >
          <PhX :size="13" aria-hidden="true" />收起
        </button>
      </div>

      <div class="mt-3 flex gap-2">
        <div class="relative min-w-0 flex-1">
          <input
            ref="categorySearchInputRef"
            :value="props.categoryQuery"
            class="input !rounded-xl !py-2 text-sm"
            placeholder="输入中文、平台原文关键词或类目 ID"
            @input="emit('updateCategoryQuery', ($event.target as HTMLInputElement).value)"
            @keyup.enter="emit('searchCategory')"
          />
        </div>
        <button
          type="button"
          class="btn btn-primary shrink-0 !rounded-xl !px-4 !py-2 text-xs"
          :disabled="props.loading || !hasCurrentDraft"
          @click="emit('searchCategory')"
        >
          <PhMagnifyingGlass :size="14" aria-hidden="true" />搜索
        </button>
      </div>

      <!-- 动态检索提示（仅在触发时展示，无静态废话） -->
      <div
        v-if="props.categoryTranslatedQuery || props.categoryAutoMatchTargetError || props.loading"
        class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
      >
        <span
          v-if="props.categoryTranslatedQuery"
          class="font-medium text-primary-700 dark:text-primary-300"
          data-testid="category-translated-query-hint"
        >
          已自动转换为「{{ props.categoryTranslatedQuery }}」检索
        </span>
        <span v-if="props.loading" class="text-accent-500 dark:text-accent-400">
          正在检索当前平台类目…
        </span>
        <span
          v-if="props.categoryAutoMatchTargetError"
          class="inline-flex items-center gap-1 text-amber-700 dark:text-amber-300"
        >
          <PhWarningCircle :size="13" aria-hidden="true" />
          {{ props.categoryAutoMatchTargetError }}
        </span>
      </div>

      <!-- 候选网格：双列高密度卡片，标出当前已选 / AI 推荐 / 禁售拦截 -->
      <div v-if="props.categoryResults.length" class="mt-3 grid max-h-80 gap-2.5 overflow-y-auto pr-0.5 md:grid-cols-2">
        <button
          v-for="item in props.categoryResults"
          :key="item.id"
          type="button"
          :disabled="isCategoryForbidden(item)"
          :data-forbidden="isCategoryForbidden(item) ? 'true' : undefined"
          class="flex flex-col gap-1 rounded-xl border p-3 text-left transition"
          :class="isCategoryForbidden(item)
            ? 'cursor-not-allowed border-rose-200 bg-rose-50/70 opacity-80 dark:border-rose-900/60 dark:bg-rose-950/25'
            : item.id === String(props.draft.categoryId || '').trim()
              ? 'border-primary-500 bg-primary-50/60 dark:border-primary-500/80 dark:bg-primary-950/30'
              : 'border-accent-200 bg-accent-50/50 hover:border-primary-400 hover:bg-primary-50/40 dark:border-dark-700 dark:bg-dark-950/60 dark:hover:border-primary-500/60 dark:hover:bg-dark-800/80'"
          @click="handleSelectCategory(item)"
        >
          <div class="flex items-start justify-between gap-2">
            <span class="text-[13px] font-semibold text-accent-950 dark:text-white">
              {{ categoryResultDisplay(item).leaf }}
            </span>
            <span v-if="isCategoryForbidden(item)" class="badge-danger shrink-0 !py-0.5 text-[10.5px]">禁售</span>
            <span
              v-else-if="item.id === String(props.draft.categoryId || '').trim()"
              class="badge-success shrink-0 !py-0.5 text-[10.5px]"
            >
              当前已选
            </span>
            <span v-else-if="item.raw.aiRecommended" class="badge-info shrink-0 !py-0.5 text-[10.5px]">AI 推荐</span>
          </div>
          <p v-if="categoryResultDisplay(item).parent" class="text-xs text-accent-600 dark:text-accent-300">
            {{ categoryResultDisplay(item).parent }}
          </p>
          <p class="truncate text-[11px] text-accent-400 dark:text-accent-500" :title="categoryResultDisplay(item).orig">
            {{ categoryResultDisplay(item).orig }}
          </p>
          <p v-if="isCategoryForbidden(item)" class="text-xs font-medium text-rose-700 dark:text-rose-300">
            {{ categoryForbiddenReason(item) || '该类目在禁售名单中，不能选择。' }}
          </p>
          <p
            v-for="(reason, index) in (item.raw.matchEvidence as string[] || [])"
            :key="index"
            class="text-[11px] text-accent-500 dark:text-accent-400"
          >
            {{ reason }}
          </p>
          <div class="mt-auto flex items-center justify-between pt-1 text-[11px] text-accent-400 dark:text-accent-500">
            <span class="font-mono">ID {{ item.id }}</span>
            <span v-if="item.raw.site || item.raw.source">{{ item.raw.site || '' }}{{ item.raw.source ? ` / ${item.raw.source}` : '' }}</span>
          </div>
        </button>
      </div>
      <p
        v-else
        class="mt-3 rounded-xl border border-dashed border-accent-200 px-3.5 py-2.5 text-center text-xs text-accent-500 dark:border-dark-700 dark:text-accent-400"
      >
        输入中文或平台原文关键词按回车搜索，或点击上方「{{ hasSelectedCategory ? 'AI 重匹配' : 'AI 匹配类目' }}」获取推荐。
      </p>
    </div>

    <!-- ========== 3. 属性加载中 / 失败 / 未加载回退 ========== -->
    <div
      v-if="categoryAttributeState === 'empty' && props.skuScope"
      class="rounded-xl border border-dashed border-accent-300 p-4 text-sm text-accent-500 dark:border-dark-600 dark:text-accent-400"
    >
      请先在「类目/属性」页选择类目。
    </div>
    <div
      v-else-if="categoryAttributeState === 'loading'"
      class="flex items-center gap-2.5 rounded-xl border border-primary-200 bg-primary-50/70 px-4 py-3 text-sm text-primary-700 dark:border-primary-500/30 dark:bg-primary-950/25 dark:text-primary-300"
    >
      <div class="size-4 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600 dark:border-primary-800 dark:border-t-primary-400" />
      正在读取平台属性定义…
    </div>
    <div
      v-else-if="categoryAttributeState === 'error'"
      class="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/25 dark:text-amber-300"
    >
      <div class="font-semibold">平台属性定义未加载</div>
      <p class="mt-1">{{ props.categoryAttributeError }}</p>
      <button
        class="btn btn-outline mt-3 !rounded-lg !px-3 !py-1.5 text-xs"
        :disabled="props.loading || props.categoryAttributeLoading"
        type="button"
        @click="emit('applyCategory', false)"
      >
        重新加载
      </button>
    </div>

    <!-- 未拿到 Schema 时的已填属性只读回显 -->
    <div v-if="filledAttributes.length" class="rounded-xl border border-accent-200 bg-accent-50/50 p-3.5 dark:border-dark-700 dark:bg-dark-950/50">
      <h4 class="text-xs font-semibold text-accent-700 dark:text-accent-200">已填属性（只读预览）</h4>
      <dl class="mt-2 grid gap-1.5 text-xs sm:grid-cols-2">
        <div v-for="attribute in filledAttributes" :key="attribute.id" class="flex gap-2">
          <dt class="shrink-0 font-mono text-accent-500">{{ attribute.id }}:</dt>
          <dd class="min-w-0 whitespace-pre-wrap break-words text-accent-800 dark:text-accent-200">{{ attribute.text }}</dd>
        </div>
      </dl>
    </div>

    <!-- ========== 4. 紧凑单行系统信息条（刊登分组 + SKU 差异字段跳转） ========== -->
    <div
      v-if="categoryAttributeState === 'ready' && (systemGroupingAttributes.length || skuVariantCount > 0)"
      class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 rounded-xl border border-accent-200/80 bg-accent-50/60 px-3.5 py-2 text-xs text-accent-600 dark:border-dark-700/80 dark:bg-dark-950/40 dark:text-accent-300"
    >
      <div class="flex min-w-0 flex-wrap items-center gap-x-3.5 gap-y-1">
        <span
          v-if="systemGroupingAttributes.length"
          data-testid="system-grouping-attributes"
          class="inline-flex min-w-0 items-center gap-1"
          title="根据 SKU 页的发布组织方式和平台组名自动填写，无需手动编辑"
        >
          <span class="shrink-0 text-accent-400 dark:text-accent-500">刊登分组（系统自动）：</span>
          <strong class="truncate font-medium text-accent-800 dark:text-accent-100">{{ systemGroupingDisplay }}</strong>
        </span>
        <span v-if="systemGroupingAttributes.length && skuVariantCount > 0" class="hidden text-accent-300 dark:text-dark-600 sm:inline" aria-hidden="true">|</span>
        <span v-if="skuVariantCount > 0" class="inline-flex items-center gap-1.5">
          <span class="size-1.5 rounded-full bg-sky-500" aria-hidden="true" />
          <span>另有 <strong class="font-semibold text-accent-900 dark:text-white">{{ skuVariantCount }}</strong> 个 SKU 差异字段（按规格填写）</span>
          <button
            type="button"
            class="inline-flex items-center gap-0.5 font-medium text-primary-700 hover:underline dark:text-primary-400"
            data-testid="jump-to-sku-tab"
            @click="emit('openSkuTab')"
          >
            前往 SKU 页 <PhArrowRight :size="12" aria-hidden="true" />
          </button>
        </span>
      </div>
      <span class="hidden text-[11px] text-accent-400 dark:text-accent-500 lg:inline">
        属性自动中文化展示 · 保存平台原文/ID
      </span>
    </div>

    <!-- Mercado 条码规则（仅适用市场出现） -->
    <DraftBarcodeFields
      v-if="!props.skuScope && categoryAttributeState === 'ready'"
      :draft="props.draft"
      :target="props.target"
      :category="props.category"
      :skus="props.productContext.skuItems"
      :loading="props.loading"
      @change="invalidateEditedAttribute('EMPTY_GTIN_REASON')"
    />

    <!-- ========== 5. 属性工作区（单行工具栏 + 预检/待复核 + 全宽双列卡片） ========== -->
    <template v-if="categoryAttributeState === 'ready'">
      <!-- 单行属性进度与筛选工具栏 -->
      <div class="flex flex-wrap items-center justify-between gap-2.5">
        <div class="flex flex-wrap items-center gap-2">
          <span
            class="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold"
            :class="completedRequiredCount >= requiredAttributeFields.length
              ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30'
              : 'bg-amber-50 text-amber-800 ring-1 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30'"
            data-testid="required-progress-badge"
          >
            <PhCheckCircle v-if="completedRequiredCount >= requiredAttributeFields.length" :size="13" weight="fill" aria-hidden="true" />
            必填已完成 {{ completedRequiredCount }} / {{ requiredAttributeFields.length }}
          </span>
          <span class="rounded-full bg-accent-100 px-2.5 py-1 text-xs font-medium text-accent-600 dark:bg-dark-800 dark:text-accent-300">
            可选属性 {{ optionalAttributeFields.length }} 个{{ completedOptionalCount ? `（已填 ${completedOptionalCount}）` : '' }}
          </span>
          <div
            v-if="totalEditableAttributeCount > 0"
            class="inline-flex rounded-xl border border-accent-200 bg-accent-50 p-0.5 dark:border-dark-700 dark:bg-dark-950"
            role="tablist"
            aria-label="属性筛选"
          >
            <button
              v-for="tab in ([
                ['all', '全部', totalEditableAttributeCount],
                ['pending', '待处理', pendingAttributeCount],
                ['required', '必填', requiredAttributeFields.length],
                ['optional', '选填', optionalAttributeFields.length],
              ] as const)"
              :key="tab[0]"
              type="button"
              role="tab"
              :aria-selected="attributeFilter === tab[0]"
              class="rounded-lg px-2.5 py-1 text-xs font-medium transition"
              :class="attributeFilter === tab[0]
                ? 'bg-primary-500 font-semibold text-white shadow-sm dark:text-dark-950'
                : 'text-accent-600 hover:text-accent-950 dark:text-accent-300 dark:hover:text-white'"
              @click="attributeFilter = tab[0]"
            >
              {{ tab[1] }}<span class="ml-1 opacity-75 tabular-nums">{{ tab[2] }}</span>
            </button>
          </div>
        </div>

        <div v-if="totalEditableAttributeCount > 0" class="w-full sm:w-64">
          <input
            v-model="attributeSearchQuery"
            type="search"
            class="input !rounded-xl !py-1.5 text-xs"
            placeholder="搜索属性（中文 / 原文 / ID）"
            data-testid="attribute-filter-search"
          />
        </div>
      </div>

      <!-- 类目预检结果前置展示，无需滚动到底部查看 -->
      <CategoryPrecheckPanel
        :result="props.categoryPrecheck"
        :can-locate-attribute="hasLoadedCategoryDefinition"
        @locate-attribute="focusAttribute"
      />

      <!-- 待复核快捷跳转条 -->
      <div
        v-if="pendingReviewAttributeIds.length"
        class="flex flex-wrap items-center gap-2 rounded-xl bg-amber-50 px-3.5 py-2.5 text-xs text-amber-800 ring-1 ring-amber-200 dark:bg-amber-950/25 dark:text-amber-300 dark:ring-amber-800/60"
      >
        <span class="font-semibold">待复核属性：</span>
        <button
          v-for="attrId in pendingReviewAttributeIds"
          :key="attrId"
          class="rounded-full bg-white px-2.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-amber-200 transition hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-400 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-dark-900 dark:text-amber-200 dark:ring-amber-800/60 dark:hover:bg-amber-950/50"
          type="button"
          :disabled="!reviewAttributeSection(attrId)"
          :title="reviewAttributeSection(attrId) ? `定位到${reviewAttributeLabel(attrId)}` : '当前平台属性定义中不存在该属性'"
          @click="focusAttribute(attrId)"
        >
          {{ reviewAttributeLabel(attrId) }}
        </button>
      </div>

      <!-- 必填属性区（全宽双列卡片） -->
      <div v-if="filteredRequiredFields.length" class="space-y-2.5">
        <button
          type="button"
          class="flex w-full items-center justify-between rounded-lg px-1 py-0.5 text-left text-xs font-semibold text-accent-700 transition hover:text-accent-950 dark:text-accent-200 dark:hover:text-white"
          @click="showRequiredAttributes = !showRequiredAttributes"
        >
          <span class="inline-flex items-center gap-1.5">
            <span>必填属性 {{ filteredRequiredFields.length }} 个</span>
            <span class="text-[11px] font-normal text-accent-400">已完成 {{ completedRequiredCount }} / {{ requiredAttributeFields.length }}</span>
          </span>
          <span class="inline-flex items-center gap-1 text-accent-500 dark:text-accent-400">
            {{ showRequiredAttributes ? '收起' : '展开' }}
            <PhCaretUp v-if="showRequiredAttributes" :size="13" aria-hidden="true" />
            <PhCaretDown v-else :size="13" aria-hidden="true" />
          </span>
        </button>
        <div
          v-if="showRequiredAttributes"
          class="grid gap-3"
          :class="props.skuScope ? '' : 'md:grid-cols-2'"
          data-testid="required-attribute-fields"
        >
          <CategoryAttributeField
            v-for="attr in filteredRequiredFields"
            :key="attr.id"
            :attr="attr"
            :model-value="effectiveAttributesMap[attr.id]"
            :platform="props.target.platform"
            :site="props.target.site"
            :category-id="String(props.draft.categoryId || '')"
            :target-identity-key="targetIdentityKey"
            :sku-scope="props.skuScope"
            :is-overridden="isAttributeOverridden(attr.id)"
            :is-inherited="isAttributeInherited(attr.id)"
            :missing="isMissingAttribute(attr.id)"
            :pending-review="pendingReviewAttributeIds.includes(attr.id)"
            :translation="attributeTranslation(attr.id)"
            @update:model-value="handleAttributeUpdate(attr.id, $event)"
            @clear-override="handleClearOverride(attr.id)"
            @register-input-ref="registerAttributeInputRef"
          />
        </div>
      </div>

      <!-- 当无任何必填且无可选（或显式筛选“必填”为空）时提示 -->
      <div
        v-else-if="!requiredAttributeFields.length && (!optionalAttributeFields.length || attributeFilter === 'required')"
        class="rounded-xl border border-accent-200/80 bg-accent-50/50 px-4 py-3 text-sm text-accent-500 dark:border-dark-700 dark:bg-dark-950/40 dark:text-accent-400"
      >
        当前没有需要手动填写的必填属性。
      </div>

      <!-- 选填属性区（必填为 0 时默认自动展开，首屏直出双列属性卡片） -->
      <div v-if="filteredOptionalFields.length" class="space-y-2.5">
        <button
          type="button"
          class="flex w-full items-center justify-between rounded-lg px-1 py-0.5 text-left text-xs font-semibold text-accent-700 transition hover:text-accent-950 dark:text-accent-200 dark:hover:text-white"
          @click="showOptionalAttributes = !showOptionalAttributes"
        >
          <span class="inline-flex items-center gap-1.5">
            <span>可选属性 {{ filteredOptionalFields.length }} 个</span>
            <span v-if="completedOptionalCount" class="text-[11px] font-normal text-primary-600 dark:text-primary-400">已填 {{ completedOptionalCount }}</span>
          </span>
          <span class="inline-flex items-center gap-1 text-accent-500 dark:text-accent-400">
            {{ showOptionalAttributes ? '收起' : '展开' }}
            <PhCaretUp v-if="showOptionalAttributes" :size="13" aria-hidden="true" />
            <PhCaretDown v-else :size="13" aria-hidden="true" />
          </span>
        </button>
        <div
          v-if="showOptionalAttributes"
          class="grid gap-3"
          :class="props.skuScope ? '' : 'md:grid-cols-2'"
          data-testid="optional-attribute-fields"
        >
          <CategoryAttributeField
            v-for="attr in filteredOptionalFields"
            :key="attr.id"
            :attr="attr"
            :model-value="effectiveAttributesMap[attr.id]"
            :platform="props.target.platform"
            :site="props.target.site"
            :category-id="String(props.draft.categoryId || '')"
            :target-identity-key="targetIdentityKey"
            :sku-scope="props.skuScope"
            :is-overridden="isAttributeOverridden(attr.id)"
            :is-inherited="isAttributeInherited(attr.id)"
            :missing="isMissingAttribute(attr.id)"
            :pending-review="pendingReviewAttributeIds.includes(attr.id)"
            :translation="attributeTranslation(attr.id)"
            @update:model-value="handleAttributeUpdate(attr.id, $event)"
            @clear-override="handleClearOverride(attr.id)"
            @register-input-ref="registerAttributeInputRef"
          />
        </div>
      </div>

      <!-- 筛选/搜索无命中时的兜底提示 -->
      <div
        v-if="totalEditableAttributeCount > 0 && !filteredRequiredFields.length && !filteredOptionalFields.length && !(attributeFilter === 'required' && !requiredAttributeFields.length)"
        class="flex items-center justify-between rounded-xl border border-dashed border-accent-200 px-4 py-3 text-xs text-accent-500 dark:border-dark-700 dark:text-accent-400"
      >
        <span>
          {{ attributeSearchQuery.trim()
            ? `没有找到包含「${attributeSearchQuery.trim()}」的属性。`
            : '当前筛选条件下没有需要处理的属性。' }}
        </span>
        <button
          type="button"
          class="font-medium text-primary-700 hover:underline dark:text-primary-400"
          @click="resetAttributeFilters"
        >
          重置筛选
        </button>
      </div>
    </template>
  </section>
</template>

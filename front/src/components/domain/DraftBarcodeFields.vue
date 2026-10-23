<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { fetchCategoryAttrs, fetchCategoryAttributeValues } from '@/api/workflow/publishing'
import type { CategoryAttributeOption, CategorySelection, DraftDetail, MarketplaceTargetSite, ProductSku } from '@/types/workflow'
import { barcodeAttributes, DEFAULT_EMPTY_GTIN_REASON } from '@/utils/barcodePolicy'

const props = withDefaults(defineProps<{
  draft: DraftDetail
  target: MarketplaceTargetSite
  category?: CategorySelection | null
  skus: ProductSku[]
  loading?: boolean
  autoLoad?: boolean
}>(), { category: null, loading: false, autoLoad: false })
const emit = defineEmits<{ change: [] }>()
const loaded = ref<CategorySelection | null>(null)
const fetching = ref(false)
const error = ref('')
let requestId = 0
const identity = computed(() => `${props.draft.draftId}:${props.target.platform}:${props.target.site}:${props.draft.categoryId}`)
const matches = (category: CategorySelection | null) => category?.platform === props.target.platform
  && category.categoryId === props.draft.categoryId && Boolean(category.fetchedAt)
const definition = computed(() => matches(props.category) ? props.category : matches(loaded.value) ? loaded.value : null)
const fields = computed(() => barcodeAttributes(props.target.platform, definition.value))
const reason = computed(() => fields.value.find(field => field.id === 'EMPTY_GTIN_REASON'))
const selected = computed(() => props.draft.skuItems.filter(row => row.selected))
const missing = computed(() => selected.value.filter(row => !props.skus.find(sku => sku.id === row.sku_id)?.barcode.trim()).length)
const extraOptions = ref<CategoryAttributeOption[]>([])
const cursor = ref('')
const more = ref(false)
const loadingOptions = ref(false)
const options = computed(() => [...new Set([...(reason.value?.options || []), ...extraOptions.value.map(option => option.value)])])
const strict = computed(() => reason.value?.valueMode === 'strict_enum'
  || (!reason.value?.allowCustomValues && reason.value?.valueMode !== 'free_text' && reason.value?.valueMode !== 'open_enum'
    && Boolean(reason.value?.options?.length || reason.value?.isDictionary)))
const value = computed({
  get: () => {
    const raw = props.draft.attributes.EMPTY_GTIN_REASON
    if (typeof raw === 'string') return raw
    if (raw && 'values' in raw) return raw.values[0]?.value || ''
    return raw && 'value' in raw ? raw.value : ''
  },
  set: (next: string) => {
    // 空字符串是用户明确清空，保留它以阻止旧豁免标记重新填回默认原因。
    const selectedOption = extraOptions.value.find(option => option.value === next)
    props.draft.attributes.EMPTY_GTIN_REASON = next && selectedOption?.id
      ? { values: [{ dictionaryValueId: selectedOption.id, value: next }] }
      : next
    emit('change')
  },
})

async function loadDefinition() {
  const current = ++requestId
  loaded.value = null
  error.value = ''
  fetching.value = false
  if (!props.autoLoad || props.target.platform !== 'mercadolibre' || !props.draft.categoryId || matches(props.category)) return
  fetching.value = true
  try {
    const result = await fetchCategoryAttrs(props.target.platform, props.draft.categoryId, props.target.site)
    if (current === requestId) loaded.value = result
  } catch (exception) {
    if (current === requestId) error.value = exception instanceof Error ? exception.message : '读取条码规则失败'
  } finally {
    if (current === requestId) fetching.value = false
  }
}
watch(() => [identity.value, props.category?.fetchedAt], loadDefinition, { immediate: true })
watch(() => [identity.value, reason.value], () => {
  extraOptions.value = []
  cursor.value = ''
  more.value = Boolean(reason.value?.hasMoreValues)
  loadingOptions.value = false
}, { immediate: true })
watch(() => [identity.value, reason.value, missing.value, options.value, props.loading], () => {
  if (props.loading || !reason.value || !missing.value || Object.hasOwn(props.draft.attributes, 'EMPTY_GTIN_REASON')) return
  const suggested = options.value.find(option => option.toLowerCase() === DEFAULT_EMPTY_GTIN_REASON.toLowerCase())
  if (suggested) value.value = suggested
}, { immediate: true })
async function loadMore() {
  const key = identity.value
  const field = reason.value
  if (!field || loadingOptions.value) return
  loadingOptions.value = true
  try {
    const result = await fetchCategoryAttributeValues(props.target.platform, props.draft.categoryId, field.id, props.target.site, '', 50, cursor.value)
    if (key !== identity.value || field !== reason.value) return
    extraOptions.value.push(...result.values)
    cursor.value = result.nextCursor
    more.value = result.hasMore
    error.value = ''
  } catch (exception) {
    if (key === identity.value) error.value = exception instanceof Error ? exception.message : '读取缺失原因选项失败'
  } finally {
    if (key === identity.value) loadingOptions.value = false
  }
}
onBeforeUnmount(() => { requestId += 1 })
</script>

<template>
  <fieldset v-if="fields.length || fetching || error" :disabled="loading" class="my-3 min-w-0 rounded-lg border border-accent-200 bg-white p-4 text-sm dark:border-dark-700 dark:bg-dark-900" data-testid="draft-barcode-fields">
    <p v-if="fetching" class="muted" role="status">正在读取商品标识规则…</p>
    <template v-if="fields.length">
      <h4 class="font-semibold">UPC / GTIN 条码</h4>
      <p class="muted mt-1">已选 {{ selected.length }} 个 SKU，{{ missing }} 个未填写条码。条码在商品的 SKU 资料中维护。</p>
      <template v-if="missing && reason">
        <label class="mt-3 block font-medium" :for="`gtin-reason-${identity}`">缺少 UPC / GTIN 的原因</label>
        <select v-if="strict" :id="`gtin-reason-${identity}`" v-model="value" class="input mt-1" data-attribute-id="EMPTY_GTIN_REASON">
          <option value="">请选择符合商品实际情况的原因</option>
          <option v-if="value && !options.includes(value)" :value="value">{{ value }}</option>
          <option v-for="option in options" :key="option" :value="option">{{ option === DEFAULT_EMPTY_GTIN_REASON ? `商品没有注册条码 · ${option}` : option }}</option>
        </select>
        <template v-else>
          <input :id="`gtin-reason-${identity}`" v-model="value" :list="`gtin-options-${identity}`" class="input mt-1" data-attribute-id="EMPTY_GTIN_REASON" placeholder="填写符合商品实际情况的原因" />
          <datalist :id="`gtin-options-${identity}`"><option v-for="option in options" :key="option" :value="option" /></datalist>
        </template>
        <p class="muted mt-2">此原因仅用于该市场中没有条码的 SKU。请核对预填内容，可修改或清空；有条码的 SKU 仍提交实际条码。</p>
        <button v-if="more" type="button" class="btn btn-outline mt-2" :disabled="loadingOptions" @click="loadMore">{{ loadingOptions ? '正在读取…' : '加载更多平台原因' }}</button>
      </template>
      <p v-else-if="missing" class="mt-2 text-amber-700 dark:text-amber-300">该类目未提供条码缺失原因，请补齐 SKU 条码后再预检。</p>
    </template>
    <p v-if="error" class="mt-2 text-rose-700" role="alert">{{ error }}</p>
    <button v-if="error && !definition" type="button" class="btn btn-outline mt-2" @click="loadDefinition">重新读取规则</button>
  </fieldset>
</template>

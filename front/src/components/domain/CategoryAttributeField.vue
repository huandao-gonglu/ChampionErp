<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { ComponentPublicInstance } from 'vue'
import { PhCheck } from '@phosphor-icons/vue'
import { fetchCategoryAttributeValues } from '@/api/workflow/publishing'
import { isCategoryDictionaryAttribute } from '@/api/workflow/normalizers'
import { isNonTranslatableText, translateTextSilent, type TextTranslationMap } from '@/api/workflow/translation'
import { attributeConstraintError, attributeConstraintHint } from '@/utils/categoryConstraints'
import type {
  CategoryAttributeDefinition,
  CategoryAttributeOption,
  CategoryAttributeTranslation,
  CategoryAttributeValue,
  CategoryDictionaryValue,
  Marketplace,
} from '@/types/workflow'

const props = withDefaults(defineProps<{
  attr: CategoryAttributeDefinition
  modelValue?: CategoryAttributeValue
  platform: Marketplace
  site: string
  categoryId: string
  targetIdentityKey: string
  skuScope?: boolean
  isOverridden?: boolean
  isInherited?: boolean
  missing?: boolean
  pendingReview?: boolean
  translation?: CategoryAttributeTranslation | null
}>(), {
  modelValue: undefined,
  skuScope: false,
  isOverridden: false,
  isInherited: false,
  missing: false,
  pendingReview: false,
  translation: null,
})

const emit = defineEmits<{
  'update:modelValue': [value: CategoryAttributeValue | undefined]
  clearOverride: []
  registerInputRef: [attrId: string, el: Element | ComponentPublicInstance | null]
}>()

const fieldRoot = ref<HTMLElement | null>(null)
const localOptionZhMap = ref<Record<string, string>>({})
let optionLocalizationSeq = 0

interface DictionaryState {
  query: string
  loadedQuery: string | null
  options: CategoryAttributeOption[]
  baseOptions: CategoryAttributeOption[]
  loading: boolean
  loadingMore: boolean
  hint: string
  error: string
  open: boolean
  requestId: number
  nextCursor: string
  hasMore: boolean
}

const dictState = ref<DictionaryState>({
  query: '',
  loadedQuery: null,
  options: [],
  baseOptions: [],
  loading: false,
  loadingMore: false,
  hint: '',
  error: '',
  open: false,
  requestId: 0,
  nextCursor: '',
  hasMore: false,
})
let searchTimer: ReturnType<typeof setTimeout> | null = null

const zhLabel = computed(() => String(props.attr.nameZh || props.translation?.label || '').trim())
const primaryLabel = computed(() => zhLabel.value || props.attr.name || props.attr.id)
const originalLabelLine = computed(() => (
  [props.attr.name || props.attr.id, props.attr.id]
    .filter((item, index, items) => item && items.indexOf(item) === index)
    .join(' · ')
))
const descriptionText = computed(() => String(
  props.attr.descriptionZh || props.translation?.help || props.attr.description || '',
).trim())
const originalDescriptionText = computed(() => {
  const orig = String(props.attr.description || '').trim()
  return orig && orig !== descriptionText.value ? orig : ''
})

function optionLabelZh(value: string, inlineLabelZh?: string): string {
  const key = String(value || '').trim()
  if (!key) return ''
  return String(
    inlineLabelZh
    || localOptionZhMap.value[key]
    || props.attr.optionLabelsZh?.[key]
    || props.translation?.values?.[key]
    || '',
  ).trim()
}

function formatBilingualValue(value: string, inlineLabelZh?: string): string {
  const zh = optionLabelZh(value, inlineLabelZh)
  return zh && zh !== value ? `${zh}（${value}）` : value
}

const isStrictEnum = computed(() => {
  if (props.attr.valueMode) return props.attr.valueMode === 'strict_enum'
  return isCategoryDictionaryAttribute(props.attr.dictionaryId, props.attr.isDictionary)
})

const isOpenEnum = computed(() => {
  if (props.attr.valueMode === 'open_enum') return true
  return props.platform === 'mercadolibre'
    && !isStrictEnum.value
    && Boolean(props.attr.options?.length || props.attr.hasMoreValues)
})

const allowsCustomValue = computed(() => Boolean(
  props.attr.allowCustomValues
  || props.attr.valueMode === 'open_enum'
  || props.attr.valueMode === 'free_text'
  || (props.platform === 'mercadolibre' && !isStrictEnum.value),
))

const usesOptionPicker = computed(() => Boolean(
  props.attr.isCollection
  || isStrictEnum.value
  || isOpenEnum.value
  || props.attr.options?.length
  || props.attr.hasMoreValues,
))

const usesRemoteOptions = computed(() => (
  isCategoryDictionaryAttribute(props.attr.dictionaryId, props.attr.isDictionary)
  || Boolean(props.attr.hasMoreValues)
))

const isBooleanField = computed(() => (
  !usesOptionPicker.value
  && !props.attr.unitOptions?.length
  && props.attr.textFormat !== 'name_value_lines'
  && String(props.attr.valueType || '').trim().toLowerCase() === 'boolean'
))

const fieldTypeBadge = computed(() => {
  if (props.attr.textFormat === 'name_value_lines') return '多行键值'
  if (props.attr.unitOptions?.length) return '数值 + 单位'
  if (isBooleanField.value) return '布尔'
  if (usesOptionPicker.value) {
    if (props.attr.isCollection) return isStrictEnum.value ? '多选字典' : '多选'
    return isStrictEnum.value ? '单选字典' : ''
  }
  return ''
})

const placeholder = computed(() => {
  if (props.attr.textFormat === 'name_value_lines') return '属性名:属性值（每行一项）'
  if (isStrictEnum.value) return props.attr.isCollection ? '请选择一个或多个平台值' : '请选择平台允许的值'
  if (isOpenEnum.value) return props.attr.isCollection ? '选择建议值或添加自定义值' : '选择建议值或输入自定义值'
  if (props.attr.isCollection) return '添加一个或多个属性值'
  return props.attr.options?.length ? '请选择平台允许的选项' : '请输入属性值'
})

const selectedDictionaryValues = computed<CategoryDictionaryValue[]>(() => {
  const raw = props.modelValue
  if (!raw || typeof raw === 'string' || !('values' in raw) || !Array.isArray(raw.values)) return []
  return raw.values
})

const legacyDictionaryValue = computed(() => (
  typeof props.modelValue === 'string' && props.modelValue.trim() ? props.modelValue.trim() : ''
))

const pickerHasValue = computed(() => Boolean(selectedDictionaryValues.value.length || legacyDictionaryValue.value))

const selectedDictionarySummary = computed(() => {
  const values = selectedDictionaryValues.value
  if (!values.length) {
    return legacyDictionaryValue.value ? formatBilingualValue(legacyDictionaryValue.value) : ''
  }
  if (props.attr.isCollection) return `已选 ${values.length} 项`
  return values[0]?.value ? formatBilingualValue(values[0].value) : ''
})

const unitValue = computed(() => {
  const raw = props.modelValue
  if (!raw || typeof raw === 'string') {
    return { value: typeof raw === 'string' ? raw : '', unit: '' }
  }
  if ('values' in raw) return { value: '', unit: '' }
  return { value: String(raw.value ?? ''), unit: String(raw.unit ?? '') }
})

const selectedUnit = computed(() => {
  if (typeof props.modelValue === 'string' && props.modelValue.trim()) return ''
  return unitValue.value.unit || props.attr.defaultUnit || props.attr.unitOptions?.[0] || ''
})

const stringInputValue = computed(() => {
  if (typeof props.modelValue === 'string') return props.modelValue
  if (props.modelValue && !('values' in props.modelValue)) return String(props.modelValue.value ?? '')
  return ''
})

const booleanSelectValue = computed(() => {
  const raw = stringInputValue.value.trim().toLowerCase()
  if (['true', '1', 'yes', 'да', 'sí', 'si'].includes(raw)) return 'true'
  if (['false', '0', 'no', 'нет'].includes(raw)) return 'false'
  return ''
})

const hasCurrentValue = computed(() => {
  if (usesOptionPicker.value) return pickerHasValue.value
  if (props.attr.unitOptions?.length) return Boolean(unitValue.value.value.trim())
  if (isBooleanField.value) return Boolean(booleanSelectValue.value)
  return Boolean(stringInputValue.value.trim())
})

const constraintHint = computed(() => attributeConstraintHint(props.attr, props.modelValue))

const validationError = computed(() => {
  const constraintError = attributeConstraintError(props.attr, props.modelValue)
  if (constraintError) return constraintError
  if (props.attr.textFormat !== 'name_value_lines') return ''
  const lines = unitValue.value.value.split(/\r\n|\r|\n/)
  for (const [index, line] of lines.entries()) {
    if (!line.trim()) continue
    const colon = line.indexOf(':')
    if (colon < 0) return `第 ${index + 1} 行缺少英文冒号，请按“属性名:属性值”填写`
    if (!line.slice(0, colon).trim() || !line.slice(colon + 1).trim()) return `第 ${index + 1} 行的属性名和属性值均不能为空`
  }
  return ''
})

const cardClass = computed(() => {
  const spanClass = props.attr.textFormat === 'name_value_lines' ? 'md:col-span-2' : ''
  if (props.missing || validationError.value || (isStrictEnum.value && legacyDictionaryValue.value)) {
    return `${spanClass} border-rose-300 bg-rose-50/40 dark:border-rose-500/45 dark:bg-rose-950/15`
  }
  if (props.pendingReview) {
    return `${spanClass} border-amber-300 bg-amber-50/40 dark:border-amber-500/40 dark:bg-amber-950/15`
  }
  if (hasCurrentValue.value) {
    return `${spanClass} border-primary-500/35 bg-primary-50/25 dark:border-primary-500/30 dark:bg-primary-950/15`
  }
  return `${spanClass} border-accent-200/90 bg-white dark:border-dark-700/90 dark:bg-dark-900/75`
})

function isOptionSelected(option: CategoryAttributeOption): boolean {
  const optId = String(option.id ?? '').trim()
  const persistedId = optId.startsWith('__local__:') ? '' : optId
  return selectedDictionaryValues.value.some((item) => {
    const itemId = String(item.dictionaryValueId ?? '').trim()
    return persistedId
      ? itemId === persistedId
      : item.value.trim().toLocaleLowerCase() === option.value.trim().toLocaleLowerCase()
  })
}

async function localizeOptionsPageInBackground(options: CategoryAttributeOption[]) {
  if (props.attr.skipValueLocalization || !options.length) return
  const content: TextTranslationMap = {}
  const valuesByKey = new Map<string, string>()
  let idx = 0
  for (const option of options) {
    const val = String(option.value || '').trim()
    if (!val || optionLabelZh(val, option.labelZh) || isNonTranslatableText(val)) continue
    const key = `opt.${idx++}`
    content[key] = val
    valuesByKey.set(key, val)
    if (idx >= 50) break
  }
  if (!Object.keys(content).length) return
  const seq = ++optionLocalizationSeq
  const res = await translateTextSilent('zh-CN', content, {
    domain: props.platform,
    scope: {
      platform: props.platform,
      site: props.site,
      category_id: props.categoryId,
    },
  })
  if (seq !== optionLocalizationSeq) return
  if (!Object.keys(res.translations).length) return
  const next = { ...localOptionZhMap.value }
  for (const [key, zh] of Object.entries(res.translations)) {
    const original = valuesByKey.get(key)
    if (original && zh) next[original] = zh
  }
  localOptionZhMap.value = next
}

function buildLocalOptions(query = ''): CategoryAttributeOption[] {
  const needle = query.trim().toLocaleLowerCase()
  return (props.attr.options || []).flatMap((value, index) => {
    const normalized = String(value || '').trim()
    if (!normalized) return []
    const zh = optionLabelZh(normalized)
    if (
      needle
      && !normalized.toLocaleLowerCase().includes(needle)
      && !(zh && zh.toLocaleLowerCase().includes(needle))
    ) {
      return []
    }
    return [{ id: `__local__:${index}`, value: normalized, ...(zh ? { labelZh: zh } : {}) }]
  })
}

function populateLocalOptions() {
  const state = dictState.value
  const opts = buildLocalOptions(state.query)
  state.options = opts
  if (!state.query.trim()) state.baseOptions = opts
  state.loadedQuery = state.query.trim()
  state.nextCursor = ''
  state.hasMore = false
  state.loading = false
  state.loadingMore = false
  state.hint = ''
  state.error = ''
  void localizeOptionsPageInBackground(opts)
}

function mergeOptions(current: CategoryAttributeOption[], incoming: CategoryAttributeOption[]) {
  const map = new Map(current.map((opt) => [String(opt.id), opt]))
  for (const opt of incoming) map.set(String(opt.id), opt)
  return [...map.values()]
}

async function loadRemoteOptions(append = false) {
  const state = dictState.value
  if (append && (!state.hasMore || !state.nextCursor || state.loading || state.loadingMore)) return
  const query = state.query.trim()
  const cursor = append ? state.nextCursor : ''
  const requestId = ++state.requestId
  state.loading = !append
  state.loadingMore = append
  state.hint = ''
  state.error = ''
  if (!append) {
    state.options = []
    state.nextCursor = ''
    state.hasMore = false
  }
  try {
    const page = await fetchCategoryAttributeValues(
      props.platform,
      props.categoryId,
      props.attr.id,
      props.site,
      query,
      50,
      cursor,
    )
    if (requestId !== state.requestId) return
    state.options = append ? mergeOptions(state.options, page.values) : page.values
    if (!query) state.baseOptions = state.options
    state.loadedQuery = query
    state.nextCursor = page.nextCursor
    state.hasMore = page.hasMore && Boolean(page.nextCursor)
    void localizeOptionsPageInBackground(page.values)
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

function openDictionary() {
  const state = dictState.value
  state.open = true
  if (!usesRemoteOptions.value) {
    populateLocalOptions()
    return
  }
  if (state.loadedQuery !== state.query.trim() && !state.loading && !state.loadingMore) {
    void loadRemoteOptions(false)
  }
}

function scheduleSearch(value: string) {
  const state = dictState.value
  state.query = value
  state.open = true
  state.requestId += 1
  state.nextCursor = ''
  state.hasMore = false
  state.loadingMore = false
  state.hint = ''
  state.error = ''
  if (searchTimer) clearTimeout(searchTimer)
  if (!usesRemoteOptions.value) {
    populateLocalOptions()
    return
  }
  const query = value.trim()
  // Ozon 远端枚举搜索至少需要 2 字符：1 字符时不发无效请求，改为在已加载项中即时本地过滤。
  if (props.platform === 'ozon' && query.length === 1) {
    const needle = query.toLocaleLowerCase()
    const pool = state.baseOptions.length ? state.baseOptions : buildLocalOptions('')
    state.options = pool.filter((opt) => {
      const zh = optionLabelZh(opt.value, opt.labelZh)
      return opt.value.toLocaleLowerCase().includes(needle)
        || (zh && zh.toLocaleLowerCase().includes(needle))
    })
    state.loadedQuery = query
    state.loading = false
    state.hint = 'Ozon 平台枚举搜索至少需要 2 个字符，已先按当前已加载项筛选'
    return
  }
  searchTimer = setTimeout(() => {
    void loadRemoteOptions(false)
  }, 250)
}

function selectOption(option: CategoryAttributeOption) {
  const dictionaryValueId = String(option.id ?? '').trim()
  if (!dictionaryValueId || dictionaryValueId === '0') return
  const persistedValueId = dictionaryValueId.startsWith('__local__:') ? '' : dictionaryValueId
  const selectedValue: CategoryDictionaryValue = {
    ...(persistedValueId ? { dictionaryValueId: persistedValueId } : {}),
    value: option.value,
  }
  if (props.attr.isCollection) {
    const existing = selectedDictionaryValues.value
    const alreadyIndex = existing.findIndex((item) => {
      const itemId = String(item.dictionaryValueId ?? '').trim()
      return persistedValueId
        ? itemId === persistedValueId
        : item.value.trim().toLocaleLowerCase() === option.value.trim().toLocaleLowerCase()
    })
    let selected: CategoryDictionaryValue[]
    if (alreadyIndex >= 0) {
      selected = existing.filter((_, idx) => idx !== alreadyIndex)
    } else {
      selected = [...existing, selectedValue]
      const maximum = props.attr.maxValueCount && props.attr.maxValueCount > 0 ? props.attr.maxValueCount : null
      if (maximum !== null && selected.length > maximum) {
        dictState.value.error = `该属性最多可选择 ${maximum} 项`
        return
      }
    }
    dictState.value.error = ''
    emit('update:modelValue', selected.length ? { values: selected } : undefined)
    // 多选保持下拉列表与当前页状态，避免连续勾选时清空重拉！
    return
  }

  if (persistedValueId) {
    emit('update:modelValue', { values: [selectedValue] })
  } else {
    emit('update:modelValue', option.value)
  }
  dictState.value.query = ''
  dictState.value.error = ''
  dictState.value.hint = ''
  dictState.value.open = false
}

function commitCustomValue() {
  if (!allowsCustomValue.value) return
  const state = dictState.value
  const value = state.query.trim()
  if (!value) return
  if (props.attr.isCollection) {
    const existing = selectedDictionaryValues.value
    const selected = [
      ...existing.filter((item) => item.value.trim().toLocaleLowerCase() !== value.toLocaleLowerCase()),
      { value },
    ]
    const maximum = props.attr.maxValueCount && props.attr.maxValueCount > 0 ? props.attr.maxValueCount : null
    if (maximum !== null && selected.length > maximum) {
      state.error = `该属性最多可填写 ${maximum} 项`
      return
    }
    emit('update:modelValue', { values: selected })
    state.query = ''
    state.error = ''
    return
  }
  emit('update:modelValue', value)
  state.query = ''
  state.error = ''
  state.open = false
}

function removeCollectionOption(index: number) {
  const values = selectedDictionaryValues.value.filter((_, idx) => idx !== index)
  emit('update:modelValue', values.length ? { values } : undefined)
}

function clearPickerValue() {
  emit('update:modelValue', undefined)
  dictState.value.query = ''
  dictState.value.error = ''
  dictState.value.hint = ''
  dictState.value.open = false
}

function clearSearch() {
  const state = dictState.value
  state.query = ''
  state.loadedQuery = null
  state.options = []
  state.nextCursor = ''
  state.hasMore = false
  state.hint = ''
  state.error = ''
  state.open = true
  openDictionary()
}

function closeDictionary() {
  setTimeout(() => {
    const active = document.activeElement
    if (
      active instanceof HTMLElement
      && fieldRoot.value?.contains(active)
      && (active.dataset.attributeId === props.attr.id || active.dataset.dictionarySearchId === props.attr.id)
    ) return
    dictState.value.open = false
  }, 150)
}

function setUnitPatch(patch: { value?: string; unit?: string }) {
  const current = unitValue.value
  const value = (patch.value ?? current.value).trim()
  const unit = (patch.unit ?? selectedUnit.value).trim()
  if (!value) {
    emit('update:modelValue', undefined)
  } else {
    emit('update:modelValue', { value, unit })
  }
}

function setTextValue(value: string) {
  emit('update:modelValue', value)
}

function setBooleanValue(value: string) {
  emit('update:modelValue', value || undefined)
}

watch(
  () => [props.targetIdentityKey, props.categoryId, props.attr.id],
  () => {
    if (searchTimer) clearTimeout(searchTimer)
    searchTimer = null
    optionLocalizationSeq += 1
    localOptionZhMap.value = {}
    dictState.value = {
      query: '',
      loadedQuery: null,
      options: [],
      baseOptions: [],
      loading: false,
      loadingMore: false,
      hint: '',
      error: '',
      open: false,
      requestId: 0,
      nextCursor: '',
      hasMore: false,
    }
  },
)

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<template>
  <div
    ref="fieldRoot"
    class="flex min-w-0 flex-col gap-1.5 rounded-xl border p-3 transition"
    :class="cardClass"
  >
    <!-- 卡片头：中文主名 + 类型微标 + 右上状态 -->
    <div class="flex items-start justify-between gap-2">
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-1.5">
          <span
            class="text-[13px] font-semibold leading-snug"
            :class="props.missing ? 'text-rose-700 dark:text-rose-300' : 'text-accent-900 dark:text-white'"
          >
            <span v-if="props.attr.required" class="text-rose-500">* </span>{{ primaryLabel }}
          </span>
          <span
            v-if="fieldTypeBadge"
            class="rounded-md bg-accent-100 px-1.5 py-0.5 text-[10.5px] font-medium text-accent-500 dark:bg-dark-800 dark:text-accent-400"
          >
            {{ fieldTypeBadge }}
          </span>
        </div>
        <span class="mt-0.5 block truncate text-[11px] text-accent-400 dark:text-accent-500" :title="originalLabelLine">
          {{ originalLabelLine }}
        </span>
      </div>

      <!-- SKU 覆盖状态或常规填写状态 -->
      <div v-if="props.skuScope" class="flex shrink-0 flex-wrap items-center gap-1.5">
        <span
          v-if="props.isOverridden"
          class="rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-medium text-primary-700 ring-1 ring-primary-200 dark:bg-primary-950/40 dark:text-primary-200 dark:ring-primary-800"
        >
          已单独覆盖
        </span>
        <span
          v-else-if="props.isInherited"
          class="rounded-full bg-accent-100 px-2 py-0.5 text-[11px] text-accent-600 dark:bg-dark-800 dark:text-accent-400"
        >
          沿用公共
        </span>
        <button
          v-if="props.isOverridden"
          type="button"
          class="text-[11px] font-medium text-primary-700 underline hover:text-primary-900 dark:text-primary-300"
          data-testid="restore-inherited-attribute"
          @click="emit('clearOverride')"
        >
          恢复沿用公共属性
        </button>
      </div>
      <div v-else class="shrink-0">
        <span
          v-if="props.missing"
          class="text-[11px] font-medium text-rose-600 dark:text-rose-300"
        >
          待补齐
        </span>
        <span
          v-else-if="hasCurrentValue"
          class="inline-flex items-center gap-0.5 text-[11px] font-medium text-primary-600 dark:text-primary-400"
        >
          <PhCheck :size="12" weight="bold" aria-hidden="true" />已填
        </span>
        <span
          v-else
          class="text-[11px] text-accent-400 dark:text-accent-500"
        >
          {{ props.attr.required ? '必填' : '选填' }}
        </span>
      </div>
    </div>

    <p v-if="props.pendingReview" class="text-[11px] font-medium text-amber-700 dark:text-amber-300">
      该属性需要核对，请确认商品资料。
    </p>

    <!-- 1. 枚举 / 字典 / 多选选择器 -->
    <div v-if="usesOptionPicker" class="relative">
      <div v-if="props.attr.isCollection && selectedDictionaryValues.length" class="mb-1.5 flex flex-wrap gap-1.5">
        <span
          v-for="(item, index) in selectedDictionaryValues"
          :key="`${item.dictionaryValueId || 'custom'}:${item.value}:${index}`"
          class="inline-flex items-center gap-1 rounded-lg bg-primary-50 px-2 py-0.5 text-xs text-primary-800 ring-1 ring-primary-200 dark:bg-primary-950/35 dark:text-primary-200 dark:ring-primary-800/60"
        >
          {{ formatBilingualValue(item.value) }}
          <button type="button" aria-label="移除选项" class="opacity-70 hover:opacity-100" @click="removeCollectionOption(index)">×</button>
        </span>
      </div>
      <div class="flex gap-1.5">
        <input
          :ref="(el) => emit('registerInputRef', props.attr.id, el)"
          :value="selectedDictionarySummary"
          class="input !rounded-lg !py-1.5 text-[13px]"
          :class="props.missing ? 'border-rose-300 bg-rose-50 dark:border-rose-700 dark:bg-rose-950/20' : ''"
          :data-attribute-id="props.attr.id"
          :placeholder="placeholder"
          readonly
          @focus="openDictionary"
          @blur="closeDictionary"
          @click="openDictionary"
        />
        <button
          v-if="pickerHasValue"
          class="btn btn-outline shrink-0 !rounded-lg !px-2.5 !py-1.5 text-xs"
          type="button"
          @click="clearPickerValue"
        >
          清除已选
        </button>
      </div>
      <div
        v-if="dictState.open"
        class="absolute z-30 mt-1 max-h-64 w-full overflow-y-auto rounded-xl border border-accent-200 bg-white p-1 shadow-xl dark:border-dark-600 dark:bg-dark-900"
      >
        <div class="sticky top-0 flex gap-1.5 bg-white p-1.5 dark:bg-dark-900">
          <input
            :value="dictState.query"
            class="input !rounded-lg !py-1.5 text-xs"
            :data-dictionary-search-id="props.attr.id"
            :placeholder="allowsCustomValue ? (props.attr.isCollection ? '搜索建议值，或输入后添加' : '搜索建议值，或输入后使用') : '搜索平台选项（支持中文或原文，不会作为属性值保存）'"
            autocomplete="off"
            @focus="openDictionary"
            @blur="closeDictionary"
            @keydown.enter.prevent="commitCustomValue"
            @input="scheduleSearch(($event.target as HTMLInputElement).value)"
          />
          <button
            v-if="allowsCustomValue && dictState.query.trim()"
            class="btn btn-primary shrink-0 !rounded-lg !px-2.5 !py-1.5 text-xs"
            type="button"
            @mousedown.prevent
            @click="commitCustomValue"
          >
            {{ props.attr.isCollection ? '添加' : '使用此值' }}
          </button>
          <button
            v-if="dictState.query"
            class="btn btn-outline shrink-0 !rounded-lg !px-2.5 !py-1.5 text-xs"
            type="button"
            @mousedown.prevent
            @click="clearSearch"
          >
            清除搜索
          </button>
        </div>
        <p v-if="allowsCustomValue && dictState.query" class="px-2.5 text-xs text-accent-500">
          {{ attributeConstraintHint(props.attr, dictState.query) }}
        </p>
        <p v-if="allowsCustomValue && attributeConstraintError(props.attr, dictState.query)" role="alert" class="px-2.5 text-xs text-rose-600 dark:text-rose-400">
          {{ attributeConstraintError(props.attr, dictState.query) }}
        </p>
        <div v-if="dictState.hint" class="px-2.5 py-1.5 text-xs text-amber-700 dark:text-amber-300">
          {{ dictState.hint }}
        </div>
        <div v-if="dictState.loading" class="p-3 text-xs text-accent-500">正在读取平台选项…</div>
        <div v-else-if="dictState.error" class="p-3 text-xs text-rose-700 dark:text-rose-400">{{ dictState.error }}</div>
        <button
          v-for="option in dictState.options"
          :key="option.id"
          class="flex w-full items-start justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm hover:bg-primary-50 dark:hover:bg-dark-800"
          :class="props.attr.isCollection && isOptionSelected(option) ? 'bg-primary-50/70 dark:bg-primary-950/30' : ''"
          type="button"
          @mousedown.prevent
          @click="selectOption(option)"
        >
          <span class="min-w-0">
            <span class="block text-[13px] font-medium text-accent-900 dark:text-white">
              {{ optionLabelZh(option.value, option.labelZh) || option.value }}
            </span>
            <span
              v-if="optionLabelZh(option.value, option.labelZh) && optionLabelZh(option.value, option.labelZh) !== option.value"
              class="mt-0.5 block text-[11px] text-accent-400 dark:text-accent-500"
            >
              {{ option.value }}
            </span>
            <span v-if="option.info" class="mt-0.5 block text-[11px] text-accent-500">{{ option.info }}</span>
          </span>
          <span v-if="props.attr.isCollection && isOptionSelected(option)" class="shrink-0 text-xs font-semibold text-primary-700 dark:text-primary-300">已选</span>
        </button>
        <button
          v-if="dictState.hasMore"
          class="mt-1 block w-full rounded-lg border border-dashed border-accent-300 px-3 py-2 text-center text-xs font-semibold text-primary-700 hover:bg-primary-50 disabled:cursor-wait disabled:opacity-60 dark:border-dark-600 dark:text-primary-300 dark:hover:bg-dark-800"
          type="button"
          :disabled="dictState.loadingMore"
          @mousedown.prevent
          @click="loadRemoteOptions(true)"
        >
          {{ dictState.loadingMore ? '正在加载更多…' : '加载更多平台选项' }}
        </button>
        <div
          v-if="!dictState.loading && !dictState.error && !dictState.options.length"
          class="p-3 text-xs text-accent-500"
        >
          {{ allowsCustomValue ? '没有匹配的建议值，可直接使用当前输入。' : '没有匹配的平台选项，请更换关键词或检查类目。' }}
        </div>
      </div>
      <p v-if="isStrictEnum && legacyDictionaryValue" class="mt-1 text-xs text-rose-600 dark:text-rose-400">
        旧值“{{ legacyDictionaryValue }}”不是平台选项，请重新选择。
      </p>
    </div>

    <!-- 2. 带单位数值控件 -->
    <div v-else-if="props.attr.unitOptions?.length" class="flex gap-1.5">
      <input
        :ref="(el) => emit('registerInputRef', props.attr.id, el)"
        :value="unitValue.value"
        class="input !rounded-lg !py-1.5 text-[13px]"
        :class="props.missing ? 'border-rose-300 bg-rose-50 dark:border-rose-700 dark:bg-rose-950/20' : ''"
        :data-attribute-id="props.attr.id"
        :placeholder="placeholder"
        @input="setUnitPatch({ value: ($event.target as HTMLInputElement).value })"
      />
      <select
        :value="selectedUnit"
        class="input w-24 shrink-0 !rounded-lg !py-1.5 text-[13px]"
        :aria-label="`单位（${primaryLabel}）`"
        @change="setUnitPatch({ unit: ($event.target as HTMLSelectElement).value })"
      >
        <option value="" disabled>请选择单位</option>
        <option v-for="unitOption in props.attr.unitOptions" :key="unitOption" :value="unitOption">{{ unitOption }}</option>
      </select>
    </div>

    <!-- 3. 布尔开关控件 -->
    <select
      v-else-if="isBooleanField"
      :ref="(el) => emit('registerInputRef', props.attr.id, el)"
      :value="booleanSelectValue"
      class="input !rounded-lg !py-1.5 text-[13px]"
      :class="props.missing ? 'border-rose-300 bg-rose-50 dark:border-rose-700 dark:bg-rose-950/20' : ''"
      :data-attribute-id="props.attr.id"
      @change="setBooleanValue(($event.target as HTMLSelectElement).value)"
    >
      <option value="">未设置</option>
      <option value="true">是（true）</option>
      <option value="false">否（false）</option>
    </select>

    <!-- 4. 多行结构化文本 -->
    <textarea
      v-else-if="props.attr.textFormat === 'name_value_lines'"
      :ref="(el) => emit('registerInputRef', props.attr.id, el)"
      :value="unitValue.value"
      class="input min-h-20 !rounded-lg !py-1.5 text-[13px]"
      :data-attribute-id="props.attr.id"
      :placeholder="placeholder"
      :aria-invalid="Boolean(validationError)"
      :aria-describedby="`attribute-format-${props.targetIdentityKey}-${props.attr.id}`"
      @input="setTextValue(($event.target as HTMLTextAreaElement).value)"
    />

    <!-- 5. 标准文本 / 数值输入 -->
    <input
      v-else
      :ref="(el) => emit('registerInputRef', props.attr.id, el)"
      :value="stringInputValue"
      class="input !rounded-lg !py-1.5 text-[13px]"
      :class="props.missing ? 'border-rose-300 bg-rose-50 dark:border-rose-700 dark:bg-rose-950/20' : ''"
      :data-attribute-id="props.attr.id"
      :placeholder="placeholder"
      @input="setTextValue(($event.target as HTMLInputElement).value)"
    />

    <!-- 字段说明与约束（紧凑排布在控件下方） -->
    <p v-if="descriptionText" class="text-[11px] leading-relaxed text-accent-500 dark:text-accent-400">
      {{ descriptionText }}
      <span v-if="originalDescriptionText" class="block text-accent-400 dark:text-accent-500">{{ originalDescriptionText }}</span>
    </p>
    <span v-if="constraintHint" class="block text-[11px] text-accent-500 dark:text-accent-400">{{ constraintHint }}</span>
    <span v-if="props.attr.textFormat" :id="`attribute-format-${props.targetIdentityKey}-${props.attr.id}`" class="block text-[11px] text-accent-500 dark:text-accent-400">{{ props.attr.formatHint }}</span>
    <span v-if="validationError" role="alert" class="block text-xs text-rose-600 dark:text-rose-400">{{ validationError }}</span>
  </div>
</template>

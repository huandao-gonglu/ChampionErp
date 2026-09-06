<script setup lang="ts">
import { computed } from 'vue'
import type { DraftDetail, PrecheckIssue, UnknownRecord } from '@/types/workflow'

const props = defineProps<{ draft: DraftDetail; issues: PrecheckIssue[] }>()
const emit = defineEmits<{ invalidatePublishValidation: [] }>()

type ConfiguredWarrantyType = 'none' | 'seller' | 'factory'
type WarrantyType = '' | ConfiguredWarrantyType
type ConfiguredWarrantyUnit = 'months' | 'years'
type WarrantyUnit = '' | ConfiguredWarrantyUnit
const warrantyTypeOptions: Array<{ value: WarrantyType; label: string }> = [
  { value: '', label: '请选择保修类型' },
  { value: 'none', label: '无保修' },
  { value: 'seller', label: '卖家保修' },
  { value: 'factory', label: '厂家保修' },
]
const warrantyUnitOptions: Array<{ value: WarrantyUnit; label: string }> = [
  { value: '', label: '请选择单位' },
  { value: 'months', label: '个月' },
  { value: 'years', label: '年' },
]

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
  return draft
})
const selectedWarrantyType = computed<WarrantyType>({
  get() {
    const typeTerm = activeDraft.value.saleTerms.find((term) => String(term.id || '') === 'WARRANTY_TYPE')
    const value = String(typeTerm?.value_id || typeTerm?.value_name || '').toLowerCase()
    if (value.includes('2230280') || value.includes('seller') || value.includes('vendedor')) return 'seller'
    if (value.includes('2230279') || value.includes('factory') || value.includes('fábrica') || value.includes('fabrica')) return 'factory'
    if (value.includes('6150835') || value.includes('no warranty') || value.includes('sin garantía') || value.includes('sin garantia')) return 'none'
    return ''
  },
  set(value) {
    if (!value) return
    applyWarrantyTerms(
      value,
      warrantyDurationValue.value || '3',
      warrantyDurationUnit.value || 'months',
    )
  },
})
const warrantyDurationValue = computed<string>({
  get() {
    const timeTerm = activeDraft.value.saleTerms.find((term) => String(term.id || '') === 'WARRANTY_TIME')
    if (!timeTerm) return ''
    const struct = timeTerm?.value_struct && typeof timeTerm.value_struct === 'object' ? timeTerm.value_struct as UnknownRecord : {}
    const number = struct.number ?? String(timeTerm?.value_name || '').match(/\d+(?:[,.]\d+)?/)?.[0] ?? ''
    return String(number || '')
  },
  set(value) {
    const type = selectedWarrantyType.value
    if (!type || type === 'none') return
    applyWarrantyTerms(type, value, warrantyDurationUnit.value || 'months')
  },
})
const warrantyDurationUnit = computed<WarrantyUnit>({
  get() {
    const timeTerm = activeDraft.value.saleTerms.find((term) => String(term.id || '') === 'WARRANTY_TIME')
    if (!timeTerm) return ''
    const struct = timeTerm?.value_struct && typeof timeTerm.value_struct === 'object' ? timeTerm.value_struct as UnknownRecord : {}
    const unit = String(struct.unit || timeTerm?.value_name || '').toLowerCase()
    if (unit.includes('year') || unit.includes('año') || unit.includes('ano')) return 'years'
    if (unit.includes('month') || unit.includes('mes')) return 'months'
    return ''
  },
  set(value) {
    const type = selectedWarrantyType.value
    if (!type || type === 'none' || !value) return
    applyWarrantyTerms(type, warrantyDurationValue.value, value)
  },
})
const warrantySummary = computed(() => {
  const type = selectedWarrantyType.value
  if (!type) return '尚未选择保修类型'
  if (type === 'none') return '已明确选择无保修'
  return warrantyDurationValue.value && warrantyDurationUnit.value
    ? `已配置 ${activeDraft.value.saleTerms.length} 条`
    : '尚未配置保修时长'
})

function hasIssue(field: string, code = '') {
  return props.issues.some((issue) => issue.field === field || issue.code === code || issue.field.startsWith(`${field}.`))
}

function applyWarrantyTerms(type: ConfiguredWarrantyType, durationValue = '3', unit: ConfiguredWarrantyUnit = 'months') {
  if (type === 'none') {
    activeDraft.value.saleTerms = [
      { id: 'WARRANTY_TYPE', value_id: '6150835', value_name: 'Sin garantía' },
    ]
    emit('invalidatePublishValidation')
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
  emit('invalidatePublishValidation')
}
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 dark:border-dark-700 dark:bg-dark-900/80" data-testid="publish-shared-fields">
    <h3 class="card-title">共享发布资料</h3>
    <p class="muted mt-1">保修与 UPC 豁免在这里统一填写，应用于当前草稿的所有目标市场。</p>
    <div class="grid gap-4 lg:grid-cols-2">
      <div data-testid="shipping-package-explanation" class="mt-4 rounded-lg bg-white p-3 text-sm dark:bg-dark-900">
        <p class="font-semibold">逐 SKU 校验</p>
        <p class="muted mt-1">卖家编码、可售库存、条码、实际发货包装和售价均取所选 SKU 的资料。请在 SKU 页编辑各项，在核价页批量计算并应用售价。</p>
        <label class="mt-3 flex items-center gap-2"><input v-model="activeDraft.allowGtinExemption" type="checkbox" data-publish-draft-field="allowGtinExemption" @change="emit('invalidatePublishValidation')" />允许无 UPC 豁免</label>
      </div>

      <div class="mt-4 rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
        <div class="text-sm font-semibold text-accent-950 dark:text-white">保修条款</div>
        <div class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ warrantySummary }} · 保修与 UPC 豁免为草稿共享资料</div>
        <div class="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_8rem]">
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('sale_terms', 'SALE_TERMS_MISSING') ? 'text-rose-700' : 'text-accent-500 dark:text-accent-400'">保修类型</span>
            <select v-model="selectedWarrantyType" class="input mt-1" :class="hasIssue('sale_terms', 'SALE_TERMS_MISSING') ? 'border-rose-300 bg-rose-50' : ''" data-publish-draft-field="warrantyType">
              <option v-for="option in warrantyTypeOptions" :key="option.value || 'unselected'" :value="option.value" :disabled="!option.value">{{ option.label }}</option>
            </select>
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">时长</span>
            <input v-model="warrantyDurationValue" class="input mt-1" :disabled="!selectedWarrantyType || selectedWarrantyType === 'none'" data-publish-draft-field="warrantyDuration" inputmode="decimal" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-500 dark:text-accent-400">单位</span>
            <select v-model="warrantyDurationUnit" class="input mt-1" :disabled="!selectedWarrantyType || selectedWarrantyType === 'none'" data-publish-draft-field="warrantyUnit">
              <option v-for="option in warrantyUnitOptions" :key="option.value || 'unselected'" :value="option.value" :disabled="!option.value">{{ option.label }}</option>
            </select>
          </label>
        </div>
      </div>
    </div>
  </section>
</template>

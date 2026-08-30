<script setup lang="ts">
import { computed } from 'vue'
import type { DraftDetail, DraftProductContext, MarketplaceOption, MarketplaceTargetSite, PayloadPreviewState, PrecheckIssue, PublishPrecheck, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  draft: DraftDetail
  productContext: DraftProductContext
  publishTargets: MarketplaceTargetSite[]
  selectedPublishTarget: MarketplaceTargetSite
  platformOptions: MarketplaceOption[]
  precheck: PublishPrecheck | null
  payloadPreview: PayloadPreviewState | null
  loading: boolean
}>()

const emit = defineEmits<{
  selectPublishTarget: [value: MarketplaceTargetSite]
  updatePackageDimension: [field: PackageDimensionField, value: string]
  invalidatePublishValidation: []
  precheck: []
  previewPayload: []
  publish: []
}>()

type ConfiguredWarrantyType = 'none' | 'seller' | 'factory'
type WarrantyType = '' | ConfiguredWarrantyType
type ConfiguredWarrantyUnit = 'months' | 'years'
type WarrantyUnit = '' | ConfiguredWarrantyUnit
type PackageDimensionField = keyof DraftDetail['packageDimensions']

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

const selectedTargetKey = computed(() => targetKey(props.selectedPublishTarget))
const targetOptions = computed(() => props.publishTargets.map((target) => ({
  ...target,
  key: targetKey(target),
  label: targetLabel(target),
})))
const hasCurrentDraft = computed(() => Boolean(props.draft.draftId))
const currentDraftTitle = computed(() => String(props.draft.title || '').trim() || (props.draft.draftId ? '草稿标题未填写' : '尚未选择草稿'))
const currentDraftSku = computed(() => String(props.draft.sku || '').trim() || '无 SKU')
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
const blockingIssues = computed(() => props.precheck?.errorItems || [])
const warningIssues = computed(() => props.precheck?.warningItems || [])
const hasPayloadConfirmation = computed(() => Boolean(props.payloadPreview?.validationDigest))
const canPreviewPayload = computed(() => Boolean(
  hasCurrentDraft.value
  && (props.precheck?.ok || activeDraft.value.status === 'ready_to_publish'),
))
const canQueuePublish = computed(() => Boolean(
  hasCurrentDraft.value
  && (props.precheck?.ok || activeDraft.value.status === 'ready_to_publish')
  && hasPayloadConfirmation.value,
))
const publishReadiness = computed(() => {
  if (!props.precheck && activeDraft.value.status === 'ready_to_publish') return '已保存为校验通过。生成 Payload 预览并确认摘要后，即可加入发布队列。'
  if (!props.precheck) return '点击上架预检后，这里会变成可处理清单。'
  if (!props.precheck.ok) return `还剩 ${blockingIssues.value.length} 个阻断项，先在本页补齐能直接处理的字段。`
  if (!hasPayloadConfirmation.value) return '预检通过。请点击 Payload 预览生成确认摘要，再加入发布队列。'
  return '预检通过且 Payload 已确认，可以加入发布队列。'
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

function issueTitle(issue: PrecheckIssue) {
  return [issue.field, issue.message].filter(Boolean).join('：')
}

function hasIssue(field: string, code = '') {
  return blockingIssues.value.some((issue) => issue.field === field || issue.code === code || issue.field.startsWith(`${field}.`))
}

function generateSku() {
  const source = activeDraft.value.draftId || props.productContext.sourceUrl || props.productContext.title || activeDraft.value.title || Date.now().toString()
  const suffix = source.replace(/[^a-zA-Z0-9]+/g, '').slice(-8).toUpperCase() || Date.now().toString().slice(-6)
  const rawModel = activeDraft.value.attributes.MODEL
  const modelText = typeof rawModel === 'string' ? rawModel : props.productContext.model || 'ML'
  const model = modelText.replace(/[^a-zA-Z0-9]+/g, '').slice(0, 10).toUpperCase() || 'ML'
  activeDraft.value.sku = `${model}-${suffix}`
  emit('invalidatePublishValidation')
}

function useDefaultStock() {
  const stock = activeDraft.value.stock || props.productContext.stock || '10'
  if (stock === activeDraft.value.stock) return
  activeDraft.value.stock = stock
  emit('invalidatePublishValidation')
}

function setPackageDimension(field: PackageDimensionField, value: string) {
  const normalizedValue = value.trim()
  activeDraft.value.packageDimensions[field] = normalizedValue
  emit('updatePackageDimension', field, normalizedValue)
  emit('invalidatePublishValidation')
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
  // 发布币种只展示店铺配置快照；未就绪统一提示待配置，不回退站点币种。
  const currency = target.listingCurrency || '店铺发布货币待配置'
  return `${platformLabel} - ${siteLabel}（${target.site || site?.code || '-'} / ${language || '-'} / ${currency || '-'}）`
}

function selectTargetByKey(value: string) {
  const target = props.publishTargets.find((item) => targetKey(item) === value)
  if (target) emit('selectPublishTarget', target)
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
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <article class="mb-6 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="text-xs font-semibold text-accent-500 dark:text-accent-400">当前预检草稿</p>
          <div class="mt-2 flex flex-wrap items-center gap-3">
            <img v-if="props.productContext.imagePool[0]?.previewUrl || props.productContext.imagePool[0]?.url" :src="props.productContext.imagePool[0]?.previewUrl || props.productContext.imagePool[0]?.url" class="size-14 rounded-lg object-cover" />
            <div class="min-w-0">
              <h3 class="truncate font-semibold text-accent-950 dark:text-white">{{ currentDraftTitle }}</h3>
              <div class="mt-1 flex flex-wrap gap-2 text-xs text-accent-500 dark:text-accent-400">
                <span>{{ currentDraftSku }}</span>
                <span>{{ props.productContext.sourcePlatform || '来源未记录' }}</span>
                <span>{{ activeDraft.status || 'pending' }}</span>
                <span>{{ targetLabel(props.selectedPublishTarget) }}</span>
              </div>
            </div>
          </div>
          <p v-if="!hasCurrentDraft" class="mt-3 text-sm text-amber-700">请先从草稿箱选择草稿，再执行发布预检。</p>
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
        <h2 class="card-title">发布预检</h2>
        <p class="muted mt-1">补齐发布资料，检查阻断项并预览最终 Payload。</p>
      </div>
      <select :value="selectedTargetKey" class="input w-80 max-w-full" :disabled="props.loading || targetOptions.length <= 1" @change="selectTargetByKey(($event.target as HTMLSelectElement).value)">
        <option v-for="target in targetOptions" :key="target.key" :value="target.key">{{ target.label }}</option>
      </select>
    </div>

    <div class="mt-5 grid min-w-0 gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
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
              <input v-model="activeDraft.sku" class="input" :class="hasIssue('sku', 'SKU_MISSING') ? 'border-rose-300 bg-rose-50' : ''" data-publish-draft-field="sku" @input="emit('invalidatePublishValidation')" />
              <button class="btn btn-outline shrink-0 px-3" type="button" @click="generateSku">生成</button>
            </div>
          </label>
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('stock', 'STOCK_MISSING') ? 'text-rose-700' : 'text-slate-500'">库存</span>
            <div class="mt-1 flex gap-2">
              <input v-model="activeDraft.stock" class="input" :class="hasIssue('stock', 'STOCK_MISSING') ? 'border-rose-300 bg-rose-50' : ''" data-publish-draft-field="stock" @input="emit('invalidatePublishValidation')" />
              <button class="btn btn-outline shrink-0 px-3" type="button" @click="useDefaultStock">填 10</button>
            </div>
          </label>
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('upc', 'UPC_MISSING') ? 'text-rose-700' : 'text-slate-500'">UPC / GTIN</span>
            <input v-model="activeDraft.upc" class="input mt-1" :class="hasIssue('upc', 'UPC_MISSING') ? 'border-rose-300 bg-rose-50' : ''" data-publish-draft-field="upc" @input="emit('invalidatePublishValidation')" />
          </label>
          <label class="mt-6 flex items-center gap-2 text-sm font-semibold text-accent-700 dark:text-accent-200">
            <input v-model="activeDraft.allowGtinExemption" type="checkbox" class="size-4 rounded border-accent-300" data-publish-draft-field="allowGtinExemption" @change="emit('invalidatePublishValidation')" />
            允许无 UPC 豁免
          </label>
        </div>

        <div class="mt-4 grid gap-3 md:grid-cols-4">
          <div class="block">
            <label for="precheck-package-length" class="text-xs font-semibold text-accent-500 dark:text-accent-400">长 cm</label>
            <input
              id="precheck-package-length"
              :value="activeDraft.packageDimensions.lengthCm"
              class="input mt-1"
              data-package-dimension-field="lengthCm"
              @input="setPackageDimension('lengthCm', ($event.target as HTMLInputElement).value)"
            />
          </div>
          <div class="block">
            <label for="precheck-package-width" class="text-xs font-semibold text-accent-500 dark:text-accent-400">宽 cm</label>
            <input
              id="precheck-package-width"
              :value="activeDraft.packageDimensions.widthCm"
              class="input mt-1"
              data-package-dimension-field="widthCm"
              @input="setPackageDimension('widthCm', ($event.target as HTMLInputElement).value)"
            />
          </div>
          <div class="block">
            <label for="precheck-package-height" class="text-xs font-semibold text-accent-500 dark:text-accent-400">高 cm</label>
            <input
              id="precheck-package-height"
              :value="activeDraft.packageDimensions.heightCm"
              class="input mt-1"
              data-package-dimension-field="heightCm"
              @input="setPackageDimension('heightCm', ($event.target as HTMLInputElement).value)"
            />
          </div>
          <div class="block">
            <label for="precheck-package-weight" class="text-xs font-semibold text-accent-500 dark:text-accent-400">重量 kg</label>
            <input
              id="precheck-package-weight"
              :value="activeDraft.packageDimensions.weightKg"
              class="input mt-1"
              data-package-dimension-field="weightKg"
              @input="setPackageDimension('weightKg', ($event.target as HTMLInputElement).value)"
            />
          </div>
        </div>

        <div class="mt-4 rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
          <div class="text-sm font-semibold text-accent-950 dark:text-white">保修条款</div>
          <div class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ warrantySummary }}</div>
          <div class="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_7rem_8rem]">
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
      </article>

      <article class="min-w-0 rounded-lg border p-4" :class="props.precheck?.ok ? 'border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10' : 'border-accent-200 bg-accent-50 dark:border-dark-700 dark:bg-dark-950/70'">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">预检结果</h3>
            <p class="mt-2 text-sm" :class="props.precheck?.ok ? 'text-emerald-700 dark:text-emerald-200' : 'text-accent-600 dark:text-accent-300'">{{ props.precheck ? (props.precheck.ok ? '预检通过，可以发布。' : '预检未通过。') : '尚未执行预检。' }}</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline" :disabled="props.loading || !hasCurrentDraft" @click="emit('precheck')">上架预检</button>
            <button class="btn btn-outline" :disabled="props.loading || !canPreviewPayload" @click="emit('previewPayload')">准备素材并预览 Payload</button>
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
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="font-semibold text-accent-950 dark:text-white">Payload 预览</h3>
            <p v-if="props.payloadPreview?.validationDigest" class="mt-1 text-xs text-accent-500 dark:text-accent-400">
              确认指纹 <span class="font-mono">{{ props.payloadPreview.validationDigest.slice(0, 16) }}…</span>，入队时将校验该指纹与当前草稿一致。
            </p>
            <p v-else class="mt-1 text-xs text-accent-500 dark:text-accent-400">尚未生成预览。入队发布前必须先预览并确认以下摘要。</p>
          </div>
          <span v-if="hasPayloadConfirmation" class="badge-info">已确认预览</span>
        </div>
        <div v-if="props.payloadPreview?.summary" class="mt-3 grid gap-2 md:grid-cols-2">
          <div class="rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
            <div class="text-xs font-semibold text-accent-500 dark:text-accent-400">店铺身份</div>
            <div class="mt-1 truncate font-mono text-accent-700 dark:text-accent-200">{{ props.payloadPreview.summary.storeIdentity || '-' }}</div>
            <div v-if="props.payloadPreview.summary.storeLabel" class="mt-0.5 truncate text-xs text-accent-500 dark:text-accent-400">{{ props.payloadPreview.summary.storeLabel }}</div>
          </div>
          <div class="rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
            <div class="text-xs font-semibold text-accent-500 dark:text-accent-400">标题 / 类目</div>
            <div class="mt-1 truncate text-accent-700 dark:text-accent-200">{{ props.payloadPreview.summary.title || '-' }}</div>
            <div class="mt-0.5 truncate text-xs text-accent-500 dark:text-accent-400">{{ props.payloadPreview.summary.categoryId || '无类目' }}</div>
          </div>
          <div class="rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
            <div class="text-xs font-semibold text-accent-500 dark:text-accent-400">价格 / 库存</div>
            <div class="mt-1 text-accent-700 dark:text-accent-200">{{ props.payloadPreview.summary.price || '-' }} {{ props.payloadPreview.summary.listingCurrency }}</div>
            <div class="mt-0.5 text-xs text-accent-500 dark:text-accent-400">库存 {{ props.payloadPreview.summary.stock || '-' }} · 图片 {{ props.payloadPreview.summary.imageCount }} 张</div>
          </div>
          <div class="rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
            <div class="text-xs font-semibold text-accent-500 dark:text-accent-400">草稿 / 平台</div>
            <div class="mt-1 truncate font-mono text-accent-700 dark:text-accent-200">{{ props.payloadPreview.summary.draftId || '-' }}</div>
            <div class="mt-0.5 truncate text-xs text-accent-500 dark:text-accent-400">{{ props.payloadPreview.summary.platform }}{{ props.payloadPreview.summary.site ? ` / ${props.payloadPreview.summary.site}` : '' }}</div>
          </div>
        </div>
        <p v-if="props.payloadPreview?.warning" class="mt-3 text-sm text-amber-700">{{ props.payloadPreview.warning }}</p>
        <ul v-if="props.payloadPreview?.warnings.length" class="mt-3 space-y-1 text-sm text-amber-700">
          <li v-for="issue in props.payloadPreview.warnings" :key="`${issue.code}-${issue.field}-${issue.message}`">⚠ {{ issue.field ? `${issue.field}：` : '' }}{{ issue.message }}</li>
        </ul>
        <pre class="mt-3 max-h-80 w-full max-w-full overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100">{{ props.payloadPreview ? JSON.stringify(props.payloadPreview.payload, null, 2) : '尚未生成 payload。' }}</pre>
      </article>
    </div>
  </section>
</template>

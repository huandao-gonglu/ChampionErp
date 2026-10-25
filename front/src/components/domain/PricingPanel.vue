<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useNow } from '@vueuse/core'
import { selectedPricingTargets } from '@/types/pricing'
import { PhArrowsClockwise, PhCheckCircle, PhLightning, PhPackage, PhSlidersHorizontal, PhWarningCircle } from '@phosphor-icons/vue'
import type { DraftIndexItem, DraftSku, DraftProductContext, MarketplaceOption, PricingInput, PricingResult, UnknownRecord } from '@/types/workflow'
import { buildSkuPricingEntries, pricingRecord, skuPricingCanApply, type SkuPricingEntry } from '@/utils/skuPricing'
import { marketName, marketSite, pricingCommonErrors, pricingErrorReason, pricingFactsIssues, pricingTargetErrors, pricingWorkspaceFingerprint, updateShippingInputCny } from '@/utils/pricingWorkspace'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import WorkspaceHint from '@/components/shared/WorkspaceHint.vue'
import PricingSkuResults from './PricingSkuResults.vue'
import PricingExchangeRates from './PricingExchangeRates.vue'
import PricingFeesEditor from './PricingFeesEditor.vue'
import PricingTemplates from './PricingTemplates.vue'
import PricingScopeDialog from './PricingScopeDialog.vue'
import './pricing-workspace.css'

const props = withDefaults(defineProps<{
  input: PricingInput
  result: PricingResult | null
  draftItems: DraftIndexItem[]
  draftId: string
  draftVersion?: string
  draftTitle: string
  productContext: DraftProductContext
  platformOptions: MarketplaceOption[]
  loading: boolean
  selectionLocked?: boolean
  skuItems: DraftSku[]
  storeConfig?: UnknownRecord
  error?: string
  refreshProduct?: () => Promise<unknown>
}>(), { storeConfig: () => ({}), error: '', draftVersion: '' })
const emit = defineEmits<{ calculate: []; apply: []; restore: []; selectDraft: [draftId: string]; refreshDrafts: []; editDraft: []; editProduct: [skuId: string] }>()
const selectedSkus = computed(() => props.skuItems.filter(row => row.selected))
const targets = computed(() => selectedPricingTargets(props.input))
const entries = computed(() => buildSkuPricingEntries(props.skuItems, props.productContext.skuItems, targets.value))
const allInputErrors = computed(() => [...pricingCommonErrors(props.input), ...targets.value.flatMap(pricingTargetErrors)])
const issues = computed(() => entries.value.filter(entry => entry.errors.length || entry.status === 'loss' || pricingFactsIssues(entry.cost, entry.dimensions).length))
const hasSavedPricing = computed(() => props.skuItems.some(row => Object.keys(pricingRecord(row.pricing?.targets)).length > 0))
const scopeOpen = ref(false)
const problemsOpen = ref(false)
const notice = ref('')
const templatesReady = ref(false)
const ratesReady = ref(false)
const prepared = computed(() => templatesReady.value && ratesReady.value)
const rates = ref<InstanceType<typeof PricingExchangeRates> | null>(null)
const referenceUsdCnyRate = ref(0)
const usdCnyRate = computed(() => {
  const rate = props.input.exchangeRateOverrides?.usd_cny_rate ?? referenceUsdCnyRate.value
  return Number.isFinite(rate) && rate > 0 ? rate : 0
})
function updateShipping(targetKey: string, amount: number | '', skuId?: string) {
  updateShippingInputCny(props.input, props.skuItems, targetKey, amount, usdCnyRate.value, skuId)
}
const stale = ref(false)
const applying = ref(false)
const refreshing = ref(false)
const now = useNow({ interval: 1000 })
const startedAt = ref(Date.now())
const elapsedSeconds = computed(() => Math.max(0, Math.floor((now.value.getTime() - startedAt.value) / 1000)))
const fingerprint = computed(() => pricingWorkspaceFingerprint(props.input, props.skuItems)
  + JSON.stringify(props.productContext.skuItems.map(sku => [sku.id, sku.cost_cny, sku.package_dimensions, sku.battery, sku.liquid])))
function quoteSnapshot() {
  return {
    draftId: props.draftId,
    fingerprint: fingerprint.value,
    applied: new Map(props.skuItems.map(row => [row.pricing, row.pricing.applied])),
  }
}
// 参数暂时偏离只停用报价；恢复原输入时可恢复同一份报价的应用状态。
let reusableQuote: ReturnType<typeof quoteSnapshot> | null = quoteSnapshot()
function invalidateQuote() {
  reusableQuote = null
  stale.value = true
}
const canCalculate = computed(() => !!props.draftId && !!selectedSkus.value.length && !!targets.value.length && !allInputErrors.value.length)
const canApply = computed(() => prepared.value && !stale.value && !props.loading && !requestActive.value && !refreshing.value && !props.error && !!props.result && !allInputErrors.value.length && !issues.value.length && skuPricingCanApply(entries.value)
  && (!!props.result.previewId || entries.value.every(entry => entry.status === 'applied')))
const allApplied = computed(() => canApply.value && entries.value.every(entry => entry.status === 'applied'))
const statusText = computed(() => props.loading ? (restoring.value ? '恢复报价中' : '核价中') : stale.value ? '待重新计算' : allApplied.value ? '已应用' : canApply.value ? '待应用售价' : issues.value.length || props.error || allInputErrors.value.length ? '有待处理项' : hasSavedPricing.value ? '已有报价' : '待计算')
let requestedFingerprint: string | null = null
const requestActive = ref(false)
const restoring = ref(false)
let disposed = false
let restoredVersion = ''
watch([prepared, () => props.draftId, () => props.draftVersion, () => props.loading], async () => {
  if (!prepared.value || !props.draftId || props.loading || requestActive.value || disposed
    || selectedSkus.value.every(row => row.pricing.applied)) return
  const key = `${props.draftId}:${props.draftVersion || ''}`
  if (restoredVersion === key) return
  restoredVersion = key
  requestedFingerprint = fingerprint.value
  requestActive.value = true
  restoring.value = true
  reusableQuote = null
  emit('restore')
  await nextTick()
  if (!props.loading) { requestActive.value = false; restoring.value = false }
}, { immediate: true, flush: 'post' })
async function requestPreview() {
  if (!prepared.value || !canCalculate.value || disposed || props.loading || refreshing.value || requestActive.value) return
  requestedFingerprint = fingerprint.value
  requestActive.value = true
  invalidateQuote()
  applying.value = false
  for (const row of props.skuItems) if (row.pricing.applied) row.pricing.applied = false
  emit('calculate')
  await nextTick()
  // 父级可能在发请求前就因授权或草稿校验失败返回，此时允许修正后再次点击。
  if (!props.loading) requestActive.value = false
}
watch(fingerprint, () => {
  if (props.loading && !requestActive.value) return
  stale.value = !reusableQuote || reusableQuote.draftId !== props.draftId || reusableQuote.fingerprint !== fingerprint.value
  notice.value = ''
  if (!props.loading) for (const row of props.skuItems) {
    row.pricing.applied = stale.value ? false : reusableQuote?.applied.get(row.pricing) ?? false
  }
}, { flush: 'sync' })
watch(() => props.error, error => {
  if (error && !props.loading) { invalidateQuote(); requestActive.value = false; applying.value = false }
})
watch(() => props.loading, async loading => {
  if (loading) { startedAt.value = Date.now(); return }
  await nextTick()
  if (disposed) return
  if (requestActive.value && requestedFingerprint !== null) {
    stale.value = !!props.error || !props.result || requestedFingerprint !== fingerprint.value
    if (restoring.value && hasSavedPricing.value && !props.error && !props.result?.previewId) {
      stale.value = true
      notice.value = '上一轮报价已过期或与当前资料不一致，请重新计算核价。'
    }
    reusableQuote = stale.value ? null : quoteSnapshot()
    if (!stale.value && applying.value && entries.value.every(entry => entry.status === 'applied')) notice.value = `${entries.value.length} 项售价已保存到当前草稿，可继续发布预检。`
  }
  applying.value = false
  restoring.value = false
  requestActive.value = false
}, { flush: 'post' })
async function refreshData() {
  if (props.loading || refreshing.value || requestActive.value) return
  invalidateQuote()
  refreshing.value = true
  try { await props.refreshProduct?.(); await rates.value?.refresh(true) }
  finally { refreshing.value = false }
}
async function apply() {
  if (!canApply.value || allApplied.value) return
  requestedFingerprint = fingerprint.value
  requestActive.value = true
  applying.value = true
  emit('apply')
  await nextTick()
  if (!props.loading) { requestActive.value = false; applying.value = false }
}
function issueText(entry: SkuPricingEntry) {
  const missing = pricingFactsIssues(entry.cost, entry.dimensions)
  return [...(missing.length ? [`请补充${missing.join('、')}`] : []), ...entry.errors, ...(entry.status === 'loss' ? ['当前售价会亏损'] : [])].join('；')
}
const issueGroups = computed(() => {
  const groups = new Map<string, { market: string; message: string; entries: SkuPricingEntry[] }>()
  for (const entry of issues.value) {
    const message = issueText(entry)
    const key = `${entry.target.targetKey}:${message}`
    const group = groups.get(key) || { market: marketName(entry.target), message, entries: [] }
    group.entries.push(entry)
    groups.set(key, group)
  }
  return [...groups.values()]
})
onBeforeUnmount(() => { disposed = true })
</script>

<template>
  <section class="pricing-workspace">
    <header class="pricing-page-header">
      <div class="min-w-0"><div class="flex items-center gap-4"><h2>核价</h2><span class="pricing-tag" :class="issues.length || error ? 'pricing-tag-amber' : 'pricing-tag-green'">{{ statusText }}</span></div><p v-if="!selectionLocked" class="pricing-subtle mt-2 text-sm">{{ draftTitle || '选择草稿后开始核价' }}<span v-if="draftId" class="ml-3">/ 仅核算本次勾选的规格与市场</span></p></div>
      <div class="flex shrink-0 flex-wrap gap-3"><button type="button" class="pricing-button" :disabled="loading || requestActive || refreshing || !draftId" @click="refreshData"><PhArrowsClockwise :size="16" :class="{ 'animate-spin': refreshing }" />{{ refreshing ? '正在刷新…' : '刷新数据' }}</button><button type="button" data-testid="pricing-calculate" class="pricing-button pricing-button-primary" :disabled="loading || requestActive || refreshing || !prepared || !canCalculate" @click="requestPreview"><PhLightning :size="17" />{{ restoring ? '正在恢复报价…' : loading && !applying ? '正在计算…' : '计算核价' }}</button><button type="button" data-testid="pricing-apply" class="pricing-button pricing-apply-button" :disabled="!canApply || allApplied" @click="apply"><PhCheckCircle v-if="allApplied" :size="17" />{{ applying && loading ? '正在保存售价…' : allApplied ? '已应用至草稿' : issues.length ? '处理问题后应用' : `应用 ${entries.length} 项售价` }}</button></div>
    </header>
    <div v-if="!selectionLocked" class="pricing-card mb-4 flex flex-wrap items-center gap-3 p-4"><label class="min-w-48 flex-1"><span class="sr-only">草稿</span><select class="pricing-input w-full" :disabled="loading || !draftItems.length" :value="draftId" @change="emit('selectDraft', ($event.target as HTMLSelectElement).value)"><option value="">选择草稿</option><option v-for="item in draftItems" :key="item.draftId" :value="item.draftId">{{ item.title || item.productTitle || item.draftId }}</option></select></label><button type="button" class="pricing-button" :disabled="loading" @click="emit('refreshDrafts')">刷新草稿</button><button type="button" class="pricing-button" :disabled="loading || !draftId" @click="emit('editDraft')">编辑草稿</button></div>
    <section class="pricing-scope-bar pricing-card" aria-label="当前核价范围"><div class="pricing-scope-count"><PhPackage :size="22" class="shrink-0 text-primary-600" /><div><p class="font-semibold">{{ selectedSkus.length }} 个 SKU</p><p class="pricing-subtle mt-1 text-[11px]">{{ issues.some(entry => pricingFactsIssues(entry.cost, entry.dimensions).length) ? '部分采购或包装资料待补充' : '采购成本与包装按规格读取' }}</p></div></div><div class="min-w-0 flex-1"><div class="flex flex-wrap gap-2"><span v-for="target in targets" :key="target.targetKey" class="pricing-tag">{{ marketName(target) }} · {{ marketSite(target) }}</span><span v-if="!targets.length" class="pricing-subtle text-xs">未选择核价市场</span></div><p class="pricing-subtle mt-2 text-[11px]">按当前店铺物流配置与运输属性匹配可用渠道</p></div><button type="button" class="pricing-button" :disabled="applying || !draftId" @click="scopeOpen = true"><PhSlidersHorizontal :size="16" />修改核价范围</button></section>
    <PricingExchangeRates ref="rates" :input="input" :disabled="loading || requestActive" @ready="ratesReady = true" @rates="referenceUsdCnyRate = $event.usd_cny_rate" @refresh-start="invalidateQuote" />
    <p v-if="loading" role="status" class="pricing-progress"><span class="h-2 w-2 animate-pulse rounded-full bg-primary-600" />{{ restoring ? '正在恢复上一轮报价' : applying ? '正在校验并保存售价' : `正在核价 ${selectedSkus.length} 个 SKU × ${targets.length} 个市场` }}，已等待 {{ elapsedSeconds }} 秒。</p>
    <div class="pricing-main-grid">
      <aside class="pricing-fees pricing-card" aria-label="费用与定价"><div class="pricing-fees-scroll"><div class="pricing-section-heading"><h3 class="inline-flex items-center gap-1">费用与定价<WorkspaceHint label="费用与定价说明" content="共用费用和定价参数，单个 SKU 可单独调整。" /></h3></div><PricingTemplates :input="input" :disabled="applying" :use-default="!hasSavedPricing" @ready="templatesReady = true" @notice="notice = $event" /><PricingFeesEditor :input="input" :disabled="applying" :usd-cny-rate="usdCnyRate" @shipping-amount="updateShipping" /></div></aside>
      <PricingSkuResults :entries="entries" :platform-options="platformOptions" :loading="loading" :stale="stale" :usd-cny-rate="usdCnyRate" :error="error || allInputErrors.join('；')" @shipping-amount="updateShipping" @repair="emit('editProduct', $event.row.sku_id)" @retry="requestPreview" @problems="problemsOpen = true" />
    </div>
    <p class="pricing-footnote" role="status"><PhCheckCircle v-if="notice || allApplied" :size="16" class="text-primary-600" /><PhWarningCircle v-else-if="issues.length" :size="16" class="text-amber-600" /><PhLightning v-else :size="16" class="text-primary-600" /><span>{{ notice || (allApplied ? `${entries.length} 项售价已保存到当前草稿。` : '参数改动后请点击「计算核价」，确认报价后点击「应用售价」保存至当前草稿。') }}</span><button v-if="issues.length" type="button" class="pricing-text-button ml-auto" @click="problemsOpen = true">查看 {{ issues.length }} 项异常</button></p>
    <PricingScopeDialog :open="scopeOpen" :input="input" :sku-items="skuItems" :skus="productContext.skuItems" :targets="input.targets" :store-config="storeConfig" :disabled="applying" @close="scopeOpen = false" />
    <WorkspaceDialog :open="problemsOpen" title="处理核价异常" variant="drawer" width="544px" @close="problemsOpen = false">
      <p class="pricing-subtle text-sm">{{ issues.length }} 项报价待处理，全部选中项就绪后再统一应用。</p>
      <p v-if="allInputErrors.length || (error && !issues.length)" class="pricing-inline-warning mt-5">{{ allInputErrors.join('；') || error }}</p>
      <article v-for="group in issueGroups" :key="`${group.market}:${group.message}`" class="pricing-template-card">
        <h4 class="font-semibold">{{ group.market }} · {{ group.entries.length }} 项报价</h4>
        <p class="mt-2 text-sm">{{ pricingErrorReason(group.message) }}</p>
        <details class="pricing-subtle mt-3 text-xs">
          <summary class="cursor-pointer">查看受影响规格与详细原因</summary>
          <p v-if="pricingErrorReason(group.message) !== group.message" class="mt-3 break-words">{{ group.message }}</p>
          <ul class="mt-3 space-y-3">
            <li v-for="entry in group.entries" :key="entry.key">
              <span>{{ entry.name }}</span>
              <button v-if="pricingFactsIssues(entry.cost, entry.dimensions).length" type="button" class="pricing-button ml-3" :disabled="loading" @click="emit('editProduct', entry.row.sku_id)">去商品补充</button>
            </li>
          </ul>
        </details>
      </article>
      <template #footer><div class="flex gap-3"><button type="button" class="pricing-button flex-1" @click="problemsOpen = false">返回核价</button><button type="button" class="pricing-button pricing-button-primary flex-1" :disabled="loading || !canCalculate" @click="requestPreview(); problemsOpen = false">重新核价</button></div></template>
    </WorkspaceDialog>
  </section>
</template>

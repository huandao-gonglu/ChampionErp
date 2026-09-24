<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useNow } from '@vueuse/core'
import type {
  DraftIndexItem,
  DraftSku,
  DraftProductContext,
  Marketplace,
  MarketplaceOption,
  PricingInput,
  PricingTargetInput,
  PricingResult,
} from '@/types/workflow'
import { buildSkuPricingEntries, skuPricingCanApply } from '@/utils/skuPricing'
import PricingSkuResults from './PricingSkuResults.vue'

const props = defineProps<{
  input: PricingInput
  result: PricingResult | null
  draftItems: DraftIndexItem[]
  draftId: string
  draftTitle: string
  productContext: DraftProductContext
  platformOptions: MarketplaceOption[]
  loading: boolean
  selectionLocked?: boolean
  skuItems: DraftSku[]
}>()

watch(() => JSON.stringify(props.input), () => {
  if (!props.loading) for (const row of props.skuItems) row.pricing.applied = false
}, { flush: 'sync' })

const emit = defineEmits<{
  calculate: []
  apply: []
  selectDraft: [draftId: string]
  refreshDrafts: []
  editDraft: []
}>()

const selectedSkus = computed(() => props.skuItems.filter(row => row.selected))
const now = useNow({ interval: 1000 })
const startedAt = ref(Date.now())
watch(() => props.loading, loading => { if (loading) startedAt.value = Date.now() }, { flush: 'sync' })
const elapsedSeconds = computed(() => Math.max(0, Math.floor((now.value.getTime() - startedAt.value) / 1000)))
const entries = computed(() => buildSkuPricingEntries(props.skuItems, props.productContext.skuItems, props.input.targets))

function numeric(value: unknown) {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function platformOption(platform: Marketplace) {
  return props.platformOptions.find((option) => option.key === platform)
}

function siteLabel(target: PricingTargetInput) {
  const option = platformOption(target.platform)
  const site = option?.sites.find((item) => item.code.toLowerCase() === target.site.toLowerCase())
  return `${option?.label || target.platform} · ${site?.label || target.site}`
}

function siteMeta(target: PricingTargetInput) {
  const option = platformOption(target.platform)
  const site = option?.sites.find((item) => item.code.toLowerCase() === target.site.toLowerCase())
  return [target.site, site?.language, target.listingCurrency || '发布币种待店铺核验'].filter(Boolean).join(' / ')
}

const commonErrors = computed(() => {
  const errors: string[] = []
  for (const [label, value] of [
    ['国内物流', props.input.domesticFreightCny],
    ['包装耗材', props.input.packagingCostCny],
    ['其他固定成本', props.input.otherCostCny],
  ] as const) {
    if (numeric(value) < 0) errors.push(`${label}不能小于 0`)
  }
  if (props.input.exchangeRateMode === 'manual' && numeric(props.input.usdCnyRate) <= 0) errors.push('手动汇率模式需要填写 USD/CNY')
  return errors
})

function targetInputErrors(target: PricingTargetInput) {
  const errors: string[] = []
  const rates = [
    ['平台佣金', target.commissionPercent],
    ['支付/结算手续费', target.paymentFeePercent],
    ['其他平台费用', target.otherFeePercent],
  ] as const
  for (const [label, value] of rates) {
    if (!Number.isFinite(Number(value)) || numeric(value) < 0 || numeric(value) >= 100) errors.push(`${label}必须在 0% 到 100% 之间`)
  }
  const feeTotal = numeric(target.commissionPercent) + numeric(target.paymentFeePercent) + numeric(target.otherFeePercent)
  if (feeTotal >= 100) errors.push('平台费用合计必须小于 100%')
  if (target.pricingMode === 'margin') {
    const margin = numeric(target.targetMarginPercent)
    if (margin < 0 || margin >= 100) errors.push('目标销售利润率必须在 0% 到 100% 之间')
    else if (feeTotal + margin >= 100) errors.push('平台费用合计 + 目标销售利润率必须小于 100%')
  }
  if (target.pricingMode === 'markup' && numeric(target.markupPercent) < 0) errors.push('成本加价率不能小于 0%')
  // 发布币种就绪性由后端依据店铺授权配置确定性校验（STORE_CURRENCY_*），
  // 前端不在核价前用目标快照拦截。
  if (target.pricingMode === 'manual' && numeric(target.manualPrice?.amount) <= 0) errors.push('手动售价必须大于 0')
  if (target.shippingQuoteMode === 'auto') {
    if (!['mercadolibre', 'ozon', 'yandex'].includes(target.platform)) errors.push('当前平台没有自动物流报价，请改为手动报价')
  } else if (numeric(target.shippingAmount) <= 0) {
    errors.push('物流报价金额必须大于 0')
  }
  return errors
}

const allInputErrors = computed(() => [
  ...commonErrors.value,
  ...props.input.targets.flatMap((target) => targetInputErrors(target)),
])

const resultHasErrors = computed(() => entries.value.some(entry => ['error', 'loss'].includes(entry.status)))
const canApply = computed(() => !allInputErrors.value.length && skuPricingCanApply(entries.value))

function feeBudget(target: PricingTargetInput) {
  const fees = numeric(target.commissionPercent) + numeric(target.paymentFeePercent) + numeric(target.otherFeePercent)
  return target.pricingMode === 'margin' ? fees + numeric(target.targetMarginPercent) : fees
}

function updateManualPrice(target: PricingTargetInput, event: Event) {
  target.manualPrice = {
    amount: String((event.target as HTMLInputElement).value || ''),
    currency: target.listingCurrency,
  }
}

function exchangeRateText() {
  const usdCny = props.result?.usdCnyRate || props.input.usdCnyRate
  const mxnUsd = props.result?.mxnUsdRate || props.input.mxnUsdRate
  const rubCny = props.result?.rubCnyRate || props.input.rubCnyRate
  return [
    usdCny > 0 ? `1 USD = ${usdCny} CNY` : '',
    mxnUsd > 0 ? `1 USD = ${mxnUsd} MXN` : '',
    rubCny > 0 ? `1 CNY = ${rubCny} RUB` : '',
  ].filter(Boolean).join(' · ')
}
</script>

<template>
  <section class="min-w-0 rounded-2xl border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-4 border-b border-accent-200 pb-5 dark:border-dark-700">
      <div class="min-w-0">
        <h2 class="text-xl font-black text-accent-950 dark:text-white">核价中心</h2>
        <p class="muted mt-1 break-words">{{ props.draftTitle || '从草稿箱选择草稿后核价。' }}</p>
        <p class="mt-2 text-xs text-accent-500 dark:text-accent-400">设置共用规则 → 按 SKU 计算运费和利润 → 确认并应用售价。</p>
      </div>
      <div class="flex shrink-0 flex-wrap gap-2">
        <button type="button" class="btn btn-outline" :disabled="props.loading || !props.draftId || !props.input.targets.length || !selectedSkus.length" @click="emit('calculate')">{{ props.loading ? '计算中…' : '计算预览' }}</button>
        <button type="button" class="btn btn-primary" :disabled="props.loading || !canApply" @click="emit('apply')">应用售价</button>
      </div>
    </div>

    <p v-if="props.loading" role="status" class="mt-4 text-sm text-accent-600 dark:text-accent-300">
      正在批量核价 {{ selectedSkus.length }} 个 SKU × {{ props.input.targets.length }} 个市场，已等待 {{ elapsedSeconds }} 秒。完成后可在操作日志查看总耗时与共用渠道查询耗时。
    </p>

    <div class="mt-4 grid gap-3 sm:grid-cols-3">
      <label v-if="!props.selectionLocked" class="block sm:col-span-3">
        <span class="field-label">草稿</span>
        <select class="input mt-1" :disabled="props.loading || !props.draftItems.length" :value="props.draftId" @change="emit('selectDraft', ($event.target as HTMLSelectElement).value)">
          <option value="">选择草稿</option><option v-for="item in props.draftItems" :key="item.draftId" :value="item.draftId">{{ item.title || item.productTitle || item.draftId }}</option>
        </select>
      </label>
      <div class="min-w-0 rounded-xl bg-accent-50 p-3 dark:bg-dark-800"><p class="field-label">来源商品</p><p class="mt-2 truncate text-sm font-semibold" :title="props.productContext.sourceTitle">{{ props.productContext.sourceTitle || props.productContext.sourceProductId || '—' }}</p></div>
      <div class="rounded-xl bg-accent-50 p-3 dark:bg-dark-800"><p class="field-label">核价范围</p><p class="mt-2 text-sm font-semibold">{{ selectedSkus.length }} 个 SKU × {{ props.input.targets.length }} 个市场</p></div>
      <div class="rounded-xl bg-accent-50 p-3 dark:bg-dark-800"><p class="field-label">全部 SKU 状态</p><p class="mt-2 text-sm font-semibold" :class="allInputErrors.length || resultHasErrors ? 'text-amber-600 dark:text-amber-300' : ''">{{ allInputErrors.length || resultHasErrors ? '有数据待处理' : canApply ? '可应用售价' : '等待计算预览' }}</p></div>
      <div v-if="!props.selectionLocked" class="flex gap-2 sm:col-span-3"><button type="button" class="btn btn-outline" :disabled="props.loading" @click="emit('refreshDrafts')">刷新</button><button type="button" class="btn btn-secondary" :disabled="props.loading || !props.draftId" @click="emit('editDraft')">编辑</button></div>
    </div>

    <details open class="group mt-5 rounded-xl border border-accent-200 dark:border-dark-700">
      <summary class="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 rounded-xl bg-accent-50 p-4 [&::-webkit-details-marker]:hidden dark:bg-dark-800">
        <div><h3 class="font-bold">共用核价设置</h3><p class="muted mt-1">作为全部已选 SKU 的默认值，单独设置优先生效。</p></div>
        <span class="text-xs font-semibold text-brand-700 dark:text-brand-300"><span class="group-open:hidden">展开设置</span><span class="hidden group-open:inline">收起设置</span></span>
      </summary>
      <fieldset :disabled="loading" class="min-w-0 space-y-5 p-4">
        <div class="grid gap-5 xl:grid-cols-2">
          <section>
            <h4 class="text-sm font-bold">每件商品的费用默认值</h4>
            <p class="muted mt-1">采购成本和包装尺寸取各 SKU 自身资料；以下固定费用可在 SKU 页单独覆盖。</p>
            <div class="mt-3 grid gap-3 sm:grid-cols-3">
              <label><span class="field-label">国内物流（CNY）</span><input v-model.number="props.input.domesticFreightCny" class="input mt-1" type="number" min="0" step="0.01" /></label>
              <label><span class="field-label">包装耗材（CNY）</span><input v-model.number="props.input.packagingCostCny" class="input mt-1" type="number" min="0" step="0.01" /></label>
              <label><span class="field-label">其他固定成本（CNY）</span><input v-model.number="props.input.otherCostCny" class="input mt-1" type="number" min="0" step="0.01" /></label>
            </div>
            <p class="field-label mt-4">共用货物属性</p>
            <div class="mt-2 flex flex-wrap gap-4 text-sm"><label class="flex items-center gap-2"><input v-model="props.input.battery" type="checkbox" />所选 SKU 含电池</label><label class="flex items-center gap-2"><input v-model="props.input.liquid" type="checkbox" />所选 SKU 含液体</label></div>
          </section>
          <section>
            <div class="flex flex-wrap items-center justify-between gap-2"><h4 class="text-sm font-bold">共用汇率</h4><select v-model="props.input.exchangeRateMode" aria-label="汇率方式" class="input w-auto"><option value="live">实时 API</option><option value="manual">手动汇率</option></select></div>
            <div v-if="props.input.exchangeRateMode === 'manual'" class="mt-3 grid gap-3 sm:grid-cols-3">
              <label><span class="field-label">1 USD = CNY</span><input v-model.number="props.input.usdCnyRate" class="input mt-1" type="number" min="0" step="0.0001" /></label>
              <label><span class="field-label">1 USD = MXN</span><input v-model.number="props.input.mxnUsdRate" class="input mt-1" type="number" min="0" step="0.0001" /></label>
              <label><span class="field-label">1 CNY = RUB</span><input v-model.number="props.input.rubCnyRate" class="input mt-1" type="number" min="0" step="0.0001" /></label>
            </div>
            <p class="mt-3 text-xs text-accent-500 dark:text-accent-400">{{ exchangeRateText() || '计算预览时读取实时汇率。' }}<span v-if="props.result?.exchangeRateSource"> · 来源：{{ props.result.exchangeRateSource }}{{ props.result.exchangeRateCached ? '（缓存）' : '' }}</span></p>
          </section>
        </div>
        <p v-if="commonErrors.length" class="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">{{ commonErrors.join('；') }}</p>

        <section class="border-t border-accent-200 pt-4 dark:border-dark-700">
          <h4 class="font-bold">各市场的默认规则</h4>
          <p class="muted mt-1">同一市场共用佣金和定价规则。自动运费、建议售价和利润在下方按 SKU 分别展示。</p>
          <div class="mt-4 grid gap-4 xl:grid-cols-2">
            <article v-for="target in props.input.targets" :key="target.targetKey" class="min-w-0 rounded-xl border border-accent-200 p-4 dark:border-dark-700" data-testid="pricing-market-defaults">
              <div class="flex flex-wrap items-start justify-between gap-2"><div><h5 class="font-bold">{{ siteLabel(target) }}</h5><p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ siteMeta(target) }}</p></div><span class="badge-muted">共用规则</span></div>
              <div class="mt-4 grid gap-3 sm:grid-cols-3">
                <label><span class="field-label">平台佣金（抽成）%</span><input v-model.number="target.commissionPercent" class="input mt-1" type="number" min="0" max="99.99" step="0.01" /></label>
                <label><span class="field-label">支付/结算手续费 %</span><input v-model.number="target.paymentFeePercent" class="input mt-1" type="number" min="0" max="99.99" step="0.01" /></label>
                <label><span class="field-label">其他平台费用 %</span><input v-model.number="target.otherFeePercent" class="input mt-1" type="number" min="0" max="99.99" step="0.01" /></label>
              </div>
              <div class="mt-4 grid gap-3 sm:grid-cols-2">
                <label><span class="field-label">默认定价方式</span><select v-model="target.pricingMode" class="input mt-1"><option value="margin">目标销售利润率</option><option value="markup">成本加价率</option><option value="manual">统一手动售价</option></select></label>
                <label v-if="target.pricingMode === 'margin'"><span class="field-label">目标销售利润率 %</span><input v-model.number="target.targetMarginPercent" class="input mt-1" type="number" min="0" max="99.99" step="0.01" /></label>
                <label v-else-if="target.pricingMode === 'markup'"><span class="field-label">成本加价率 %</span><input v-model.number="target.markupPercent" class="input mt-1" type="number" min="0" step="0.01" /></label>
                <label v-else><span class="field-label">统一手动售价（{{ target.listingCurrency || '店铺发布币种' }}）</span><input :value="target.manualPrice?.amount || ''" class="input mt-1" type="number" min="0" step="0.01" @input="updateManualPrice(target, $event)" /></label>
              </div>
              <p class="mt-2 text-xs text-accent-500 dark:text-accent-400">{{ target.pricingMode === 'margin' ? `利润 ÷ 售价；平台费用 + 目标利润率为 ${feeBudget(target).toFixed(2)}%，必须小于 100%。` : target.pricingMode === 'markup' ? '利润 ÷ 成本；100% 表示利润等于成本。' : '此金额用于该市场所有未单独设置售价的 SKU。' }}</p>
              <div class="mt-4 border-t border-accent-200 pt-4 dark:border-dark-700">
                <h6 class="text-sm font-bold">国际物流规则</h6>
                <div class="mt-3 grid gap-3 sm:grid-cols-2">
                  <label><span class="field-label">报价方式</span><select v-model="target.shippingQuoteMode" class="input mt-1"><option value="auto" :disabled="!['mercadolibre', 'ozon', 'yandex'].includes(target.platform)">自动获取最低运费</option><option value="manual">统一手动运费</option></select></label>
                  <label><span class="field-label">物流报价币种</span><select v-model="target.shippingCurrency" class="input mt-1"><option value="USD">USD</option><option value="CNY">CNY</option></select></label>
                  <label v-if="target.shippingQuoteMode === 'manual'" class="sm:col-span-2"><span class="field-label">默认每件国际运费（{{ target.shippingCurrency }}）</span><input v-model.number="target.shippingAmount" class="input mt-1" type="number" min="0" step="0.01" placeholder="物流商报价" /></label>
                </div>
                <p class="mt-3 rounded-lg bg-accent-50 p-3 text-xs leading-relaxed text-accent-600 dark:bg-dark-800 dark:text-accent-300">{{ target.shippingQuoteMode === 'auto' ? '点击“计算预览”后，按各 SKU 的包装尺寸和重量独立获取最低有效运费。金额、渠道和计费重量见下方 SKU 明细。' : '此金额是该市场每个 SKU 的默认运费。不同规格运费不同时，请在下方明细中单独填写。' }}</p>
              </div>
              <p v-if="targetInputErrors(target).length" class="mt-3 rounded-lg bg-amber-50 p-3 text-xs font-semibold text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">{{ targetInputErrors(target).join('；') }}</p>
            </article>
          </div>
          <p v-if="!props.input.targets.length" class="mt-3 text-sm text-accent-500">当前草稿没有目标市场，请先在草稿中选择。</p>
        </section>
      </fieldset>
    </details>

    <PricingSkuResults :entries="entries" :platform-options="props.platformOptions" :loading="props.loading" />
  </section>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import type {
  DraftIndexItem,
  DraftSku,
  UnknownRecord,
  DraftProductContext,
  Marketplace,
  MarketplaceOption,
  PricingInput,
  PricingTargetInput,
  PricingTargetResult,
  PricingResult,
} from '@/types/workflow'
import { useCurrency } from '@/composables/useCurrency'

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

const { formatMoney, formatPercent } = useCurrency()
const resultByKey = computed(() => new Map((props.result?.results || []).map((item) => [item.targetKey, item])))

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

function resultFor(target: PricingTargetInput): PricingTargetResult | undefined {
  return resultByKey.value.get(target.targetKey)
}

function appliedNetProceedsFor(target: PricingTargetInput) {
  const result = resultFor(target)
  return result?.destinationResults.some((destination) => destination.pricingModel === 'net_proceeds')
    ? result.appliedNetProceeds
    : null
}

function resultErrors(target: PricingTargetInput) {
  const result = resultFor(target)
  if (!result?.errors.length) return ''
  return result.errors
    .map((item) => typeof item === 'string' ? item : String(item.message || item.field || ''))
    .filter(Boolean)
    .join('；')
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
    if (target.platform !== 'mercadolibre') errors.push('当前平台没有自动物流报价，请改为手动报价')
  } else if (numeric(target.shippingAmount) <= 0) {
    errors.push('物流报价金额必须大于 0')
  }
  return errors
}

const allInputErrors = computed(() => [
  ...commonErrors.value,
  ...props.input.targets.flatMap((target) => targetInputErrors(target)),
])

const resultHasErrors = computed(() => Boolean(props.result?.results.some((item) => item.errors.length)))
const hasLoss = computed(() => Boolean(props.result?.results.some((item) => item.isLoss)))
const canApply = computed(() => Boolean(props.result?.results.length) && !allInputErrors.value.length && !resultHasErrors.value && !hasLoss.value)

function feeBudget(target: PricingTargetInput) {
  const fees = numeric(target.commissionPercent) + numeric(target.paymentFeePercent) + numeric(target.otherFeePercent)
  return target.pricingMode === 'margin' ? fees + numeric(target.targetMarginPercent) : fees
}

function shippingCny(target: PricingTargetInput) {
  const result = resultFor(target)
  if (result) return result.shippingCostCny
  if (target.shippingQuoteMode === 'auto') return 0
  return target.shippingCurrency === 'CNY'
    ? numeric(target.shippingAmount)
    : numeric(target.shippingAmount) * numeric(props.input.usdCnyRate)
}

function pricingStep(target: PricingTargetInput) {
  if (target.listingCurrency === 'RUB') return 100
  if (target.listingCurrency === 'MXN') return 10
  return 1
}

function applySuggested(target: PricingTargetInput) {
  const suggested = resultFor(target)?.suggestedPrice
  if (suggested && numeric(suggested.amount) > 0) {
    target.pricingMode = 'manual'
    target.manualPrice = { ...suggested }
  }
}

function adjustPrice(target: PricingTargetInput, multiplier: number) {
  const base = numeric(target.manualPrice?.amount) || numeric(resultFor(target)?.suggestedPrice.amount)
  const amount = Math.max(0, Math.round((base + pricingStep(target) * multiplier) * 100) / 100)
  target.pricingMode = 'manual'
  target.manualPrice = { amount: String(amount), currency: target.listingCurrency }
}

function updateManualPrice(target: PricingTargetInput, event: Event) {
  target.manualPrice = {
    amount: String((event.target as HTMLInputElement).value || ''),
    currency: target.listingCurrency,
  }
}

function applyPrice() {
  emit('apply')
}

function targetStatus(target: PricingTargetInput) {
  if (targetInputErrors(target).length || resultErrors(target)) return '需处理'
  const result = resultFor(target)
  if (!result) return '待预览'
  if (result.isLoss) return '亏损'
  return target.pricingMode === 'manual' && numeric(target.manualPrice?.amount) > 0 ? '手动售价' : '建议价可用'
}

function targetStatusClass(target: PricingTargetInput) {
  const status = targetStatus(target)
  if (status === '建议价可用' || status === '手动售价') return 'badge-success'
  if (status === '亏损' || status === '需处理') return 'badge-danger'
  return 'badge-muted'
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
        <p class="muted mt-1 truncate">{{ props.draftTitle || '从草稿箱选择草稿后核价。' }}</p>
        <p class="mt-2 text-xs text-accent-500 dark:text-accent-400">先确认商品成本和物流报价，再预览利润，最后明确应用售价。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-outline" :disabled="props.loading || !props.draftId || !props.input.targets.length" @click="emit('calculate')">
          {{ props.loading ? '计算中…' : '计算预览' }}
        </button>
        <button class="btn btn-primary" :disabled="props.loading || !canApply" @click="applyPrice">应用售价</button>
      </div>
    </div>

    <div class="mt-4 grid gap-3" :class="props.selectionLocked ? 'lg:grid-cols-3' : 'lg:grid-cols-[minmax(18rem,32rem)_1fr_1fr_auto]'">
      <label v-if="!props.selectionLocked" class="block">
        <span class="field-label">草稿</span>
        <select class="input mt-1" :disabled="props.loading || !props.draftItems.length" :value="props.draftId" @change="emit('selectDraft', ($event.target as HTMLSelectElement).value)">
          <option value="">选择草稿</option>
          <option v-for="item in props.draftItems" :key="item.draftId" :value="item.draftId">{{ item.title || item.productTitle || item.draftId }}</option>
        </select>
      </label>
      <div class="rounded-xl bg-accent-50 p-3 dark:bg-dark-800">
        <p class="field-label">来源商品</p>
        <p class="mt-2 truncate text-sm font-semibold text-accent-950 dark:text-white" :title="props.productContext.sourceTitle || props.productContext.sourceProductId">{{ props.productContext.sourceTitle || props.productContext.sourceProductId || '-' }}</p>
      </div>
      <div class="rounded-xl bg-accent-50 p-3 dark:bg-dark-800">
        <p class="field-label">SKU 售价</p>
        <p class="mt-2 text-sm font-semibold text-accent-950 dark:text-white">每个 SKU × 目标市场独立保存</p>
      </div>
      <div class="rounded-xl bg-accent-50 p-3 dark:bg-dark-800">
        <p class="field-label">核价状态</p>
        <p class="mt-2 text-sm font-semibold" :class="allInputErrors.length || resultHasErrors ? 'text-amber-600 dark:text-amber-300' : canApply ? 'text-emerald-600 dark:text-emerald-300' : 'text-accent-600 dark:text-accent-300'">
          {{ allInputErrors.length || resultHasErrors ? '有数据待处理' : canApply ? '可应用售价' : '等待计算预览' }}
        </p>
      </div>
      <div v-if="!props.selectionLocked" class="flex items-end gap-2">
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('refreshDrafts')">刷新</button>
        <button class="btn btn-secondary" :disabled="props.loading || !props.draftId" @click="emit('editDraft')">编辑</button>
      </div>
    </div>

    <div v-if="commonErrors.length" class="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm font-semibold text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
      {{ commonErrors.join('；') }}
    </div>

    <div class="mt-5 overflow-x-auto">
      <table class="w-full text-left text-sm">
        <thead class="text-xs text-slate-500"><tr><th class="p-2">SKU</th><th>目标市场</th><th>售价</th><th>利润 CNY</th><th>核价状态</th></tr></thead><tbody>
          <template v-for="row in skuItems.filter(row => row.selected)" :key="row.sku_id">
            <tr v-for="(quote, key) in (row.pricing.targets as Record<string, UnknownRecord> || {})" :key="String(key)" class="border-t border-accent-200 dark:border-dark-700"><td class="p-2">{{ productContext.skuItems.find(sku => sku.id === row.sku_id)?.name || row.sku }}</td><td>{{ key }}</td><td>{{ (quote.applied_price as UnknownRecord)?.amount }} {{ (quote.applied_price as UnknownRecord)?.currency }}</td><td :class="quote.is_loss ? 'text-red-500' : ''">{{ quote.profit_cny }}</td><td>{{ (quote.errors as unknown[])?.length ? '数据待处理' : row.pricing.applied ? '已应用' : '预览' }}</td></tr>
            <tr v-if="!Object.keys(row.pricing.targets as UnknownRecord || {}).length" class="border-t border-accent-200 dark:border-dark-700"><td class="p-2">{{ productContext.skuItems.find(sku => sku.id === row.sku_id)?.name || row.sku }}</td><td colspan="4">尚未核价</td></tr>
          </template>
        </tbody>
      </table>
      <p class="muted mt-2">下方市场卡片显示首个已选 SKU 的计算示例；上表列出全部规格的独立结果。修改参数后需重新应用售价。</p>
    </div>

    <div class="mt-5 grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(22rem,1fr)]">
      <div class="space-y-5">
        <article class="rounded-xl border border-accent-200 p-4 dark:border-dark-700">
          <div>
            <h3 class="font-black text-accent-950 dark:text-white">1 · 共用费用模板</h3>
            <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">以下费用按每件商品计；采购成本和包装资料取各 SKU 的实际值。单个 SKU 可在 SKU 页覆盖费用。</p>
          </div>
          <div class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <label><span class="field-label">国内物流（CNY）</span><input v-model.number="props.input.domesticFreightCny" class="input mt-1" type="number" min="0" step="0.01" /></label>
            <label><span class="field-label">包装耗材（CNY）</span><input v-model.number="props.input.packagingCostCny" class="input mt-1" type="number" min="0" step="0.01" /></label>
            <label><span class="field-label">其他固定成本（CNY）</span><input v-model.number="props.input.otherCostCny" class="input mt-1" type="number" min="0" step="0.01" /></label>
          </div>
        </article>

        <article class="rounded-xl border border-accent-200 p-4 dark:border-dark-700">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="font-black text-accent-950 dark:text-white">2 · 汇率</h3>
              <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">成本固定以 CNY 统计，平台售价使用目标市场币种。</p>
            </div>
            <select v-model="props.input.exchangeRateMode" class="input w-auto min-w-36">
              <option value="live">实时 API</option>
              <option value="manual">手动汇率</option>
            </select>
          </div>
          <div v-if="props.input.exchangeRateMode === 'manual'" class="mt-4 grid gap-3 sm:grid-cols-3">
            <label><span class="field-label">1 USD 等于多少 CNY</span><input v-model.number="props.input.usdCnyRate" class="input mt-1" type="number" min="0" step="0.0001" /></label>
            <label><span class="field-label">1 USD 等于多少 MXN</span><input v-model.number="props.input.mxnUsdRate" class="input mt-1" type="number" min="0" step="0.0001" /></label>
            <label><span class="field-label">1 CNY 等于多少 RUB</span><input v-model.number="props.input.rubCnyRate" class="input mt-1" type="number" min="0" step="0.0001" /></label>
          </div>
          <div class="mt-4 rounded-lg bg-accent-50 p-3 text-xs leading-relaxed text-accent-600 dark:bg-dark-800 dark:text-accent-300">
            <span v-if="exchangeRateText()">{{ exchangeRateText() }}</span>
            <span v-else>计算预览时会读取实时汇率。</span>
            <span v-if="props.result?.exchangeRateSource"> · 来源：{{ props.result.exchangeRateSource }}{{ props.result.exchangeRateCached ? '（缓存）' : '' }}</span>
          </div>
        </article>
      </div>

      <aside class="rounded-xl border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/40 xl:sticky xl:top-4 xl:self-start">
        <h3 class="font-black text-accent-950 dark:text-white">计算规则</h3>
        <div class="mt-4 space-y-4 text-xs leading-relaxed text-accent-600 dark:text-accent-300">
          <div><p class="font-bold text-accent-950 dark:text-white">目标销售利润率</p><p class="mt-1">利润 ÷ 售价。平台费用合计 + 目标利润率必须小于 100%。</p></div>
          <div><p class="font-bold text-accent-950 dark:text-white">成本加价率</p><p class="mt-1">利润 ÷ 成本。填写 100% 表示利润等于成本，是有效输入。</p></div>
          <div><p class="font-bold text-accent-950 dark:text-white">国际物流费</p><p class="mt-1">只填写一种报价原币，折算 CNY 由系统计算，不能重复填写 USD 和 CNY。</p></div>
          <div><p class="font-bold text-accent-950 dark:text-white">应用售价</p><p class="mt-1">计算预览不会保存；只有点击“应用售价”才会写入草稿。</p></div>
        </div>
      </aside>
    </div>

    <div class="mt-5 space-y-4">
      <article v-for="target in props.input.targets" :key="target.targetKey" class="rounded-xl border border-accent-200 p-4 dark:border-dark-700">
        <div class="flex min-w-0 items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="truncate font-black text-accent-950 dark:text-white" :title="siteLabel(target)">3 · {{ siteLabel(target) }}</h3>
            <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ siteMeta(target) }}</p>
          </div>
          <span :class="targetStatusClass(target)">{{ targetStatus(target) }}</span>
        </div>

        <div class="mt-4 grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(20rem,1fr)]">
          <div class="space-y-5">
            <div>
              <p class="text-sm font-bold text-accent-950 dark:text-white">平台费用</p>
              <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">佣金是平台抽成；支付手续费是收款或结算通道费用。</p>
              <div class="mt-3 grid gap-3 sm:grid-cols-3">
                <label><span class="field-label">平台佣金（抽成）%</span><input v-model.number="target.commissionPercent" class="input mt-1" type="number" min="0" max="99.99" step="0.01" /></label>
                <label><span class="field-label">支付/结算手续费 %</span><input v-model.number="target.paymentFeePercent" class="input mt-1" type="number" min="0" max="99.99" step="0.01" /></label>
                <label><span class="field-label">其他平台费用 %</span><input v-model.number="target.otherFeePercent" class="input mt-1" type="number" min="0" max="99.99" step="0.01" /></label>
              </div>
            </div>

            <div>
              <p class="text-sm font-bold text-accent-950 dark:text-white">定价方式</p>
              <div class="mt-2 flex flex-wrap gap-2">
                <button class="btn py-2" :class="target.pricingMode === 'margin' ? 'btn-primary' : 'btn-outline'" @click="target.pricingMode = 'margin'">目标利润率</button>
                <button class="btn py-2" :class="target.pricingMode === 'markup' ? 'btn-primary' : 'btn-outline'" @click="target.pricingMode = 'markup'">成本加价率</button>
                <button class="btn py-2" :class="target.pricingMode === 'manual' ? 'btn-primary' : 'btn-outline'" @click="target.pricingMode = 'manual'">手动售价</button>
              </div>
              <div class="mt-3 grid gap-3 sm:grid-cols-[minmax(12rem,18rem)_1fr]">
                <label v-if="target.pricingMode === 'margin'"><span class="field-label">目标销售利润率 %</span><input v-model.number="target.targetMarginPercent" class="input mt-1" type="number" min="0" max="99.99" step="0.01" /><p class="mt-1 text-[11px] text-accent-500 dark:text-accent-400">利润 ÷ 售价</p></label>
                <label v-else-if="target.pricingMode === 'markup'"><span class="field-label">成本加价率 %</span><input v-model.number="target.markupPercent" class="input mt-1" type="number" min="0" step="0.01" /><p class="mt-1 text-[11px] text-accent-500 dark:text-accent-400">利润 ÷ 成本，可填写 100%</p></label>
                <label v-else><span class="field-label">手动售价（{{ target.listingCurrency || '店铺发布币种' }}）</span><input :value="target.manualPrice?.amount || ''" class="input mt-1" type="number" min="0" step="0.01" @input="updateManualPrice(target, $event)" /></label>
                <div class="rounded-lg bg-accent-50 p-3 dark:bg-dark-800">
                  <div class="flex items-center justify-between text-xs"><span class="font-semibold text-accent-600 dark:text-accent-300">比例预算</span><span :class="feeBudget(target) >= 100 ? 'text-rose-600 dark:text-rose-300' : 'text-accent-950 dark:text-white'">{{ feeBudget(target).toFixed(2) }}%</span></div>
                  <div class="mt-2 h-2 overflow-hidden rounded-full bg-accent-200 dark:bg-dark-700"><div class="h-full rounded-full" :class="feeBudget(target) >= 100 ? 'bg-rose-500' : 'bg-emerald-500'" :style="{ width: `${Math.min(100, Math.max(0, feeBudget(target)))}%` }" /></div>
                  <p class="mt-2 text-[11px] text-accent-500 dark:text-accent-400">目标利润率模式下，该比例必须小于 100%。</p>
                </div>
              </div>
            </div>

            <div>
              <p class="text-sm font-bold text-accent-950 dark:text-white">国际物流报价</p>
              <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">原币金额只填写一次，折算物流成本 CNY 为只读结果。</p>
              <div class="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <label><span class="field-label">报价方式</span><select v-model="target.shippingQuoteMode" class="input mt-1"><option value="auto" :disabled="target.platform !== 'mercadolibre'">系统按计费重估算</option><option value="manual">手动填写物流报价</option></select></label>
                <label><span class="field-label">物流报价币种</span><select v-model="target.shippingCurrency" class="input mt-1" :disabled="target.shippingQuoteMode === 'auto'"><option value="USD">USD</option><option value="CNY">CNY</option></select></label>
                <label><span class="field-label">物流报价金额</span><input v-model.number="target.shippingAmount" class="input mt-1" type="number" min="0" step="0.01" :disabled="target.shippingQuoteMode === 'auto'" :placeholder="target.shippingQuoteMode === 'auto' ? '计算时估算' : '物流商报价'" /></label>
                <div class="rounded-lg bg-accent-50 p-3 dark:bg-dark-800"><p class="field-label">折算物流成本（只读）</p><p class="mt-2 text-sm font-bold text-accent-950 dark:text-white">{{ formatMoney(shippingCny(target), 'CNY') }}</p><p class="mt-1 text-[11px] text-accent-500 dark:text-accent-400">{{ resultFor(target)?.shippingSource === 'system_estimate' ? '系统估算' : '按报价币种折算' }}</p></div>
              </div>
            </div>
          </div>

          <aside class="rounded-xl bg-accent-50 p-4 dark:bg-dark-800/80">
            <p class="field-label">建议买家售价</p>
            <p class="mt-2 text-2xl font-black text-accent-950 dark:text-white">{{ resultFor(target) ? formatMoney(numeric(resultFor(target)!.suggestedPrice.amount), resultFor(target)!.suggestedPrice.currency) : '-' }}</p>
            <button v-if="numeric(resultFor(target)?.suggestedPrice.amount) > 0" class="mt-2 text-xs font-bold text-brand-700 hover:underline dark:text-brand-300" @click="applySuggested(target)">改为手动售价</button>

            <div class="mt-4"><span class="field-label">本次买家售价</span><p class="mt-1 text-lg font-bold">{{ resultFor(target) ? formatMoney(numeric(resultFor(target)!.appliedPrice.amount), resultFor(target)!.appliedPrice.currency) : '先计算预览' }}</p></div>
            <div v-if="appliedNetProceedsFor(target)" class="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-900/60 dark:bg-emerald-950/30">
              <span class="field-label text-emerald-700 dark:text-emerald-300">Mercado 期望到账额</span>
              <p class="mt-1 text-lg font-bold text-emerald-800 dark:text-emerald-200">{{ formatMoney(numeric(appliedNetProceedsFor(target)!.amount), appliedNetProceedsFor(target)!.currency) }}</p>
              <p class="mt-1 text-[11px] text-emerald-700/80 dark:text-emerald-300/80">用于 pricing_model=net_proceeds 的销售市场，不是买家看到的售价。</p>
            </div>
            <div class="mt-2 grid grid-cols-3 gap-2">
              <button class="btn btn-outline px-2 py-1.5 text-xs" @click="adjustPrice(target, -1)">-{{ pricingStep(target) }}</button>
              <button class="btn btn-outline px-2 py-1.5 text-xs" @click="adjustPrice(target, 1)">+{{ pricingStep(target) }}</button>
              <button class="btn btn-outline px-2 py-1.5 text-xs" @click="adjustPrice(target, 5)">+{{ pricingStep(target) * 5 }}</button>
            </div>

            <div v-if="resultFor(target)" class="mt-4 space-y-2 border-t border-accent-200 pt-4 text-xs dark:border-dark-700">
              <div class="flex justify-between gap-3"><span class="text-accent-500 dark:text-accent-400">商品与物流总成本</span><strong>{{ formatMoney(resultFor(target)!.totalCostCny, 'CNY') }}</strong></div>
              <div class="flex justify-between gap-3"><span class="text-accent-500 dark:text-accent-400">平台佣金</span><strong>{{ formatMoney(resultFor(target)!.commissionCny, 'CNY') }}</strong></div>
              <div class="flex justify-between gap-3"><span class="text-accent-500 dark:text-accent-400">支付/结算手续费</span><strong>{{ formatMoney(resultFor(target)!.paymentFeeCny, 'CNY') }}</strong></div>
              <div v-if="resultFor(target)!.otherFeeCny" class="flex justify-between gap-3"><span class="text-accent-500 dark:text-accent-400">其他平台费用</span><strong>{{ formatMoney(resultFor(target)!.otherFeeCny, 'CNY') }}</strong></div>
              <div class="flex justify-between gap-3 border-t border-accent-200 pt-2 dark:border-dark-700"><span class="font-bold text-accent-700 dark:text-accent-200">预计利润</span><strong :class="resultFor(target)!.isLoss ? 'text-rose-600 dark:text-rose-300' : 'text-emerald-600 dark:text-emerald-300'">{{ formatMoney(resultFor(target)!.profitCny, 'CNY') }}</strong></div>
              <div class="flex justify-between gap-3"><span class="text-accent-500 dark:text-accent-400">实际利润率</span><strong>{{ formatPercent(resultFor(target)!.marginPercent) }}</strong></div>
              <div class="flex justify-between gap-3"><span class="text-accent-500 dark:text-accent-400">盈亏平衡售价</span><strong>{{ formatMoney(numeric(resultFor(target)!.minimumPrice.amount), resultFor(target)!.minimumPrice.currency) }}</strong></div>
            </div>

            <p v-if="targetInputErrors(target).length" class="mt-4 rounded-lg bg-amber-100 p-3 text-xs font-semibold text-amber-900 dark:bg-amber-950/60 dark:text-amber-200">{{ targetInputErrors(target).join('；') }}</p>
            <p v-if="resultErrors(target)" class="mt-3 rounded-lg bg-rose-100 p-3 text-xs font-semibold text-rose-900 dark:bg-rose-950/60 dark:text-rose-200">{{ resultErrors(target) }}</p>
          </aside>
        </div>
      </article>

      <p v-if="!props.input.targets.length" class="rounded-xl border border-dashed border-accent-300 p-8 text-center text-sm text-accent-500 dark:border-dark-600 dark:text-accent-300">当前草稿没有目标市场。</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useCurrency } from '@/composables/useCurrency'
import { pricingRecord, type SkuPricingEntry } from '@/utils/skuPricing'

const props = defineProps<{ entry: SkuPricingEntry; loading: boolean }>()
const { formatMoney } = useCurrency()
const quote = computed(() => props.entry.quote || {})
const basis = computed(() => pricingRecord(quote.value.calculation_basis))
const evidence = computed(() => pricingRecord(basis.value.shipping_evidence))
const destinations = computed(() => (Array.isArray(quote.value.destination_results) ? quote.value.destination_results : []).map(pricingRecord))
const overrides = computed(() => pricingRecord(pricingRecord(props.entry.row.pricing_overrides?.targets)[props.entry.target.targetKey.toLowerCase()]))
const currency = computed(() => String(quote.value.listing_currency || props.entry.target.listingCurrency || ''))
const step = computed(() => currency.value === 'RUB' ? 100 : currency.value === 'MXN' ? 10 : 1)

function money(value: unknown, unit = 'CNY') {
  return value === undefined || value === null || value === '' ? '—' : formatMoney(Number(value), unit)
}
function price(value: unknown) {
  const item = pricingRecord(value)
  return item.currency ? money(item.amount, String(item.currency)) : '—'
}
function setOverride(key: string, value: unknown) {
  const row = props.entry.row
  const own = { ...overrides.value }
  if (value === '' || value === null) delete own[key]
  else own[key] = value
  row.pricing_overrides = {
    ...row.pricing_overrides,
    targets: { ...pricingRecord(row.pricing_overrides?.targets), [props.entry.target.targetKey.toLowerCase()]: own },
  }
  row.pricing.applied = false
}
function setManualPrice(amount: string) {
  setOverride('manual_price', amount ? { amount, currency: currency.value } : null)
}
function adjustPrice(multiplier: number) {
  const base = Number(pricingRecord(overrides.value.manual_price).amount ?? pricingRecord(quote.value.applied_price).amount ?? 0)
  setManualPrice(String(Math.max(0, Math.round((base + step.value * multiplier) * 100) / 100)))
}
</script>

<template>
  <div class="space-y-4 p-4">
    <p class="text-sm font-bold">{{ entry.name }} · {{ entry.target.platform }} / {{ entry.target.site }}</p>
    <div class="grid gap-4 lg:grid-cols-2">
      <section class="rounded-xl border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
        <h4 class="text-sm font-bold">此 SKU 的物流报价</h4>
        <template v-if="entry.quote && !entry.errors.length">
          <p class="mt-2 text-lg font-bold">{{ money(quote.shipping_amount, String(quote.shipping_currency || entry.target.shippingCurrency)) }}</p>
          <p class="muted mt-1">折算成本 {{ money(quote.shipping_cost_cny) }} · {{ quote.shipping_quote_mode === 'auto' ? '自动报价' : '手动报价' }}</p>
          <dl v-if="Object.keys(evidence).length" class="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
            <dt class="text-accent-500">物流渠道</dt><dd>{{ evidence.route || '—' }}</dd>
            <dt class="text-accent-500">原币报价</dt><dd>{{ evidence.original_amount }} {{ evidence.original_currency }}</dd>
            <dt class="text-accent-500">计费重量</dt><dd>{{ evidence.billable_g ?? '—' }} g</dd>
          </dl>
          <details v-if="Object.keys(evidence).length" class="mt-3 text-xs text-accent-500 dark:text-accent-400">
            <summary class="cursor-pointer">报价依据</summary>
            <p class="mt-2 break-all">版本/来源：{{ evidence.tariff_version || evidence.endpoint || '—' }}</p>
            <p class="mt-1">换算比例：{{ evidence.exchange_rate ?? '—' }} · 报价时间：{{ evidence.quoted_at || '—' }}</p>
            <p v-if="evidence.limitation" class="mt-1">{{ evidence.limitation }}</p>
          </details>
          <div v-if="destinations.length" class="mt-3 space-y-2 border-t border-accent-200 pt-3 text-xs dark:border-dark-700">
            <div v-for="destination in destinations" :key="`${destination.site_id}:${destination.logistic_type}`">
              <p class="font-semibold">{{ destination.site_id }} · {{ destination.logistic_type }}</p>
              <p v-if="destination.shipping_currency">运费 {{ money(destination.shipping_amount, String(destination.shipping_currency)) }}</p>
              <p>{{ destination.pricing_model === 'net_proceeds' ? 'Mercado 期望到账额' : '买家售价' }} {{ price(destination.pricing_model === 'net_proceeds' ? destination.net_proceeds : destination.price) }}</p>
            </div>
          </div>
        </template>
        <p v-else class="muted mt-3">{{ entry.errors.length ? '本次核价存在异常，请处理下方问题后重新计算。' : '点击“计算预览”，按此 SKU 的包装尺寸、重量和目标市场获取运费。' }}</p>
      </section>

      <section class="rounded-xl border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
        <h4 class="text-sm font-bold">此 SKU 的成本与利润</h4>
        <dl v-if="entry.quote && !entry.errors.length" class="mt-3 space-y-2 text-sm">
          <div v-for="[key, label] in [['total_cost_cny', '商品与物流总成本'], ['commission_cny', '平台佣金'], ['payment_fee_cny', '支付/结算手续费'], ['other_fee_cny', '其他平台费用'], ['profit_cny', '预计利润']]" :key="key" class="flex justify-between gap-3">
            <dt class="text-accent-500 dark:text-accent-400">{{ label }}</dt><dd class="font-semibold">{{ money(quote[key]) }}</dd>
          </div>
          <div class="flex justify-between gap-3"><dt class="text-accent-500 dark:text-accent-400">盈亏平衡售价</dt><dd>{{ price(quote.minimum_price) }}</dd></div>
          <div v-if="quote.applied_net_proceeds" class="rounded-lg bg-emerald-50 p-3 dark:bg-emerald-950/40">
            <dt class="font-bold">Mercado 期望到账额</dt><dd>{{ price(quote.applied_net_proceeds) }}</dd>
            <p class="mt-1 text-xs">不是买家看到的售价，按此 SKU 的销售市场单独计算。</p>
          </div>
        </dl>
        <p v-else class="muted mt-3">{{ entry.errors.length ? '本次核价未完成，暂不展示成本和利润结果。' : '尚未核价。计算后在这里查看成本拆分和利润。' }}</p>
      </section>
    </div>

    <fieldset :disabled="loading" class="rounded-xl border border-accent-200 p-4 dark:border-dark-700">
      <legend class="px-1 text-sm font-bold">仅调整此 SKU · {{ entry.target.platform }} / {{ entry.target.site }}</legend>
      <p class="muted">留空沿用共用规则；修改后重新计算预览并应用售价。采购成本、包装和固定费用可在 SKU 页单独调整。</p>
      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <label><span class="field-label">此 SKU 手动运费（{{ entry.target.shippingCurrency }}）</span><input :value="overrides.shipping_amount ?? ''" type="number" min="0" step="0.01" class="input mt-1" placeholder="沿用共用报价规则" @input="setOverride('shipping_amount', ($event.target as HTMLInputElement).value)" /></label>
        <label><span class="field-label">此 SKU 手动售价（{{ currency || '先核价确认币种' }}）</span><input :value="pricingRecord(overrides.manual_price).amount ?? ''" :disabled="!currency" type="number" min="0" step="0.01" class="input mt-1" placeholder="沿用共用定价规则" @input="setManualPrice(($event.target as HTMLInputElement).value)" /></label>
      </div>
      <div v-if="entry.quote && currency" class="mt-3 flex flex-wrap gap-2">
        <button type="button" class="btn btn-outline text-xs" :disabled="Number(pricingRecord(quote.suggested_price).amount || 0) <= 0" @click="setManualPrice(String(pricingRecord(quote.suggested_price).amount))">采用此 SKU 建议价</button>
        <button v-for="multiplier in [-1, 1, 5]" :key="multiplier" type="button" class="btn btn-outline text-xs" @click="adjustPrice(multiplier)">{{ multiplier > 0 ? '+' : '' }}{{ multiplier * step }} {{ currency }}</button>
      </div>
    </fieldset>
    <p v-if="entry.errors.length" class="rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">{{ entry.errors.join('；') }}</p>
    <p v-else-if="entry.status === 'loss'" class="text-sm font-semibold text-rose-600 dark:text-rose-300">此 SKU 当前售价会亏损，请调整定价。</p>
  </div>
</template>

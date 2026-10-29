<script setup lang="ts">
import { computed } from 'vue'
import { PhCheckCircle, PhPackage, PhSealCheck, PhTruck } from '@phosphor-icons/vue'
import { useCurrency } from '@/composables/useCurrency'
import { pricingRecord, shippingCostCny, type SkuPricingEntry } from '@/utils/skuPricing'
import { marketName, marketSite, pricingFactsIssues, shippingInputCny } from '@/utils/pricingWorkspace'

const props = withDefaults(defineProps<{ entry: SkuPricingEntry; loading: boolean; stale?: boolean; usdCnyRate?: number }>(), { stale: false, usdCnyRate: 0 })
const emit = defineEmits<{ repair: []; retry: []; shippingAmount: [amount: number | ''] }>()
const { formatMoney, formatPercent } = useCurrency()
const quote = computed(() => props.entry.quote || {})
const basis = computed(() => pricingRecord(quote.value.calculation_basis))
const returnLoss = computed(() => pricingRecord(quote.value.return_loss))
const evidence = computed(() => pricingRecord(basis.value.shipping_evidence))
const fulfillmentPlan = computed(() => pricingRecord(evidence.value.fulfillment_plan))
const commission = computed(() => pricingRecord(basis.value.commission_evidence))
const commissionSource = computed(() => commission.value.source === 'official_tariff' ? 'Ozon 官方费率表' : commission.value.source === 'official_fee_page' ? 'Mercado 官方费率页' : `${marketName(props.entry.target)} 官方上架前试算`)
const destinations = computed(() => (Array.isArray(quote.value.destination_results) ? quote.value.destination_results : []).map(pricingRecord))
const overrides = computed(() => pricingRecord(pricingRecord(props.entry.row.pricing_overrides?.targets)[props.entry.target.targetKey.toLowerCase()]))
const currency = computed(() => String(quote.value.listing_currency || props.entry.target.listingCurrency || ''))
const step = computed(() => currency.value === 'RUB' ? 100 : currency.value === 'MXN' ? 10 : 1)
const factIssues = computed(() => pricingFactsIssues(props.entry.cost, props.entry.dimensions))
const fresh = computed(() => !!props.entry.quote && !props.loading && !props.stale && !props.entry.errors.length && !factIssues.value.length)
const manualPricing = computed(() => !!overrides.value.manual_price || quote.value.pricing_mode === 'manual')
const fees = computed(() => [
  { label: '采购成本', value: basis.value.cost_cny ?? props.entry.cost },
  { label: '国内物流', value: basis.value.domestic_freight_cny ?? basis.value.freight_cny },
  { label: '包装耗材', value: basis.value.packaging_cost_cny ?? basis.value.prep_fee_cny },
  { label: '其他固定成本', value: basis.value.other_cost_cny },
  { label: '国际运费', value: quote.value.shipping_cost_cny },
  ...(props.entry.target.platform === 'ozon' ? [{ label: 'OGL 电子服务费', value: quote.value.electronic_service_fee_cny }] : []),
  { label: '预计货损分摊', value: quote.value.return_loss_cny ?? 0 },
  { label: `官方佣金 · ${quote.value.commission_percent ?? '—'}%`, value: quote.value.commission_cny },
  { label: `支付 / 结算费 · ${quote.value.payment_fee_percent ?? 0}%`, value: quote.value.payment_fee_cny },
  { label: `其他平台费 · ${quote.value.other_fee_percent ?? 0}%`, value: quote.value.other_fee_cny },
])
const total = computed(() => Number(quote.value.total_cost_cny || 0) + Number(quote.value.commission_cny || 0) + Number(quote.value.payment_fee_cny || 0) + Number(quote.value.other_fee_cny || 0))
function money(value: unknown, unit = 'CNY') {
  return value === undefined || value === null || value === '' ? '—' : formatMoney(Number(value), unit)
}
function price(value: unknown) { const item = pricingRecord(value); return item.currency ? money(item.amount, String(item.currency)) : '—' }
function setOverride(key: string, value: unknown) {
  const row = props.entry.row
  const own = { ...overrides.value }
  if (value === '' || value === null) delete own[key]
  else own[key] = value
  row.pricing_overrides = { ...row.pricing_overrides, targets: { ...pricingRecord(row.pricing_overrides?.targets), [props.entry.target.targetKey.toLowerCase()]: own } }
  row.pricing.applied = false
}
function setManualPrice(amount: string) { setOverride('manual_price', amount ? { amount, currency: currency.value } : null) }
function adjustPrice(multiplier: number) {
  const base = Number(pricingRecord(overrides.value.manual_price).amount ?? pricingRecord(quote.value.applied_price).amount ?? 0)
  setManualPrice(String(Math.max(0, Math.round((base + step.value * multiplier) * 100) / 100)))
}
</script>

<template>
  <div class="pricing-detail-content">
    <div class="flex flex-wrap items-start justify-between gap-3"><div><p class="font-semibold">{{ entry.name }}</p><p class="pricing-subtle mt-1 text-xs">{{ entry.row.sku || entry.row.sku_id }}</p></div><span class="pricing-tag">{{ marketName(entry.target) }} · {{ marketSite(entry.target) }}</span></div>
    <div v-if="fresh" class="pricing-detail-hero">
      <p class="flex items-center justify-center gap-1.5 text-xs"><PhCheckCircle :size="15" />{{ entry.status === 'applied' ? '已应用至草稿' : '已完成核价' }}</p>
      <p class="mt-4 text-xs">{{ manualPricing ? '本次买家售价' : '建议售价' }} · {{ currency }}</p><p class="mt-2 text-4xl font-bold tracking-tight">{{ pricingRecord(quote.applied_price).amount }}</p>
      <div class="mt-4 flex justify-center gap-5 text-sm" :class="entry.status === 'loss' ? 'text-rose-600 dark:text-rose-300' : ''"><span>利润 {{ money(quote.profit_cny) }}</span><span>利润率 {{ formatPercent(Number(quote.margin_percent || 0)) }}</span></div>
      <div v-if="quote.applied_net_proceeds" class="mt-4 border-t border-primary-200 pt-3 text-sm dark:border-primary-700"><p>Mercado 期望到账额 {{ price(quote.applied_net_proceeds) }}</p><p class="mt-1 text-xs">此金额不是买家看到的售价。</p></div>
    </div>
    <div v-else class="pricing-inline-warning mt-5" role="status"><p>{{ loading ? '正在重新核价，旧报价已停用。' : stale ? '参数已改变，请点击「重新核价」。' : '本次核价尚未完成，请处理问题后重试。' }}</p><p v-if="entry.errors.length" class="mt-2">{{ entry.errors.join('；') }}</p><p v-if="entry.quote && commission.purpose !== 'prelisting'" class="mt-2">历史售价：{{ price(quote.applied_price) }}，请重新核价后应用。</p><div class="mt-3 flex gap-4"><button v-if="factIssues.length" type="button" class="underline" :disabled="loading" @click="emit('repair')">去商品补充{{ factIssues.join('、') }}</button><button type="button" class="underline" :disabled="loading" @click="emit('retry')">重新核价</button></div></div>
    <section v-if="fresh" class="pricing-detail-section">
      <div class="pricing-section-heading"><h4>费用明细</h4><span class="pricing-subtle">统一折算 CNY</span></div>
      <dl class="mt-4 space-y-3 text-sm"><div v-for="fee in fees" :key="fee.label" class="flex justify-between gap-4"><dt class="pricing-subtle">{{ fee.label }}</dt><dd>{{ money(fee.value) }}</dd></div><div class="flex justify-between gap-4 border-t border-accent-200 pt-3 font-semibold dark:border-dark-700"><dt>费用合计</dt><dd>{{ money(total) }}</dd></div></dl>
      <p v-if="Number(returnLoss.return_rate_percent) > 0" class="pricing-subtle mt-3 text-xs" data-testid="return-loss-detail">货损为本地预估：退货率 {{ returnLoss.return_rate_percent }}%，{{ returnLoss.return_disposition === 'resell' ? '退回再售（采购成本完整回收）' : '弃货' }}，每次损失 {{ money(returnLoss.single_loss_cny) }}。分摊＝退货率 ÷（1－退货率）× 每次损失；利润已扣除分摊金额。</p>
      <p class="pricing-subtle mt-3 text-xs">盈亏平衡估算（按本次费率）：{{ price(quote.minimum_price) }}</p>
    </section>
    <section v-if="fresh && Object.keys(commission).length" class="pricing-detail-evidence">
      <PhSealCheck :size="20" class="shrink-0 text-primary-600" /><div class="min-w-0 flex-1"><h4 class="text-sm font-semibold">{{ commissionSource }}</h4><p class="pricing-subtle mt-1 text-xs">已匹配当前类目与本次售价</p><details class="pricing-subtle mt-3 text-xs"><summary class="cursor-pointer">佣金查询依据</summary><p class="mt-2 break-all">类目：{{ commission.category_id || '—' }} · 查询时间：{{ commission.quoted_at || '—' }}</p><p v-if="commission.file_url" class="mt-2"><a class="underline" :href="String(commission.file_url)" target="_blank" rel="noopener noreferrer">查看官方费率表</a> · 生效日期：{{ commission.effective_from }}</p><p v-if="commission.category_page" class="mt-2"><a class="underline" :href="String(commission.category_page)" target="_blank" rel="noopener noreferrer">查看官方类目费率</a><a v-if="commission.pricing_page" class="ml-3 underline" :href="String(commission.pricing_page)" target="_blank" rel="noopener noreferrer">查看固定收费标准</a></p><p v-if="commission.scope" class="mt-2">适用范围：{{ commission.scope }} · 品牌规则：{{ commission.brand_rule }}</p><p v-if="commission.limitation" class="mt-2">{{ commission.limitation }}</p></details></div>
    </section>
    <section v-if="fresh" class="pricing-detail-evidence">
      <PhTruck :size="20" class="shrink-0 text-primary-600" /><div class="min-w-0 flex-1"><h4 class="text-sm font-semibold">{{ quote.shipping_quote_mode === 'auto' ? '自动 · 平台物流' : '手动物流报价' }}</h4><p class="pricing-subtle mt-1 text-xs">{{ evidence.route || '共用或单独填写的运费' }} · 计费重 {{ quote.billable_weight_kg ?? (Number(evidence.billable_g || 0) / 1000) }} kg</p><p class="pricing-subtle mt-1 text-xs">{{ ['length_cm', 'width_cm', 'height_cm'].map(key => entry.dimensions[key] || '—').join(' × ') }} cm · 包装重 {{ entry.dimensions.weight_kg || '—' }} kg</p><details v-if="Object.keys(evidence).length" class="pricing-subtle mt-3 text-xs"><summary class="cursor-pointer">物流报价依据</summary><p class="mt-2">原币报价：{{ evidence.original_amount }} {{ evidence.original_currency }}</p><p class="mt-2">计费重量：{{ evidence.billable_g ?? '—' }} g · 换算比例：{{ evidence.exchange_rate ?? '—' }}</p><p class="mt-2 break-all">版本 / 来源：{{ evidence.tariff_version || evidence.endpoint || '—' }}</p><p class="mt-2">报价时间：{{ evidence.quoted_at || '—' }}</p><p v-if="evidence.limitation" class="mt-2">{{ evidence.limitation }}</p></details></div>
    </section>
    <section v-if="fresh && fulfillmentPlan.fingerprint" class="pricing-detail-section" aria-label="核价物流方案">
      <h4>本次核价与发布方案</h4>
      <p class="mt-3 text-sm">发布仓库：{{ fulfillmentPlan.publish_warehouse_name }}（{{ fulfillmentPlan.publish_warehouse_id }}）</p>
      <p class="mt-2 text-sm">物流渠道：{{ fulfillmentPlan.route_name }}</p>
      <p class="pricing-subtle mt-3 text-xs">发布库存仅使用此仓库；方案配置变化后需要重新核价。</p>
    </section>
    <div v-if="fresh && destinations.length" class="pricing-inline-note mt-4"><div v-for="destination in destinations" :key="`${destination.site_id}:${destination.logistic_type}`" class="mb-3 last:mb-0"><p class="font-semibold">{{ destination.site_id }} · {{ destination.logistic_type }}</p><p class="mt-1">{{ destination.pricing_model === 'net_proceeds' ? 'Mercado 期望到账额' : '买家售价' }} {{ price(destination.pricing_model === 'net_proceeds' ? destination.net_proceeds : destination.price) }}</p><p v-if="destination.shipping_currency" class="mt-1">运费 {{ money(shippingCostCny({ usd_cny_rate: quote.usd_cny_rate, ...destination })) }} · 官方佣金 {{ destination.commission_percent ?? '—' }}%</p></div></div>
    <fieldset :disabled="loading" class="pricing-detail-section">
      <legend class="sr-only">单独调整此 SKU</legend><h4 class="font-semibold">单独调整此 SKU</h4><p class="pricing-subtle mt-2 text-xs">留空沿用共用规则，修改后请点击「计算核价」。</p>
      <details class="mt-4">
        <summary class="cursor-pointer text-sm">货损单独调整（选填）</summary>
        <label class="mt-4 block"><span class="field-label">此 SKU 预计退货率（%）</span><input :value="overrides.return_rate_percent ?? ''" aria-label="此 SKU 预计退货率 %" type="number" min="0" max="99.99" step="0.01" class="pricing-input mt-2 w-full" :placeholder="`沿用共用 ${entry.target.returnRatePercent || 0}%`" @input="setOverride('return_rate_percent', ($event.target as HTMLInputElement).value)" /></label>
        <label class="mt-4 block"><span class="field-label">此 SKU 退货处理方式</span><select :value="overrides.return_disposition ?? ''" aria-label="此 SKU 退货处理方式" class="pricing-input mt-2 w-full" @change="setOverride('return_disposition', ($event.target as HTMLSelectElement).value)"><option value="">沿用共用（{{ entry.target.returnDisposition === 'resell' ? '退回再售' : '弃货' }}）</option><option value="discard">弃货</option><option value="resell">退回再售</option></select></label>
        <label class="mt-4 block"><span class="field-label">此 SKU 每次退货额外费用（元/件）</span><input :value="overrides.return_handling_cost_cny ?? ''" aria-label="此 SKU 每次退货额外费用（人民币）" type="number" min="0" step="0.01" class="pricing-input mt-2 w-full" :placeholder="`沿用共用 ${entry.target.returnHandlingCostCny || 0} 元`" @input="setOverride('return_handling_cost_cny', ($event.target as HTMLInputElement).value)" /></label>
        <p class="pricing-subtle mt-2 text-xs">退回再售按采购成本完整回收；额外费用包含退回、处理及其他不退费用，不重复填写原发货运费。</p>
      </details>
      <template v-if="!['yandex', 'ozon'].includes(entry.target.platform)">
        <label class="mt-4 block"><span class="field-label">此 SKU 手动运费（人民币）</span><input :value="shippingInputCny(overrides.shipping_amount, entry.target.shippingCurrency, usdCnyRate)" :disabled="entry.target.shippingCurrency !== 'CNY' && !(usdCnyRate > 0)" aria-label="此 SKU 手动运费（人民币）" type="number" min="0" step="0.01" class="pricing-input mt-2 w-full" placeholder="沿用自动或共用运费" @input="emit('shippingAmount', ($event.target as HTMLInputElement).value === '' ? '' : Number(($event.target as HTMLInputElement).value))" /></label>
        <p v-if="entry.target.shippingCurrency !== 'CNY' && !(usdCnyRate > 0)" class="pricing-subtle mt-2 text-xs">已有外币运费需要 USD/CNY 汇率，请等待加载或填写汇率。</p>
      </template>
      <label class="mt-4 block"><span class="field-label">此 SKU 手动售价（{{ currency || '核价后确认币种' }}）</span><input :value="pricingRecord(overrides.manual_price).amount ?? ''" :disabled="!currency" type="number" min="0" step="0.01" class="pricing-input mt-2 w-full" placeholder="沿用建议售价" @input="setManualPrice(($event.target as HTMLInputElement).value)" /></label>
      <div v-if="fresh && currency" class="mt-3 flex flex-wrap gap-2"><button type="button" class="pricing-button !px-2 !py-1.5 text-xs" :disabled="Number(pricingRecord(quote.suggested_price).amount || 0) <= 0" @click="setManualPrice(String(pricingRecord(quote.suggested_price).amount))">采用建议价</button><button v-for="multiplier in [-1, 1, 5]" :key="multiplier" type="button" class="pricing-button !px-2 !py-1.5 text-xs" @click="adjustPrice(multiplier)">{{ multiplier > 0 ? '+' : '' }}{{ multiplier * step }} {{ currency }}</button></div>
    </fieldset>
    <p v-if="fresh" class="pricing-subtle mt-5 flex items-center gap-2 text-xs"><PhPackage :size="16" />运输属性：{{ basis.battery ? '含电池' : '不含电池' }} · {{ basis.liquid ? '含液体' : '不含液体' }}</p>
    <p v-if="entry.status === 'loss' && fresh" class="pricing-field-error mt-4">此 SKU 当前售价会亏损，请调整定价。</p>
  </div>
</template>

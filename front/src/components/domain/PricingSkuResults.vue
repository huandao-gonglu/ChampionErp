<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhCaretRight, PhCheckCircle, PhMagnifyingGlass, PhSlidersHorizontal, PhWarningCircle } from '@phosphor-icons/vue'
import { useCurrency } from '@/composables/useCurrency'
import { pricingRecord, shippingCostCny, type SkuPricingEntry } from '@/utils/skuPricing'
import { marketName, marketSite, pricingFactsIssues, skuHasPricingOverrides } from '@/utils/pricingWorkspace'
import type { MarketplaceOption } from '@/types/workflow'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import PricingSkuDetails from './PricingSkuDetails.vue'

const props = withDefaults(defineProps<{ entries: SkuPricingEntry[]; platformOptions: MarketplaceOption[]; loading: boolean; stale?: boolean; error?: string; usdCnyRate?: number }>(), { stale: false, error: '', usdCnyRate: 0 })
const emit = defineEmits<{ repair: [entry: SkuPricingEntry]; retry: []; problems: []; shippingAmount: [targetKey: string, amount: number | '', skuId: string] }>()
const { formatMoney, formatPercent } = useCurrency()
const search = ref('')
const market = ref('')
const problemsOnly = ref(false)
const detailKey = ref('')
const markets = computed(() => [...new Map(props.entries.map(entry => [entry.target.targetKey, entry])).values()])
const skuCount = computed(() => new Set(props.entries.map(entry => entry.row.sku_id)).size)
function factIssues(entry: SkuPricingEntry) { return pricingFactsIssues(entry.cost, entry.dimensions) }
function problem(entry: SkuPricingEntry) { return ['error', 'loss'].includes(entry.status) || !!factIssues(entry).length }
const problemCount = computed(() => props.entries.filter(problem).length)
const readyCount = computed(() => props.stale || props.loading ? 0 : props.entries.filter(entry => entry.quote && !problem(entry)).length)
const applied = computed(() => readyCount.value > 0 && props.entries.every(entry => entry.status === 'applied'))
const filtered = computed(() => props.entries.filter(entry => (
  (!market.value || entry.target.targetKey === market.value)
  && (!problemsOnly.value || problem(entry))
  && `${entry.name} ${entry.row.sku} ${entry.row.sku_id}`.toLowerCase().includes(search.value.trim().toLowerCase())
)))
const groups = computed(() => {
  const rows = new Map<string, SkuPricingEntry[]>()
  filtered.value.forEach(entry => { const group = rows.get(entry.row.sku_id) || []; group.push(entry); rows.set(entry.row.sku_id, group) })
  return [...rows.values()]
})
const activeEntry = computed(() => props.entries.find(entry => entry.key === detailKey.value) || null)
watch(markets, values => { if (!values.some(entry => entry.target.targetKey === market.value)) market.value = '' })
function money(value: unknown, currency = 'CNY') {
  return value === undefined || value === null || value === '' || !Number.isFinite(Number(value)) ? '—' : formatMoney(Number(value), currency)
}
function price(value: unknown) {
  const item = pricingRecord(value)
  return item.currency ? money(item.amount, String(item.currency)) : '—'
}
function showQuote(entry: SkuPricingEntry) { return entry.quote && !props.loading && !props.stale && !entry.errors.length && !factIssues(entry).length }
function logistics(entry: SkuPricingEntry) {
  const basis = pricingRecord(entry.quote?.calculation_basis)
  return pricingRecord(pricingRecord(basis.shipping_evidence).fulfillment_plan)
}
function status(entry: SkuPricingEntry) {
  if (factIssues(entry).length) return '资料待补充'
  if (entry.errors.length) return '核价异常'
  if (props.loading) return '计算中…'
  if (props.stale) return '等待重新核价'
  return ({ pending: '待核价', error: '核价异常', loss: '亏损', applied: '已应用', preview: '未应用' })[entry.status]
}
</script>

<template>
  <section class="pricing-results pricing-card" aria-label="SKU 核价结果" :aria-busy="loading">
    <header class="pricing-results-header">
      <div class="flex flex-wrap items-center gap-3"><h3>SKU 核价结果</h3><span class="pricing-tag">{{ entries.length }} 项报价</span><span class="ml-auto flex items-center gap-1.5 text-xs" :class="problemCount || error ? 'text-amber-600 dark:text-amber-300' : 'text-primary-700 dark:text-primary-300'"><PhWarningCircle v-if="problemCount || error" :size="16" /><PhCheckCircle v-else-if="readyCount" :size="16" />{{ loading ? '查询佣金与运费中' : applied ? '已全部应用' : problemCount ? `${readyCount} / ${entries.length} 项已就绪` : stale ? '等待重新核价' : readyCount ? '已计算' : '等待手动计算' }}</span></div>
      <p class="pricing-subtle mt-2 text-xs">{{ skuCount }} 个规格 × {{ markets.length }} 个市场，展开可查看物流与佣金依据。</p>
      <div class="pricing-results-toolbar">
        <div class="pricing-results-tabs" aria-label="筛选核价市场">
          <button type="button" :class="{ active: !market }" :aria-pressed="!market" @click="market = ''">全部 {{ entries.length }}</button>
          <button v-for="entry in markets" :key="entry.target.targetKey" type="button" :class="{ active: market === entry.target.targetKey }" :aria-pressed="market === entry.target.targetKey" @click="market = entry.target.targetKey">{{ marketName(entry.target) }} {{ entries.filter(item => item.target.targetKey === entry.target.targetKey).length }}</button>
        </div>
        <label class="pricing-results-search"><PhMagnifyingGlass :size="15" /><input v-model="search" type="search" aria-label="搜索规格或 SKU" placeholder="搜索规格或 SKU" /></label>
        <button type="button" class="pricing-button pricing-problem-filter" :class="{ 'pricing-button-selected': problemsOnly }" :aria-pressed="problemsOnly" @click="problemsOnly = !problemsOnly"><PhSlidersHorizontal :size="15" />仅看异常<span v-if="problemCount"> {{ problemCount }}</span></button>
      </div>
      <div v-if="error" role="alert" class="pricing-results-alert"><span class="min-w-0 flex-1">{{ error }}</span><button type="button" :disabled="loading" @click="emit('retry')">重试</button><button type="button" @click="emit('problems')">查看详情</button></div>
    </header>
    <div class="pricing-results-scroll" tabindex="0" aria-label="SKU 报价表（可横向滚动）">
      <table class="pricing-results-table">
        <caption class="sr-only">按 SKU 合并采购成本和包装，按市场分别展示国际运费、官方佣金、建议售价与人民币利润。</caption>
        <colgroup><col style="width:23%" /><col style="width:13%" /><col style="width:14%" /><col style="width:13%" /><col style="width:15%" /><col style="width:17%" /><col style="width:5%" /></colgroup>
        <thead><tr><th scope="col">SKU / 采购与包装</th><th scope="col">市场</th><th scope="col">国际运费</th><th scope="col">官方佣金</th><th scope="col">建议售价</th><th scope="col">利润 / 利润率</th><th scope="col"><span class="sr-only">明细</span></th></tr></thead>
        <tbody v-for="(group, groupIndex) in groups" :key="group[0].row.sku_id" :class="{ 'pricing-sku-alternate': groupIndex % 2 }">
          <tr v-for="(entry, index) in group" :key="entry.key" :data-sku-id="entry.row.sku_id" :data-target-key="entry.target.targetKey">
            <th v-if="index === 0" :rowspan="group.length" scope="rowgroup" class="pricing-sku-cell">
              <p class="font-semibold">{{ entry.name }}</p><p class="pricing-subtle mt-1 break-all text-[11px]">{{ entry.row.sku || entry.row.sku_id }}</p>
              <p class="mt-5 text-xs font-normal">采购 {{ money(entry.cost) }}</p><p class="pricing-subtle mt-2 whitespace-nowrap text-[11px]">{{ ['length_cm', 'width_cm', 'height_cm'].map(key => entry.dimensions[key] || '—').join(' × ') }} cm</p><p class="pricing-subtle mt-1.5 text-[11px]">{{ entry.dimensions.weight_kg || '—' }} kg</p>
              <button v-if="factIssues(entry).length" type="button" class="pricing-tag pricing-tag-amber mt-2" :disabled="loading" @click="emit('repair', entry)">补充资料</button><span v-else class="pricing-tag mt-2">资料完整</span>
            </th>
            <td><p class="font-medium">{{ marketName(entry.target) }}</p><p class="pricing-subtle mt-1 text-[11px]">{{ marketSite(entry.target) }}</p></td>
            <td><template v-if="showQuote(entry)"><p class="whitespace-nowrap">{{ money(shippingCostCny(entry.quote || {})) }}</p><p class="pricing-subtle mt-1 text-[10px]">CNY · {{ entry.quote?.shipping_quote_mode === 'auto' ? '自动' : '手动' }}</p><p v-if="logistics(entry).route_name" class="pricing-subtle mt-1 text-[10px]">{{ logistics(entry).route_name }}</p><p v-if="logistics(entry).publish_warehouse_name" class="pricing-subtle mt-1 text-[10px]">发布仓：{{ logistics(entry).publish_warehouse_name }}</p></template><span v-else class="pricing-subtle text-xs">{{ status(entry) }}</span></td>
            <td><template v-if="showQuote(entry)"><p>{{ entry.quote?.commission_percent ?? '—' }}%</p><p class="pricing-subtle mt-1 text-[10px]">{{ money(entry.quote?.commission_cny) }}<span v-if="entry.quote?.commission_cny !== undefined" class="sr-only"> CNY</span></p></template><span v-else class="pricing-subtle">—</span></td>
            <td><template v-if="showQuote(entry)"><p class="whitespace-nowrap text-[15px] font-bold">{{ price(entry.quote?.applied_price) }}</p><p class="pricing-subtle mt-1 text-[10px]">{{ entry.quote?.applied_net_proceeds ? `到账 ${price(entry.quote.applied_net_proceeds)}` : `发布币种 ${entry.quote?.listing_currency || entry.target.listingCurrency}` }}</p><span v-if="skuHasPricingOverrides(entry.row, entry.target.targetKey)" class="mt-1 block text-[10px] text-primary-700 dark:text-primary-300">有单独设置</span></template><span v-else class="pricing-subtle">—</span></td>
            <td :class="entry.status === 'loss' ? 'text-rose-600 dark:text-rose-300' : 'text-primary-700 dark:text-primary-300'"><template v-if="showQuote(entry)"><p class="whitespace-nowrap font-semibold">{{ money(entry.quote?.profit_cny) }}</p><p class="mt-1 text-[10px]">{{ formatPercent(Number(entry.quote?.margin_percent || 0)) }}<span v-if="entry.status === 'loss'"> · 亏损</span><span v-else-if="entry.status === 'applied'"> · 已应用</span></p></template><span v-else class="pricing-subtle">—</span></td>
            <td class="!px-1"><button type="button" class="pricing-detail-button" :aria-label="`查看 ${entry.name} ${marketName(entry.target)} 核价明细`" @click="detailKey = entry.key"><PhCaretRight :size="16" /><span class="sr-only">查看明细</span></button></td>
          </tr>
        </tbody>
        <tbody v-if="!groups.length"><tr><td colspan="7" class="!py-16 text-center"><PhMagnifyingGlass :size="28" class="mx-auto text-accent-300" /><p class="pricing-subtle mt-3">{{ entries.length ? '没有符合筛选条件的 SKU。' : '请先选择核价 SKU 和目标市场。' }}</p><button v-if="entries.length" type="button" class="pricing-text-button mx-auto mt-3" @click="search = ''; market = ''; problemsOnly = false">清除筛选</button></td></tr></tbody>
      </table>
    </div>
    <footer class="pricing-results-footer"><span>共 {{ groups.length }} 个 SKU · {{ filtered.length }} 项报价<span v-if="filtered.length !== entries.length"> / 全部 {{ entries.length }} 项</span></span><span>连续滚动查看</span></footer>
  </section>
  <WorkspaceDialog :open="!!activeEntry" title="SKU 核价明细" variant="drawer" width="544px" @close="detailKey = ''">
    <PricingSkuDetails v-if="activeEntry" :entry="activeEntry" :loading="loading" :stale="stale" :usd-cny-rate="usdCnyRate" @shipping-amount="emit('shippingAmount', activeEntry.target.targetKey, $event, activeEntry.row.sku_id)" @repair="emit('repair', activeEntry)" @retry="emit('retry')" />
    <template #footer><button type="button" class="pricing-button w-full" @click="detailKey = ''">完成</button></template>
  </WorkspaceDialog>
</template>

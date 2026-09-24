<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useCurrency } from '@/composables/useCurrency'
import { pricingRecord, type SkuPricingEntry } from '@/utils/skuPricing'
import type { MarketplaceOption } from '@/types/workflow'
import SkuCollapsibleSection from './SkuCollapsibleSection.vue'
import PricingSkuDetails from './PricingSkuDetails.vue'

const props = defineProps<{ entries: SkuPricingEntry[]; platformOptions: MarketplaceOption[]; loading: boolean }>()
const { formatMoney, formatPercent } = useCurrency()
const search = ref('')
const market = ref('')
const problemsOnly = ref(false)
const page = ref(1)
const expanded = ref('')
const detailId = useId()
const pageSize = 20
const statusLabels = { pending: '待核价', error: '数据待处理', loss: '亏损', applied: '已应用', preview: '预览 · 未应用' }
const markets = computed(() => [...new Map(props.entries.map(entry => [entry.target.targetKey, entry])).values()])
const skuCount = computed(() => new Set(props.entries.map(entry => entry.row.sku_id)).size)
const problemCount = computed(() => props.entries.filter(entry => ['error', 'loss'].includes(entry.status)).length)
const filtered = computed(() => props.entries.filter(entry => (
  (!market.value || entry.target.targetKey === market.value)
  && (!problemsOnly.value || ['error', 'loss'].includes(entry.status))
  && `${entry.name} ${entry.row.sku} ${entry.row.sku_id}`.toLowerCase().includes(search.value.trim().toLowerCase())
)))
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch([search, market, problemsOnly], () => { page.value = 1 })
watch(pageCount, count => { page.value = Math.min(page.value, count) })
watch(markets, values => { if (!values.some(entry => entry.target.targetKey === market.value)) market.value = '' })

function marketLabel(entry: SkuPricingEntry) {
  const option = props.platformOptions.find(item => item.key === entry.target.platform)
  const site = option?.sites.find(item => item.code.toLowerCase() === entry.target.site.toLowerCase())
  return `${option?.label || entry.target.platform} · ${site?.label || entry.target.site}`
}
function money(value: unknown, currency = 'CNY') {
  return value === undefined || value === null || value === '' ? '—' : formatMoney(Number(value), currency)
}
function price(value: unknown) {
  const item = pricingRecord(value)
  return item.currency ? money(item.amount, String(item.currency)) : '—'
}
function hasOverrides(entry: SkuPricingEntry) {
  return Object.keys(entry.row.overrides).length > 0
    || Object.keys(pricingRecord(entry.row.pricing_overrides?.common)).length > 0
    || Object.keys(pricingRecord(pricingRecord(entry.row.pricing_overrides?.targets)[entry.target.targetKey.toLowerCase()])).length > 0
}
</script>

<template>
  <SkuCollapsibleSection title="各 SKU 核价结果" :summary="`${skuCount} 个 SKU · ${entries.length} 项市场报价`" class="mt-6 border-t border-accent-200 pt-5 dark:border-dark-700">
    <p class="muted">每行对应一个 SKU 在一个市场的结果。国际运费按该规格的包装单独计算；展开明细可查看渠道、计费重量并单独调整售价。</p>
    <div class="my-4 flex flex-wrap items-end gap-3">
      <label class="min-w-48 flex-1"><span class="field-label">查找 SKU</span><input v-model="search" type="search" class="input mt-1" placeholder="名称或卖家编码" /></label>
      <label><span class="field-label">筛选市场</span><select v-model="market" class="input mt-1"><option value="">全部市场</option><option v-for="entry in markets" :key="entry.target.targetKey" :value="entry.target.targetKey">{{ marketLabel(entry) }}</option></select></label>
      <label class="flex min-h-10 items-center gap-2 text-sm"><input v-model="problemsOnly" type="checkbox" />只看异常（{{ problemCount }}）</label>
    </div>
    <div class="overflow-x-auto rounded-xl border border-accent-200 dark:border-dark-700">
      <table class="w-full min-w-[760px] text-left text-sm">
        <caption class="sr-only">按 SKU 和市场分别展示成本、包装、国际运费、售价和利润</caption>
        <thead class="bg-accent-50 text-xs text-accent-500 dark:bg-dark-800 dark:text-accent-400">
          <tr><th scope="col" class="p-3">SKU / 市场</th><th scope="col" class="p-3">采购成本 / 包装</th><th scope="col" class="p-3">国际运费</th><th scope="col" class="p-3">买家售价</th><th scope="col" class="p-3">利润 / 利润率</th></tr>
        </thead>
        <tbody>
          <template v-for="entry in visible" :key="entry.key">
            <tr class="border-t border-accent-200 align-top dark:border-dark-700" :data-sku-id="entry.row.sku_id" :data-target-key="entry.target.targetKey">
              <th scope="row" class="max-w-64 p-3 font-normal">
                <p class="font-semibold">{{ entry.name }}</p><p class="mt-1 break-all text-xs text-accent-500 dark:text-accent-400">{{ entry.row.sku || entry.row.sku_id }}</p><p class="mt-1 text-xs">{{ marketLabel(entry) }}</p>
                <span v-if="hasOverrides(entry)" class="mt-1 inline-block text-xs text-brand-700 dark:text-brand-300">有单独设置</span>
                <div class="mt-2 flex flex-wrap items-center gap-2">
                  <span :class="entry.status === 'applied' ? 'badge-success' : ['error', 'loss'].includes(entry.status) ? 'badge-danger' : 'badge-muted'">{{ statusLabels[entry.status] }}</span>
                  <button type="button" class="rounded text-xs font-semibold text-brand-700 hover:underline dark:text-brand-300" :aria-expanded="expanded === entry.key" :aria-controls="expanded === entry.key ? detailId : undefined" :aria-label="`${expanded === entry.key ? '收起' : '查看'} ${entry.name} ${marketLabel(entry)} 核价明细`" @click="expanded = expanded === entry.key ? '' : entry.key">{{ expanded === entry.key ? '收起' : '查看明细' }}</button>
                </div>
              </th>
              <td class="p-3"><p>{{ money(entry.cost) }}</p><p class="mt-1 whitespace-nowrap text-xs text-accent-500 dark:text-accent-400">{{ ['length_cm', 'width_cm', 'height_cm'].map(key => entry.dimensions[key] || '—').join(' × ') }} cm</p><p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ entry.dimensions.weight_kg || '—' }} kg</p></td>
              <td class="p-3">
                <template v-if="entry.quote && !entry.errors.length"><p class="whitespace-nowrap font-semibold">{{ money(entry.quote.shipping_amount, String(entry.quote.shipping_currency || entry.target.shippingCurrency)) }}</p><p class="mt-1 text-xs text-accent-500 dark:text-accent-400">折合 {{ money(entry.quote.shipping_cost_cny) }}</p><p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ entry.quote.shipping_quote_mode === 'auto' ? '自动报价' : '手动报价' }}</p></template>
                <span v-else class="text-accent-500 dark:text-accent-400">{{ entry.errors.length ? '查看异常' : '待核价' }}</span>
              </td>
              <td class="whitespace-nowrap p-3"><p class="text-xs text-accent-500 dark:text-accent-400">建议买家售价</p><p class="mt-1">{{ entry.errors.length ? '—' : price(entry.quote?.suggested_price) }}</p><p class="mt-2 text-xs text-accent-500 dark:text-accent-400">本次买家售价</p><p class="mt-1 font-semibold">{{ entry.errors.length ? '—' : price(entry.quote?.applied_price) }}</p></td>
              <td class="whitespace-nowrap p-3" :class="entry.status === 'loss' ? 'text-rose-600 dark:text-rose-300' : ''"><p>{{ entry.errors.length ? '—' : money(entry.quote?.profit_cny) }}</p><p v-if="entry.quote && !entry.errors.length" class="mt-1 text-xs">{{ formatPercent(Number(entry.quote.margin_percent || 0)) }}</p></td>
            </tr>
            <tr v-if="expanded === entry.key" :id="detailId" class="bg-accent-50 dark:bg-dark-950/50"><td colspan="5"><PricingSkuDetails :entry="entry" :loading="loading" /></td></tr>
          </template>
          <tr v-if="!visible.length"><td colspan="5" class="p-8 text-center text-accent-500 dark:text-accent-400">{{ entries.length ? '没有符合筛选条件的 SKU。' : '请先选择发布 SKU 和目标市场。' }}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm">
      <p class="text-accent-500 dark:text-accent-400">共 {{ filtered.length }} 项 · 每页 {{ pageSize }} 项</p>
      <div class="flex items-center gap-3"><button type="button" class="btn btn-outline !py-1" :disabled="page === 1" @click="page--">上一页</button><span>{{ page }} / {{ pageCount }}</span><button type="button" class="btn btn-outline !py-1" :disabled="page === pageCount" @click="page++">下一页</button></div>
    </div>
    <p class="muted mt-3">修改共用规则或单独设置后需重新计算。预览不保存，点击“应用售价”才会保存全部已选 SKU 的售价。</p>
  </SkuCollapsibleSection>
</template>

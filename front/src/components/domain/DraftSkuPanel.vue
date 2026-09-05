<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhArrowCounterClockwise } from '@phosphor-icons/vue'
import ProductAttributesEditor from './ProductAttributesEditor.vue'
import type { DraftDetail, DraftSku, ProductSku, UnknownRecord } from '@/types/workflow'

const props = defineProps<{ draft: DraftDetail; skus: ProductSku[]; loading: boolean }>()
const expanded = ref('')
const targetKey = ref('')
watch(() => [props.draft.draftId, props.skus.map(row => row.id).join(',')], () => {
  for (const sku of props.skus) {
    if (!props.draft.skuItems.some(row => row.sku_id === sku.id)) props.draft.skuItems.push({ sku_id: sku.id, selected: false, sku: '', stock: '', overrides: {}, attributes_by_target: {}, pricing: {}, publications: {} })
  }
  targetKey.value = `${props.draft.targetSites[0]?.platform}:${props.draft.targetSites[0]?.site}`.toLowerCase()
}, { immediate: true })
watch(() => JSON.stringify(props.draft.skuItems.map(row => [row.sku_id, row.overrides, row.pricing_overrides])), (next, previous) => {
  if (previous && next !== previous && !props.loading) for (const row of props.draft.skuItems) row.pricing.applied = false
}, { flush: 'sync' })
const pairs = computed(() => props.draft.skuItems.map(row => ({ row, sku: props.skus.find(sku => sku.id === row.sku_id) })).filter((pair): pair is { row: DraftSku; sku: ProductSku } => Boolean(pair.sku)))
const selectedCount = computed(() => pairs.value.filter(pair => pair.row.selected).length)
function dimensions(sku: ProductSku, row: DraftSku) { return { ...sku.package_dimensions, ...(row.overrides.package_dimensions as UnknownRecord || {}) } }
function fee(row: DraftSku, key: string) { return (row.pricing_overrides?.common as UnknownRecord || {})[key] }
function setFee(row: DraftSku, key: string, value: string) {
  const common = { ...(row.pricing_overrides?.common as UnknownRecord || {}) }
  if (value === '') delete common[key]; else common[key] = value
  row.pricing_overrides = { ...row.pricing_overrides, common }
  row.pricing = {}
}
function targetFee(row: DraftSku) { return ((row.pricing_overrides?.targets as Record<string, UnknownRecord> || {})[targetKey.value] || {}) }
function quoteCurrency(row: DraftSku) { return String(((row.pricing.targets as Record<string, UnknownRecord> || {})[targetKey.value] || {}).listing_currency || '') }
function setTargetFee(row: DraftSku, key: string, value: unknown) {
  const own = { ...targetFee(row) }
  if (value === '' || value == null) delete own[key]; else own[key] = value
  row.pricing_overrides = { ...row.pricing_overrides, targets: { ...(row.pricing_overrides?.targets as UnknownRecord || {}), [targetKey.value]: own } }
  row.pricing.applied = false
}
const statusLabels: Record<string, string> = { published: '已发布', failed: '失败', dispatching: '正在发送', outcome_unknown: '结果待核对', pending_confirmation: '等待平台确认', confirmed: '已确认组合', mismatch: '平台分组不一致', awaiting_remote_confirmation: '组合结果待确认', not_requested: '独立刊登' }
function statusLabel(status: unknown) { return statusLabels[String(status)] || String(status || '等待确认') }
const groupingResults = computed(() => ((props.draft.raw.target_sites as UnknownRecord[]) || []).map(target => ({ target: `${target.platform}:${target.site}`, grouping: ((target.last_publish_task as UnknownRecord || {}).grouping as UnknownRecord) })).filter(item => item.grouping))
function attributes(row: DraftSku) { return row.attributes_by_target[targetKey.value] || {} }
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-3"><div><h3 class="font-bold">发布 SKU · 已选择 {{ selectedCount }} / {{ pairs.length }}</h3><p class="muted mt-1">修改只影响当前草稿。成本和包装资料默认跟随商品，展开一行可单独调整。</p></div><div class="flex gap-2"><button class="btn btn-outline" :disabled="loading" @click="pairs.forEach(({row, sku}) => row.selected = sku.active)">全选启用规格</button><button class="btn btn-outline" :disabled="loading" @click="pairs.forEach(({row}) => row.selected = false)">取消全选</button></div></div>
    <div class="flex flex-wrap gap-4"><label class="text-sm">发布组织方式<select v-model="draft.grouping.mode" class="input mt-1"><option value="combined">组合展示</option><option value="separate">独立刊登</option></select></label><label class="grow text-sm">平台组名<input v-model="draft.grouping.name" class="input mt-1" :placeholder="draft.title" /></label></div>
    <p v-for="item in groupingResults" :key="item.target" class="text-sm">{{ item.target }}：{{ statusLabel(item.grouping.status) }}</p>
    <p v-if="!pairs.length" class="rounded-lg bg-slate-50 p-5 text-sm dark:bg-dark-900">商品暂无 SKU，请先在商品的“规格与 SKU”中添加。</p>
    <div class="overflow-x-auto">
      <table v-if="pairs.length" class="w-full min-w-[750px] text-left text-sm">
        <thead class="text-xs text-slate-500"><tr><th class="p-2">发布</th><th>商品规格</th><th>平台卖家编码</th><th>可售库存</th><th>采购成本 CNY</th><th>包装 cm / kg</th><th>远端状态</th><th /></tr></thead><tbody>
          <template v-for="{row, sku} in pairs" :key="row.sku_id">
            <tr class="border-t border-accent-100 dark:border-dark-700"><td class="p-2"><input v-model="row.selected" type="checkbox" :disabled="loading || !sku.active" :aria-label="`发布 ${sku.name}`" /></td><td class="p-2"><span>{{ sku.name }}</span><p class="text-xs text-slate-500">{{ Object.values(sku.options).join(' / ') }}{{ !sku.active ? ' · 已停用' : '' }}</p></td><td class="max-w-40 p-1"><input v-model="row.sku" :disabled="Object.keys(row.publications).length > 0" class="input !py-1.5" placeholder="保存后生成" aria-label="平台卖家编码" /></td><td class="w-24 p-1"><input v-model="row.stock" class="input !py-1.5" min="0" type="number" aria-label="可售库存" /></td><td class="p-2">{{ row.overrides.cost_cny ?? sku.cost_cny }}</td><td class="p-2 text-xs">{{ ['length_cm','width_cm','height_cm'].map(k => dimensions(sku, row)[k] || '—').join(' × ') }} / {{ dimensions(sku,row).weight_kg || '—' }}</td><td class="p-2 text-xs"><div v-for="(publication, key) in row.publications" :key="key">{{ key }}：{{ statusLabel(publication.status) }}<p class="font-mono">{{ publication.external_id || publication.item_id || publication.offer_id }}</p><p class="text-red-500">{{ publication.error }}</p></div><span v-if="!Object.keys(row.publications).length">未发布</span></td><td><button class="btn btn-outline !px-2 !py-1" @click="expanded = expanded === row.sku_id ? '' : row.sku_id">详情</button></td></tr>
            <tr v-if="expanded === row.sku_id">
              <td colspan="8" class="bg-slate-50 p-4 dark:bg-dark-950">
                <div class="mb-3 flex items-center justify-between"><span class="text-sm font-semibold">此草稿的规格资料</span><button class="flex items-center gap-1 text-xs" @click="row.overrides = {}"><PhArrowCounterClockwise />恢复使用商品资料</button></div>
                <div class="grid gap-3 md:grid-cols-3"><label class="text-xs">采购成本 CNY<input :value="row.overrides.cost_cny ?? sku.cost_cny" type="number" class="input mt-1" @input="row.overrides.cost_cny = ($event.target as HTMLInputElement).value" /></label><label v-for="[key,label] in [['length_cm','包装长 cm'],['width_cm','包装宽 cm'],['height_cm','包装高 cm'],['weight_kg','包装重量 kg']]" :key="key" class="text-xs">{{ label }}<input :value="dimensions(sku,row)[key]" type="number" step="any" class="input mt-1" @input="row.overrides.package_dimensions = {...(row.overrides.package_dimensions as UnknownRecord || {}), [key]: ($event.target as HTMLInputElement).value}" /></label><label class="text-xs">条码<input :value="row.overrides.barcode ?? sku.barcode" class="input mt-1" @input="row.overrides.barcode = ($event.target as HTMLInputElement).value" /></label><label class="text-xs md:col-span-3">SKU 图片地址<input :value="row.overrides.image ?? sku.image" class="input mt-1" @input="row.overrides.image = ($event.target as HTMLInputElement).value" /></label></div>
                <label class="mt-4 block text-sm">目标市场<select v-model="targetKey" class="input mt-1"><option v-for="target in draft.targetSites" :key="`${target.platform}:${target.site}`" :value="`${target.platform}:${target.site}`.toLowerCase()">{{ target.platform }} · {{ target.site }}</option></select></label>
                <div class="my-3 grid gap-3 md:grid-cols-3"><label v-for="[key, label] in [['domestic_freight_cny', '国内运费 CNY'], ['packaging_cost_cny', '包装耗材 CNY'], ['other_cost_cny', '其他固定费用 CNY']]" :key="key" class="text-xs">{{ label }}<input :value="fee(row, key)" class="input mt-1" type="number" min="0" step="any" placeholder="沿用核价共用模板" @input="setFee(row, key, ($event.target as HTMLInputElement).value)" /></label></div>
                <div class="my-3 grid gap-3 md:grid-cols-2"><label class="text-xs">此目标的国际运费（使用核价页物流币种）<input :value="targetFee(row).shipping_amount" class="input mt-1" type="number" min="0" step="any" placeholder="沿用共用物流报价" @input="setTargetFee(row, 'shipping_amount', ($event.target as HTMLInputElement).value)" /></label><label class="text-xs">此目标的手动售价 {{ quoteCurrency(row) }}<input :value="(targetFee(row).manual_price as UnknownRecord)?.amount" class="input mt-1" type="number" min="0" step="any" :disabled="!quoteCurrency(row)" placeholder="先核价以确认币种；留空使用共用规则" @input="setTargetFee(row, 'manual_price', ($event.target as HTMLInputElement).value ? {amount: ($event.target as HTMLInputElement).value, currency: quoteCurrency(row)} : null)" /></label></div>
                <ProductAttributesEditor empty-message="尚未设置属性" :model-value="attributes(row)" title="此 SKU 的平台差异属性" description="填写平台属性编号和对应值；没有单独设置的属性沿用草稿共同属性。" @update:model-value="row.attributes_by_target[targetKey] = $event" />
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </section>
</template>

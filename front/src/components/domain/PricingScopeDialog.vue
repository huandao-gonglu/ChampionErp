<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhPackage, PhStorefront } from '@phosphor-icons/vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import type { DraftSku, PricingInput, PricingTargetInput, ProductSku, UnknownRecord } from '@/types/workflow'
import { marketName, marketSite } from '@/utils/pricingWorkspace'
import { pricingRecord } from '@/utils/skuPricing'
import { selectedPricingTargets } from '@/types/pricing'

const props = defineProps<{ open: boolean; input: PricingInput; skuItems: DraftSku[]; skus: ProductSku[]; targets: PricingTargetInput[]; storeConfig: UnknownRecord; disabled?: boolean }>()
const emit = defineEmits<{ close: []; confirm: [] }>()
const selectedIds = ref<string[]>([])
const selectedMarkets = ref<string[]>([])
const search = ref('')
const productSkus = computed(() => new Map(props.skus.map(sku => [sku.id, sku])))
const visibleSkus = computed(() => props.skuItems.filter(row => `${productSkus.value.get(row.sku_id)?.name || ''} ${row.sku} ${row.sku_id}`.toLowerCase().includes(search.value.toLowerCase().trim())))
const allSelected = computed(() => props.skuItems.length > 0 && selectedIds.value.length === props.skuItems.length)
watch(() => props.open, open => {
  if (!open) return
  selectedIds.value = props.skuItems.filter(row => row.selected).map(row => row.sku_id)
  selectedMarkets.value = selectedPricingTargets(props.input).map(target => target.targetKey)
  search.value = ''
})
function selectAll(event: Event) { selectedIds.value = (event.target as HTMLInputElement).checked ? props.skuItems.map(row => row.sku_id) : [] }
function storeName(target: PricingTargetInput) {
  const store = pricingRecord(props.storeConfig[target.platform])
  return String(store.nickname || store.store_name || store.business_name || `${marketName(target)} 店铺`)
}
function warehouseSummary(target: PricingTargetInput) {
  const store = pricingRecord(props.storeConfig[target.platform])
  const warehouseIds = Array.isArray(store.warehouse_ids) ? store.warehouse_ids : []
  if (warehouseIds.length) return `已配置仓库：${warehouseIds.join('、')}`
  if (target.platform === 'ozon') return '按店铺当前有效仓库与配送渠道报价'
  if (target.platform === 'mercadolibre') return '按授权销售国家与物流方式报价'
  return '按平台当前物流配置报价'
}
function confirm() {
  if (!selectedIds.value.length || !selectedMarkets.value.length || props.disabled) return
  props.skuItems.forEach(row => { row.selected = selectedIds.value.includes(row.sku_id) })
  props.input.selectedTargetKeys = [...selectedMarkets.value]
  emit('confirm')
  emit('close')
}
</script>

<template>
  <WorkspaceDialog :open="open" title="核价范围" variant="drawer" width="544px" :close-disabled="disabled" @close="emit('close')">
    <p class="pricing-subtle text-sm">选择本次核价的规格与市场。</p>
    <section class="mt-6">
      <h4 class="flex items-center gap-2 font-semibold"><PhPackage :size="18" class="text-primary-600" />商品范围<span class="pricing-tag ml-auto">已选 {{ selectedIds.length }} / {{ skuItems.length }}</span></h4>
      <input v-model="search" aria-label="搜索核价范围 SKU" class="pricing-input my-3 w-full" type="search" placeholder="搜索规格或 SKU" />
      <label class="flex items-center gap-3 border-b border-accent-200 pb-3 text-sm dark:border-dark-700"><input type="checkbox" :checked="allSelected" :indeterminate="!!selectedIds.length && !allSelected" @change="selectAll" />全部 {{ skuItems.length }} 个 SKU</label>
      <div class="max-h-56 overflow-y-auto overscroll-contain">
        <label v-for="row in visibleSkus" :key="row.sku_id" class="flex items-center gap-3 py-2.5 text-sm"><input v-model="selectedIds" type="checkbox" :value="row.sku_id" /><span class="min-w-0"><span class="block truncate">{{ productSkus.get(row.sku_id)?.name || row.sku || row.sku_id }}</span><span class="pricing-subtle block text-xs">{{ row.sku || row.sku_id }}</span></span></label>
        <p v-if="!visibleSkus.length" class="pricing-subtle py-5 text-center text-sm">没有匹配的 SKU。</p>
      </div>
    </section>
    <section class="mt-6 border-t border-accent-200 pt-5 dark:border-dark-700">
      <h4 class="font-semibold">市场与物流配置</h4>
      <label v-for="target in targets" :key="target.targetKey" class="pricing-scope-market">
        <input v-model="selectedMarkets" type="checkbox" :value="target.targetKey" class="mt-1" />
        <span><strong class="block font-semibold">{{ marketName(target) }} · {{ marketSite(target) }}</strong><span class="pricing-subtle mt-2 flex items-center gap-2 text-xs"><PhStorefront :size="14" />{{ storeName(target) }}</span><span class="pricing-subtle mt-2 block text-xs">{{ warehouseSummary(target) }}</span></span>
      </label>
    </section>

    <p class="pricing-inline-note mt-6">确认范围后请点击「计算核价」。采购成本和包装资料按每个 SKU 读取，具体物流渠道可在结果明细中查看。</p>
    <template #footer><div class="flex justify-end gap-3"><button type="button" class="pricing-button" :disabled="disabled" @click="emit('close')">取消</button><button type="button" class="pricing-button pricing-button-primary" :disabled="disabled || !selectedIds.length || !selectedMarkets.length" @click="confirm">确认范围</button></div></template>
  </WorkspaceDialog>
</template>

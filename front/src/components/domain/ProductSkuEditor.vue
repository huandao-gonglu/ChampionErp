<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { PhPlus, PhTrash, PhCaretDown } from '@phosphor-icons/vue'
import ProductAttributesEditor from './ProductAttributesEditor.vue'
import SkuImagePicker from './SkuImagePicker.vue'
import SkuCollapsibleSection from './SkuCollapsibleSection.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { imageUrl } from '@/utils/draftImages'
import type { ImageAsset, ProductSku } from '@/types/workflow'

const rows = defineModel<ProductSku[]>({ required: true })
const props = defineProps<{ disabled?: boolean; images?: ImageAsset[]; focusSkuId?: string }>()
const expanded = ref('')
const preview = ref<{ url: string; name: string } | null>(null)
const failedImages = ref(new Set<string>())
const skuImageUrls = computed(() => new Map((props.images || []).map(image => [image.id, imageUrl(image)])))
function skuImageUrl(row: ProductSku) { return skuImageUrls.value.get(row.image_asset_id) || '' }
const selected = ref<string[]>([])
const batchField = ref('cost_cny')
const batchValue = ref('')
const batchFields = [['cost_cny', '采购成本 CNY'], ['length_cm', '包装长 cm'], ['width_cm', '包装宽 cm'], ['height_cm', '包装高 cm'], ['weight_kg', '包装重量 kg'], ['supplier_stock', '供应商库存'], ['battery', '含电池'], ['liquid', '含液体']]
const batchBoolean = computed(() => ['battery', 'liquid'].includes(batchField.value))
const batchValid = computed(() => batchValue.value !== '' && (batchBoolean.value
  ? ['true', 'false'].includes(batchValue.value)
  : Number.isFinite(Number(batchValue.value)) && Number(batchValue.value) >= 0
    && (!['length_cm', 'width_cm', 'height_cm', 'weight_kg'].includes(batchField.value) || Number(batchValue.value) > 0)
    && (batchField.value !== 'supplier_stock' || Number.isInteger(Number(batchValue.value)))))
watch(batchField, () => { batchValue.value = '' })
watch(() => props.focusSkuId, async id => {
  if (!id) return
  expanded.value = id
  selected.value = [id]
  await nextTick()
  document.getElementById(`product-sku-${id}`)?.scrollIntoView?.({ block: 'center' })
}, { immediate: true })
function applyBatch() {
  if (props.disabled || !batchValid.value || !selected.value.length) return
  for (const row of rows.value.filter(row => selected.value.includes(row.id))) {
    if (['length_cm', 'width_cm', 'height_cm', 'weight_kg'].includes(batchField.value)) row.package_dimensions[batchField.value] = String(batchValue.value)
    else row[batchField.value] = batchBoolean.value ? batchValue.value === 'true' : String(batchValue.value)
  }
}

const fields = [['length_cm', '包装长 cm'], ['width_cm', '包装宽 cm'], ['height_cm', '包装高 cm'], ['weight_kg', '包装重 kg']]
function add() {
  const id = crypto.randomUUID()
  rows.value.push({ id, source_sku_id: '', name: '', options: {}, cost_cny: '', supplier_stock: '', image_asset_id: '', barcode: '', package_dimensions: {}, battery: false, liquid: false, active: true, source_snapshot: {} })
  expanded.value = id
}
</script>

<template>
  <SkuCollapsibleSection class="mt-5 rounded-xl border border-accent-200 p-3 dark:border-dark-700" title="规格与 SKU" :summary="`${rows.length} 个`">
    <div class="mb-3 flex items-center justify-between gap-3">
      <p class="muted">每个规格维护独立资料，保存后所有引用草稿都会使用；成本或包装改变后需要重新核价。</p>
      <button class="btn btn-outline" :disabled="disabled" @click="add"><PhPlus />添加 SKU</button>
    </div>
    <fieldset :disabled="disabled" class="mb-4 flex flex-wrap items-center gap-2">
      <label class="text-sm"><input type="checkbox" aria-label="全选批量编辑 SKU" :checked="rows.length > 0 && selected.length === rows.length" :indeterminate="selected.length > 0 && selected.length < rows.length" @change="selected = ($event.target as HTMLInputElement).checked ? rows.map(row => row.id) : []" /> 全选</label>
      <select v-model="batchField" class="input !w-auto" aria-label="批量修改字段"><option v-for="[key, label] in batchFields" :key="key" :value="key">{{ label }}</option></select>
      <select v-if="batchBoolean" v-model="batchValue" class="input !w-auto" aria-label="批量设置值"><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select>
      <input v-else v-model="batchValue" type="number" min="0" step="any" class="input !w-32" aria-label="批量设置值" />
      <button type="button" class="btn btn-outline" :disabled="!selected.length || !batchValid" @click="applyBatch">应用到选中 {{ selected.length }} 个 SKU</button><span class="muted">应用后点击“保存商品”</span>
    </fieldset>
    <p v-if="!rows.length" class="py-5 text-center text-sm text-slate-500">暂无 SKU，请采集商品或添加一个规格。</p>
    <div v-else class="overflow-x-auto">
      <table class="w-full min-w-[850px] text-left text-sm">
        <thead class="text-xs text-slate-500"><tr><th>选择</th><th class="p-2">启用</th><th class="p-2">图片</th><th>名称 / 规格</th><th>采购成本 CNY</th><th>供应商库存</th><th v-for="field in fields" :key="field[0]">{{ field[1] }}</th><th /></tr></thead>
        <tbody>
          <template v-for="row in rows" :key="row.id">
            <tr :id="`product-sku-${row.id}`" class="border-t border-accent-100 dark:border-dark-800">
              <td><input v-model="selected" type="checkbox" :value="row.id" :aria-label="`批量选择 ${row.name}`" :disabled="disabled" /></td>
              <td class="p-2"><input v-model="row.active" type="checkbox" :disabled="disabled" :aria-label="`启用 ${row.name}`" /></td>
              <td class="w-20 p-2">
                <button v-if="skuImageUrl(row) && !failedImages.has(skuImageUrl(row))" type="button" class="block size-16 overflow-hidden rounded-lg border border-accent-200 bg-white focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-dark-700" :aria-label="`预览 ${row.name} 的图片`" @click="preview = { url: skuImageUrl(row), name: row.name }">
                  <img :key="skuImageUrl(row)" :src="skuImageUrl(row)" :alt="`${row.name} 的图片`" loading="lazy" class="size-full object-contain" @error="failedImages.add(skuImageUrl(row))" />
                </button>
                <span v-else class="flex size-16 items-center justify-center rounded-lg border border-accent-200 p-1 text-center text-xs text-slate-500 dark:border-dark-700">{{ row.image_asset_id ? '图片不可用' : '未设置图片' }}</span>
              </td>
              <td class="min-w-44 p-1"><input v-model="row.name" class="input !py-1.5" aria-label="SKU 名称" :disabled="disabled" /><span class="text-xs text-slate-500">{{ Object.values(row.options).join(' / ') }}</span></td>
              <td class="w-28 p-1"><input v-model="row.cost_cny" class="input !py-1.5" type="number" min="0" step="0.01" aria-label="采购成本 CNY" :disabled="disabled" /></td>
              <td class="w-24 p-1"><input v-model="row.supplier_stock" class="input !py-1.5" aria-label="供应商库存" :disabled="disabled" /></td>
              <td v-for="field in fields" :key="field[0]" class="w-24 p-1"><input v-model="row.package_dimensions[field[0]]" class="input !py-1.5" type="number" min="0" step="any" :aria-label="field[1]" :disabled="disabled" /><small v-if="row.package_estimates?.[field[0]] && Number(row.package_dimensions[field[0]]) === row.package_estimates[field[0]].suggested" class="text-xs text-slate-500" :title="row.package_estimates[field[0]].basis">确认估算 · {{ row.package_estimates[field[0]].low }}–{{ row.package_estimates[field[0]].high }} cm</small></td>
              <td class="whitespace-nowrap"><button class="p-2" :aria-label="`编辑 ${row.name} 详情`" @click="expanded = expanded === row.id ? '' : row.id"><PhCaretDown /></button><button v-if="!row.source_sku_id" class="p-2 text-red-500" aria-label="停用 SKU" title="停用 SKU" :disabled="disabled" @click="row.active = false"><PhTrash /></button></td>
            </tr>
            <tr v-if="expanded === row.id">
              <td colspan="11" class="bg-slate-50 p-3 dark:bg-dark-950">
                <div class="grid gap-3 md:grid-cols-2"><SkuImagePicker v-model="row.image_asset_id" :images="images" :disabled="disabled" :label="`${row.name} 的图片`" /><label class="text-xs">商品条码<input v-model="row.barcode" class="input mt-1" :disabled="disabled" /></label></div>
                <div class="my-3 flex gap-5"><label><input v-model="row.battery" type="checkbox" :disabled="disabled" />含电池</label><label><input v-model="row.liquid" type="checkbox" :disabled="disabled" />含液体</label></div>
                <ProductAttributesEditor v-model="row.options" empty-message="尚未设置属性" title="规格属性" description="按实际商品填写颜色、尺寸等维度；不会自动生成不存在的组合。" :disabled="disabled" />
                <p v-if="row.source_sku_id" class="mt-2 text-xs text-slate-500">来源 SKU：{{ row.source_sku_id }}</p>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </SkuCollapsibleSection>
  <WorkspaceDialog :open="Boolean(preview)" title="SKU 图片预览" @close="preview = null">
    <img v-if="preview" :src="preview.url" :alt="`${preview.name} 的图片完整预览`" class="mx-auto max-h-[70dvh] max-w-full object-contain" />
  </WorkspaceDialog>
</template>

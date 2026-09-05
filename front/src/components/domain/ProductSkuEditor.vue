<script setup lang="ts">
import { ref } from 'vue'
import { PhPlus, PhTrash, PhCaretDown } from '@phosphor-icons/vue'
import ProductAttributesEditor from './ProductAttributesEditor.vue'
import type { ProductSku } from '@/types/workflow'

const rows = defineModel<ProductSku[]>({ required: true })
defineProps<{ disabled?: boolean }>()
const expanded = ref('')
const fields = [['length_cm', '包装长 cm'], ['width_cm', '包装宽 cm'], ['height_cm', '包装高 cm'], ['weight_kg', '包装重 kg']]
function add() {
  const id = crypto.randomUUID()
  rows.value.push({ id, source_sku_id: '', name: '', options: {}, cost_cny: '', supplier_stock: '', image: '', barcode: '', package_dimensions: {}, active: true, source_snapshot: {} })
  expanded.value = id
}
</script>

<template>
  <section class="mt-5 rounded-xl border border-accent-200 p-3 dark:border-dark-700">
    <div class="mb-3 flex items-center justify-between gap-3">
      <div><h3 class="font-bold">规格与 SKU <span class="text-sm font-normal text-slate-500">{{ rows.length }} 个</span></h3><p class="muted mt-1">每个规格维护独立成本与包装信息。停用后保留身份和发布关联。</p></div>
      <button class="btn btn-outline" :disabled="disabled" @click="add"><PhPlus />添加 SKU</button>
    </div>
    <p v-if="!rows.length" class="py-5 text-center text-sm text-slate-500">暂无 SKU，请采集商品或添加一个规格。</p>
    <div v-else class="overflow-x-auto">
      <table class="w-full min-w-[850px] text-left text-sm">
        <thead class="text-xs text-slate-500"><tr><th class="p-2">启用</th><th>名称 / 规格</th><th>采购成本 CNY</th><th>供应商库存</th><th v-for="field in fields" :key="field[0]">{{ field[1] }}</th><th /></tr></thead>
        <tbody>
          <template v-for="row in rows" :key="row.id">
            <tr class="border-t border-accent-100 dark:border-dark-800">
              <td class="p-2"><input v-model="row.active" type="checkbox" :disabled="disabled" :aria-label="`启用 ${row.name}`" /></td>
              <td class="min-w-44 p-1"><input v-model="row.name" class="input !py-1.5" aria-label="SKU 名称" :disabled="disabled" /><span class="text-xs text-slate-500">{{ Object.values(row.options).join(' / ') }}</span></td>
              <td class="w-28 p-1"><input v-model="row.cost_cny" class="input !py-1.5" type="number" min="0" step="0.01" aria-label="采购成本 CNY" :disabled="disabled" /></td>
              <td class="w-24 p-1"><input v-model="row.supplier_stock" class="input !py-1.5" aria-label="供应商库存" :disabled="disabled" /></td>
              <td v-for="field in fields" :key="field[0]" class="w-24 p-1"><input v-model="row.package_dimensions[field[0]]" class="input !py-1.5" type="number" min="0" step="any" :aria-label="field[1]" :disabled="disabled" /></td>
              <td class="whitespace-nowrap"><button class="p-2" :aria-label="`编辑 ${row.name} 详情`" @click="expanded = expanded === row.id ? '' : row.id"><PhCaretDown /></button><button v-if="!row.source_sku_id" class="p-2 text-red-500" aria-label="停用 SKU" title="停用 SKU" :disabled="disabled" @click="row.active = false"><PhTrash /></button></td>
            </tr>
            <tr v-if="expanded === row.id">
              <td colspan="9" class="bg-slate-50 p-3 dark:bg-dark-950">
                <div class="grid gap-3 md:grid-cols-2"><label class="text-xs">SKU 图片地址<input v-model="row.image" class="input mt-1" :disabled="disabled" /></label><label class="text-xs">商品条码<input v-model="row.barcode" class="input mt-1" :disabled="disabled" /></label></div>
                <ProductAttributesEditor v-model="row.options" empty-message="尚未设置属性" title="规格属性" description="按实际商品填写颜色、尺寸等维度；不会自动生成不存在的组合。" :disabled="disabled" />
                <p v-if="row.source_sku_id" class="mt-2 text-xs text-slate-500">来源 SKU：{{ row.source_sku_id }}</p>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </section>
</template>

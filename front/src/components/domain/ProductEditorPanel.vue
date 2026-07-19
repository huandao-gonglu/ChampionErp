<script setup lang="ts">
import { computed } from 'vue'
import type { Product } from '@/types/workflow'

const props = defineProps<{
  product: Product
  loading: boolean
}>()

const emit = defineEmits<{
  save: []
  assignUpc: []
}>()

function listModel(getter: () => string[], setter: (value: string[]) => void) {
  return computed({
    get: () => getter().join('\n'),
    set: (value: string) => setter(value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)),
  })
}

const sellingPointsText = listModel(() => props.product.sellingPoints, (value) => { props.product.sellingPoints = value })
const packageIncludesText = listModel(() => props.product.packageIncludes, (value) => { props.product.packageIncludes = value })
const materialsText = listModel(() => props.product.materials, (value) => { props.product.materials = value })
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">商品编辑</h2>
        <p class="muted mt-1">维护商品来源事实、供应链字段和内部资料；平台标题、描述和价格在草稿箱单独编辑。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-primary" :disabled="props.loading" @click="emit('save')">保存商品</button>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('assignUpc')">分配 UPC</button>
      </div>
    </div>

    <div class="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      <label class="block"><span class="text-xs font-semibold text-slate-500">内部商品名</span><input v-model="props.product.name" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">来源标题</span><input v-model="props.product.source.title" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">品牌</span><input v-model="props.product.brand" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">来源平台</span><input v-model="props.product.source.sourcePlatform" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">品类</span><input v-model="props.product.category" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">长 cm</span><input v-model="props.product.source.dimensions.lengthCm" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">宽 cm</span><input v-model="props.product.source.dimensions.widthCm" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">高 cm</span><input v-model="props.product.source.dimensions.heightCm" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">重量 kg</span><input v-model="props.product.source.weightKg" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">SKU</span><input v-model="props.product.sku" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">Model</span><input v-model="props.product.model" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">库存</span><input v-model="props.product.stock" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">UPC</span><input v-model="props.product.upc" class="input mt-1" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">采购成本</span><input v-model="props.product.cost" class="input mt-1" /></label>
    </div>

    <div class="mt-5 grid gap-4 xl:grid-cols-2">
      <label class="block"><span class="text-xs font-semibold text-slate-500">卖点，每行一个</span><textarea v-model="sellingPointsText" class="input mt-1 min-h-24" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">包装清单，每行一个</span><textarea v-model="packageIncludesText" class="input mt-1 min-h-24" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">材质，每行一个</span><textarea v-model="materialsText" class="input mt-1 min-h-24" /></label>
    </div>
  </section>
</template>

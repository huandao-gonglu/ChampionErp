<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Marketplace, Product } from '@/types/workflow'

const props = defineProps<{
  product: Product
  activeMarketplace: Marketplace
  loading: boolean
}>()

const emit = defineEmits<{
  save: []
  generateCopy: []
  assignUpc: []
  setMarketplace: [value: Marketplace]
  goPricing: []
  goImages: []
  goPublish: []
}>()

const platforms: Array<{ key: Marketplace; label: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre' },
  { key: 'wildberries', label: 'Wildberries' },
  { key: 'ozon', label: 'Ozon' },
]

function listModel(getter: () => string[], setter: (value: string[]) => void) {
  return computed({
    get: () => getter().join('\n'),
    set: (value: string) => setter(value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)),
  })
}

const sellingPointsText = listModel(() => props.product.sellingPoints, (value) => { props.product.sellingPoints = value })
const packageIncludesText = listModel(() => props.product.packageIncludes, (value) => { props.product.packageIncludes = value })
const materialsText = listModel(() => props.product.materials, (value) => { props.product.materials = value })
const aiCopyRequested = ref(false)
const aiCopyGenerating = computed(() => aiCopyRequested.value && props.loading)

function generateAiCopy() {
  aiCopyRequested.value = true
  emit('generateCopy')
}

watch(() => props.loading, (loading) => {
  if (!loading) aiCopyRequested.value = false
})
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">商品编辑</h2>
        <p class="muted mt-1">基础信息、平台草稿、类目属性、价格和图片围绕同一个 source + drafts 工作模型保存。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-primary" :disabled="props.loading" @click="emit('save')">保存商品</button>
        <button class="btn btn-secondary" :disabled="props.loading" @click="generateAiCopy">
          <span v-if="aiCopyGenerating" class="size-3 animate-spin rounded-full border-2 border-white/50 border-t-white"></span>
          {{ aiCopyGenerating ? '正在生成' : '生成 AI 文案' }}
        </button>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('assignUpc')">分配 UPC</button>
      </div>
    </div>

    <div v-if="aiCopyGenerating" class="mt-4 rounded-2xl border border-dashed border-blue-200 bg-blue-50 p-4 text-sm text-blue-950">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p class="font-semibold">正在生成 AI 文案</p>
          <p class="mt-1 text-blue-800">正在生成当前平台的标题、描述和卖点，请稍候。</p>
        </div>
        <span class="badge-info">生成中</span>
      </div>
      <div class="mt-3 h-2 overflow-hidden rounded-full bg-white">
        <div class="h-full w-1/3 animate-pulse rounded-full bg-blue-500"></div>
      </div>
    </div>

    <div class="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      <label class="block"><span class="text-xs font-semibold text-slate-500">商品标题</span><input v-model="props.product.name" class="input mt-1" /></label>
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
      <label class="block"><span class="text-xs font-semibold text-slate-500">平台</span><select :value="props.activeMarketplace" class="input mt-1" @change="emit('setMarketplace', ($event.target as HTMLSelectElement).value as Marketplace)"><option v-for="platform in platforms" :key="platform.key" :value="platform.key">{{ platform.label }}</option></select></label>
    </div>

    <div class="mt-5 grid gap-3 md:grid-cols-4">
      <button class="rounded-2xl border bg-slate-50 p-4 text-left hover:bg-slate-100" @click="emit('goPublish')"><div class="font-semibold">类目 / 必填属性</div><div class="mt-1 text-xs text-slate-500">搜索、选择、AI 填充属性</div></button>
      <button class="rounded-2xl border bg-slate-50 p-4 text-left hover:bg-slate-100" @click="emit('goPricing')"><div class="font-semibold">价格 / 净收益</div><div class="mt-1 text-xs text-slate-500">计算建议售价</div></button>
      <button class="rounded-2xl border bg-slate-50 p-4 text-left hover:bg-slate-100" @click="emit('goImages')"><div class="font-semibold">图片区</div><div class="mt-1 text-xs text-slate-500">全选、上传、翻译图</div></button>
      <button class="rounded-2xl border bg-slate-50 p-4 text-left hover:bg-slate-100" @click="emit('goPublish')"><div class="font-semibold">保存 / 发布预检</div><div class="mt-1 text-xs text-slate-500">生成 payload 并校验</div></button>
    </div>

    <div class="mt-5 grid gap-4 xl:grid-cols-2">
      <label class="block"><span class="text-xs font-semibold text-slate-500">卖点，每行一个</span><textarea v-model="sellingPointsText" class="input mt-1 min-h-24" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">包装清单，每行一个</span><textarea v-model="packageIncludesText" class="input mt-1 min-h-24" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">材质，每行一个</span><textarea v-model="materialsText" class="input mt-1 min-h-24" /></label>
      <label class="block"><span class="text-xs font-semibold text-slate-500">{{ props.activeMarketplace }} 平台草稿描述</span><textarea v-model="props.product.drafts[props.activeMarketplace].description" class="input mt-1 min-h-24" /></label>
    </div>
  </section>
</template>

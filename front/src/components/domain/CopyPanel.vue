<script setup lang="ts">
import type { Marketplace, Product } from '@/types/workflow'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'

const props = defineProps<{
  product: Product
  activeMarketplace: Marketplace
  imagePrompt: string
}>()

const emit = defineEmits<{
  setMarketplace: [value: Marketplace]
  generate: []
  generateImagePrompt: []
}>()

const marketplaces: Array<{ key: Marketplace; label: string; language: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre Mexico', language: '西班牙语' },
  { key: 'wildberries', label: 'Wildberries', language: '俄语' },
  { key: 'ozon', label: 'Ozon', language: '俄语' },
]

function copyPrompt() {
  if (props.imagePrompt) void navigator.clipboard?.writeText(props.imagePrompt)
}
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">AI 文案 / 翻译</h2>
        <p class="muted mt-1">批量 AI 标题描述、GPT 半自动生图、API 生图和 GPT 结果导入都只写入 drafts / image_pool。</p>
      </div>
      <select :value="props.activeMarketplace" class="input w-72" @change="emit('setMarketplace', ($event.target as HTMLSelectElement).value as Marketplace)">
        <option v-for="marketplace in marketplaces" :key="marketplace.key" :value="marketplace.key">{{ marketplace.label }} / {{ marketplace.language }}</option>
      </select>
    </div>

    <div class="mt-4 flex flex-wrap gap-2">
      <button class="btn btn-primary" @click="emit('generate')">生成 AI 文案</button>
      <button class="btn btn-secondary" @click="emit('generateImagePrompt')">生成 GPT 生图任务包</button>
      <button class="btn btn-outline" :disabled="!props.imagePrompt" @click="copyPrompt">复制生图提示词</button>
    </div>

    <article class="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
      <div class="flex items-center justify-between gap-3">
        <h3 class="font-semibold text-slate-950">{{ props.product.drafts[props.activeMarketplace].language || props.activeMarketplace }}</h3>
        <span :class="statusBadgeClass(props.product.drafts[props.activeMarketplace].status)">
          {{ workflowStatusLabel(props.product.drafts[props.activeMarketplace].status) }}
        </span>
      </div>
      <div class="mt-4 space-y-3">
        <label class="block">
          <p class="text-xs font-semibold text-slate-500">标题</p>
          <input v-model="props.product.drafts[props.activeMarketplace].title" class="input mt-1" placeholder="待生成" />
        </label>
        <label class="block">
          <p class="text-xs font-semibold text-slate-500">描述</p>
          <textarea v-model="props.product.drafts[props.activeMarketplace].description" class="input mt-1 min-h-36" placeholder="待生成" />
        </label>
        <div>
          <p class="text-xs font-semibold text-slate-500">Bullets</p>
          <div class="mt-2 flex flex-wrap gap-2">
            <span v-for="bullet in props.product.drafts[props.activeMarketplace].bullets" :key="bullet" class="badge-info">{{ bullet }}</span>
            <span v-if="!props.product.drafts[props.activeMarketplace].bullets.length" class="badge-muted">待生成</span>
          </div>
        </div>
      </div>
    </article>

    <article v-if="props.imagePrompt" class="mt-4 rounded-2xl border border-blue-100 bg-blue-50 p-4">
      <div class="flex items-center justify-between gap-3">
        <h3 class="font-semibold text-blue-950">GPT 生图任务包</h3>
        <button class="btn btn-outline py-1.5" @click="copyPrompt">复制</button>
      </div>
      <pre class="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-xl bg-white p-3 text-xs text-slate-700">{{ props.imagePrompt }}</pre>
    </article>
  </section>
</template>

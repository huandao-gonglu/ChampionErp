<script setup lang="ts">
import { ref, watch } from 'vue'
import ImagePoolPanel from '@/components/domain/ImagePoolPanel.vue'
import type { ImageAsset, Marketplace, Product } from '@/types/workflow'

const props = defineProps<{
  product: Product
  activeMarketplace: Marketplace
  imagePrompt: string
  images: ImageAsset[]
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  setMarketplace: [value: Marketplace]
  generatePrompt: [language: string]
  translate: [language: string]
  upload: [files: File[]]
  syncGenerated: []
  save: []
  setMain: [imageId: string]
  delete: [imageIds: string[]]
  clear: []
}>()

const marketplaces: Array<{ key: Marketplace; label: string; language: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre Mexico', language: '西班牙语' },
  { key: 'wildberries', label: 'Wildberries', language: '俄语' },
  { key: 'ozon', label: 'Ozon', language: '俄语' },
]

const languageOptions = [
  { value: 'Spanish (Mexico)', label: '西班牙语 / Mexico' },
  { value: 'Portuguese (Brazil)', label: '葡萄牙语 / Brazil' },
  { value: 'English', label: '英语' },
  { value: 'Russian', label: '俄语' },
]

const targetLanguage = ref(defaultLanguage(props.activeMarketplace))

function defaultLanguage(platform: Marketplace) {
  if (platform === 'mercadolibre') return 'Spanish (Mexico)'
  if (platform === 'wildberries' || platform === 'ozon') return 'Russian'
  return 'English'
}

watch(
  () => props.activeMarketplace,
  (platform) => {
    targetLanguage.value = defaultLanguage(platform)
  },
)

function copyPrompt() {
  if (props.imagePrompt) void navigator.clipboard?.writeText(props.imagePrompt)
}

function translateSelectedImages() {
  emit('translate', targetLanguage.value)
}

function generatePrompt() {
  emit('generatePrompt', targetLanguage.value)
}
</script>

<template>
  <div class="space-y-5">
    <section class="card">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="card-title">图片编辑 / AI 图片工作台</h2>
        </div>
        <div class="flex flex-wrap gap-2">
          <select :value="props.activeMarketplace" class="input w-64" @change="emit('setMarketplace', ($event.target as HTMLSelectElement).value as Marketplace)">
            <option v-for="marketplace in marketplaces" :key="marketplace.key" :value="marketplace.key">
              {{ marketplace.label }}
            </option>
          </select>
          <select v-model="targetLanguage" class="input w-56">
            <option v-for="language in languageOptions" :key="language.value" :value="language.value">
              {{ language.label }}
            </option>
          </select>
        </div>
      </div>

      <div v-if="props.error" class="mt-3 rounded-2xl bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
        {{ props.error }}
      </div>
    </section>

    <section class="card">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 class="font-semibold text-slate-950">AI 生图</h3>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-secondary" :disabled="props.loading" @click="generatePrompt">生成生图提示词</button>
          <button class="btn btn-outline" :disabled="!props.imagePrompt" @click="copyPrompt">复制提示词</button>
          <button class="btn btn-secondary" :disabled="props.loading" @click="emit('syncGenerated')">导入 ChatGPT 生成图</button>
          <button class="btn btn-primary" :disabled="props.loading || !props.images.length" @click="translateSelectedImages">AI 翻译/重绘选中图</button>
        </div>
      </div>

      <div v-if="props.imagePrompt" class="mt-4 rounded-2xl border border-blue-100 bg-blue-50 p-4">
          <div class="flex items-center justify-between gap-3">
            <h3 class="font-semibold text-blue-950">生图提示词</h3>
            <button class="btn btn-outline py-1.5" :disabled="!props.imagePrompt" @click="copyPrompt">复制</button>
          </div>
          <pre class="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-white p-3 text-xs text-slate-700">{{ props.imagePrompt }}</pre>
      </div>
    </section>

    <ImagePoolPanel
      :images="props.images"
      :loading="props.loading"
      :show-translate-action="false"
      @translate="translateSelectedImages"
      @upload="emit('upload', $event)"
      @clear="emit('clear')"
      @save="emit('save')"
      @set-main="emit('setMain', $event)"
      @delete="emit('delete', $event)"
      @sync-generated="emit('syncGenerated')"
    />
  </div>
</template>

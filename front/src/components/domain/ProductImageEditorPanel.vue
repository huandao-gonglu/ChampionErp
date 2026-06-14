<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
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
const imageAiAction = ref<'prompt' | 'sync' | 'translate' | ''>('')
const imageAiStartedAt = ref(0)
const imageAiNow = ref(0)
const imageAiTargets = ref<Array<{ id: string; label: string }>>([])
let imageAiTimer: number | null = null
const IMAGE_AI_ESTIMATED_MS_PER_IMAGE = 180_000
const imageAiRunning = computed(() => Boolean(imageAiAction.value && props.loading))
const imageAiTotal = computed(() => Math.max(1, imageAiTargets.value.length))
const imageAiElapsedMs = computed(() => Math.max(0, imageAiNow.value - imageAiStartedAt.value))
const imageAiCurrentIndex = computed(() => {
  if (!imageAiRunning.value) return 0
  return Math.min(imageAiTotal.value, Math.floor(imageAiElapsedMs.value / IMAGE_AI_ESTIMATED_MS_PER_IMAGE) + 1)
})
const imageAiCurrentTarget = computed(() => imageAiTargets.value[imageAiCurrentIndex.value - 1])
const imageAiProgressPercent = computed(() => {
  if (!imageAiRunning.value) return 0
  const totalMs = imageAiTotal.value * IMAGE_AI_ESTIMATED_MS_PER_IMAGE
  const percent = Math.round((imageAiElapsedMs.value / totalMs) * 100)
  return Math.min(98, Math.max(1, percent))
})
const imageAiProgressTitle = computed(() => {
  if (imageAiAction.value === 'prompt') return '正在生成生图任务包'
  if (imageAiAction.value === 'sync') return '正在导入 ChatGPT 生成图'
  if (imageAiAction.value === 'translate') return '正在 AI 翻译/重绘图片'
  return 'AI 生图处理中'
})
const imageAiProgressDescription = computed(() => {
  if (imageAiAction.value === 'sync') return '正在同步已生成图片到当前商品图片池。'
  const target = imageAiCurrentTarget.value
  if (target) return `正在处理第 ${imageAiCurrentIndex.value}/${imageAiTotal.value} 张：${target.label}`
  if (imageAiAction.value === 'prompt') return '正在整理当前商品的生图任务包。'
  if (imageAiAction.value === 'translate') return '正在处理图片翻译和重绘结果。'
  return '正在处理 AI 图片任务。'
})
const imageAiProgressMeta = computed(() => {
  if (imageAiAction.value === 'sync') return '正在同步已生成图片到当前商品图片池，请稍候。'
  return `预计按单张最多 180 秒计算，当前进度 ${imageAiProgressPercent.value}%。`
})

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

function selectedOrAllImageTargets() {
  const selected = props.images.filter((image) => image.selected)
  const images = selected.length ? selected : props.images
  return images.map((image, index) => ({
    id: image.id,
    label: image.id || image.usage || `图片 ${index + 1}`,
  }))
}

function startImageAiProgress(action: 'prompt' | 'sync' | 'translate', targets = selectedOrAllImageTargets()) {
  imageAiAction.value = action
  imageAiTargets.value = targets.length ? targets : [{ id: action, label: action === 'sync' ? '生成图导入' : '当前商品图片' }]
  imageAiStartedAt.value = Date.now()
  imageAiNow.value = imageAiStartedAt.value
  if (imageAiTimer) window.clearInterval(imageAiTimer)
  imageAiTimer = window.setInterval(() => {
    imageAiNow.value = Date.now()
  }, 1000)
}

function stopImageAiProgress() {
  if (imageAiTimer) {
    window.clearInterval(imageAiTimer)
    imageAiTimer = null
  }
  imageAiAction.value = ''
  imageAiTargets.value = []
  imageAiStartedAt.value = 0
  imageAiNow.value = 0
}

function translateSelectedImages() {
  startImageAiProgress('translate')
  emit('translate', targetLanguage.value)
}

function generatePrompt() {
  startImageAiProgress('prompt')
  emit('generatePrompt', targetLanguage.value)
}

function syncGeneratedImages() {
  startImageAiProgress('sync', [{ id: 'sync-generated', label: 'ChatGPT 生成图' }])
  emit('syncGenerated')
}

watch(() => props.loading, (loading) => {
  if (!loading) stopImageAiProgress()
})

onBeforeUnmount(() => {
  stopImageAiProgress()
})
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
          <button class="btn btn-secondary" :disabled="props.loading" @click="generatePrompt">
            <span v-if="imageAiRunning && imageAiAction === 'prompt'" class="size-3 animate-spin rounded-full border-2 border-white/50 border-t-white"></span>
            {{ imageAiRunning && imageAiAction === 'prompt' ? '正在生成' : '生成生图提示词' }}
          </button>
          <button class="btn btn-outline" :disabled="!props.imagePrompt" @click="copyPrompt">复制提示词</button>
          <button class="btn btn-secondary" :disabled="props.loading" @click="syncGeneratedImages">
            <span v-if="imageAiRunning && imageAiAction === 'sync'" class="size-3 animate-spin rounded-full border-2 border-white/50 border-t-white"></span>
            {{ imageAiRunning && imageAiAction === 'sync' ? '正在导入' : '导入 ChatGPT 生成图' }}
          </button>
          <button class="btn btn-primary" :disabled="props.loading || !props.images.length" @click="translateSelectedImages">
            <span v-if="imageAiRunning && imageAiAction === 'translate'" class="size-3 animate-spin rounded-full border-2 border-white/50 border-t-white"></span>
            {{ imageAiRunning && imageAiAction === 'translate' ? '正在处理' : 'AI 翻译/重绘选中图' }}
          </button>
        </div>
      </div>

      <div v-if="imageAiRunning" class="mt-4 rounded-2xl border border-dashed border-blue-200 bg-blue-50 p-4 text-sm text-blue-950">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="font-semibold">{{ imageAiProgressTitle }}</p>
            <p class="mt-1 text-blue-800">{{ imageAiProgressDescription }}</p>
            <p class="mt-1 text-xs text-blue-700">{{ imageAiProgressMeta }}</p>
          </div>
          <span class="badge-info">{{ imageAiProgressPercent }}%</span>
        </div>
        <div class="mt-3 h-2 overflow-hidden rounded-full bg-white">
          <div class="h-full rounded-full bg-blue-500 transition-all duration-500" :style="{ width: `${imageAiProgressPercent}%` }"></div>
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
      @sync-generated="syncGeneratedImages"
    />
  </div>
</template>

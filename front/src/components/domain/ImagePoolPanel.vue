<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { ImageAsset, Marketplace } from '@/types/workflow'

const props = defineProps<{
  images: ImageAsset[]
  loading: boolean
  showTranslateAction?: boolean
}>()

const emit = defineEmits<{
  translate: []
  upload: [files: File[]]
  clear: []
  save: []
  syncGenerated: []
  setMain: [imageId: string]
  delete: [imageIds: string[]]
}>()

const input = ref<HTMLInputElement | null>(null)
const previewImage = ref<ImageAsset | null>(null)
const platforms: Array<{ key: Marketplace; label: string }> = [
  { key: 'mercadolibre', label: 'ML' },
  { key: 'wildberries', label: 'WB' },
  { key: 'ozon', label: 'Ozon' },
]

function choose() { input.value?.click() }
function selected(event: Event) {
  const node = event.target as HTMLInputElement
  const files = Array.from(node.files || [])
  if (files.length) emit('upload', files)
  node.value = ''
}

function togglePlatform(image: ImageAsset, platform: Marketplace, checked: boolean) {
  const values = new Set(image.platforms)
  if (checked) values.add(platform)
  else values.delete(platform)
  image.platforms = Array.from(values) as Marketplace[]
}

function selectedIds() {
  return props.images.filter((image) => image.selected).map((image) => image.id)
}

const previewSrc = computed(() => {
  const image = previewImage.value
  return image ? image.previewUrl || image.url || image.path : ''
})

function openPreview(image: ImageAsset) {
  if (image.previewUrl || image.url || image.path) previewImage.value = image
}

function closePreview() {
  previewImage.value = null
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closePreview()
}

if (typeof window !== 'undefined') {
  window.addEventListener('keydown', handleKeydown)
}

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('keydown', handleKeydown)
  }
})
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">图片池 / AI 图片</h2>
      </div>
      <div class="flex flex-wrap gap-2">
        <input ref="input" type="file" accept="image/*" multiple class="hidden" @change="selected" />
        <button class="btn btn-outline" :disabled="props.loading" @click="choose">上传图片</button>
        <button class="btn btn-secondary" :disabled="props.loading" @click="emit('syncGenerated')">导入 ChatGPT 生成图</button>
        <button v-if="props.showTranslateAction !== false" class="btn btn-primary" :disabled="props.loading || !props.images.length" @click="emit('translate')">AI 翻译/重绘图片</button>
        <button class="btn btn-outline" :disabled="props.loading || !props.images.length" @click="emit('save')">保存图片池</button>
        <button class="btn btn-outline" :disabled="props.loading || !selectedIds().length" @click="emit('delete', selectedIds())">删除选中</button>
        <button class="btn btn-outline" :disabled="props.loading || !props.images.length" @click="emit('clear')">清空图片池</button>
      </div>
    </div>

    <div class="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <article v-for="image in props.images" :key="image.id" class="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <img
          :src="image.previewUrl || image.url || image.path"
          :alt="image.id"
          class="h-40 w-full cursor-zoom-in object-cover"
          title="双击预览"
          @dblclick="openPreview(image)"
        />
          <div class="space-y-2 p-3">
            <div class="flex items-center justify-between gap-2">
              <label class="flex min-w-0 items-center gap-2">
                <input v-model="image.selected" type="checkbox" class="size-4 rounded border-slate-300" />
                <span class="truncate text-sm font-semibold text-slate-950">{{ image.id }}</span>
              </label>
              <span class="badge" :class="image.origin === 'ai_generated' ? 'bg-purple-50 text-purple-700 ring-1 ring-purple-200' : 'bg-slate-100 text-slate-600 ring-1 ring-slate-200'">
                {{ image.origin }}
              </span>
            </div>
            <p class="text-xs text-slate-500">{{ image.width }}×{{ image.height }} · {{ image.usage }}</p>
            <p v-if="image.targetLanguage" class="text-xs font-medium text-brand-700">{{ image.targetLanguage }} · from {{ image.translatedFromId }}</p>
            <div class="flex flex-wrap gap-2 text-xs">
              <label v-for="platform in platforms" :key="platform.key" class="flex items-center gap-1 rounded-lg bg-slate-50 px-2 py-1 ring-1 ring-slate-200">
                <input
                  type="checkbox"
                  :checked="image.platforms.includes(platform.key)"
                  @change="togglePlatform(image, platform.key, ($event.target as HTMLInputElement).checked)"
                />
                {{ platform.label }}
              </label>
            </div>
            <div class="flex flex-wrap gap-1.5">
              <span v-for="platform in image.platforms" :key="platform" class="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-600">
                {{ platform }}
              </span>
            </div>
            <div class="flex flex-wrap gap-2">
              <button class="btn btn-outline py-1.5 text-xs" :disabled="props.loading || image.isMain" @click="emit('setMain', image.id)">
                {{ image.isMain ? '当前主图' : '设为主图' }}
              </button>
              <button class="btn btn-outline py-1.5 text-xs" :disabled="props.loading" @click="emit('delete', [image.id])">删除</button>
            </div>
          </div>
      </article>
      <div v-if="!props.images.length" class="col-span-full rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-10 text-center text-sm text-slate-500">
        暂无图片。请先采集商品、上传图片，或导入手动图片链接。
      </div>
    </div>
  </section>

  <div
    v-if="previewImage"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
    @click.self="closePreview"
  >
    <div class="relative flex max-h-full w-full max-w-6xl flex-col gap-3">
      <div class="flex items-center justify-between gap-3 rounded-xl bg-white px-4 py-3 shadow-card dark:bg-dark-900">
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold text-slate-950 dark:text-white">{{ previewImage.id }}</div>
          <div class="text-xs text-slate-500 dark:text-accent-300">{{ previewImage.width }}×{{ previewImage.height }} · {{ previewImage.origin }}</div>
        </div>
        <button class="btn btn-outline py-1.5" @click="closePreview">关闭</button>
      </div>
      <div class="flex min-h-0 items-center justify-center overflow-hidden rounded-xl bg-slate-950">
        <img :src="previewSrc" :alt="previewImage.id" class="max-h-[78vh] max-w-full object-contain" />
      </div>
    </div>
  </div>
</template>

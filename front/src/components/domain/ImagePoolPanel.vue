<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { ImageAsset } from '@/types/workflow'

const props = defineProps<{
  images: ImageAsset[]
  loading: boolean
  showTranslateAction?: boolean
  showDraftControls?: boolean
  draftAssetIds?: string[]
}>()

const emit = defineEmits<{
  translate: []
  imageEdit: [prompt: string]
  upload: [files: File[]]
  clear: []
  save: []
  setMain: [imageId: string]
  delete: [imageIds: string[]]
  toggleDraftImage: [image: ImageAsset, checked: boolean]
}>()

const input = ref<HTMLInputElement | null>(null)
const previewImage = ref<ImageAsset | null>(null)
const imageEditPromptOpen = ref(false)
const imageEditPrompt = ref('')

function choose() { input.value?.click() }
function selected(event: Event) {
  const node = event.target as HTMLInputElement
  const files = Array.from(node.files || [])
  if (files.length) emit('upload', files)
  node.value = ''
}

function selectedIds() {
  if (props.showDraftControls) {
    const draftIds = draftAssetIdSet.value
    return props.images.filter((image) => draftIds.has(image.id)).map((image) => image.id)
  }
  return props.images.filter((image) => image.selected).map((image) => image.id)
}

const draftAssetIdSet = computed(() => new Set(props.draftAssetIds || []))
const selectedImageCount = computed(() => selectedIds().length)

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

function openImageEditPrompt() {
  if (!selectedImageCount.value || props.loading) return
  imageEditPrompt.value = ''
  imageEditPromptOpen.value = true
}

function closeImageEditPrompt() {
  imageEditPromptOpen.value = false
  imageEditPrompt.value = ''
}

function submitImageEdit() {
  const prompt = imageEditPrompt.value.trim()
  if (!prompt || props.loading) return
  emit('imageEdit', prompt)
  closeImageEditPrompt()
}

function isInDraft(image: ImageAsset) {
  return draftAssetIdSet.value.has(image.id)
}

function eventChecked(event: Event) {
  return Boolean((event.target as HTMLInputElement | null)?.checked)
}

function toggleSelection(image: ImageAsset, checked: boolean) {
  image.selected = checked
  if (props.showDraftControls) {
    emit('toggleDraftImage', image, checked)
  }
}

function imageLabel(image: ImageAsset, index: number) {
  if (!image.id) return `素材 #${index + 1}`
  if (image.id.length <= 30) return image.id
  return `${image.id.slice(0, 18)}...${image.id.slice(-6)}`
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  if (imageEditPromptOpen.value) {
    closeImageEditPrompt()
    return
  }
  closePreview()
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
        <h2 class="card-title">素材图片池</h2>
        <p class="muted mt-1">{{ selectedImageCount }} 张已选素材</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <input ref="input" type="file" accept="image/*" multiple class="hidden" @change="selected" />
        <button class="btn btn-outline" :disabled="props.loading" @click="choose">上传图片</button>
        <button class="btn btn-secondary" :disabled="props.loading || !selectedImageCount" @click="openImageEditPrompt">AI 图生图</button>
        <button v-if="props.showTranslateAction !== false" class="btn btn-primary" :disabled="props.loading || !props.images.length" @click="emit('translate')">AI 翻译/重绘</button>
        <button class="btn btn-outline" :disabled="props.loading || !props.images.length" @click="emit('save')">保存素材库</button>
        <button class="btn btn-outline" :disabled="props.loading || !selectedIds().length" @click="emit('delete', selectedIds())">删除选中</button>
        <button class="btn btn-outline" :disabled="props.loading || !props.images.length" @click="emit('clear')">清空图片池</button>
      </div>
    </div>

    <div class="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
      <article v-for="(image, index) in props.images" :key="image.id" class="overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-dark-700 dark:bg-dark-900">
        <img
          :src="image.previewUrl || image.url || image.path"
          :alt="image.id"
          class="h-40 w-full cursor-zoom-in object-cover"
          title="双击预览"
          @dblclick="openPreview(image)"
        />
          <div class="space-y-2 p-3">
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <p class="truncate text-sm font-semibold text-slate-950 dark:text-white" :title="image.id">{{ imageLabel(image, index) }}</p>
                <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">{{ image.width }}×{{ image.height }} · {{ image.usage }}</p>
              </div>
              <span class="badge" :class="image.origin === 'ai_generated' ? 'bg-purple-50 text-purple-700 ring-1 ring-purple-200' : 'bg-slate-100 text-slate-600 ring-1 ring-slate-200'">
                {{ image.origin }}
              </span>
            </div>
            <p v-if="image.targetLanguage" class="text-xs font-medium text-brand-700 dark:text-primary-200">{{ image.targetLanguage }} · from {{ image.derivedFromId }}</p>
            <div class="flex flex-wrap items-center gap-2">
              <label
                class="inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs font-semibold"
                :class="props.showDraftControls && isInDraft(image)
                  ? 'border-primary-200 bg-primary-50 text-primary-700 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-200'
                  : 'border-slate-200 text-slate-600 dark:border-dark-700 dark:text-accent-200'"
              >
                <input
                  type="checkbox"
                  class="size-4 rounded border-slate-300"
                  :checked="props.showDraftControls ? isInDraft(image) : image.selected"
                  @change="toggleSelection(image, eventChecked($event))"
                />
                <span>选择素材</span>
              </label>
              <button v-if="!props.showDraftControls" class="btn btn-outline py-1.5 text-xs" :disabled="props.loading || image.isMain" @click="emit('setMain', image.id)">
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
    v-if="imageEditPromptOpen"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-sm"
    @click.self="closeImageEditPrompt"
  >
    <form class="w-full max-w-2xl rounded-2xl bg-white p-5 shadow-2xl ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700" @submit.prevent="submitImageEdit">
      <div class="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 class="text-lg font-black text-slate-950 dark:text-white">图生图</h3>
          <p class="mt-1 text-sm text-slate-500 dark:text-accent-300">已选 {{ selectedImageCount }} 张图片</p>
        </div>
        <button type="button" class="btn btn-outline py-1.5" @click="closeImageEditPrompt">关闭</button>
      </div>
      <textarea
        v-model="imageEditPrompt"
        autofocus
        rows="5"
        class="input min-h-[132px] resize-y"
        placeholder="输入本次图片处理要求"
      />
      <div class="mt-4 flex flex-wrap justify-end gap-2">
        <button type="button" class="btn btn-outline" :disabled="props.loading" @click="closeImageEditPrompt">取消</button>
        <button type="submit" class="btn btn-primary" :disabled="props.loading || !imageEditPrompt.trim()">生成图片</button>
      </div>
    </form>
  </div>

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

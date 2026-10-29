<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchSystemSettings, previewWatermark, saveSystemSettings } from '@/api/systemSettings'
import type { BackendWatermarkSettings } from '@/types/workflow.generated'

const { t } = useI18n({ useScope: 'local', messages: {
  zh: {
    title: '发布图片水印', help: '斜向平铺，可单独使用文字或图片，也可同时使用。保存后，有内容就添加水印，全部清空则不添加；本地原图不变。',
    content: '水印文字', placeholder: '填写水印文字（可留空）', appearance: '平铺效果',
    upload: '水印图片', chooseImage: '上传图片', replaceImage: '更换图片', imageHelp: 'PNG / JPEG / WebP · 最大 2 MiB、400 万像素，推荐透明 PNG。',
    remove: '移除图片', color: '文字颜色', density: '疏密', sparse: '稀疏', dense: '密集',
    opacity: '不透明度', size: '水印大小', save: '保存水印设置', saved: '水印设置已保存', preview: '效果预览',
    previewAlt: '草稿图片上的水印效果', logoAlt: '已上传的水印图片', loading: '正在读取水印设置…', previewLoading: '正在更新效果…', retry: '重新读取',
    failed: '操作失败', invalidFile: '文件格式或大小不符合要求', empty: '未填写文字或图片，发布时使用原图。',
  },
  en: {
    title: 'Publication watermark', help: 'Use diagonal tiles with text, an image, or both. After saving, content is applied automatically; clear both to publish without a watermark. Local originals stay unchanged.',
    content: 'Watermark text', placeholder: 'Enter watermark text (optional)', appearance: 'Tile appearance',
    upload: 'Watermark image', chooseImage: 'Upload image', replaceImage: 'Replace image', imageHelp: 'PNG / JPEG / WebP · Up to 2 MiB and 4 megapixels. Transparent PNG recommended.',
    remove: 'Remove image', color: 'Text color', density: 'Density', sparse: 'Sparse', dense: 'Dense',
    opacity: 'Opacity', size: 'Watermark size', save: 'Save watermark', saved: 'Watermark settings saved', preview: 'Preview',
    previewAlt: 'Watermark on a draft image', logoAlt: 'Uploaded watermark image', loading: 'Loading watermark settings…', previewLoading: 'Updating preview…', retry: 'Reload',
    failed: 'Operation failed', invalidFile: 'Unsupported file format or size', empty: 'No text or image. Original images will be published.',
  },
} })
const settings = ref<BackendWatermarkSettings | null>(null)
const busy = ref(false)
const loading = ref(true)
const error = ref('')
const notice = ref('')
const preview = ref('')
const sampleName = ref('')
const previewMessage = ref('')
const previewLoading = ref(false)
const hasContent = computed(() => Boolean(settings.value?.text.trim() || settings.value?.image_data_url))
let disposed = false
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
let previewVersion = 0

function schedulePreview() {
  clearTimeout(timer)
  controller?.abort()
  const version = ++previewVersion
  notice.value = ''; previewMessage.value = ''
  if (!settings.value) return
  const snapshot = { ...settings.value }
  previewLoading.value = true
  timer = setTimeout(async () => {
    const request = new AbortController()
    controller = request
    try {
      const result = await previewWatermark(snapshot, request.signal)
      if (disposed || version !== previewVersion) return
      preview.value = result.preview_url
      sampleName.value = result.sample_name
      previewMessage.value = result.message
    } catch (exc) {
      if (!disposed && version === previewVersion) {
        preview.value = ''; sampleName.value = ''
        previewMessage.value = exc instanceof Error ? exc.message : t('failed')
      }
    } finally {
      if (!disposed && version === previewVersion) previewLoading.value = false
    }
  }, 300)
}
watch(settings, schedulePreview, { deep: true, flush: 'sync' })
async function load() {
  loading.value = true; error.value = ''
  try { const result = await fetchSystemSettings(); if (!disposed) settings.value = result.watermark }
  catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : t('failed') }
  finally { if (!disposed) loading.value = false }
}
async function save() {
  if (busy.value || !settings.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await saveSystemSettings({ watermark: { ...settings.value } })
    if (!disposed) { settings.value = result.watermark; notice.value = t('saved') }
  } catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : t('failed') }
  finally { if (!disposed) busy.value = false }
}
function dataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error(t('invalidFile')))
    reader.readAsDataURL(file)
  })
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !settings.value) return
  busy.value = true; error.value = ''
  try {
    if (file.size > 2 * 1024 * 1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) throw new Error(t('invalidFile'))
    const value = await dataUrl(file)
    if (!disposed) settings.value.image_data_url = value
  } catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : t('failed') }
  finally { input.value = ''; if (!disposed) busy.value = false }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true; previewVersion++; clearTimeout(timer); controller?.abort() })
</script>

<template>
  <div class="space-y-4">
    <div><h3 class="font-semibold">{{ t('title') }}</h3><p class="muted mt-2">{{ t('help') }}</p></div>
    <p v-if="loading" role="status">{{ t('loading') }}</p>
    <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error }} <button v-if="!settings" class="underline" :disabled="loading" @click="load">{{ t('retry') }}</button></p>
    <form v-if="settings" class="grid items-start gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]" @submit.prevent="save">
      <fieldset :disabled="busy || loading" class="min-w-0 overflow-hidden rounded-xl border border-accent-200 dark:border-dark-700">
        <div class="space-y-5 p-5">
          <div class="space-y-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <label for="watermark-text" class="text-sm font-medium">{{ t('content') }}</label>
              <label class="flex items-center gap-2 text-xs text-accent-500 dark:text-accent-400">
                {{ t('color') }}
                <input v-model="settings.color" type="color" class="h-7 w-9 cursor-pointer rounded border border-accent-200 bg-transparent p-0.5 dark:border-dark-600" />
              </label>
            </div>
            <textarea id="watermark-text" v-model="settings.text" :placeholder="t('placeholder')" class="input block w-full resize-y" rows="3" maxlength="200" />
          </div>
          <div class="space-y-2">
            <p class="text-sm font-medium">{{ t('upload') }}</p>
            <div class="flex items-center gap-4 rounded-lg border border-dashed border-accent-300 bg-accent-50/60 p-3 dark:border-dark-600 dark:bg-dark-800/50">
              <img v-if="settings.image_data_url" :src="settings.image_data_url" :alt="t('logoAlt')" class="h-16 w-20 shrink-0 rounded bg-white object-contain dark:bg-dark-700" />
              <div class="min-w-0 space-y-2">
                <div class="flex flex-wrap items-center gap-3">
                  <label class="cursor-pointer rounded text-sm font-medium text-primary-700 focus-within:ring-2 focus-within:ring-primary-500 dark:text-primary-300" :class="{ 'pointer-events-none opacity-50': busy || loading }">
                    {{ settings.image_data_url ? t('replaceImage') : t('chooseImage') }}
                    <input data-testid="watermark-logo" type="file" accept="image/png,image/jpeg,image/webp" class="sr-only" @change="upload" />
                  </label>
                  <button v-if="settings.image_data_url" type="button" class="text-xs text-accent-500 underline underline-offset-2 dark:text-accent-400" @click="settings.image_data_url = ''">{{ t('remove') }}</button>
                </div>
                <p class="muted text-xs leading-relaxed">{{ t('imageHelp') }}</p>
              </div>
            </div>
          </div>
        </div>
        <div class="space-y-4 border-t border-accent-200 p-5 dark:border-dark-700">
          <h4 class="text-sm font-medium">{{ t('appearance') }}</h4>
          <label class="block text-sm">
            <span class="flex items-center justify-between gap-3"><span>{{ t('size') }}</span><span class="watermark-value">{{ settings.size_percent }}%</span></span>
            <input v-model.number="settings.size_percent" data-testid="watermark-size" type="range" min="5" max="80" class="mt-2 block w-full accent-primary-600" />
          </label>
          <label class="block text-sm">
            <span class="flex items-center justify-between gap-3"><span>{{ t('density') }}</span><span class="watermark-value">{{ settings.density }}</span></span>
            <input v-model.number="settings.density" data-testid="watermark-density" type="range" min="1" max="10" class="mt-2 block w-full accent-primary-600" />
            <span class="mt-1 flex justify-between text-xs text-accent-500 dark:text-accent-400"><span>{{ t('sparse') }}</span><span>{{ t('dense') }}</span></span>
          </label>
          <label class="block text-sm">
            <span class="flex items-center justify-between gap-3"><span>{{ t('opacity') }}</span><span class="watermark-value">{{ settings.opacity }}%</span></span>
            <input v-model.number="settings.opacity" type="range" min="1" max="100" class="mt-2 block w-full accent-primary-600" />
          </label>
        </div>
        <div class="space-y-3 border-t border-accent-200 bg-accent-50/60 p-5 dark:border-dark-700 dark:bg-dark-800/50">
          <p v-if="!hasContent" class="muted text-xs">{{ t('empty') }}</p>
          <div class="flex flex-wrap items-center gap-3">
            <button type="submit" class="btn btn-primary">{{ t('save') }}</button>
            <p v-if="notice" role="status" class="text-sm text-primary-700 dark:text-primary-300">{{ notice }}</p>
          </div>
        </div>
      </fieldset>
      <section data-testid="watermark-preview" :aria-label="t('preview')" :aria-busy="previewLoading" class="space-y-3 lg:sticky lg:top-4 lg:self-start">
        <div class="relative flex min-h-72 items-center justify-center overflow-hidden rounded-lg border border-accent-200 bg-accent-50 dark:border-dark-700 dark:bg-dark-800">
          <img v-if="preview" :src="preview" :alt="t('previewAlt')" class="max-h-[40rem] w-full object-contain transition-opacity" :class="{ 'opacity-40': previewLoading }" />
          <p v-if="previewLoading" role="status" class="absolute rounded bg-white/90 px-3 py-2 text-sm text-accent-700 dark:bg-dark-900/90 dark:text-accent-200">{{ t('previewLoading') }}</p>
          <p v-else-if="previewMessage" role="status" class="p-6 text-center text-sm text-accent-500 dark:text-accent-300">{{ previewMessage }}</p>
        </div>
        <p v-if="sampleName" class="truncate text-xs text-accent-500">{{ sampleName }}</p>
      </section>
    </form>
  </div>
</template>

<style scoped>
.watermark-value {
  @apply min-w-12 rounded-md bg-accent-100 px-2 py-1 text-center text-xs tabular-nums text-accent-700 dark:bg-dark-700 dark:text-accent-200;
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import PageSaveButton from '@/components/layout/PageSaveButton.vue'
import { usePageSaveAction, type PageSaveAction } from '@/composables/usePageSaveAction'
import { fetchSystemSettings, previewWatermark, saveSystemSettings } from '@/api/systemSettings'
import type { BackendWatermarkSettings } from '@/types/workflow.generated'

const formId = useId()
const formEl = ref<HTMLFormElement | null>(null)
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
const saveAction = computed<PageSaveAction | null>(() => (
  settings.value
    ? {
        label: busy.value ? '保存中…' : '保存水印设置',
        disabled: busy.value || loading.value,
        form: formId,
        submitForm: () => formEl.value?.requestSubmit(),
      }
    : null
))
const { hasHost } = usePageSaveAction(saveAction)
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
        previewMessage.value = exc instanceof Error ? exc.message : '操作失败'
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
  catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : '操作失败' }
  finally { if (!disposed) loading.value = false }
}
async function save() {
  if (busy.value || !settings.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await saveSystemSettings({ watermark: { ...settings.value } })
    if (!disposed) { settings.value = result.watermark; notice.value = '水印设置已保存' }
  } catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : '操作失败' }
  finally { if (!disposed) busy.value = false }
}
function dataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('文件格式或大小不符合要求'))
    reader.readAsDataURL(file)
  })
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !settings.value) return
  busy.value = true; error.value = ''
  try {
    if (file.size > 2 * 1024 * 1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) throw new Error('文件格式或大小不符合要求')
    const value = await dataUrl(file)
    if (!disposed) settings.value.image_data_url = value
  } catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : '操作失败' }
  finally { input.value = ''; if (!disposed) busy.value = false }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true; previewVersion++; clearTimeout(timer); controller?.abort() })
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div><h3 class="font-semibold">发布图片水印</h3><p class="muted mt-2">斜向平铺，可单独使用文字或图片，也可同时使用。保存后，有内容就添加水印，全部清空则不添加；本地原图不变。</p></div>
      <PageSaveButton v-if="!hasHost && saveAction" :action="saveAction" />
    </div>
    <p v-if="loading" role="status">正在读取水印设置…</p>
    <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error }} <button v-if="!settings" class="underline" :disabled="loading" @click="load">重新读取</button></p>
    <p v-if="notice" role="status" class="text-sm text-primary-700 dark:text-primary-300">{{ notice }}</p>
    <form v-if="settings" :id="formId" ref="formEl" class="grid items-start gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]" @submit.prevent="save">
      <fieldset :disabled="busy || loading" class="min-w-0 overflow-hidden rounded-xl border border-accent-200 dark:border-dark-700">
        <div class="space-y-5 p-5">
          <div class="space-y-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <label for="watermark-text" class="text-sm font-medium">水印文字</label>
              <label class="flex items-center gap-2 text-xs text-accent-500 dark:text-accent-400">
                文字颜色
                <input v-model="settings.color" type="color" class="h-7 w-9 cursor-pointer rounded border border-accent-200 bg-transparent p-0.5 dark:border-dark-600" />
              </label>
            </div>
            <textarea id="watermark-text" v-model="settings.text" placeholder="填写水印文字（可留空）" class="input block w-full resize-y" rows="3" maxlength="200" />
          </div>
          <div class="space-y-2">
            <p class="text-sm font-medium">水印图片</p>
            <div class="flex items-center gap-4 rounded-lg border border-dashed border-accent-300 bg-accent-50/60 p-3 dark:border-dark-600 dark:bg-dark-800/50">
              <img v-if="settings.image_data_url" :src="settings.image_data_url" alt="已上传的水印图片" class="h-16 w-20 shrink-0 rounded bg-white object-contain dark:bg-dark-700" />
              <div class="min-w-0 space-y-2">
                <div class="flex flex-wrap items-center gap-3">
                  <label class="cursor-pointer rounded text-sm font-medium text-primary-700 focus-within:ring-2 focus-within:ring-primary-500 dark:text-primary-300" :class="{ 'pointer-events-none opacity-50': busy || loading }">
                    {{ settings.image_data_url ? '更换图片' : '上传图片' }}
                    <input data-testid="watermark-logo" type="file" accept="image/png,image/jpeg,image/webp" class="sr-only" @change="upload" />
                  </label>
                  <button v-if="settings.image_data_url" type="button" class="text-xs text-accent-500 underline underline-offset-2 dark:text-accent-400" @click="settings.image_data_url = ''">移除图片</button>
                </div>
                <p class="muted text-xs leading-relaxed">PNG / JPEG / WebP · 最大 2 MiB、400 万像素，推荐透明 PNG。</p>
              </div>
            </div>
          </div>
        </div>
        <div class="space-y-4 border-t border-accent-200 p-5 dark:border-dark-700">
          <h4 class="text-sm font-medium">平铺效果</h4>
          <label class="block text-sm">
            <span class="flex items-center justify-between gap-3"><span>水印大小</span><span class="watermark-value">{{ settings.size_percent }}%</span></span>
            <input v-model.number="settings.size_percent" data-testid="watermark-size" type="range" min="5" max="80" class="mt-2 block w-full accent-primary-600" />
          </label>
          <label class="block text-sm">
            <span class="flex items-center justify-between gap-3"><span>疏密</span><span class="watermark-value">{{ settings.density }}</span></span>
            <input v-model.number="settings.density" data-testid="watermark-density" type="range" min="1" max="10" class="mt-2 block w-full accent-primary-600" />
            <span class="mt-1 flex justify-between text-xs text-accent-500 dark:text-accent-400"><span>稀疏</span><span>密集</span></span>
          </label>
          <label class="block text-sm">
            <span class="flex items-center justify-between gap-3"><span>不透明度</span><span class="watermark-value">{{ settings.opacity }}%</span></span>
            <input v-model.number="settings.opacity" type="range" min="1" max="100" class="mt-2 block w-full accent-primary-600" />
          </label>
        </div>
        <div v-if="!hasContent" class="border-t border-accent-200 bg-accent-50/60 px-5 py-3 dark:border-dark-700 dark:bg-dark-800/50">
          <p class="muted text-xs">未填写文字或图片，发布时使用原图。</p>
        </div>
      </fieldset>
      <section data-testid="watermark-preview" aria-label="效果预览" :aria-busy="previewLoading" class="space-y-3 lg:sticky lg:top-4 lg:self-start">
        <div class="relative flex min-h-72 items-center justify-center overflow-hidden rounded-lg border border-accent-200 bg-accent-50 dark:border-dark-700 dark:bg-dark-800">
          <img v-if="preview" :src="preview" alt="草稿图片上的水印效果" class="max-h-[40rem] w-full object-contain transition-opacity" :class="{ 'opacity-40': previewLoading }" />
          <p v-if="previewLoading" role="status" class="absolute rounded bg-white/90 px-3 py-2 text-sm text-accent-700 dark:bg-dark-900/90 dark:text-accent-200">正在更新效果…</p>
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

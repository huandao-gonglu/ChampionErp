<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { ImageAsset } from '@/types/workflow'

const props = withDefaults(defineProps<{
  modelValue: string
  images?: ImageAsset[]
  disabled?: boolean
  label?: string
  inherited?: boolean
  allowInherit?: boolean
}>(), { images: () => [], label: 'SKU 图片' })
const emit = defineEmits<{ 'update:modelValue': [assetId: string]; inherit: [] }>()
const open = ref(false)
const dialog = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const current = computed(() => props.images.find(image => image.id === props.modelValue))
async function show() {
  open.value = true
  await nextTick()
  dialog.value?.querySelector<HTMLButtonElement>('button')?.focus()
}
function close() {
  open.value = false
  trigger.value?.focus()
}
function trapFocus(event: KeyboardEvent) {
  const buttons = Array.from(dialog.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [])
  const first = buttons[0], last = buttons[buttons.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
function choose(assetId: string) {
  emit('update:modelValue', assetId)
  close()
}
</script>

<template>
  <div class="space-y-2">
    <div class="flex items-center gap-3">
      <img v-if="current" :src="current.previewUrl || current.url || current.path" :alt="label" class="size-14 rounded-lg border object-contain" />
      <div class="space-y-1">
        <p class="text-xs" :class="modelValue && !current ? 'text-red-600' : 'text-slate-500'">{{ modelValue && !current ? '关联图片已移出图片池，请重新选择' : inherited ? '使用商品默认图' : current ? '已选择图片' : '未单独指定，使用公共发布图片' }}</p>
        <button ref="trigger" type="button" class="btn btn-outline !px-3 !py-1.5 text-xs" :disabled="disabled" :aria-label="`选择${label}`" @click="show">从图片池选图</button>
        <button v-if="allowInherit && !inherited" type="button" class="ml-2 text-xs text-primary-700" :disabled="disabled" @click="emit('inherit')">恢复商品默认图</button>
      </div>
    </div>
    <Teleport to="body">
      <div v-if="open" ref="dialog" role="dialog" aria-modal="true" :aria-label="`选择${label}`" class="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/60 p-4" @click.self="close" @keydown.esc.stop="close" @keydown.tab="trapFocus">
        <section class="max-h-[80vh] w-full max-w-3xl overflow-auto rounded-2xl bg-white p-5 shadow-xl dark:bg-dark-900">
          <div class="mb-4 flex items-center justify-between"><h3 class="font-bold">{{ label }} · 从图片池选择</h3><button type="button" class="btn btn-outline" @click="close">关闭</button></div>
          <p v-if="!images.length" class="py-6 text-sm text-slate-500">图片池为空，请先在图片页上传图片。</p>
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <button v-for="image in images" :key="image.id" type="button" class="overflow-hidden rounded-xl border-2 p-2 text-left" :class="image.id === modelValue ? 'border-primary-500' : 'border-slate-200 dark:border-dark-700'" :aria-label="`使用图片 ${image.id}`" :aria-pressed="image.id === modelValue" :disabled="disabled" @click="choose(image.id)">
              <img :src="image.previewUrl || image.url || image.path" :alt="image.id" class="aspect-square w-full object-contain" />
              <span class="mt-2 block truncate text-xs">{{ image.origin === 'ai_generated' ? '处理后的图片' : image.origin === 'local_upload' ? '上传图片' : '商品图片' }}<template v-if="image.width && image.height"> · {{ image.width }}×{{ image.height }}</template></span>
            </button>
          </div>
          <div class="mt-4 flex flex-wrap gap-3">
            <button v-if="allowInherit" type="button" class="btn btn-outline" :disabled="disabled" @click="emit('inherit'); close()">使用商品默认图</button>
            <button type="button" class="btn btn-outline" :disabled="disabled" @click="choose('')">使用公共发布图片</button>
          </div>
        </section>
      </div>
    </Teleport>
  </div>
</template>

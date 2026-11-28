<script setup lang="ts">
import type { DesktopImageAction } from '@/utils/desktopHandoff'
import { vDesktop } from '@/utils/desktopHandoffDirective'
import { modelOperationReason } from '@/utils/desktop'
import { computed, ref, watch } from 'vue'
import {
  PhFloppyDisk,
  PhImage,
  PhMagicWand,
  PhMagnifyingGlass,
  PhSparkle,
  PhStar,
  PhTrash,
  PhUploadSimple,
  PhX,
} from '@phosphor-icons/vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import SkuImageAssignmentDialog from './SkuImageAssignmentDialog.vue'
import { imageSourceLabel, imageUsageLabel, imageUrl, type SkuImageAssignment, type SkuImageBatchChange } from '@/utils/draftImages'
import type { ImageAsset } from '@/types/workflow'

const props = defineProps<{
  images: ImageAsset[]; loading: boolean; showTranslateAction?: boolean
  hideSaveAction?: boolean
  skuAssignments?: SkuImageAssignment[]
}>()
const emit = defineEmits<{
  translate: [imageIds: string[]]; imageEdit: [request: { prompt: string; imageIds: string[] }]
  upload: [files: File[]]; clear: []; save: []; setMain: [imageId: string]; delete: [imageIds: string[]]
  assignSkus: [changes: SkuImageBatchChange[]]
}>()
const input = ref<HTMLInputElement | null>(null)
const previewImage = ref<ImageAsset | null>(null)
const assignmentImage = ref<ImageAsset | null>(null)
const imageEditPromptOpen = ref(false)
const imageEditPrompt = ref('')
const selectedImageIdSet = ref(new Set<string>())
const query = ref('')
const filter = ref('all')
const origin = ref('all')
const language = ref('all')
const deleteRequest = ref<{ ids: string[]; clear: boolean } | null>(null)
const assignmentsByImage = computed(() => {
  const result = new Map<string, SkuImageAssignment[]>()
  for (const sku of props.skuAssignments || []) result.set(sku.imageAssetId, [...(result.get(sku.imageAssetId) || []), sku])
  return result
})
const imageReason = computed(() => modelOperationReason('image.translate'))
const selectedIds = computed(() => props.images.filter(image => selectedImageIdSet.value.has(image.id)).map(image => image.id))
const languages = computed(() => [...new Set(props.images.map(image => image.targetLanguage || 'original'))])
function label(image: ImageAsset) { return `${imageSourceLabel(image)} ${props.images.indexOf(image) + 1}` }
const visibleImages = computed(() => props.images.filter(image => {
  const linked = assignmentsByImage.value.get(image.id) || []
  const text = [label(image), imageUsageLabel(image), image.id, ...linked.map(sku => sku.name)].join(' ').toLocaleLowerCase()
  return (!query.value.trim() || text.includes(query.value.trim().toLocaleLowerCase()))
    && (origin.value === 'all' || imageSourceLabel(image) === origin.value)
    && (language.value === 'all' || (image.targetLanguage || 'original') === language.value)
    && (filter.value === 'all'
      || (filter.value === 'linked' && linked.length > 0) || (filter.value === 'unlinked' && !linked.length)
      || (filter.value === 'selected' && selectedImageIdSet.value.has(image.id)))
}))
const deletionUses = computed(() => (props.skuAssignments || []).filter(sku => deleteRequest.value?.ids.includes(sku.imageAssetId)))
function selectedFiles(event: Event) {
  const node = event.target as HTMLInputElement
  const files = Array.from(node.files || [])
  if (files.length) emit('upload', files)
  node.value = ''
}
function selectImage(image: ImageAsset, checked: boolean) {
  const next = new Set(selectedImageIdSet.value)
  if (checked) next.add(image.id); else next.delete(image.id)
  selectedImageIdSet.value = next
}
function selectVisible() { selectedImageIdSet.value = new Set([...selectedImageIdSet.value, ...visibleImages.value.map(image => image.id)]) }
function openEdit() { imageEditPrompt.value = ''; imageEditPromptOpen.value = true }
function pendingImageAction(): DesktopImageAction | undefined {
  if (imageEditPromptOpen.value) return { action: 'edit', imageIds: [...selectedIds.value], prompt: imageEditPrompt.value }
}
function restoreImageAction(state: DesktopImageAction) {
  selectedImageIdSet.value = new Set(state.imageIds)
  imageEditPrompt.value = state.prompt
  imageEditPromptOpen.value = true
}
defineExpose({ pendingImageAction, restoreImageAction })

function submitEdit() {
  if (imageReason.value || !imageEditPrompt.value.trim() || props.loading || !selectedIds.value.length) return
  emit('imageEdit', { prompt: imageEditPrompt.value.trim(), imageIds: [...selectedIds.value] })
  imageEditPromptOpen.value = false
}
function confirmDelete() {
  if (props.loading || !deleteRequest.value) return
  if (deleteRequest.value.clear) emit('clear'); else emit('delete', deleteRequest.value.ids)
  deleteRequest.value = null
}
watch(() => props.images, () => {
  const ids = new Set(props.images.map(image => image.id))
  selectedImageIdSet.value = new Set([...selectedImageIdSet.value].filter(id => ids.has(id)))
  if (previewImage.value && !ids.has(previewImage.value.id)) previewImage.value = null
})
</script>

<template>
  <section class="rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-800/70" aria-label="素材图片池">
    <!-- 顶部操作栏（扁平化，去除原生 details 折叠框） -->
    <div class="space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-bold text-accent-950 dark:text-white">
            素材图片池
            <span class="ml-1.5 rounded-full bg-accent-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-accent-600 dark:bg-dark-700 dark:text-accent-200">{{ images.length }}</span>
          </h2>
          <p class="mt-0.5 text-xs text-accent-500 dark:text-accent-400">商品共享素材，上传与 AI 处理结果会同步保存；删除共享素材会影响引用它的草稿。</p>
        </div>
        <div class="flex flex-wrap items-center gap-1.5">
          <button
            v-if="!hideSaveAction"
            type="button"
            class="btn btn-outline !min-h-8 !rounded-xl !px-2.5 !py-1 text-xs"
            :disabled="loading || !images.length"
            @click="emit('save')"
          >
            <PhFloppyDisk :size="14" aria-hidden="true" />保存商品素材
          </button>
          <button
            type="button"
            class="btn btn-outline !min-h-8 !rounded-xl border-rose-200 !px-2.5 !py-1 text-xs text-rose-700 hover:bg-rose-50 dark:border-rose-500/30 dark:text-rose-300 dark:hover:bg-rose-500/10"
            :disabled="loading || !selectedIds.length"
            @click="deleteRequest = { ids: [...selectedIds], clear: false }"
          >
            从素材库删除选中
          </button>
          <button
            type="button"
            class="btn btn-outline !min-h-8 !rounded-xl !px-2.5 !py-1 text-xs text-accent-500 hover:text-rose-700 dark:text-accent-400 dark:hover:text-rose-300"
            :disabled="loading || !images.length"
            @click="deleteRequest = { ids: images.map(image => image.id), clear: true }"
          >
            清空商品素材库
          </button>
        </div>
      </div>

      <p v-if="imageReason" role="status" class="rounded-lg bg-amber-50 px-3 py-1.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">{{ imageReason }}</p>

      <!-- 上传、AI 处理与筛选 -->
      <div class="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-accent-200/80 bg-accent-50/70 p-2.5 dark:border-dark-700 dark:bg-dark-900/50">
        <div class="flex flex-wrap items-center gap-1.5">
          <input ref="input" type="file" accept="image/*" multiple class="hidden" @change="selectedFiles" />
          <button type="button" class="btn btn-outline !min-h-8 !rounded-lg !px-2.5 !py-1 text-xs" :disabled="loading" @click="input?.click()">
            <PhUploadSimple :size="14" aria-hidden="true" />上传图片
          </button>
          <button type="button" class="btn btn-secondary !min-h-8 !rounded-lg !px-2.5 !py-1 text-xs" :disabled="!!imageReason || loading || !selectedIds.length" @click="openEdit">
            <PhMagicWand :size="14" aria-hidden="true" />AI 图生图（{{ selectedIds.length }}）
          </button>
          <button
            v-if="showTranslateAction !== false"
            v-desktop="'image.translate'"
            type="button"
            class="btn btn-primary !min-h-8 !rounded-lg !px-2.5 !py-1 text-xs"
            :disabled="!!imageReason || loading || !selectedIds.length"
            @click="emit('translate', [...selectedIds])"
          >
            <PhSparkle :size="14" aria-hidden="true" />AI 翻译/重绘（{{ selectedIds.length }}）
          </button>
          <button type="button" class="btn btn-outline !min-h-8 !rounded-lg !px-2.5 !py-1 text-xs" :disabled="loading || !visibleImages.length" @click="selectVisible">
            选择筛选结果
          </button>
          <button v-if="selectedIds.length" type="button" class="btn btn-outline !min-h-8 !rounded-lg !px-2.5 !py-1 text-xs" :disabled="loading" @click="selectedImageIdSet = new Set()">
            取消选择
          </button>
        </div>

        <div class="flex flex-wrap items-center gap-1.5">
          <div class="relative w-44">
            <PhMagnifyingGlass :size="14" aria-hidden="true" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-accent-400" />
            <input v-model="query" aria-label="搜索素材" class="input h-8 !rounded-lg !py-0 !pl-8 !pr-6 text-xs" placeholder="搜索款式、用途" />
            <button v-if="query" type="button" aria-label="清空素材搜索" class="absolute right-1.5 top-1/2 -translate-y-1/2 text-accent-400 hover:text-accent-700" @click="query = ''">
              <PhX :size="12" aria-hidden="true" />
            </button>
          </div>
          <select v-model="filter" aria-label="图片用途" class="input h-8 !w-auto !rounded-lg !py-0 text-xs">
            <option value="all">全部用途</option>
            <option value="linked">已关联 SKU</option>
            <option value="unlinked">未关联 SKU</option>
            <option value="selected">已选择处理</option>
          </select>
          <select v-model="origin" aria-label="素材来源" class="input h-8 !w-auto !rounded-lg !py-0 text-xs">
            <option value="all">全部来源</option>
            <option>采集图片</option>
            <option>上传图片</option>
            <option>AI 翻译</option>
            <option>AI 处理</option>
          </select>
          <select v-model="language" aria-label="图片语言" class="input h-8 !w-auto !rounded-lg !py-0 text-xs">
            <option value="all">全部语言</option>
            <option v-for="item in languages" :key="item" :value="item">{{ item === 'original' ? '原图 / 未标注' : item }}</option>
          </select>
        </div>
      </div>

      <p class="text-xs text-accent-500 tabular-nums dark:text-accent-400" aria-live="polite">
        显示 {{ visibleImages.length }} / {{ images.length }} 张 · 已选择 {{ selectedIds.length }} 张
        <span v-if="selectedIds.some(id => !visibleImages.some(image => image.id === id))">（包含筛选外的已选图片）</span>
      </p>
    </div>

    <!-- 紧凑图片卡片网格 -->
    <div v-if="visibleImages.length" class="mt-4 grid grid-cols-[repeat(auto-fill,minmax(min(100%,12.5rem),1fr))] gap-3">
      <article
        v-for="image in visibleImages"
        :key="image.id"
        :data-image-id="image.id"
        class="group relative flex min-w-0 flex-col overflow-hidden rounded-xl border bg-white transition dark:bg-dark-900"
        :class="selectedImageIdSet.has(image.id) ? 'border-primary-500 ring-2 ring-primary-300/80 dark:ring-primary-500/40' : 'border-accent-200/90 hover:border-accent-300 dark:border-dark-700 dark:hover:border-dark-600'"
      >
        <div class="relative bg-accent-100/80 dark:bg-dark-800">
          <button
            type="button"
            class="block w-full focus-visible:ring-4 focus-visible:ring-inset focus-visible:ring-primary-500"
            :aria-label="`预览${label(image)}`"
            @click="previewImage = image"
          >
            <img :src="imageUrl(image)" referrerpolicy="no-referrer" :alt="label(image)" class="aspect-square w-full object-contain" />
          </button>

          <!-- 左上角悬浮勾选框 -->
          <label class="absolute left-2 top-2 grid size-7 cursor-pointer place-items-center rounded-lg bg-white/90 shadow-sm backdrop-blur-sm transition hover:bg-white dark:bg-dark-900/90">
            <input
              type="checkbox"
              class="size-4 cursor-pointer accent-primary-600"
              :disabled="loading"
              :aria-label="`选择处理图片 ${image.id}`"
              :checked="selectedImageIdSet.has(image.id)"
              @change="selectImage(image, ($event.target as HTMLInputElement).checked)"
            />
          </label>

          <!-- 右上角主图徽标 -->
          <span
            v-if="image.isMain"
            class="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md bg-primary-600/95 px-2 py-0.5 text-[10px] font-semibold text-white shadow-sm"
          >
            <PhStar :size="10" weight="fill" aria-hidden="true" />默认主图
          </span>

          <!-- 右下角尺寸角标 -->
          <span
            v-if="image.width && image.height"
            class="absolute bottom-1.5 right-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-white"
          >
            {{ image.width }}×{{ image.height }}
          </span>
        </div>

        <div class="flex flex-1 flex-col justify-between gap-2 p-2.5">
          <div>
            <div class="flex items-center justify-between gap-1 text-xs">
              <span class="truncate font-semibold text-accent-900 dark:text-accent-100" :title="image.id">{{ label(image) }}</span>
              <span class="shrink-0 text-[11px] text-accent-500 dark:text-accent-400">{{ imageUsageLabel(image) }}<span v-if="image.targetLanguage"> · {{ image.targetLanguage }}</span></span>
            </div>
            <p
              v-if="assignmentsByImage.get(image.id)?.length"
              class="mt-1 line-clamp-1 text-[11px] font-medium text-primary-700 dark:text-primary-300"
              :title="assignmentsByImage.get(image.id)?.map(sku => sku.name).join('、')"
            >
              SKU：{{ assignmentsByImage.get(image.id)?.map(sku => sku.name).join('、') }}
            </p>
          </div>

          <div class="flex flex-wrap gap-1.5 pt-0.5">
            <button
              v-if="skuAssignments?.length"
              type="button"
              class="btn btn-outline !min-h-7 flex-1 !rounded-lg !px-2 !py-1 text-[11px]"
              :disabled="loading"
              @click="assignmentImage = image"
            >
              设置 SKU 主图（{{ assignmentsByImage.get(image.id)?.length || 0 }}）
            </button>
            <button
              type="button"
              class="btn btn-outline !min-h-7 !rounded-lg !px-2 !py-1 text-[11px]"
              :class="skuAssignments?.length ? '' : 'w-full'"
              :disabled="loading || image.isMain"
              @click="emit('setMain', image.id)"
            >
              {{ image.isMain ? '商品默认主图' : '设为商品主图' }}
            </button>
          </div>
        </div>
      </article>
    </div>

    <div v-else class="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <div class="grid size-11 place-items-center rounded-2xl bg-accent-100 text-accent-400 dark:bg-dark-800">
        <PhImage :size="22" aria-hidden="true" />
      </div>
      <p class="text-sm text-accent-500 dark:text-accent-400">
        {{ !images.length ? '暂无图片，请上传或采集商品图片。' : '没有符合条件的素材，请调整筛选条件。' }}
      </p>
    </div>
  </section>

  <WorkspaceDialog :open="Boolean(previewImage)" title="图片预览" @close="previewImage = null">
    <img v-if="previewImage" :src="imageUrl(previewImage)" referrerpolicy="no-referrer" :alt="label(previewImage)" class="mx-auto max-h-[70dvh] max-w-full object-contain" />
  </WorkspaceDialog>
  <WorkspaceDialog :open="imageEditPromptOpen" title="图生图" @close="imageEditPromptOpen = false">
    <form v-desktop="'image.translate'" class="space-y-4" @submit.prevent="submitEdit">
      <p>已选 {{ selectedIds.length }} 张图片</p>
      <p v-if="imageReason" role="status" class="muted">{{ imageReason }}</p>
      <label class="block text-sm">处理要求<textarea v-model="imageEditPrompt" rows="5" class="input mt-2" placeholder="输入本次图片处理要求" /></label>
      <div class="flex justify-end gap-3">
        <button type="button" class="btn btn-outline" @click="imageEditPromptOpen = false">取消</button>
        <button type="submit" class="btn btn-primary" :disabled="!!imageReason || loading || !imageEditPrompt.trim() || !selectedIds.length">生成图片</button>
      </div>
    </form>
  </WorkspaceDialog>
  <SkuImageAssignmentDialog :image="assignmentImage" :images="images" :assignments="skuAssignments || []" :draft-scope="false" :loading="loading" @close="assignmentImage = null" @apply="emit('assignSkus', $event)" />
  <WorkspaceDialog :open="Boolean(deleteRequest)" title="删除商品共享素材" @close="deleteRequest = null">
    <div class="space-y-4">
      <p>将从商品素材库删除 {{ deleteRequest?.ids.length }} 张图片。引用这些素材的其他草稿也会受影响；这不是从当前草稿移除图片。</p>
      <p v-if="deletionUses.length">当前关联的 SKU：{{ deletionUses.map(sku => sku.name).join('、') }}。删除后需重新选图。</p>
      <p v-else>当前商品未关联 SKU 主图；其他草稿仍可能引用这些素材。</p>
      <p>确认已不再需要这些共享素材后再删除。</p>
      <div class="flex justify-end gap-3">
        <button type="button" class="btn btn-outline" @click="deleteRequest = null">取消</button>
        <button type="button" class="btn btn-primary" :disabled="loading" @click="confirmDelete">
          <PhTrash :size="15" aria-hidden="true" />确认删除共享素材
        </button>
      </div>
    </div>
  </WorkspaceDialog>
</template>

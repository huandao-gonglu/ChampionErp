<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import SkuImageAssignmentDialog from './SkuImageAssignmentDialog.vue'
import { imageSourceLabel, imageUsageLabel, imageUrl, type SkuImageAssignment, type SkuImageBatchChange } from '@/utils/draftImages'
import type { ImageAsset } from '@/types/workflow'

const props = defineProps<{
  images: ImageAsset[]; loading: boolean; showTranslateAction?: boolean; showDraftControls?: boolean
  draftAssetIds?: string[]; skuAssignments?: SkuImageAssignment[]
}>()
const emit = defineEmits<{
  translate: [imageIds: string[]]; imageEdit: [request: { prompt: string; imageIds: string[] }]
  upload: [files: File[]]; clear: []; save: []; setMain: [imageId: string]; delete: [imageIds: string[]]
  toggleDraftImage: [image: ImageAsset, checked: boolean]; assignSkus: [changes: SkuImageBatchChange[]]
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
const draftAssetIdSet = computed(() => new Set(props.draftAssetIds || []))
const assignmentsByImage = computed(() => {
  const result = new Map<string, SkuImageAssignment[]>()
  for (const sku of props.skuAssignments || []) result.set(sku.imageAssetId, [...(result.get(sku.imageAssetId) || []), sku])
  return result
})
const selectedIds = computed(() => props.images.filter(image => selectedImageIdSet.value.has(image.id)).map(image => image.id))
const languages = computed(() => [...new Set(props.images.map(image => image.targetLanguage || 'original'))])
function label(image: ImageAsset) { return `${imageSourceLabel(image)} ${props.images.indexOf(image) + 1}` }
const visibleImages = computed(() => props.images.filter(image => {
  const linked = assignmentsByImage.value.get(image.id) || []
  const text = [label(image), imageUsageLabel(image), image.id, ...linked.map(sku => sku.name)].join(' ').toLocaleLowerCase()
  return (!query.value.trim() || text.includes(query.value.trim().toLocaleLowerCase()))
    && (origin.value === 'all' || imageSourceLabel(image) === origin.value)
    && (language.value === 'all' || (image.targetLanguage || 'original') === language.value)
    && (filter.value === 'all' || (filter.value === 'published' && draftAssetIdSet.value.has(image.id))
      || (filter.value === 'unpublished' && !draftAssetIdSet.value.has(image.id))
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
function submitEdit() {
  if (!imageEditPrompt.value.trim() || props.loading || !selectedIds.value.length) return
  emit('imageEdit', { prompt: imageEditPrompt.value.trim(), imageIds: [...selectedIds.value] })
  imageEditPromptOpen.value = false
}
function confirmDelete() {
  if (props.showDraftControls || props.loading || !deleteRequest.value) return
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
  <section class="card" aria-label="素材图片池">
    <div class="space-y-4">
      <div><h2 class="card-title">素材图片池</h2><p class="muted mt-1">商品共享素材。上传和 AI 处理结果会保存到素材库；{{ showDraftControls ? '草稿仅保存图片引用，删除共享素材请前往商品库。AI 处理前会先保存当前图片设置，生成结果自动保存。' : '删除素材会影响引用它的商品和草稿。' }}</p></div>
      <div class="flex flex-wrap gap-2">
        <input ref="input" type="file" accept="image/*" multiple class="hidden" @change="selectedFiles" />
        <button type="button" class="btn btn-outline" :disabled="loading" @click="input?.click()">上传图片</button>
        <button type="button" class="btn btn-secondary" :disabled="loading || !selectedIds.length" @click="openEdit">AI 图生图（{{ selectedIds.length }}）</button>
        <button v-if="showTranslateAction !== false" type="button" class="btn btn-primary" :disabled="loading || !selectedIds.length" @click="emit('translate', [...selectedIds])">AI 翻译/重绘（{{ selectedIds.length }}）</button>
        <button type="button" class="btn btn-outline" :disabled="loading || !visibleImages.length" @click="selectVisible">选择筛选结果</button>
        <button v-if="selectedIds.length" type="button" class="btn btn-outline" :disabled="loading" @click="selectedImageIdSet = new Set()">取消选择</button>
      </div>
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <label class="text-sm">搜索素材<input v-model="query" class="input mt-1" placeholder="搜索款式、颜色、用途" /></label>
        <label class="text-sm">图片用途<select v-model="filter" class="input mt-1"><option value="all">全部素材</option><option v-if="showDraftControls" value="published">已加入公共图集</option><option v-if="showDraftControls" value="unpublished">未加入公共图集</option><option value="linked">已关联 SKU</option><option value="unlinked">未关联 SKU</option><option value="selected">已选择处理</option></select></label>
        <label class="text-sm">素材来源<select v-model="origin" class="input mt-1"><option value="all">全部来源</option><option>采集图片</option><option>上传图片</option><option>AI 处理</option></select></label>
        <label class="text-sm">图片语言<select v-model="language" class="input mt-1"><option value="all">全部语言</option><option v-for="item in languages" :key="item" :value="item">{{ item === 'original' ? '原图 / 未标注' : item }}</option></select></label>
      </div>
      <p class="muted" aria-live="polite">显示 {{ visibleImages.length }} / {{ images.length }} 张。已选择 {{ selectedIds.length }} 张，本次仅处理这些图片<span v-if="selectedIds.some(id => !visibleImages.some(image => image.id === id))">（包含筛选外的已选图片）</span></p>
      <details v-if="!showDraftControls" class="rounded-lg border border-slate-200 p-3 dark:border-dark-700"><summary class="cursor-pointer text-sm">管理商品共享素材</summary><div class="mt-3 flex flex-wrap gap-2"><button type="button" class="btn btn-outline" :disabled="loading || !images.length" @click="emit('save')">保存商品素材</button><button type="button" class="btn btn-outline" :disabled="loading || !selectedIds.length" @click="deleteRequest = { ids: [...selectedIds], clear: false }">从素材库删除选中</button><button type="button" class="btn btn-outline" :disabled="loading || !images.length" @click="deleteRequest = { ids: images.map(image => image.id), clear: true }">清空商品素材库</button></div></details>
    </div>
    <div class="mt-5 grid grid-cols-[repeat(auto-fill,minmax(min(100%,13rem),1fr))] gap-4">
      <article v-for="image in visibleImages" :key="image.id" :data-image-id="image.id" class="min-w-0 overflow-hidden rounded-xl border bg-white dark:bg-dark-900" :class="selectedImageIdSet.has(image.id) ? 'border-primary-500 ring-2 ring-primary-300' : 'border-slate-200 dark:border-dark-700'">
        <button type="button" class="block w-full bg-slate-100 focus-visible:ring-4 focus-visible:ring-inset focus-visible:ring-primary-500 dark:bg-dark-800" :aria-label="`预览${label(image)}`" @click="previewImage = image"><img :src="imageUrl(image)" :alt="label(image)" class="aspect-square w-full object-contain" /></button>
        <div class="space-y-3 p-3">
          <div class="flex flex-wrap items-center justify-between gap-1"><span class="text-sm font-semibold" :title="image.id">{{ label(image) }}</span><span class="text-xs text-slate-500">{{ image.width }}×{{ image.height }}</span></div>
          <p class="text-xs text-slate-500">{{ imageUsageLabel(image) }}<span v-if="image.targetLanguage"> · {{ image.targetLanguage }}</span></p>
          <p v-if="assignmentsByImage.get(image.id)?.length" class="line-clamp-2 text-xs text-primary-700 dark:text-primary-200" :title="assignmentsByImage.get(image.id)?.map(sku => sku.name).join('、')">SKU 主图：{{ assignmentsByImage.get(image.id)?.map(sku => sku.name).join('、') }}</p>
          <button v-if="skuAssignments?.length" type="button" class="btn btn-outline w-full !px-2 !py-2 text-xs" :disabled="loading" @click="assignmentImage = image">设置 SKU 主图（{{ assignmentsByImage.get(image.id)?.length || 0 }}）</button>
          <label class="flex items-center gap-2 text-sm"><input type="checkbox" :disabled="loading" :aria-label="`选择处理图片 ${image.id}`" :checked="selectedImageIdSet.has(image.id)" @change="selectImage(image, ($event.target as HTMLInputElement).checked)" />选择处理</label>
          <button v-if="showDraftControls" type="button" class="btn btn-outline w-full !px-2 !py-2 text-xs" :disabled="loading" @click="emit('toggleDraftImage', image, !draftAssetIdSet.has(image.id))">{{ draftAssetIdSet.has(image.id) ? '从当前草稿移除' : '加入公共图集' }}</button>
          <button v-else type="button" class="btn btn-outline w-full !py-2 text-xs" :disabled="loading || image.isMain" @click="emit('setMain', image.id)">{{ image.isMain ? '商品默认主图' : '设为商品主图' }}</button>
        </div>
      </article>
    </div>
    <p v-if="!images.length" class="py-8 text-center text-sm text-slate-500">暂无图片，请上传或采集商品图片。</p>
    <p v-else-if="!visibleImages.length" class="py-8 text-center text-sm text-slate-500">没有符合条件的素材，请调整筛选条件。</p>
  </section>
  <WorkspaceDialog :open="Boolean(previewImage)" title="图片预览" @close="previewImage = null"><img v-if="previewImage" :src="imageUrl(previewImage)" :alt="label(previewImage)" class="mx-auto max-h-[70dvh] max-w-full object-contain" /></WorkspaceDialog>
  <WorkspaceDialog :open="imageEditPromptOpen" title="图生图" @close="imageEditPromptOpen = false"><form class="space-y-4" @submit.prevent="submitEdit"><p>已选 {{ selectedIds.length }} 张图片</p><label class="block text-sm">处理要求<textarea v-model="imageEditPrompt" rows="5" class="input mt-2" placeholder="输入本次图片处理要求" /></label><div class="flex justify-end gap-3"><button type="button" class="btn btn-outline" @click="imageEditPromptOpen = false">取消</button><button type="submit" class="btn btn-primary" :disabled="loading || !imageEditPrompt.trim() || !selectedIds.length">生成图片</button></div></form></WorkspaceDialog>
  <SkuImageAssignmentDialog :image="assignmentImage" :images="images" :assignments="skuAssignments || []" :draft-scope="Boolean(showDraftControls)" :loading="loading" @close="assignmentImage = null" @apply="emit('assignSkus', $event)" />
  <WorkspaceDialog :open="Boolean(deleteRequest)" title="删除商品共享素材" @close="deleteRequest = null"><div class="space-y-4"><p>将从商品素材库删除 {{ deleteRequest?.ids.length }} 张图片。引用这些素材的其他草稿也会受影响；这不是从当前草稿移除图片。</p><p v-if="deletionUses.length">当前关联的 SKU：{{ deletionUses.map(sku => sku.name).join('、') }}。删除后需重新选图。</p><p v-else>当前商品未关联 SKU 主图；其他草稿仍可能引用这些素材。</p><p>确认已不再需要这些共享素材后再删除。</p><div class="flex justify-end gap-3"><button type="button" class="btn btn-outline" @click="deleteRequest = null">取消</button><button type="button" class="btn btn-primary" :disabled="loading" @click="confirmDelete">确认删除共享素材</button></div></div></WorkspaceDialog>
</template>

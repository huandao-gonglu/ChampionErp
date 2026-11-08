<script setup lang="ts">
import { vDesktop } from '@/utils/desktopHandoffDirective'
import { modelOperationReason } from '@/utils/desktop'
import { computed, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import DraftSkuImagePreview from './DraftSkuImagePreview.vue'
import DraftImageSetDialog from './DraftImageSetDialog.vue'
import { imageUrl, skuImageAssignments } from '@/utils/draftImages'
import type { DraftDetail, DraftProductContext, ImageAsset } from '@/types/workflow'
import type { DraftImageWorkbenchController } from '@/composables/useDraftImageWorkbench'

const props = defineProps<{ draft: DraftDetail; productContext: DraftProductContext; loading: boolean; controller: DraftImageWorkbenchController }>()
const selected = ref<string[]>([])
const preview = ref<ImageAsset | null>(null)
const previewOpen = ref(false)
const imageSetOpen = ref(false)
const imageReason = computed(() => modelOperationReason('image.translate'))
const planReason = computed(() => modelOperationReason('image.plan'))
const dialog = ref<'edit' | 'translate' | 'remove' | 'add' | null>(null)
const targets = ref<string[]>([])
const prompt = ref('')
const additions = ref<string[]>([])
const input = ref<HTMLInputElement | null>(null)
const dragging = ref('')
const busy = computed(() => props.loading || props.controller.busy.value)
const batch = computed(() => props.controller.batch.value)
const notice = computed(() => props.controller.notice.value)
const byId = computed(() => new Map(props.productContext.imagePool.map(image => [image.id, image])))
const assignments = computed(() => skuImageAssignments(props.productContext.skuItems, props.draft).filter(sku => props.draft.skuItems.some(row => row.sku_id === sku.skuId)))
const commonIds = computed(() => [...props.draft.images].sort((a, b) => a.order - b.order).map(ref => ref.assetId))
const skuIds = computed(() => [...new Set(assignments.value.map(sku => sku.imageAssetId).filter(Boolean))])
const hasSkuTargets = computed(() => assignments.value.some(sku => sku.selected && byId.value.has(sku.imageAssetId)))
const currentIds = computed(() => [...new Set([...commonIds.value, ...skuIds.value])])
const skuNamesByImage = computed(() => {
  const names = new Map<string, string[]>()
  for (const sku of assignments.value) {
    if (!sku.imageAssetId) continue
    const values = names.get(sku.imageAssetId) || []
    values.push(sku.name)
    names.set(sku.imageAssetId, values)
  }
  return new Map([...names].map(([id, values]) => [id, values.join('、')]))
})
const canProcess = computed(() => selected.value.length > 0 && selected.value.every(id => byId.value.has(id)))
const selectedCommon = computed(() => selected.value.filter(id => commonIds.value.includes(id)))
const available = computed(() => props.productContext.imagePool.filter(image => !commonIds.value.includes(image.id)))
const regions = computed(() => [
  { id: 'common', title: '共用主图', ids: commonIds.value },
  { id: 'sku', title: 'SKU 图片', ids: skuIds.value },
])
const failedIds = computed(() => batch.value?.image_set ? [] : batch.value?.items.filter(item => item.status === 'failed' && !item.outcome_unknown && currentIds.value.includes(item.source_id)).map(item => item.source_id) || [])
function name(id: string) {
  const ref = props.draft.images.find(image => image.assetId === id)
  return ref?.label || assignments.value.find(sku => sku.imageAssetId === id)?.name || '共用图片'
}
function status(id: string) {
  if (batch.value?.image_set) return ''
  const item = batch.value?.items.find(item => item.source_id === id)
  if (item?.outcome_unknown) return '结果待确认'
  if (item?.status === 'failed') return item.error || '处理失败'
  if (item?.status === 'queued') return '等待处理'
  if (item?.status === 'running') return '处理中'
  if (batch.value?.items.some(item => item.status === 'success' && item.result_urls.includes(imageUrl(byId.value.get(id)!)))) return '已更新'
  return ''
}
function toggle(id: string, checked: boolean) {
  selected.value = checked ? [...new Set([...selected.value, id])] : selected.value.filter(value => value !== id)
}
function selectRegion(ids: string[]) {
  const valid = ids.filter(id => byId.value.has(id) || commonIds.value.includes(id))
  selected.value = valid.every(id => selected.value.includes(id)) ? selected.value.filter(id => !ids.includes(id)) : [...new Set([...selected.value, ...valid])]
}
function open(action: 'edit' | 'translate' | 'remove' | 'add') {
  if (busy.value) return
  targets.value = [...(action === 'remove' ? selectedCommon.value : selected.value)]
  additions.value = []; prompt.value = ''; dialog.value = action
}
function submit() {
  if (busy.value) return
  const action = dialog.value
  const ids = [...targets.value]
  if (action === 'edit' && !prompt.value.trim()) return
  dialog.value = null; selected.value = []
  if (action === 'remove') void props.controller.remove(ids)
  else if (action === 'add') void props.controller.add([...additions.value])
  else if (action) void props.controller.start(action, ids, prompt.value.trim())
}
function drop(id: string) {
  if (busy.value || !dragging.value || id === dragging.value) { dragging.value = ''; return }
  const ids = commonIds.value.filter(value => value !== dragging.value)
  ids.splice(commonIds.value.indexOf(id), 0, dragging.value)
  dragging.value = ''
  void props.controller.reorder(ids)
}
function move(id: string, step: number) {
  if (busy.value) return
  const ids = [...commonIds.value]
  const from = ids.indexOf(id), to = from + step
  if (to < 0 || to >= ids.length) return
  ids.splice(from, 1); ids.splice(to, 0, id)
  void props.controller.reorder(ids)
}
function files(event: Event) {
  const node = event.target as HTMLInputElement
  const uploads = Array.from(node.files || [])
  node.value = ''; dialog.value = null
  void props.controller.upload(uploads)
}
watch(currentIds, ids => { selected.value = selected.value.filter(id => ids.includes(id)) })
watch(() => props.draft.draftId, () => { selected.value = []; dialog.value = null; preview.value = null; imageSetOpen.value = false })
watch(busy, value => { if (value) dragging.value = '' })
</script>

<template>
  <section class="space-y-6" aria-label="草稿图片处理">
    <header class="flex items-center justify-between gap-3">
      <div class="min-w-0"><h2 class="card-title">图片处理</h2><p class="muted mt-1 truncate">{{ productContext.sourceTitle || productContext.title }}</p></div>
      <button class="btn btn-outline shrink-0" type="button" @click="previewOpen = true">发布预览</button>
    </header>
    <div class="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3 shadow-sm dark:border-dark-700 dark:bg-dark-900" aria-label="图片处理工具栏">
      <span class="mr-3 min-w-32 text-sm font-medium">{{ selected.length ? `已选 ${selected.length} 张` : '勾选图片开始处理' }}</span>
      <button v-desktop="'image.translate'" class="btn btn-primary" type="button" :disabled="!!imageReason || busy || !canProcess" @click="open('translate')">翻译图片</button>
      <button v-desktop="'image.translate'" class="btn btn-outline" type="button" :disabled="!!imageReason || busy || !canProcess" @click="open('edit')">AI 修改</button>
      <button class="btn btn-outline" type="button" :disabled="(!currentIds.length || (!!planReason && !hasSkuTargets)) && !batch?.image_set" @click="imageSetOpen = true">{{ batch?.image_set ? '查看套图' : '生成套图' }}</button>
      <button class="btn btn-outline" type="button" :disabled="busy" @click="open('add')">添加图片</button>
      <button class="btn btn-outline" type="button" :disabled="!selected.length" @click="selected = []">取消选择</button>
      <button v-if="selectedCommon.length" class="btn btn-outline" type="button" :disabled="busy" @click="open('remove')">移出共用区（{{ selectedCommon.length }}）</button>
      <div class="ml-auto flex gap-2">
        <button class="btn btn-outline" type="button" :disabled="busy || !controller.undoLabel.value" :title="controller.undoLabel.value ? `撤销：${controller.undoLabel.value}` : '本次打开尚无可撤销操作；关闭或刷新后清空历史'" @click="controller.undo()">↶ 撤销<span v-if="controller.undoLabel.value" class="sr-only">：{{ controller.undoLabel.value }}</span></button>
        <button class="btn btn-outline" type="button" :disabled="busy || !controller.redoLabel.value" :title="controller.redoLabel.value ? `重做：${controller.redoLabel.value}` : '没有可重做的操作'" @click="controller.redo()">↷ 重做<span v-if="controller.redoLabel.value" class="sr-only">：{{ controller.redoLabel.value }}</span></button>
      </div>
    </div>
    <div v-if="notice || batch" role="status" class="flex flex-wrap items-center gap-3 rounded-lg bg-slate-100 px-4 py-3 text-sm dark:bg-dark-800">
      <span v-if="notice">{{ notice }}</span>
      <span v-else-if="batch?.image_set">{{ batch.terminal ? '套图生成结束' : '正在生成套图' }} · {{ batch.completed }} / {{ batch.total }} 张 · 成功 {{ batch.success }} 张，可查看套图并选择采用。</span>
      <span v-else-if="batch && !batch.terminal">正在处理 {{ batch.total }} 张图片 · {{ batch.completed }} / {{ batch.total }} 完成</span>
      <span v-else-if="batch">上次处理：成功 {{ batch.success }} 张<span v-if="batch.failed"> · {{ batch.failed }} 张未完成，保留旧图</span></span>
      <button v-if="failedIds.length" type="button" class="ml-auto text-primary-700 underline dark:text-primary-300" @click="selected = [...failedIds]">选中失败项</button>
    </div>
    <p v-if="imageReason" role="status" class="muted">{{ imageReason }}</p>
    <p v-if="planReason && planReason !== imageReason" role="status" class="muted">{{ planReason }}</p>
    <section v-for="region in regions" :key="region.id" :data-region="region.id" class="border-b border-slate-200 pb-6 last:border-0 dark:border-dark-700">
      <header class="mb-4 flex flex-wrap items-center gap-3">
        <h3 class="text-base font-semibold">{{ region.title }}</h3>
        <p class="muted">{{ region.ids.length }} 张<span v-if="region.id === 'common'"> · 每个 SKU 都携带</span></p>
        <span v-if="region.id === 'common'" class="muted ml-auto hidden sm:inline">拖动手柄调整顺序</span>
        <button type="button" :class="region.id === 'common' ? 'ml-auto sm:ml-4' : 'ml-auto'" class="text-sm text-primary-700 dark:text-primary-300" :disabled="!region.ids.length" @click="selectRegion(region.ids)">{{ region.ids.length && region.ids.every(id => selected.includes(id)) ? '取消本区选择' : '全选本区' }}</button>
      </header>
      <div class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,9rem),11.5rem))] gap-4">
        <article v-for="id in region.ids" :key="id" :data-image-id="id" class="min-w-0 rounded-xl border bg-white p-2 dark:bg-dark-900" :class="selected.includes(id) ? 'border-primary-500 ring-2 ring-primary-200 dark:ring-primary-700' : 'border-slate-200 dark:border-dark-700'" @dragover.prevent @drop.prevent="region.id === 'common' && drop(id)">
          <div class="relative">
            <button type="button" class="block aspect-square w-full overflow-hidden rounded-lg bg-slate-50 focus-visible:ring-2 focus-visible:ring-primary-500 dark:bg-dark-800" :disabled="!byId.has(id)" :aria-label="`放大图片 ${name(id)}`" @click="preview = byId.get(id) || null">
              <img v-if="byId.has(id)" :src="imageUrl(byId.get(id)!)" :alt="name(id)" :draggable="false" class="h-full w-full object-contain" />
              <span v-else class="text-sm text-rose-600">图片缺失</span>
            </button>
            <input type="checkbox" class="absolute left-2 top-2 h-5 w-5 rounded border-slate-300 accent-primary-600" :aria-label="`选择处理图片 ${id}`" :disabled="region.id === 'sku' && !byId.has(id)" :checked="selected.includes(id)" @change="toggle(id, ($event.target as HTMLInputElement).checked)" />
            <span v-if="byId.has(id) && status(id)" class="absolute inset-x-0 bottom-0 truncate rounded-b-lg bg-slate-950/80 px-2 py-1.5 text-xs text-white" :title="status(id)">{{ status(id) }}</span>
          </div>
          <div class="mt-2 flex h-6 items-center justify-between gap-2">
            <span class="truncate text-xs text-slate-500 dark:text-slate-400" :title="name(id)">{{ name(id) }}</span>
            <button v-if="region.id === 'common'" type="button" class="cursor-grab px-1 text-slate-400 focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-default" :draggable="!busy" :disabled="busy" :aria-label="`排序共用图片 ${name(id)}`" title="拖动排序，也可用左右方向键调整" @dragstart="dragging = id; $event.dataTransfer?.setData('text/plain', id)" @dragend="dragging = ''" @keydown.left.prevent="move(id, -1)" @keydown.right.prevent="move(id, 1)">⠿</button>
          </div>
          <p v-if="skuNamesByImage.has(id)" class="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400" :title="`关联 SKU：${skuNamesByImage.get(id)}`">关联 SKU：{{ skuNamesByImage.get(id) }}</p>
          <p v-if="region.id === 'sku' && commonIds.includes(id)" class="mt-1 text-xs text-slate-500 dark:text-slate-400">同时用于共用区 · 同一张图片</p>
        </article>
      </div>
      <p v-if="!region.ids.length" class="muted py-6">{{ region.id === 'common' ? '暂无共用图片，可从上方添加。' : '暂无独立的 SKU 图片。' }}</p>
    </section>
    <footer class="muted flex justify-between gap-4"><span>共 {{ currentIds.length }} 张图片（已去重）</span><span>点击图片可放大查看</span></footer>
    <input ref="input" type="file" accept="image/*" multiple class="hidden" @change="files" />
  </section>
  <WorkspaceDialog :open="Boolean(preview)" title="图片预览" @close="preview = null"><img v-if="preview" :src="imageUrl(preview)" alt="图片完整预览" class="mx-auto max-h-[70dvh] max-w-full object-contain" /></WorkspaceDialog>
  <WorkspaceDialog :open="previewOpen" title="发布图片预览" @close="previewOpen = false"><DraftSkuImagePreview :draft="draft" :images="productContext.imagePool" :assignments="assignments" /></WorkspaceDialog>
  <DraftImageSetDialog :open="imageSetOpen" :draft="draft" :images="productContext.imagePool" :reference-ids="commonIds.length ? commonIds : currentIds" :controller="controller" :loading="loading" :skus="assignments" @close="imageSetOpen = false" />
  <WorkspaceDialog :open="Boolean(dialog)" :title="dialog === 'edit' ? 'AI 修改' : dialog === 'translate' ? '翻译图片' : dialog === 'remove' ? `移出 ${targets.length} 张共用主图？` : '添加共用图片'" :close-disabled="busy" @close="dialog = null">
    <form class="space-y-5" @submit.prevent="submit">
      <template v-if="dialog === 'add'">
        <button class="btn btn-outline" type="button" :disabled="busy" @click="input?.click()">上传新图片</button>
        <p class="muted">或从商品素材中选择，添加到本草稿共用区。</p>
        <div class="grid max-h-80 grid-cols-3 gap-3 overflow-y-auto sm:grid-cols-5">
          <label v-for="image in available" :key="image.id" class="relative cursor-pointer rounded-lg border p-1 dark:border-dark-700"><img :src="imageUrl(image)" alt="可添加图片" class="aspect-square w-full object-contain" /><input v-model="additions" type="checkbox" :value="image.id" :aria-label="`添加共用图片 ${image.id}`" class="absolute left-2 top-2 h-5 w-5" /></label>
        </div>
      </template>
      <template v-else>
        <p v-if="dialog !== 'remove'" class="muted">已选 {{ targets.length }} 张 · 使用当前显示的图片</p>
        <div class="flex max-h-48 flex-wrap gap-3 overflow-y-auto"><img v-for="id in targets" :key="id" :src="byId.has(id) ? imageUrl(byId.get(id)!) : ''" :alt="name(id)" class="h-20 w-20 rounded-lg object-contain" /></div>
        <template v-if="dialog === 'remove'"><p>将从本草稿全部 SKU 的共用图中移出。</p><p class="muted">图片文件和独立的 SKU 图片关联保留，可撤销。</p></template>
        <template v-else><p v-if="imageReason" role="status" class="muted">{{ imageReason }}</p><label v-if="dialog === 'edit'" class="block text-sm font-medium">修改要求<textarea v-model="prompt" class="input mt-2" rows="5" placeholder="例如：修正标题文字，保留每张图各自的商品颜色和版式。" /></label><p v-else>翻译为 {{ draft.language }}。</p><p class="muted">处理成功后自动使用新图，可撤销。失败的图片继续使用当前图。</p></template>
      </template>
      <div class="flex justify-end gap-3"><button type="button" class="btn btn-outline" :disabled="busy" @click="dialog = null">取消</button><button v-desktop="'image.translate'" type="submit" class="btn btn-primary" :disabled="busy || ((dialog === 'edit' || dialog === 'translate') && !!imageReason) || (dialog === 'edit' && !prompt.trim()) || (dialog === 'add' && !additions.length)">{{ dialog === 'remove' ? '移出共用区' : dialog === 'add' ? `添加 ${additions.length} 张` : `开始处理 ${targets.length} 张` }}</button></div>
    </form>
  </WorkspaceDialog>
</template>

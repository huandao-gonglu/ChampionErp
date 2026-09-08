<script setup lang="ts">
import { computed, ref } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { imageRoles, imageSourceLabel, imageUrl } from '@/utils/draftImages'
import type { DraftDetail, DraftImageRole, ImageAsset } from '@/types/workflow'

const props = defineProps<{ draft: DraftDetail; images: ImageAsset[]; loading: boolean }>()
const preview = ref<ImageAsset | null>(null)
const ordered = computed(() => [...props.draft.images].sort((a, b) => a.order - b.order))
const imageById = computed(() => new Map(props.images.map(image => [image.id, image])))
const draftRows = computed(() => ordered.value.map(ref => ({ ref, image: imageById.value.get(ref.assetId) })))
// 发布接口按顺序取第一张作为主图，编辑时同步角色与顺序。
function applyOrder(refs = ordered.value) {
  props.draft.images = refs.map((item, order) => ({ ...item, order, role: order === 0 ? 'main' : item.role === 'main' ? 'detail' : item.role }))
}
function setMain(assetId: string) {
  if (props.loading) return
  applyOrder([...ordered.value.filter(item => item.assetId === assetId), ...ordered.value.filter(item => item.assetId !== assetId)])
}
function setRole(assetId: string, event: Event) {
  if (props.loading) return
  const role = (event.target as HTMLSelectElement).value as DraftImageRole
  const ref = props.draft.images.find(item => item.assetId === assetId)
  if (ref) ref.role = role
  applyOrder()
}
function remove(assetId: string) {
  if (!props.loading) applyOrder(ordered.value.filter(item => item.assetId !== assetId))
}
function move(assetId: string, direction: -1 | 1) {
  if (props.loading) return
  const refs = [...ordered.value]
  const index = refs.findIndex(item => item.assetId === assetId)
  const next = index + direction
  if (index < 0 || next < 0 || next >= refs.length) return
  ;[refs[index], refs[next]] = [refs[next], refs[index]]
  applyOrder(refs)
}
</script>

<template>
  <section class="card" aria-label="公共图集">
    <h2 class="card-title">公共图集 · {{ draft.images.length }} 张</h2>
    <p class="muted mt-1">供所有 SKU 共用。第一张为公共主图；设置了 SKU 主图时，公共图片会顺延并去重。移除仅影响当前草稿的公共图集。</p>
    <div class="mt-4 max-h-[34rem] space-y-3 overflow-y-auto pr-1">
      <article v-for="(row, index) in draftRows" :key="row.ref.assetId" :data-draft-image-id="row.ref.assetId" class="grid grid-cols-[5rem_minmax(0,1fr)] gap-3 rounded-lg border border-accent-200 p-3 dark:border-dark-700">
        <button v-if="row.image" type="button" class="self-start rounded focus-visible:ring-4 focus-visible:ring-primary-500" :aria-label="`预览公共图片 ${index + 1}`" @click="preview = row.image"><img :src="imageUrl(row.image)" :alt="`公共图片 ${index + 1}`" class="aspect-square w-full rounded object-contain" /></button>
        <div v-else class="text-sm text-rose-600">素材缺失，请移除后重新选图</div>
        <div class="min-w-0 space-y-2">
          <div class="flex flex-wrap gap-2 text-sm font-semibold"><span :title="row.ref.assetId">公共图片 {{ index + 1 }}</span><span v-if="index === 0" class="badge-success">公共主图</span></div>
          <p v-if="row.image" class="text-xs text-slate-500">{{ imageSourceLabel(row.image) }} · {{ row.image.width }}×{{ row.image.height }}</p>
          <div class="flex flex-wrap items-center gap-2">
            <select class="input max-w-28 py-1 text-sm" :aria-label="`公共图片 ${index + 1} 用途`" :disabled="loading || index === 0" :value="index === 0 ? 'main' : row.ref.role" @change="setRole(row.ref.assetId, $event)">
              <option v-for="role in imageRoles.filter(role => index === 0 || role.value !== 'main')" :key="role.value" :value="role.value">{{ role.label }}</option>
            </select>
            <button type="button" class="btn btn-outline px-2 py-1 text-xs" :disabled="loading || index === 0" @click="setMain(row.ref.assetId)">设为公共主图</button>
          </div>
          <div class="flex flex-wrap gap-2">
            <button type="button" class="btn btn-outline px-2 py-1 text-xs" :aria-label="`上移公共图片 ${index + 1}`" :disabled="loading || index === 0" @click="move(row.ref.assetId, -1)">上移</button>
            <button type="button" class="btn btn-outline px-2 py-1 text-xs" :aria-label="`下移公共图片 ${index + 1}`" :disabled="loading || index === draftRows.length - 1" @click="move(row.ref.assetId, 1)">下移</button>
            <button type="button" class="btn btn-outline px-2 py-1 text-xs" :disabled="loading" @click="remove(row.ref.assetId)">从当前草稿移除</button>
          </div>
        </div>
      </article>
      <p v-if="!draftRows.length" class="py-6 text-center text-sm text-slate-500">公共图集为空，请从下方素材池加入图片。</p>
    </div>
  </section>
  <WorkspaceDialog :open="Boolean(preview)" title="公共图片预览" @close="preview = null"><img v-if="preview" :src="imageUrl(preview)" alt="公共图片完整预览" class="mx-auto max-h-[70dvh] max-w-full object-contain" /></WorkspaceDialog>
</template>

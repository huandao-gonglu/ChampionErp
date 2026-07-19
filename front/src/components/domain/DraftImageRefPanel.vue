<script setup lang="ts">
import { computed } from 'vue'
import type { DraftDetail, DraftImageRef, DraftImageRole, ImageAsset } from '@/types/workflow'

const props = defineProps<{
  draft: DraftDetail
  images: ImageAsset[]
  loading: boolean
}>()

const emit = defineEmits<{
  save: []
}>()

const imageRoleOptions: Array<{ value: DraftImageRole; label: string }> = [
  { value: 'main', label: '主图' },
  { value: 'detail', label: '详情' },
  { value: 'size', label: '尺寸' },
  { value: 'scene', label: '场景' },
  { value: 'package', label: '包装' },
  { value: 'selling_point', label: '卖点' },
  { value: 'material', label: '材质' },
  { value: 'other', label: '其他' },
]

const orderedDraftImages = computed(() => [...props.draft.images].sort((left, right) => left.order - right.order))
const imageById = computed(() => new Map(props.images.map((image) => [image.id, image])))
const draftRows = computed(() => orderedDraftImages.value.map((ref) => ({
  ref,
  image: imageById.value.get(ref.assetId),
})))

function draftImageFor(assetId: string): DraftImageRef | undefined {
  return props.draft.images.find((item) => item.assetId === assetId)
}

function normalizeDraftImageOrders() {
  props.draft.images = orderedDraftImages.value.map((item, index) => ({ ...item, order: index }))
  let mainSeen = false
  props.draft.images.forEach((item) => {
    if (item.role !== 'main') return
    if (mainSeen) {
      item.role = 'detail'
    } else {
      mainSeen = true
    }
  })
  if (props.draft.images.length && !mainSeen) {
    props.draft.images[0].role = 'main'
  }
}

function setDraftImageRole(assetId: string, role: DraftImageRole) {
  const ref = draftImageFor(assetId)
  if (!ref) return
  if (role === 'main') {
    props.draft.images = props.draft.images.map((item) => ({ ...item, role: item.assetId === assetId ? 'main' : item.role === 'main' ? 'detail' : item.role }))
  } else {
    ref.role = role
  }
  normalizeDraftImageOrders()
}

function setMainDraftImage(assetId: string) {
  setDraftImageRole(assetId, 'main')
}

function imagePreview(image: ImageAsset) {
  return image.previewUrl || image.url || image.path
}

function removeDraftImage(assetId: string) {
  props.draft.images = props.draft.images.filter((item) => item.assetId !== assetId)
  normalizeDraftImageOrders()
}

function moveDraftImage(assetId: string, direction: -1 | 1) {
  const current = orderedDraftImages.value.map((item, index) => ({ ...item, order: index }))
  const index = current.findIndex((item) => item.assetId === assetId)
  const nextIndex = index + direction
  if (index < 0 || nextIndex < 0 || nextIndex >= current.length) return
  const next = [...current]
  const draftImage = next[index]
  next[index] = next[nextIndex]
  next[nextIndex] = draftImage
  props.draft.images = next.map((item, order) => ({ ...item, order }))
  normalizeDraftImageOrders()
}

function setDraftImageRoleFromEvent(assetId: string, event: Event) {
  setDraftImageRole(assetId, String((event.target as HTMLSelectElement | null)?.value || '') as DraftImageRole)
}

function imageLabel(assetId: string, index: number) {
  if (!assetId) return `图片 #${index + 1}`
  if (assetId.length <= 28) return assetId
  return `${assetId.slice(0, 16)}...${assetId.slice(-6)}`
}
</script>

<template>
  <section class="card h-full">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">草稿图片</h2>
        <p class="muted mt-1">{{ props.draft.images.length }} 张发布图片</p>
      </div>
      <button class="btn btn-primary" :disabled="props.loading || !props.draft.draftId" @click="emit('save')">保存发布图片</button>
    </div>

    <div class="mt-5 space-y-3">
      <article
        v-for="(row, index) in draftRows"
        :key="row.ref.assetId"
        class="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-3 rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900"
      >
        <img
          v-if="row.image"
          :src="imagePreview(row.image)"
          :alt="row.ref.assetId"
          class="aspect-square rounded object-cover"
        />
        <div v-else class="flex aspect-square items-center justify-center rounded bg-accent-100 text-xs font-semibold text-accent-500 dark:bg-dark-800 dark:text-accent-300">
          缺图
        </div>
        <div class="min-w-0 space-y-3">
          <div class="min-w-0">
            <div class="mb-1 flex flex-wrap items-center gap-2">
              <span class="badge-muted">#{{ index + 1 }}</span>
              <span v-if="row.ref.role === 'main'" class="badge-success">主图</span>
            </div>
            <p class="truncate text-sm font-semibold text-slate-800 dark:text-accent-100" :title="row.ref.assetId">
              {{ imageLabel(row.ref.assetId, index) }}
            </p>
            <p class="mt-1 text-xs text-accent-500 dark:text-accent-300">
              <template v-if="row.image">{{ row.image.width }}×{{ row.image.height }} · {{ row.image.origin }}</template>
              <template v-else>素材库中未找到该图片</template>
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <select
              class="input max-w-32 py-1.5 text-sm"
              :disabled="props.loading"
              :value="row.ref.role"
              @change="setDraftImageRoleFromEvent(row.ref.assetId, $event)"
            >
              <option v-for="option in imageRoleOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <button class="btn btn-outline px-3 py-1.5 text-xs" :disabled="props.loading || row.ref.role === 'main'" @click="setMainDraftImage(row.ref.assetId)">设主图</button>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button class="btn btn-outline px-3 py-1.5 text-xs" :disabled="props.loading || index === 0" title="上移" @click="moveDraftImage(row.ref.assetId, -1)">上移</button>
            <button class="btn btn-outline px-3 py-1.5 text-xs" :disabled="props.loading || index === draftRows.length - 1" title="下移" @click="moveDraftImage(row.ref.assetId, 1)">下移</button>
            <button class="btn btn-outline px-3 py-1.5 text-xs" :disabled="props.loading" @click="removeDraftImage(row.ref.assetId)">移除</button>
          </div>
        </div>
      </article>
      <div v-if="!draftRows.length" class="rounded-lg border border-dashed border-accent-200 p-6 text-center text-sm text-accent-500 dark:border-dark-700 dark:text-accent-300">
        暂未选择发布图片
      </div>
    </div>
  </section>
</template>

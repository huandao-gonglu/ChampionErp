<script setup lang="ts">
import { computed, watch } from 'vue'
import DraftImageRefPanel from '@/components/domain/DraftImageRefPanel.vue'
import ImagePoolPanel from '@/components/domain/ImagePoolPanel.vue'
import type { DraftDetail, ImageAsset, Product } from '@/types/workflow'

const props = defineProps<{
  title?: string
  product: Product
  images: ImageAsset[]
  loading: boolean
  error?: string
  showTranslateAction?: boolean
  draft?: DraftDetail
}>()

const emit = defineEmits<{
  translate: []
  imageEdit: [prompt: string]
  upload: [files: File[]]
  save: []
  setMain: [imageId: string]
  delete: [imageIds: string[]]
  clear: []
  saveDraftImages: []
}>()

const draftAssetIds = computed(() => props.draft?.images.map((image) => image.assetId) ?? [])
const imageIdsSignature = computed(() => props.images.map((image) => image.id).join('\n'))

function orderedDraftImages(draft: DraftDetail) {
  return [...draft.images].sort((left, right) => left.order - right.order)
}

function normalizeDraftImageOrders(draft: DraftDetail) {
  draft.images = orderedDraftImages(draft).map((item, index) => ({ ...item, order: index }))
  let mainSeen = false
  draft.images.forEach((item) => {
    if (item.role !== 'main') return
    if (mainSeen) {
      item.role = 'detail'
    } else {
      mainSeen = true
    }
  })
  if (draft.images.length && !mainSeen) {
    draft.images[0].role = 'main'
  }
}

function toggleDraftImage(image: ImageAsset, checked: boolean) {
  const draft = props.draft
  if (!draft) return
  image.selected = checked
  const exists = draft.images.some((item) => item.assetId === image.id)
  if (checked && !exists) {
    draft.images.push({
      assetId: image.id,
      role: draft.images.length ? 'detail' : 'main',
      order: draft.images.length,
    })
  }
  if (!checked) {
    draft.images = draft.images.filter((item) => item.assetId !== image.id)
  }
  normalizeDraftImageOrders(draft)
}

function syncDraftImageSelection() {
  if (!props.draft) return
  const selectedIds = new Set(draftAssetIds.value)
  props.images.forEach((image) => {
    image.selected = selectedIds.has(image.id)
  })
}

watch([draftAssetIds, imageIdsSignature], syncDraftImageSelection, { immediate: true })
</script>

<template>
  <div class="space-y-5">
    <section class="card">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 class="card-title">{{ props.draft ? '当前草稿商品' : props.title || '商品库图片编辑' }}</h2>
          <p class="muted mt-1 truncate" :title="props.product.source.title || props.product.name || props.product.productId">
            {{ props.product.source.title || props.product.name || props.product.productId || '当前商品' }}
          </p>
        </div>
        <div v-if="props.draft" class="flex flex-wrap gap-2 text-xs font-semibold">
          <span class="rounded-full bg-primary-50 px-3 py-1.5 text-primary-700 ring-1 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30">
            发布图片 {{ props.draft.images.length }} 张
          </span>
          <span class="rounded-full bg-accent-100 px-3 py-1.5 text-accent-600 ring-1 ring-accent-200 dark:bg-dark-800 dark:text-accent-300 dark:ring-dark-600">
            素材 {{ props.images.length }} 张
          </span>
        </div>
      </div>

      <div v-if="props.error" class="mt-3 rounded-lg bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
        {{ props.error }}
      </div>
    </section>

    <div v-if="props.draft" class="grid items-start gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.75fr)]">
      <ImagePoolPanel
        :images="props.images"
        :loading="props.loading"
        :show-translate-action="props.showTranslateAction === true"
        :show-draft-controls="true"
        :draft-asset-ids="draftAssetIds"
        @translate="emit('translate')"
        @image-edit="emit('imageEdit', $event)"
        @upload="emit('upload', $event)"
        @clear="emit('clear')"
        @save="emit('save')"
        @set-main="emit('setMain', $event)"
        @delete="emit('delete', $event)"
        @toggle-draft-image="toggleDraftImage"
      />

      <DraftImageRefPanel
        class="xl:sticky xl:top-4"
        :draft="props.draft"
        :images="props.images"
        :loading="props.loading"
        @save="emit('saveDraftImages')"
      />
    </div>

    <ImagePoolPanel
      v-else
      :images="props.images"
      :loading="props.loading"
      :show-translate-action="props.showTranslateAction === true"
      @translate="emit('translate')"
      @image-edit="emit('imageEdit', $event)"
      @upload="emit('upload', $event)"
      @clear="emit('clear')"
      @save="emit('save')"
      @set-main="emit('setMain', $event)"
      @delete="emit('delete', $event)"
    />
  </div>
</template>

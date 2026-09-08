<script setup lang="ts">
import { computed } from 'vue'
import DraftImageRefPanel from '@/components/domain/DraftImageRefPanel.vue'
import DraftSkuImagePreview from '@/components/domain/DraftSkuImagePreview.vue'
import { skuImageAssignments, type SkuImageBatchChange } from '@/utils/draftImages'
import ImagePoolPanel from '@/components/domain/ImagePoolPanel.vue'
import type { DraftDetail, ImageAsset, Product } from '@/types/workflow'

interface ImageEditRequest {
  prompt: string
  imageIds: string[]
}

const props = defineProps<{
  title?: string
  product: Product
  images: ImageAsset[]
  loading: boolean
  error?: string
  showTranslateAction?: boolean
  draft?: DraftDetail
  saveStatus?: string
}>()

const emit = defineEmits<{
  translate: [imageIds: string[]]
  imageEdit: [request: ImageEditRequest]
  upload: [files: File[]]
  save: []
  setMain: [imageId: string]
  delete: [imageIds: string[]]
  clear: []
  saveSkuImages: []
}>()

const draftAssetIds = computed(() => props.draft?.images.map((image) => image.assetId) ?? [])
const skuAssignments = computed(() => skuImageAssignments(props.product.skuItems, props.draft))

function assignSkus(changes: SkuImageBatchChange[]) {
  if (props.loading) return
  let changed = false
  for (const change of changes) {
    if (change.mode === 'assign' && !props.images.some(image => image.id === change.assetId)) continue
    for (const skuId of change.skuIds) {
      const sku = props.product.skuItems.find(sku => sku.id === skuId && sku.active)
      if (!sku) continue
      if (props.draft) {
        let row = props.draft.skuItems.find(row => row.sku_id === skuId)
        if (change.mode === 'inherit') {
          if (row) delete row.overrides.image_asset_id
        } else {
          if (!row) {
            row = { sku_id: skuId, selected: false, sku: '', stock: '', overrides: {}, attributes_by_target: {}, pricing: {}, publications: {} }
            props.draft.skuItems.push(row)
          }
          row.overrides.image_asset_id = change.mode === 'public' ? '' : change.assetId
        }
      } else {
        sku.image_asset_id = change.mode === 'assign' ? change.assetId : ''
      }
      changed = true
    }
  }
  // 草稿图片统一手动保存；商品批量关联在全部修改后仅保存一次。
  if (changed && !props.draft) emit('saveSkuImages')
}

function orderedDraftImages(draft: DraftDetail) {
  return [...draft.images].sort((left, right) => left.order - right.order)
}

function normalizeDraftImageOrders(draft: DraftDetail) {
  draft.images = orderedDraftImages(draft).map((item, order) => ({
    ...item, order, role: order === 0 ? 'main' : item.role === 'main' ? 'detail' : item.role,
  }))
}

function toggleDraftImage(image: ImageAsset, checked: boolean) {
  const draft = props.draft
  if (!draft || props.loading) return
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
            公共图集 {{ props.draft.images.length }} 张
          </span>
          <span class="rounded-full bg-accent-100 px-3 py-1.5 text-accent-600 ring-1 ring-accent-200 dark:bg-dark-800 dark:text-accent-300 dark:ring-dark-600">
            素材 {{ props.images.length }} 张
          </span>
        </div>
      </div>

      <div v-if="props.error" class="mt-3 rounded-lg bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
        {{ props.error }}
      </div>
      <p class="muted mt-3">{{ props.draft ? '公共图集供所有 SKU 共用；每个 SKU 的专属主图会排在公共图集前。图片选择、排序和 SKU 关联统一由顶部“保存图片设置”保存。' : '在素材下点击“设置 SKU 主图”批量关联，应用后保存商品默认图。' }}</p>
      <p v-if="props.draft" class="mt-2 text-sm font-semibold" role="status">{{ saveStatus }}</p>
    </section>

    <div v-if="props.draft" class="space-y-5">
      <div class="grid items-start gap-5 xl:grid-cols-2">
        <DraftImageRefPanel :draft="props.draft" :images="props.images" :loading="props.loading" />
        <DraftSkuImagePreview :draft="props.draft" :images="props.images" :assignments="skuAssignments" />
      </div>
      <ImagePoolPanel
        :images="props.images"
        :loading="props.loading"
        :show-translate-action="props.showTranslateAction === true"
        :show-draft-controls="true"
        :draft-asset-ids="draftAssetIds"
        :sku-assignments="skuAssignments"
        @assign-skus="assignSkus"
        @translate="emit('translate', $event)"
        @image-edit="emit('imageEdit', $event)"
        @upload="emit('upload', $event)"
        @toggle-draft-image="toggleDraftImage"
      />
    </div>

    <ImagePoolPanel
      v-else
      :images="props.images"
      :loading="props.loading"
      :show-translate-action="props.showTranslateAction === true"
      :sku-assignments="skuAssignments"
      @assign-skus="assignSkus"
      @translate="emit('translate', $event)"
      @image-edit="emit('imageEdit', $event)"
      @upload="emit('upload', $event)"
      @clear="emit('clear')"
      @save="emit('save')"
      @set-main="emit('setMain', $event)"
      @delete="emit('delete', $event)"
    />
  </div>
</template>

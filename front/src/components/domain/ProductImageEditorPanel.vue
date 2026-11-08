<script setup lang="ts">
import { computed } from 'vue'
import { skuImageAssignments, type SkuImageBatchChange } from '@/utils/draftImages'
import ImagePoolPanel from '@/components/domain/ImagePoolPanel.vue'
import type { ImageAsset, Product } from '@/types/workflow'

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

const skuAssignments = computed(() => skuImageAssignments(props.product.skuItems))

function assignSkus(changes: SkuImageBatchChange[]) {
  if (props.loading) return
  let changed = false
  for (const change of changes) {
    if (change.mode === 'assign' && !props.images.some(image => image.id === change.assetId)) continue
    for (const skuId of change.skuIds) {
      const sku = props.product.skuItems.find(sku => sku.id === skuId && sku.active)
      if (!sku) continue
      sku.image_asset_id = change.mode === 'assign' ? change.assetId : ''
      changed = true
    }
  }
  // 商品批量关联在全部修改后仅保存一次。
  if (changed) emit('saveSkuImages')
}

</script>

<template>
  <div class="space-y-5">
    <section class="card">
      <h2 class="card-title">{{ title || '采集箱图片编辑' }}</h2>
      <p class="muted mt-1">{{ product.source.title || product.name || product.productId }}</p>
      <p v-if="error" role="alert" class="mt-3 text-sm text-rose-600">{{ error }}</p>
    </section>
    <ImagePoolPanel
      :images="images"
      :loading="loading"
      :show-translate-action="showTranslateAction === true"
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

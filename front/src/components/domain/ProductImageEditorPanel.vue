<script setup lang="ts">
import type { DesktopImageAction } from '@/utils/desktopHandoff'
import { computed, ref } from 'vue'
import { PhWarningCircle } from '@phosphor-icons/vue'
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
  embedded?: boolean
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

const imagePool = ref<InstanceType<typeof ImagePoolPanel>>()
defineExpose({
  pendingImageAction: () => imagePool.value?.pendingImageAction(),
  restoreImageAction: (state: DesktopImageAction) => imagePool.value?.restoreImageAction(state),
})

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
  <div class="space-y-4">
    <div
      v-if="error && !embedded"
      role="alert"
      class="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
    >
      <PhWarningCircle :size="17" weight="fill" class="mt-0.5 shrink-0 text-rose-500 dark:text-rose-400" aria-hidden="true" />
      <span>{{ error }}</span>
    </div>
    <ImagePoolPanel
      ref="imagePool"
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

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { imageUrl, skuPublishImages, type SkuImageAssignment } from '@/utils/draftImages'
import type { DraftDetail, ImageAsset } from '@/types/workflow'
const props = defineProps<{ draft: DraftDetail; images: ImageAsset[]; assignments: SkuImageAssignment[] }>()
const selectedId = ref('')
const preview = ref<ImageAsset | null>(null)
const candidates = computed(() => props.assignments.filter(sku => sku.selected))
watch(candidates, skus => { if (!skus.some(sku => sku.skuId === selectedId.value)) selectedId.value = skus[0]?.skuId || '' }, { immediate: true })
const sku = computed(() => candidates.value.find(sku => sku.skuId === selectedId.value))
const imageMap = computed(() => new Map(props.images.map(image => [image.id, image])))
const missingSkuImage = computed(() => Boolean(sku.value?.imageAssetId && !imageMap.value.has(sku.value.imageAssetId)))
const rows = computed(() => sku.value ? skuPublishImages(props.draft, sku.value, props.images).map(ref => ({ ...ref, image: imageMap.value.get(ref.assetId) })) : [])
</script>
<template>
  <section class="card" aria-label="SKU 发布图片预览">
    <h2 class="card-title">SKU 发布图片预览</h2>
    <p class="muted mt-1">按当前编辑内容展示单个 SKU 的图片与顺序。保存后生效，图片格式、数量等平台要求仍以发布预检为准。</p>
    <label v-if="candidates.length" class="mt-4 block text-sm">查看 SKU<select v-model="selectedId" class="input mt-1"><option v-for="item in candidates" :key="item.skuId" :value="item.skuId">{{ item.name }}</option></select></label>
    <p v-else class="mt-4 text-sm text-slate-500">尚未选择发布的 SKU，请先在 SKU 页选择。</p>
    <template v-if="sku">
      <p v-if="missingSkuImage" role="alert" class="mt-3 text-sm text-rose-600">该 SKU 关联图片已不在素材库，无法组装发布图片，请重新设置 SKU 主图。</p>
      <template v-else>
        <p class="mt-3 text-sm">{{ !sku.imageAssetId ? '使用公共图集主图' : sku.inherited ? '主图沿用商品默认图' : '主图由当前草稿单独设置' }} · 共 {{ rows.length }} 张</p>
        <p v-if="!rows.length" role="alert" class="mt-2 text-sm text-rose-600">尚无明确的图片选择，请设置 SKU 主图或加入公共图集后再预检。</p>
        <div class="mt-3 grid max-h-[26rem] grid-cols-3 gap-3 overflow-auto">
          <div v-for="(row, index) in rows" :key="row.assetId" :data-preview-image-id="row.assetId">
            <button v-if="row.image" type="button" class="block w-full rounded-lg border focus-visible:ring-4 focus-visible:ring-primary-500 dark:border-dark-700" :aria-label="`预览 SKU 发布图片 ${index + 1}`" @click="preview = row.image"><img :src="imageUrl(row.image)" :alt="`发布图片 ${index + 1}`" class="aspect-square w-full object-contain" /></button>
            <p v-else class="text-sm text-rose-600">公共素材缺失，请重新选图</p>
            <p class="mt-1 text-xs">{{ index + 1 }} · {{ index === 0 ? '主图' : '附图' }}</p>
          </div>
        </div>
      </template>
    </template>
  </section>
  <WorkspaceDialog :open="Boolean(preview)" title="SKU 发布图片预览" @close="preview = null"><img v-if="preview" :src="imageUrl(preview)" alt="SKU 发布图片完整预览" class="mx-auto max-h-[70dvh] max-w-full object-contain" /></WorkspaceDialog>
</template>

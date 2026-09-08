<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { imageUrl, type SkuImageAssignment, type SkuImageBatchChange } from '@/utils/draftImages'
import type { ImageAsset } from '@/types/workflow'
const props = defineProps<{ image: ImageAsset | null; images: ImageAsset[]; assignments: SkuImageAssignment[]; draftScope: boolean; loading: boolean }>()
const emit = defineEmits<{ close: []; apply: [changes: SkuImageBatchChange[]] }>()
const query = ref('')
const selected = ref<string[]>([])
watch(() => props.image, image => { query.value = ''; selected.value = props.assignments.filter(sku => sku.imageAssetId === image?.id).map(sku => sku.skuId) }, { immediate: true })
const visible = computed(() => props.assignments.filter(sku => sku.name.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
function selectVisible(checked: boolean) {
  const ids = new Set(visible.value.map(sku => sku.skuId))
  selected.value = checked ? [...new Set([...selected.value, ...ids])] : selected.value.filter(id => !ids.has(id))
}
function apply() {
  if (!props.image || props.loading) return
  const removed = props.assignments.filter(sku => sku.imageAssetId === props.image!.id && !selected.value.includes(sku.skuId)).map(sku => sku.skuId)
  emit('apply', [{ assetId: '', skuIds: removed, mode: 'public' }, { assetId: props.image.id, skuIds: selected.value.filter(id => props.assignments.find(sku => sku.skuId === id)?.imageAssetId !== props.image!.id), mode: 'assign' }])
  emit('close')
}
function inherit() {
  if (props.loading || !props.draftScope) return
  emit('apply', [{ assetId: '', skuIds: [...selected.value], mode: 'inherit' }])
  emit('close')
}
function thumbnail(sku: SkuImageAssignment) { return props.images.find(image => image.id === sku.imageAssetId) }
</script>
<template>
  <WorkspaceDialog :open="Boolean(image)" title="批量设置 SKU 主图" @close="emit('close')">
    <div v-if="image" class="space-y-4">
      <div class="flex items-center gap-4"><img :src="imageUrl(image)" alt="将要使用的 SKU 主图" class="size-24 rounded-lg border object-contain" /><p class="text-sm">勾选的 SKU 将使用这张图片作为主图。取消已有勾选将改用公共图集。{{ draftScope ? '应用后需保存草稿，不会修改商品默认图。' : '应用后保存商品默认图。' }}</p></div>
      <label class="block text-sm">搜索 SKU<input v-model="query" class="input mt-1" placeholder="输入款式、颜色或尺码" /></label>
      <div class="flex flex-wrap items-center gap-3 text-sm"><span aria-live="polite">已选择 {{ selected.length }} / {{ assignments.length }} 个 SKU</span><button class="btn btn-outline" type="button" :disabled="loading" @click="selectVisible(true)">选中搜索结果</button><button class="btn btn-outline" type="button" :disabled="loading" @click="selectVisible(false)">取消搜索结果</button></div>
      <div class="max-h-80 space-y-2 overflow-auto rounded-lg border p-3 dark:border-dark-700">
        <label v-for="sku in visible" :key="sku.skuId" class="flex items-center gap-3 rounded-lg p-2 hover:bg-slate-100 dark:hover:bg-dark-800">
          <input v-model="selected" type="checkbox" :value="sku.skuId" :disabled="loading" :aria-label="`选择 SKU ${sku.name}`" />
          <img v-if="thumbnail(sku)" :src="imageUrl(thumbnail(sku)!)" alt="当前主图" class="size-12 shrink-0 object-contain" />
          <span><span class="block text-sm">{{ sku.name }}<span v-if="!sku.selected" class="ml-2 text-xs text-slate-500">未选择发布</span></span><span class="text-xs text-slate-500">{{ !sku.imageAssetId ? '使用公共图集' : sku.inherited ? '沿用商品默认图' : draftScope ? '草稿单独设置' : '商品默认图' }}</span></span>
        </label>
        <p v-if="!visible.length" class="py-4 text-sm text-slate-500">没有匹配的 SKU。</p>
      </div>
      <div class="flex flex-wrap justify-end gap-3"><button v-if="draftScope" class="btn btn-outline" type="button" :disabled="loading || !selected.length" @click="inherit">所选 SKU 恢复商品默认图</button><button class="btn btn-primary" type="button" :disabled="loading" @click="apply">应用 SKU 图片设置</button></div>
    </div>
  </WorkspaceDialog>
</template>

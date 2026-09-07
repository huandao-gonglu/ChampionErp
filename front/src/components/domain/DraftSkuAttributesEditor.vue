<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import CategoryAttributesPanel from './CategoryAttributesPanel.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'
import type { CategoryAttributeValue, ProductSku } from '@/types/workflow'

const fillMessage = ref('')
const props = defineProps<{ skuId: string; sku: ProductSku; targetKey: string }>()
const { targetEditors } = storeToRefs(useWorkflowStore())
const { currentDraftProductContext } = storeToRefs(useWorkflowCatalogStore())
const { platformOptions } = storeToRefs(useWorkflowPublishingStore())
const editor = computed(() => targetEditors.value.find(item => item.key === props.targetKey))
const row = computed(() => editor.value?.draft.skuItems.find(item => item.sku_id === props.skuId))
const customAttributes = computed(() => row.value?.custom_attributes_by_target?.[props.targetKey] || [])
function addCustomAttribute() {
  if (row.value) ((row.value.custom_attributes_by_target ||= {})[props.targetKey] ||= []).push({ name: '', value: '' })
}
const category = computed(() => {
  const definition = editor.value?.state.category
  return definition ? { ...definition,
    requiredAttributes: definition.requiredAttributes.filter(attr => attr.variationRole === 'variant' && attr.managedBy !== 'listing_grouping'),
    optionalAttributes: definition.optionalAttributes.filter(attr => attr.variationRole === 'variant' && attr.managedBy !== 'listing_grouping'),
  } : null
})
const skuDraft = computed(() => {
  const current = editor.value
  if (!current || !row.value) return null
  // 读取时沿用共同值；编辑时只写当前 SKU。公共属性表仍使用同一个控件。
  const attributes = new Proxy({} as Record<string, CategoryAttributeValue>, {
    get: (_target, field: string) => row.value?.attributes_by_target[props.targetKey]?.[field] ?? current.draft.attributes[field],
    set: (_target, field: string, value: CategoryAttributeValue) => {
      if (row.value) (row.value.attributes_by_target[props.targetKey] ||= {})[field] = value
      return true
    },
    deleteProperty: (_target, field: string) => {
      if (row.value) (row.value.attributes_by_target[props.targetKey] ||= {})[field] = ''
      return true
    },
  })
  return { ...current.draft, attributes, validationErrors: [] }
})
watch(() => [editor.value?.key, editor.value?.draft.categoryId], async () => {
  const current = editor.value
  if (current?.draft.categoryId && !current.state.busy
    && (!current.state.category?.fetchedAt || current.state.category.categoryId !== current.draft.categoryId)) {
    await current.run(current.actions.loadCategoryAttributes)
  }
}, { immediate: true })
async function fillCurrent() {
  const current = editor.value
  if (!current) return
  fillMessage.value = ''
  const result = await current.run(() => current.actions.fillAttributesByAi(props.skuId))
  if (result) fillMessage.value = `新增 ${result.filledCount} 项，还有 ${result.needReview.length} 个差异字段待核对。${result.warning || ''}`
}
</script>

<template>
  <fieldset v-if="editor && skuDraft" :disabled="editor.state.busy || editor.state.loading" class="mt-4 min-w-0">
    <p class="mb-2 text-sm">来源规格：{{ Object.entries(sku.options).map(([key, value]) => `${key}：${value}`).join('；') }}</p>
    <p class="mb-3 text-sm text-accent-500">下方字段来自当前平台类目。未单独设置时沿用共同属性；AI 只补空值。组合展示要求属性组合能表达真实区别，并非每个字段都必须不同。</p>
    <p v-if="fillMessage" class="mb-3 text-sm whitespace-pre-line" role="status">{{ fillMessage }}</p>
    <div v-if="editor.target.platform === 'mercadolibre'" class="mb-4 rounded border p-3" data-testid="sku-custom-attributes">
      <p class="font-medium">自定义 SKU 规格（Mercado User Products）</p>
      <p class="muted my-2">用于类目字段没有定义的款式差异。各 SKU 使用相同的属性名称，值填写真实规格；类目必填项仍需填写。请使用刊登语言。</p>
      <div v-for="(attribute, index) in customAttributes" :key="index" class="mb-2 flex gap-2">
        <input v-model="attribute.name" class="input" aria-label="自定义规格名称" placeholder="例如：款式" @input="editor.actions.invalidateCategoryPrecheck" />
        <input v-model="attribute.value" class="input" aria-label="自定义规格值" placeholder="例如：护颈款" @input="editor.actions.invalidateCategoryPrecheck" />
        <button class="btn btn-outline" @click="customAttributes.splice(index, 1); editor.actions.invalidateCategoryPrecheck()">删除规格</button>
      </div>
      <button class="btn btn-outline" @click="addCustomAttribute">增加自定义规格</button>
    </div>
    <CategoryAttributesPanel
      sku-scope
      :draft="skuDraft" :product-context="currentDraftProductContext" :target="editor.target"
      :platform-options="platformOptions" :category="category" category-query="" :category-results="[]"
      :category-attribute-translations="editor.state.categoryAttributeTranslations"
      :category-attribute-translations-source="editor.state.categoryAttributeTranslationsSource"
      :category-attribute-translating="editor.state.categoryAttributeTranslating"
      :category-attribute-loading="editor.state.categoryAttributeLoading"
      :category-attribute-error="editor.state.categoryAttributeError"
      :category-result-translations="{}" category-result-translations-source="" :category-result-translating="false"
      :category-precheck="null" :precheck="null" :loading="editor.state.busy || editor.state.loading"
      @apply-category="editor.run(editor.actions.loadCategoryAttributes)"
      @translate-category-attributes="editor.run(editor.actions.translateCategoryAttributes)"
      @fill-attributes="fillCurrent"
      @invalidate-category-precheck="editor.actions.invalidateCategoryPrecheck"
    />
    <p v-if="category?.fetchedAt && !category.requiredAttributes.length && !category.optionalAttributes.length" class="mt-2 text-sm text-amber-700">{{ editor.target.platform === 'mercadolibre' ? '当前类目没有预定义的 SKU 差异字段，User Products 可使用上方自定义规格。' : '当前类目没有 SKU 差异属性，请回到“类目/属性”核对分类，或根据平台能力调整发布组织方式。' }}</p>
    <p v-if="editor.state.busy" role="status" class="mt-2 text-sm">正在处理此 SKU 的平台属性…</p>
  </fieldset>
  <p v-else class="mt-3 text-sm">请先选择目标市场。</p>
</template>

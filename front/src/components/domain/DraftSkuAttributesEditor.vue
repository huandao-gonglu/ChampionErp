<script setup lang="ts">
import { computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import CategoryAttributesPanel from './CategoryAttributesPanel.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'
import type { CategoryAttributeValue, ProductSku } from '@/types/workflow'

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
  return definition ? {
    ...definition,
    requiredAttributes: definition.requiredAttributes.filter(
      attr => attr.variationRole === 'variant' && attr.managedBy !== 'listing_grouping',
    ),
    optionalAttributes: definition.optionalAttributes.filter(
      attr => attr.variationRole === 'variant' && attr.managedBy !== 'listing_grouping',
    ),
  } : null
})

// 彻底移除 JS Proxy：显式传递公共属性与 SKU 覆盖切片，删除即真 delete 并立即恢复沿用。
const inheritedAttributes = computed<Record<string, CategoryAttributeValue>>(
  () => editor.value?.target.attributes || {},
)
const overrideAttributes = computed<Record<string, CategoryAttributeValue>>(
  () => row.value?.attributes_by_target?.[props.targetKey] || {},
)

function handleUpdateSkuAttribute(attrId: string, value: CategoryAttributeValue | undefined) {
  if (!row.value || !editor.value) return
  const bucket = (row.value.attributes_by_target[props.targetKey] ||= {})
  if (value === undefined) {
    // 若该字段原本无公共值，清除即删键；若存在公共值，写入空串表示显式清空覆盖，可通过“恢复沿用”删键。
    if (Object.prototype.hasOwnProperty.call(inheritedAttributes.value, attrId)) {
      bucket[attrId] = ''
    } else {
      delete bucket[attrId]
    }
  } else {
    bucket[attrId] = value
  }
  editor.value.actions.invalidateCategoryPrecheck()
}

function handleClearSkuAttributeOverride(attrId: string) {
  if (!row.value || !editor.value) return
  const bucket = row.value.attributes_by_target[props.targetKey]
  if (bucket && Object.prototype.hasOwnProperty.call(bucket, attrId)) {
    delete bucket[attrId]
    if (!Object.keys(bucket).length) {
      delete row.value.attributes_by_target[props.targetKey]
    }
  }
  editor.value.actions.invalidateCategoryPrecheck()
}

function handleApplyCategory(forceRefresh?: boolean) {
  const current = editor.value
  if (!current) return
  void current.run(() => current.actions.loadCategoryAttributes({ forceRefresh }))
}

watch(() => [editor.value?.key, editor.value?.target.categoryId], async () => {
  const current = editor.value
  if (current?.target.categoryId && !current.state.busy
    && (!current.state.category?.fetchedAt || current.state.category.categoryId !== current.target.categoryId)) {
    await current.run(() => current.actions.loadCategoryAttributes({ auto: true }))
  }
}, { immediate: true })
</script>

<template>
  <fieldset v-if="editor && row" :disabled="editor.state.busy || editor.state.loading" class="mt-4 min-w-0">
    <p class="mb-2 text-sm">来源规格：{{ Object.entries(sku.options).map(([key, value]) => `${key}：${value}`).join('；') }}</p>
    <p class="mb-3 text-sm text-accent-500">下方字段来自当前平台类目。未单独设置时沿用共同属性，修改后将标记“已单独覆盖”，可随时点击“恢复沿用公共属性”。</p>
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
      :draft="editor.draft"
      :product-context="currentDraftProductContext"
      :target="editor.target"
      :platform-options="platformOptions"
      :category="category"
      category-query=""
      :category-results="[]"
      :inherited-attributes="inheritedAttributes"
      :override-attributes="overrideAttributes"
      :category-attribute-translations="editor.state.categoryAttributeTranslations"
      :category-attribute-translations-source="editor.state.categoryAttributeTranslationsSource"
      :category-attribute-translating="editor.state.categoryAttributeTranslating"
      :category-attribute-loading="editor.state.categoryAttributeLoading"
      :category-attribute-error="editor.state.categoryAttributeError"
      :category-result-translations="{}"
      category-result-translations-source=""
      :category-result-translating="false"
      :category-precheck="null"
      :precheck="null"
      :loading="editor.state.busy || editor.state.loading"
      @apply-category="handleApplyCategory"
      @update-attribute="handleUpdateSkuAttribute"
      @clear-attribute-override="handleClearSkuAttributeOverride"
      @invalidate-category-precheck="editor.actions.invalidateCategoryPrecheck"
    />
    <p v-if="category?.fetchedAt && !category.requiredAttributes.length && !category.optionalAttributes.length" class="mt-2 text-sm text-amber-700">
      {{ editor.target.platform === 'mercadolibre' ? '当前类目没有预定义的 SKU 差异字段，User Products 可使用上方自定义规格。' : '当前类目没有 SKU 差异属性，请回到“类目/属性”核对分类，或根据平台能力调整发布组织方式。' }}
    </p>
    <p v-if="editor.state.busy" role="status" class="mt-2 text-sm">正在处理此 SKU 的平台属性…</p>
  </fieldset>
  <p v-else class="mt-3 text-sm">请先选择目标市场。</p>
</template>

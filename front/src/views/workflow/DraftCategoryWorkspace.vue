<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import CategoryAttributesPanel from '@/components/domain/CategoryAttributesPanel.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'

const workflow = useWorkflowStore()
const { targetEditors } = storeToRefs(workflow)
const { currentDraftProductContext } = storeToRefs(useWorkflowCatalogStore())
const { platformOptions } = storeToRefs(useWorkflowPublishingStore())
const { loading } = storeToRefs(useWorkflowActivityStore())

const emit = defineEmits<{
  updatePackageDimension: [field: 'lengthCm' | 'widthCm' | 'heightCm' | 'weightKg', value: string]
}>()

let active = true
onBeforeUnmount(() => { active = false })
onMounted(async () => {
  for (const editor of targetEditors.value) {
    if (!active) break
    if (editor.draft.categoryId && (!editor.state.category?.fetchedAt || editor.state.category.categoryId !== editor.draft.categoryId)) {
      await editor.run(editor.actions.loadCategoryAttributes)
    }
  }
})
</script>

<template>
  <div>
    <div class="mb-4">
      <h2 class="card-title">类目/属性</h2>
      <p class="muted mt-1">各平台的操作区全部展开。每个区域内的搜索、AI 匹配和属性编辑只作用于该平台。</p>
    </div>
    <div class="grid items-start gap-6" :class="targetEditors.length > 1 ? 'xl:grid-cols-2' : ''">
      <fieldset
        v-for="editor in targetEditors"
        :key="editor.key"
        :aria-label="`${editor.target.platform} 类目与属性`"
        :disabled="loading"
        class="relative min-w-0"
        data-testid="category-target-editor"
      >
        <CategoryAttributesPanel
          :draft="editor.draft"
          :product-context="currentDraftProductContext"
          :target="editor.target"
          :platform-options="platformOptions"
          :column-layout="targetEditors.length > 1"
          :category="editor.state.category"
          :category-query="editor.state.categoryQuery"
          :category-results="editor.state.categoryResults"
          :category-auto-match-product-name="editor.state.categoryAutoMatchProductName"
          :category-auto-match-target-error="editor.state.categoryRecommendations[editor.key]?.error || editor.state.error"
          :category-attribute-translations="editor.state.categoryAttributeTranslations"
          :category-attribute-translations-source="editor.state.categoryAttributeTranslationsSource"
          :category-attribute-translating="editor.state.categoryAttributeTranslating"
          :category-attribute-loading="editor.state.categoryAttributeLoading"
          :category-attribute-error="editor.state.categoryAttributeError"
          :category-result-translations="editor.state.categoryResultTranslations"
          :category-result-translations-source="editor.state.categoryResultTranslationsSource"
          :category-result-translating="editor.state.categoryResultTranslating"
          :category-precheck="editor.state.categoryPrecheck"
          :precheck="null"
          :loading="loading"
          @update-category-query="editor.state.categoryQuery = $event"
          @search-category="editor.run(editor.actions.searchCategory)"
          @suggest-category="editor.run(editor.actions.suggestCategoryByAi)"
          @select-category="editor.run(() => editor.actions.selectCategory($event))"
          @apply-category="editor.run(editor.actions.loadCategoryAttributes)"
          @translate-category-results="editor.run(editor.actions.translateCategoryResults)"
          @translate-category-attributes="editor.run(editor.actions.translateCategoryAttributes)"
          @fill-attributes="editor.run(editor.actions.fillAttributesByAi)"
          @update-package-dimension="(field, value) => emit('updatePackageDimension', field, value)"
          @invalidate-category-precheck="editor.actions.invalidateCategoryPrecheck"
          @category-precheck="editor.run(editor.actions.runCategoryOnlyPrecheck)"
        />
        <div v-if="editor.state.busy" class="absolute inset-0 z-10 flex items-start justify-center rounded-lg bg-white/80 px-4 pt-12 backdrop-blur-sm dark:bg-dark-950/80" role="status">
          <div class="sticky top-4 rounded-lg border border-accent-200 bg-white p-4 text-center dark:border-dark-700 dark:bg-dark-900">
            <div class="mx-auto mb-3 size-6 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" />
            <p class="text-sm text-accent-700 dark:text-accent-200">{{ editor.state.categoryAutoMatchMessage || '正在处理该平台的类目与属性…' }}</p>
          </div>
        </div>
      </fieldset>
    </div>
  </div>
</template>

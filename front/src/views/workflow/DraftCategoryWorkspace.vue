<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { PhLightning } from '@phosphor-icons/vue'
import CategoryAttributesPanel from '@/components/domain/CategoryAttributesPanel.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'
import { useAiPageContext } from '@/composables/useAiPageContext'
import { vDesktop } from '@/utils/desktopHandoffDirective'
import { modelOperationReason } from '@/utils/desktop'
import type { CategoryAttributeValue, MarketplaceTargetSite } from '@/types/workflow'

const emit = defineEmits<{
  openSkuTab: []
}>()

const workflow = useWorkflowStore()
const { targetEditors, batchCategoryMatching } = storeToRefs(workflow)
const { currentDraftProductContext } = storeToRefs(useWorkflowCatalogStore())
const { platformOptions } = storeToRefs(useWorkflowPublishingStore())

const activeTargetKey = ref('')
const focusedAttribute = ref('')

// 保持激活平台有效：进入草稿或目标平台增删时自动回落到首个目标。
watch(
  () => targetEditors.value.map((editor) => editor.key),
  (keys) => {
    if (!keys.length) {
      activeTargetKey.value = ''
      return
    }
    if (!keys.includes(activeTargetKey.value)) {
      activeTargetKey.value = keys[0]
    }
  },
  { immediate: true },
)

const activeEditor = computed(() => (
  targetEditors.value.find((editor) => editor.key === activeTargetKey.value)
  ?? targetEditors.value[0]
))

function selectTargetTab(key: string): void {
  activeTargetKey.value = key
  focusedAttribute.value = ''
}

function focusBackground(key: string, event: Event): void {
  if (key && activeTargetKey.value !== key) {
    activeTargetKey.value = key
  }
  focusedAttribute.value = (event.target as HTMLElement).closest<HTMLElement>('[data-attribute-id]')?.dataset.attributeId || ''
}

function platformTabTitle(target: MarketplaceTargetSite): string {
  const option = platformOptions.value.find((item) => item.key === target.platform)
  const site = option?.sites.find((item) => item.code.toLowerCase() === String(target.site || '').toLowerCase())
  const platformLabel = option?.label || target.platform
  const siteLabel = site?.label || target.site
  return siteLabel ? `${platformLabel} · ${siteLabel}` : platformLabel
}

function hasAttrValue(value: CategoryAttributeValue | undefined): boolean {
  if (value == null) return false
  if (typeof value === 'string') return Boolean(value.trim())
  if ('values' in value) return Array.isArray(value.values) && value.values.length > 0
  return Boolean(String(value.value ?? '').trim())
}

function targetTabStatus(editor: (typeof targetEditors.value)[number]): {
  tone: 'busy' | 'ready' | 'pending' | 'empty'
  text: string
} {
  if (editor.state.busy || editor.state.categoryAttributeLoading) {
    return { tone: 'busy', text: '处理中…' }
  }
  if (!String(editor.target.categoryId || '').trim()) {
    return { tone: 'empty', text: '待选类目' }
  }
  const category = editor.state.category
  if (category?.fetchedAt && category.categoryId === String(editor.target.categoryId || '').trim()) {
    const attrs = editor.target.attributes || {}
    const required = (category.requiredAttributes || []).filter(
      (attr) => !(editor.target.platform === 'mercadolibre' && attr.id === 'EMPTY_GTIN_REASON')
        && attr.required
        && !attr.readOnly
        && attr.managedBy !== 'listing_grouping'
        && attr.managedBy !== 'draft_derived',
    )
    const done = required.filter((attr) => hasAttrValue(attrs[attr.id])).length
    if (done < required.length) {
      return { tone: 'pending', text: `必填 ${done}/${required.length}` }
    }
    return { tone: 'ready', text: `已选 · 必填 ${done}/${required.length}` }
  }
  return { tone: 'ready', text: '已选类目' }
}

// 进入页面或切换草稿时，为所有已选类目的目标并行自动加载属性定义（只读不触发多余保存）。
watch(
  () => targetEditors.value.map((editor) => `${editor.key}:${editor.target.categoryId || ''}`).join('|'),
  () => {
    const pending = targetEditors.value.filter(
      (editor) => editor.target.categoryId
        && !editor.state.busy
        && (!editor.state.category?.fetchedAt || editor.state.category.categoryId !== editor.target.categoryId),
    )
    if (!pending.length) return
    void Promise.allSettled(
      pending.map((editor) => editor.run(() => editor.actions.loadCategoryAttributes({ auto: true }))),
    )
  },
  { immediate: true },
)

useAiPageContext(() => {
  const editor = activeEditor.value
  if (!editor) return null
  return {
    page: 'draft_editor',
    section: 'category',
    draft_id: editor.draft.draftId || undefined,
    product_id: editor.draft.productId || currentDraftProductContext.value.productId || undefined,
    platform: editor.target.platform,
    site: editor.target.site || undefined,
    attribute_id: focusedAttribute.value || undefined,
  }
}, 10)
</script>

<template>
  <div class="space-y-3.5">
    <!-- 多平台状态标签页 + 一键并行匹配（单平台时省去多余顶栏，信息并入类目状态条） -->
    <div
      v-if="targetEditors.length > 1"
      class="flex flex-wrap items-center justify-between gap-2.5"
    >
      <div class="flex flex-wrap items-center gap-2" role="tablist" aria-label="目标平台">
        <button
          v-for="editor in targetEditors"
          :key="`tab-${editor.key}`"
          type="button"
          role="tab"
          :aria-selected="editor.key === activeTargetKey"
          :data-testid="`platform-tab-${editor.key}`"
          class="inline-flex items-center gap-2 rounded-xl border px-3.5 py-2 text-xs font-medium transition"
          :class="editor.key === activeTargetKey
            ? 'border-primary-500/50 bg-primary-50/80 text-accent-950 shadow-sm dark:border-primary-500/40 dark:bg-primary-950/35 dark:text-white'
            : 'border-accent-200 bg-white text-accent-600 hover:border-accent-300 hover:text-accent-950 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-300 dark:hover:border-dark-600 dark:hover:text-white'"
          @click="selectTargetTab(editor.key)"
        >
          <span
            class="size-2 shrink-0 rounded-full"
            :class="{
              'animate-pulse bg-primary-500': targetTabStatus(editor).tone === 'busy',
              'bg-emerald-500': targetTabStatus(editor).tone === 'ready',
              'bg-amber-500': targetTabStatus(editor).tone === 'pending',
              'bg-accent-400 dark:bg-dark-500': targetTabStatus(editor).tone === 'empty',
            }"
            aria-hidden="true"
          />
          <span class="font-semibold">{{ platformTabTitle(editor.target) }}</span>
          <span
            class="text-[11px] font-normal"
            :class="{
              'text-primary-700 dark:text-primary-300': targetTabStatus(editor).tone === 'busy',
              'text-emerald-700 dark:text-emerald-300': targetTabStatus(editor).tone === 'ready' && editor.key === activeTargetKey,
              'text-amber-700 dark:text-amber-300': targetTabStatus(editor).tone === 'pending',
              'text-accent-400 dark:text-accent-400': targetTabStatus(editor).tone === 'empty' || (targetTabStatus(editor).tone === 'ready' && editor.key !== activeTargetKey),
            }"
          >
            {{ targetTabStatus(editor).text }}
          </span>
        </button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <span v-if="modelOperationReason('category.product_match')" role="status" class="text-xs text-accent-500 dark:text-accent-400">
          {{ modelOperationReason('category.product_match') }}
        </span>
        <button
          v-desktop="'category.product_match'"
          type="button"
          class="inline-flex items-center gap-1.5 rounded-xl border border-primary-500/40 bg-primary-500/10 px-3 py-1.5 text-xs font-semibold text-primary-700 transition hover:bg-primary-500/15 disabled:cursor-not-allowed disabled:opacity-50 dark:border-primary-500/35 dark:bg-primary-500/15 dark:text-primary-300 dark:hover:bg-primary-500/20"
          data-testid="batch-auto-match-categories"
          :disabled="!!modelOperationReason('category.product_match') || batchCategoryMatching || targetEditors.some(e => e.state.busy)"
          @click="workflow.autoMatchAllTargets()"
        >
          <PhLightning :size="14" aria-hidden="true" />
          {{ batchCategoryMatching ? '正在并行匹配全部平台…' : '一键 AI 匹配全部平台' }}
        </button>
      </div>
    </div>

    <!-- 各平台独立保持挂载（v-show 切换）：100% 全宽工作区 + 完整保留并行状态与输入上下文 -->
    <div class="min-w-0">
      <fieldset
        v-for="editor in targetEditors"
        v-show="editor.key === activeTargetKey"
        :key="editor.key"
        :hidden="targetEditors.length > 1 && editor.key !== activeTargetKey"
        :role="targetEditors.length > 1 ? 'tabpanel' : undefined"
        :aria-label="`${editor.target.platform} 类目与属性`"
        :disabled="editor.state.busy"
        class="relative min-w-0"
        data-testid="category-target-editor"
        @pointerdown="focusBackground(editor.key, $event)"
        @focusin="focusBackground(editor.key, $event)"
      >
        <CategoryAttributesPanel
          :draft="editor.draft"
          :product-context="currentDraftProductContext"
          :target="editor.target"
          :platform-options="platformOptions"
          :category="editor.state.category"
          :category-query="editor.state.categoryQuery"
          :category-translated-query="editor.state.categoryRecommendations[editor.key]?.translatedQuery || ''"
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
          :loading="editor.state.busy || editor.state.loading"
          @update-category-query="editor.state.categoryQuery = $event"
          @search-category="editor.run(editor.actions.searchCategory)"
          @suggest-category="editor.run(editor.actions.suggestCategoryByAi)"
          @select-category="editor.run(() => editor.actions.selectCategory($event))"
          @apply-category="(forceRefresh) => editor.run(() => editor.actions.loadCategoryAttributes({ forceRefresh }))"
          @invalidate-category-precheck="editor.actions.invalidateCategoryPrecheck"
          @category-precheck="editor.run(editor.actions.runCategoryOnlyPrecheck)"
          @open-sku-tab="emit('openSkuTab')"
        />
        <div
          v-if="editor.state.busy"
          class="absolute inset-0 z-10 flex items-start justify-center rounded-2xl bg-white/80 px-4 pt-12 backdrop-blur-sm dark:bg-dark-950/80"
          role="status"
        >
          <div class="sticky top-4 rounded-xl border border-accent-200 bg-white px-5 py-4 text-center shadow-lg dark:border-dark-700 dark:bg-dark-900">
            <div class="mx-auto mb-3 size-6 animate-spin rounded-full border-2 border-primary-100 border-t-primary-500 dark:border-dark-700 dark:border-t-primary-400" />
            <p class="text-sm text-accent-700 dark:text-accent-200">{{ editor.state.categoryAutoMatchMessage || '正在处理该平台的类目与属性…' }}</p>
          </div>
        </div>
      </fieldset>
    </div>
  </div>
</template>

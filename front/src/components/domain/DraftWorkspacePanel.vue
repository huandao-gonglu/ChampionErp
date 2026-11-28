<script setup lang="ts">
import { PhArrowLeft, PhArrowsClockwise, PhWarningCircle } from '@phosphor-icons/vue'

export type DraftWorkspaceTab = 'text' | 'images' | 'category' | 'skus' | 'pricing' | 'precheck'

const props = defineProps<{
  activeTab: DraftWorkspaceTab
  draftTitle: string
  draftId: string
  syncMessage?: string
  errorMessage?: string
  refreshing?: boolean
  reloadDisabled?: boolean
  editingDisabled?: boolean
}>()

const emit = defineEmits<{
  updateActiveTab: [tab: DraftWorkspaceTab]
  close: []
  reload: []
}>()

const tabs: Array<{ key: DraftWorkspaceTab; label: string; summary: string }> = [
  { key: 'text', label: '编辑文本', summary: '标题和商品描述' },
  { key: 'images', label: '编辑图片', summary: '公共图集和 SKU 主图' },
  { key: 'category', label: '类目/属性', summary: '类目与必填属性' },
  { key: 'skus', label: 'SKU', summary: '选品、规格和销售设置' },
  { key: 'pricing', label: '核价', summary: '成本、运费和利润' },
  { key: 'precheck', label: '发布预检', summary: '校验与发布准备' },
]
</script>

<template>
  <section class="flex min-w-0 flex-col space-y-4">
    <!-- 吸顶工作台顶栏：与页面级 PageHeader 对齐同一右上角坐标，保存按钮固定在最右侧 -->
    <div class="page-header-sticky border-b border-accent-200/90 !pb-0 dark:border-dark-700">
      <header class="flex flex-wrap items-start justify-between gap-3">
        <div class="flex min-w-0 flex-1 basis-64 items-center gap-3">
          <button
            type="button"
            class="btn btn-outline !min-h-9 shrink-0 !rounded-xl !px-3 !py-1.5 text-sm"
            @click="emit('close')"
          >
            <PhArrowLeft :size="15" aria-hidden="true" />返回草稿箱
          </button>
          <span class="h-5 w-px shrink-0 bg-accent-200 dark:bg-dark-700" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <h2 class="truncate text-base font-bold text-accent-950 dark:text-white" :title="props.draftTitle">
              {{ props.draftTitle || '草稿编辑' }}
            </h2>
            <p v-if="props.draftId" class="truncate font-mono text-[11px] text-accent-500 dark:text-accent-400">
              {{ props.draftId }}
            </p>
          </div>
        </div>
        <div class="ml-auto flex shrink-0 flex-wrap items-center justify-end gap-2">
          <slot name="secondary-actions" />
          <button
            v-if="!props.syncMessage"
            type="button"
            class="btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm"
            :disabled="props.refreshing || props.reloadDisabled || !props.draftId"
            @click="emit('reload')"
          >
            <PhArrowsClockwise :size="15" :class="{ 'animate-spin': props.refreshing }" aria-hidden="true" />{{ props.refreshing ? '正在加载…' : '重新加载最新数据' }}
          </button>
          <slot name="actions" />
        </div>
      </header>

      <div
        v-if="props.syncMessage"
        role="alert"
        class="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
      >
        <p class="min-w-0 flex-1">{{ props.syncMessage }}</p>
        <button
          type="button"
          class="btn btn-outline !min-h-8 shrink-0 !rounded-lg !px-3 !py-1 text-xs"
          :disabled="props.refreshing || props.reloadDisabled || !props.draftId"
          @click="emit('reload')"
        >
          {{ props.refreshing ? '正在加载…' : '重新加载最新数据' }}
        </button>
      </div>

      <p
        v-if="props.errorMessage"
        role="alert"
        class="mt-3 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
      >
        <PhWarningCircle :size="17" weight="fill" class="mt-0.5 shrink-0 text-rose-500 dark:text-rose-400" aria-hidden="true" />
        <span class="min-w-0 break-words">{{ props.errorMessage }}</span>
      </p>

      <nav class="mt-3 flex gap-1 overflow-x-auto" role="tablist" aria-label="草稿编辑功能">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="draft-workspace-tab"
          :title="tab.summary"
          role="tab"
          :aria-selected="props.activeTab === tab.key"
          @click="emit('updateActiveTab', tab.key)"
        >
          {{ tab.label }}
        </button>
      </nav>
    </div>

    <div :key="props.activeTab" role="tabpanel" class="min-w-0 rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-900 sm:p-5">
      <div :inert="props.editingDisabled && props.activeTab !== 'images' ? true : undefined">
        <slot :name="props.activeTab" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.draft-workspace-tab {
  @apply shrink-0 whitespace-nowrap border-b-2 border-transparent px-3.5 py-2.5 text-xs font-medium text-accent-600 transition hover:text-accent-950 dark:text-accent-300 dark:hover:text-white;
}
.draft-workspace-tab[aria-selected='true'] {
  @apply border-primary-500 font-semibold text-primary-700 dark:text-primary-300;
}
</style>

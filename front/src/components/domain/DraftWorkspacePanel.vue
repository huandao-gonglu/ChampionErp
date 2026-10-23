<script setup lang="ts">
export type DraftWorkspaceTab = 'text' | 'images' | 'category' | 'skus' | 'pricing' | 'precheck'

const props = defineProps<{
  activeTab: DraftWorkspaceTab
  draftTitle: string
  draftId: string
  syncMessage?: string
  errorMessage?: string
  refreshing?: boolean
  reloadDisabled?: boolean
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
  <section class="flex h-full min-h-0 flex-col gap-5">
    <div class="shrink-0 space-y-3">
      <header class="flex flex-wrap items-center justify-between gap-3">
        <div class="min-w-0 flex-1 basis-64">
          <h2 class="truncate text-base font-bold text-slate-950 dark:text-white" :title="props.draftTitle">{{ props.draftTitle || '草稿编辑' }}</h2>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <slot name="actions" />
          <button v-if="!props.syncMessage" class="btn btn-outline" :disabled="props.refreshing || props.reloadDisabled || !props.draftId" @click="emit('reload')">{{ props.refreshing ? '正在加载…' : '重新加载最新数据' }}</button>
          <button class="btn btn-outline" @click="emit('close')">关闭</button>
        </div>
      </header>

      <div v-if="props.syncMessage" role="alert" class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
        <p class="min-w-0 flex-1">{{ props.syncMessage }}</p>
        <button type="button" class="btn btn-outline shrink-0" :disabled="props.refreshing || props.reloadDisabled || !props.draftId" @click="emit('reload')">{{ props.refreshing ? '正在加载…' : '重新加载最新数据' }}</button>
      </div>

      <nav class="flex gap-1 overflow-x-auto border-b border-accent-200 dark:border-dark-700" role="tablist" aria-label="草稿编辑功能">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          class="shrink-0 rounded-t-lg px-4 py-2.5 text-sm transition"
          :title="tab.summary"
          :class="props.activeTab === tab.key
            ? 'border border-primary-200 bg-white text-primary-700 shadow-sm dark:border-primary-500/40 dark:bg-dark-900 dark:text-primary-100'
            : 'border border-transparent text-accent-600 hover:bg-white/80 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-dark-900 dark:hover:text-white'"
          role="tab"
          :aria-selected="props.activeTab === tab.key"
          @click="emit('updateActiveTab', tab.key)"
        >
          <span class="font-semibold">{{ tab.label }}</span>
        </button>
      </nav>
      <p v-if="props.errorMessage" role="alert" class="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">{{ props.errorMessage }}</p>
    </div>

    <div :key="props.activeTab" role="tabpanel" class="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
      <slot :name="props.activeTab" />
    </div>
  </section>
</template>

<script setup lang="ts">
export type DraftWorkspaceTab = 'text' | 'skus' | 'images' | 'category' | 'pricing' | 'precheck'

const props = defineProps<{
  activeTab: DraftWorkspaceTab
  draftTitle: string
  draftId: string
}>()

const emit = defineEmits<{
  updateActiveTab: [tab: DraftWorkspaceTab]
  close: []
}>()

const tabs: Array<{ key: DraftWorkspaceTab; label: string; summary: string }> = [
  { key: 'text', label: '编辑文本', summary: '标题、描述和卖点' },
  { key: 'skus', label: 'SKU', summary: '选品、规格和销售设置' },
  { key: 'images', label: '编辑图片', summary: '发布图和图片池' },
  { key: 'category', label: '类目/属性', summary: '类目与必填属性' },
  { key: 'pricing', label: '核价', summary: '成本、运费和利润' },
  { key: 'precheck', label: '发布预检', summary: '校验与发布准备' },
]
</script>

<template>
  <section class="space-y-5">
    <header class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">草稿工作台</p>
        <h2 class="mt-1 truncate text-xl font-black text-slate-950 dark:text-white" :title="props.draftTitle">{{ props.draftTitle || '草稿编辑' }}</h2>
        <p class="mt-1 truncate font-mono text-xs text-accent-500 dark:text-accent-300" :title="props.draftId">{{ props.draftId || '未保存草稿' }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <slot name="actions" />
        <button class="btn btn-outline" @click="emit('close')">关闭</button>
      </div>
    </header>

    <nav class="grid gap-2 rounded-2xl border border-accent-200 bg-slate-50 p-2 dark:border-dark-700 dark:bg-dark-950/70 sm:grid-cols-2 xl:grid-cols-6" aria-label="草稿编辑功能">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="rounded-xl px-4 py-3 text-left transition"
        :class="props.activeTab === tab.key
          ? 'border border-primary-200 bg-white text-primary-700 shadow-sm dark:border-primary-500/40 dark:bg-dark-900 dark:text-primary-100'
          : 'border border-transparent text-accent-600 hover:bg-white/80 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-dark-900 dark:hover:text-white'"
        role="tab"
        :aria-selected="props.activeTab === tab.key"
        @click="emit('updateActiveTab', tab.key)"
      >
        <span class="block text-base font-bold">{{ tab.label }}</span>
        <span class="mt-1 block text-sm text-accent-500 dark:text-accent-400">{{ tab.summary }}</span>
      </button>
    </nav>

    <div role="tabpanel">
      <slot :name="props.activeTab" />
    </div>
  </section>
</template>

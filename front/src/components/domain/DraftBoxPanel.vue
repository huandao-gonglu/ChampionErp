<script setup lang="ts">
import { computed, ref } from 'vue'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'
import type { DraftIndexItem, Marketplace } from '@/types/workflow'

const props = defineProps<{
  drafts: DraftIndexItem[]
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: []
  editText: [item: DraftIndexItem]
  editImages: [item: DraftIndexItem]
  goPublish: [item: DraftIndexItem]
}>()

const platformFilter = ref<'all' | Marketplace>('all')
const draftScope = ref<'active' | 'published' | 'all'>('active')
const activeDraftStatusSet = new Set(['claimed', 'copy_ready', 'images_ready', 'ready_to_publish', 'failed', 'not_ready'])
const draftStatusSet = new Set([...activeDraftStatusSet, 'published'])
const platformLabels: Record<Marketplace, string> = {
  mercadolibre: 'Mercado Libre',
  wildberries: 'Wildberries',
  ozon: 'Ozon',
}

const allDraftRows = computed(() => props.drafts.filter((item) => {
  if (platformFilter.value !== 'all' && item.platform !== platformFilter.value) return false
  return draftStatusSet.has(String(item.status || ''))
}))

const draftRows = computed(() => allDraftRows.value.filter((row) => {
  if (draftScope.value === 'published') return row.status === 'published'
  if (draftScope.value === 'all') return true
  return activeDraftStatusSet.has(String(row.status || ''))
}))

const activeDraftCount = computed(() => allDraftRows.value.filter((row) => activeDraftStatusSet.has(String(row.status || ''))).length)
const publishedDraftCount = computed(() => allDraftRows.value.filter((row) => row.status === 'published').length)
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">草稿箱</p>
        <h2 class="mt-2 card-title">平台草稿箱</h2>
        <p class="muted mt-1">展示未发布完成的平台编辑稿；发布成功后默认从这里移出，去 ML 已发布或发布日志查看结果。</p>
      </div>
      <div class="grid w-full gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:w-auto">
        <select v-model="platformFilter" class="input sm:w-48">
          <option value="all">全部平台</option>
          <option value="mercadolibre">Mercado Libre</option>
          <option value="wildberries">Wildberries</option>
          <option value="ozon">Ozon</option>
        </select>
        <select v-model="draftScope" class="input sm:w-44">
          <option value="active">待编辑/待发布</option>
          <option value="published">已发布</option>
          <option value="all">全部草稿</option>
        </select>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('refresh')">刷新</button>
      </div>
    </div>

    <div class="mt-5 rounded-lg border border-accent-200 bg-accent-50 p-3 text-sm text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
      当前显示：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ draftRows.length }}</span> 条
      <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
      待处理：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ activeDraftCount }}</span>
      <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
      已发布：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ publishedDraftCount }}</span>
    </div>
    <div v-if="props.error" class="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
      {{ props.error }}
    </div>

    <div class="mt-5 overflow-auto rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full min-w-[1120px] text-left text-sm">
        <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
          <tr>
            <th class="min-w-80 p-3">商品</th>
            <th class="whitespace-nowrap p-3">平台</th>
            <th class="whitespace-nowrap p-3">草稿状态</th>
            <th class="min-w-64 p-3">来源</th>
            <th class="min-w-72 p-3">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="row in draftRows" :key="row.draftId" class="align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60">
            <td class="min-w-80 max-w-lg p-3">
              <div class="flex gap-3">
                <img v-if="row.mainImage" :src="row.mainImage" class="size-12 rounded-lg object-cover" />
                <div v-else class="flex size-12 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-[10px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">无图</div>
                <div class="min-w-0">
                  <div class="font-semibold text-accent-950 dark:text-white">{{ row.title || row.productTitle || row.productId || '-' }}</div>
                  <div class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400">{{ row.productTitle || row.productId }}</div>
                </div>
              </div>
            </td>
            <td class="whitespace-nowrap p-3"><span class="badge-muted">{{ platformLabels[row.platform] }}</span></td>
            <td class="whitespace-nowrap p-3"><span :class="statusBadgeClass(row.status)">{{ workflowStatusLabel(row.status) }}</span></td>
            <td class="min-w-64 p-3">
              <div class="font-semibold text-accent-950 dark:text-white">{{ row.sourcePlatform || '-' }}</div>
              <div class="max-w-xs truncate text-xs text-accent-500 dark:text-accent-400">{{ row.sourceUrl }}</div>
            </td>
            <td class="min-w-72 p-3">
              <div class="flex flex-nowrap gap-2">
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('editText', row)">编辑文本</button>
                <button class="btn btn-secondary py-1.5" :disabled="props.loading" @click="emit('editImages', row)">编辑图片</button>
                <button class="btn btn-primary py-1.5" :disabled="props.loading" @click="emit('goPublish', row)">发布预检</button>
              </div>
            </td>
          </tr>
          <tr v-if="!draftRows.length">
            <td class="p-6 text-center text-accent-500 dark:text-accent-300" colspan="5">暂无平台草稿。可先从商品库或发布预检页推到平台草稿箱。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

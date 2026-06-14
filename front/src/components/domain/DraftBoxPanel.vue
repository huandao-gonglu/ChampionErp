<script setup lang="ts">
import { computed, ref } from 'vue'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'
import type { Marketplace, ProductIndexItem, WorkflowStatus } from '@/types/workflow'

const props = defineProps<{
  items: ProductIndexItem[]
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: []
  editText: [item: ProductIndexItem, platform: Marketplace]
  editImages: [item: ProductIndexItem, platform: Marketplace]
  goPublish: [item: ProductIndexItem, platform: Marketplace]
}>()

const platformFilter = ref<'all' | Marketplace>('all')
const draftStatusSet = new Set(['claimed', 'copy_ready', 'images_ready', 'ready_to_publish', 'published', 'failed', 'not_ready'])
const platformLabels: Record<Marketplace, string> = {
  mercadolibre: 'Mercado Libre',
  wildberries: 'Wildberries',
  ozon: 'Ozon',
}

const draftRows = computed(() => props.items.flatMap((item) => {
  return (Object.entries(item.draftStatuses) as Array<[Marketplace, WorkflowStatus]>)
    .filter(([platform, status]) => {
      if (platformFilter.value !== 'all' && platform !== platformFilter.value) return false
      return draftStatusSet.has(String(status || ''))
    })
    .map(([platform, status]) => ({ item, platform, status }))
}))
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">草稿箱</p>
        <h2 class="mt-2 card-title">平台草稿箱</h2>
        <p class="muted mt-1">展示已经认领到平台草稿状态机的商品，可继续编辑并进入发布预检。</p>
      </div>
      <div class="grid w-full gap-3 sm:grid-cols-[minmax(0,1fr)_auto] lg:w-auto">
        <select v-model="platformFilter" class="input sm:w-48">
          <option value="all">全部平台</option>
          <option value="mercadolibre">Mercado Libre</option>
          <option value="wildberries">Wildberries</option>
          <option value="ozon">Ozon</option>
        </select>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('refresh')">刷新</button>
      </div>
    </div>

    <div class="mt-5 rounded-lg border border-accent-200 bg-accent-50 p-3 text-sm text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
      草稿：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ draftRows.length }}</span> 条
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
          <tr v-for="row in draftRows" :key="`${row.item.productId}-${row.platform}`" class="align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60">
            <td class="min-w-80 max-w-lg p-3">
              <div class="flex gap-3">
                <img v-if="row.item.mainImage" :src="row.item.mainImage" class="size-12 rounded-lg object-cover" />
                <div v-else class="flex size-12 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-[10px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">无图</div>
                <div class="min-w-0">
                  <div class="font-semibold text-accent-950 dark:text-white">{{ row.item.title || row.item.productId || '-' }}</div>
                  <div class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400">{{ row.item.productId }}</div>
                </div>
              </div>
            </td>
            <td class="whitespace-nowrap p-3"><span class="badge-muted">{{ platformLabels[row.platform] }}</span></td>
            <td class="whitespace-nowrap p-3"><span :class="statusBadgeClass(row.status)">{{ workflowStatusLabel(row.status) }}</span></td>
            <td class="min-w-64 p-3">
              <div class="font-semibold text-accent-950 dark:text-white">{{ row.item.sourcePlatform || '-' }}</div>
              <div class="max-w-xs truncate text-xs text-accent-500 dark:text-accent-400">{{ row.item.sourceUrl }}</div>
            </td>
            <td class="min-w-72 p-3">
              <div class="flex flex-nowrap gap-2">
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('editText', row.item, row.platform)">编辑文本</button>
                <button class="btn btn-secondary py-1.5" :disabled="props.loading" @click="emit('editImages', row.item, row.platform)">编辑图片</button>
                <button class="btn btn-primary py-1.5" :disabled="props.loading" @click="emit('goPublish', row.item, row.platform)">发布预检</button>
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

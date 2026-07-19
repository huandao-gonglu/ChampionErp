<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ProductIndexItem } from '@/types/workflow'

const props = defineProps<{
  items: ProductIndexItem[]
  selectedIds: string[]
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: []
  load: [item: ProductIndexItem]
  editImages: [item: ProductIndexItem]
  toggle: [productId: string, checked: boolean]
  selectAll: [checked: boolean, productIds: string[]]
  deleteItem: [item: ProductIndexItem]
  deleteSelected: []
  claim: []
}>()

const workflowFilter = ref('all')
const doneStatuses = new Set(['done', 'success', 'ready', 'ready_to_publish', 'published', 'completed', 'true', 'real_publish_success'])

const filteredItems = computed(() => props.items.filter((item) => {
  if (workflowFilter.value === 'all') return true
  if (workflowFilter.value === 'collected') return item.collectStatus === 'success' || item.collectStatus === 'collected'
  return item.collectStatus !== 'success' && item.collectStatus !== 'collected'
}))

const allChecked = computed(() => filteredItems.value.length > 0 && filteredItems.value.every((item) => props.selectedIds.includes(item.productId)))
const selectedCount = computed(() => props.selectedIds.length)

function confirmDelete(item: ProductIndexItem) {
  const title = item.title || item.productId || '该商品'
  if (window.confirm(`确认删除商品「${title}」吗？删除后不可恢复。`)) emit('deleteItem', item)
}

function confirmDeleteSelected() {
  if (!selectedCount.value) return
  if (window.confirm(`确认批量删除已勾选的 ${selectedCount.value} 个商品吗？删除后不可恢复。`)) emit('deleteSelected')
}

function statusClass(value: string) {
  const status = String(value || '').toLowerCase()
  if (doneStatuses.has(status)) return 'badge-success'
  if (status === 'failed' || status === 'not_ready' || status === 'false') return 'badge-danger'
  if (status === 'pending' || status === 'partial') return 'badge-info'
  return 'badge-muted'
}
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">商品库</p>
        <h2 class="mt-2 card-title">商品母库</h2>
        <p class="muted mt-1">保存采集后的商品事实、供应链字段和通用图片；发布相关内容进入草稿箱后再编辑。</p>
      </div>
      <select v-model="workflowFilter" class="input w-full sm:w-44">
        <option value="all">全部商品</option>
        <option value="collected">已采集</option>
        <option value="not_collected">待补全</option>
      </select>
    </div>

    <div v-if="props.error" class="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
      {{ props.error }}
    </div>

    <div class="mt-5 rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="text-sm text-accent-500 dark:text-accent-400">
          本地商品库：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ filteredItems.length }} / {{ props.items.length }}</span> 条记录
          <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
          已勾选：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ selectedCount }}</span> 个
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-outline py-2" :disabled="props.loading" @click="emit('refresh')">刷新商品库</button>
          <button class="btn btn-secondary py-2" :disabled="props.loading || !selectedCount" @click="emit('claim')">推到草稿箱</button>
          <button class="btn btn-outline py-2 text-rose-700 dark:text-rose-200" :disabled="props.loading || !selectedCount" @click="confirmDeleteSelected">批量删除</button>
        </div>
      </div>
    </div>

    <div class="mt-5 overflow-hidden rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[5%]" />
          <col class="w-[8%]" />
          <col class="w-[18%]" />
          <col class="w-[33%]" />
          <col class="w-[9%]" />
          <col class="w-[12%]" />
          <col class="w-[15%]" />
        </colgroup>
        <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
          <tr>
            <th class="p-2"><input class="size-4 rounded border-accent-300 text-primary-600" type="checkbox" aria-label="全选当前商品" :checked="allChecked" :disabled="props.loading || !filteredItems.length" @change="emit('selectAll', ($event.target as HTMLInputElement).checked, filteredItems.map((item) => item.productId))" /></th>
            <th class="p-2">主图</th>
            <th class="p-3">来源</th>
            <th class="p-3">商品标题</th>
            <th class="p-3">采集</th>
            <th class="p-3">更新时间</th>
            <th class="p-3">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="item in filteredItems" :key="item.productId" class="align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60">
            <td class="p-2"><input class="size-4 rounded border-accent-300 text-primary-600" type="checkbox" aria-label="勾选商品" :checked="props.selectedIds.includes(item.productId)" :disabled="props.loading" @change="emit('toggle', item.productId, ($event.target as HTMLInputElement).checked)" /></td>
            <td class="p-2">
              <img v-if="item.mainImage" :src="item.mainImage" class="size-10 rounded-lg object-cover" />
              <div v-else class="flex size-10 items-center justify-center rounded-lg bg-accent-100 text-[10px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">无图</div>
            </td>
            <td class="min-w-0 p-3">
              <div class="truncate font-semibold text-accent-950 dark:text-white" :title="item.sourcePlatform || '-'">{{ item.sourcePlatform || '-' }}</div>
              <div class="truncate text-xs text-accent-500 dark:text-accent-400" :title="item.sourceUrl">{{ item.sourceUrl }}</div>
            </td>
            <td class="min-w-0 p-3">
              <div class="truncate font-semibold text-accent-950 dark:text-white" :title="item.title || '-'">{{ item.title || '-' }}</div>
              <div class="mt-1 truncate font-mono text-xs text-accent-500 dark:text-accent-400" :title="item.productId">{{ item.productId }}</div>
            </td>
            <td class="p-3"><span class="inline-flex max-w-full truncate" :class="statusClass(item.collectStatus)" :title="item.collectStatus || '-'">{{ item.collectStatus || '-' }}</span></td>
            <td class="p-3 text-xs text-accent-500 dark:text-accent-300"><span class="block truncate" :title="item.updatedAt || item.createdAt">{{ item.updatedAt || item.createdAt || '-' }}</span></td>
            <td class="p-3">
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading" @click="emit('load', item)">编辑文本</button>
                <button class="btn btn-secondary whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading" @click="emit('editImages', item)">编辑图片</button>
                <button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs text-rose-700 dark:text-rose-200" :disabled="props.loading" @click="confirmDelete(item)">删除</button>
              </div>
            </td>
          </tr>
          <tr v-if="!filteredItems.length"><td class="p-6 text-center text-accent-500 dark:text-accent-300" colspan="7">当前筛选下暂无商品。可在采集页导入或采集商品后加入商品库。</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useClipboard } from '@/composables/useClipboard'
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
  deleteDraft: [item: DraftIndexItem]
  deleteDrafts: [items: DraftIndexItem[]]
}>()

const platformFilter = ref<'all' | Marketplace>('all')
const draftScope = ref<'active' | 'published' | 'all'>('active')
const selectedDraftIds = ref<string[]>([])
const copiedDraftId = ref('')
const { copy: copyToClipboard } = useClipboard()
let copiedDraftTimer: number | null = null
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
const selectedDrafts = computed(() => props.drafts.filter((item) => selectedDraftIds.value.includes(draftIdOf(item))))
const selectedCount = computed(() => selectedDrafts.value.length)
const visibleDraftIds = computed(() => draftRows.value.map(draftIdOf).filter(Boolean))
const allChecked = computed(() => visibleDraftIds.value.length > 0 && visibleDraftIds.value.every((id) => selectedDraftIds.value.includes(id)))

function draftIdOf(item: DraftIndexItem) {
  return String(item.draftId || '').trim()
}

function toggleDraftSelection(item: DraftIndexItem, checked: boolean) {
  const draftId = draftIdOf(item)
  if (!draftId) return
  const exists = selectedDraftIds.value.includes(draftId)
  if (checked && !exists) selectedDraftIds.value.push(draftId)
  if (!checked) selectedDraftIds.value = selectedDraftIds.value.filter((id) => id !== draftId)
}

function selectVisibleDrafts(checked: boolean) {
  if (!checked) {
    selectedDraftIds.value = selectedDraftIds.value.filter((id) => !visibleDraftIds.value.includes(id))
    return
  }
  selectedDraftIds.value = Array.from(new Set([...selectedDraftIds.value, ...visibleDraftIds.value]))
}

function deleteSelectedDrafts() {
  if (!selectedDrafts.value.length) return
  emit('deleteDrafts', selectedDrafts.value)
}

async function copyDraftId(item: DraftIndexItem) {
  const draftId = draftIdOf(item)
  if (!draftId) return
  await copyToClipboard(draftId)
  copiedDraftId.value = draftId
  if (copiedDraftTimer) window.clearTimeout(copiedDraftTimer)
  copiedDraftTimer = window.setTimeout(() => {
    copiedDraftId.value = ''
    copiedDraftTimer = null
  }, 1500)
}

watch(() => props.drafts.map(draftIdOf), (draftIds) => {
  const existingIds = new Set(draftIds)
  selectedDraftIds.value = selectedDraftIds.value.filter((id) => existingIds.has(id))
})

onBeforeUnmount(() => {
  if (copiedDraftTimer) window.clearTimeout(copiedDraftTimer)
})
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

    <div class="mt-5 rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="text-sm text-accent-500 dark:text-accent-400">
          当前显示：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ draftRows.length }}</span> 条
          <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
          待处理：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ activeDraftCount }}</span>
          <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
          已发布：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ publishedDraftCount }}</span>
          <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
          已勾选：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ selectedCount }}</span> 个
        </div>
        <button class="btn btn-outline px-3 py-1.5 text-xs text-rose-600 hover:border-rose-300 hover:bg-rose-50 dark:text-rose-200 dark:hover:border-rose-500/50 dark:hover:bg-rose-500/10" :disabled="props.loading || !selectedCount" @click="deleteSelectedDrafts">批量删除选中</button>
      </div>
    </div>
    <div v-if="props.error" class="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
      {{ props.error }}
    </div>

    <div class="mt-5 overflow-hidden rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[5%]" />
          <col class="w-[24%]" />
          <col class="w-[11%]" />
          <col class="w-[12%]" />
          <col class="w-[19%]" />
          <col class="w-[29%]" />
        </colgroup>
        <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
          <tr>
            <th class="p-3"><input class="size-4 rounded border-accent-300 text-primary-600" type="checkbox" aria-label="全选当前草稿" :checked="allChecked" :disabled="props.loading || !visibleDraftIds.length" @change="selectVisibleDrafts(($event.target as HTMLInputElement).checked)" /></th>
            <th class="p-3">商品</th>
            <th class="whitespace-nowrap p-3">平台</th>
            <th class="whitespace-nowrap p-3">草稿状态</th>
            <th class="p-3">来源</th>
            <th class="p-3">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="row in draftRows" :key="row.draftId" class="align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60">
            <td class="p-3"><input class="size-4 rounded border-accent-300 text-primary-600" type="checkbox" aria-label="勾选草稿" :checked="selectedDraftIds.includes(draftIdOf(row))" :disabled="props.loading || !draftIdOf(row)" @change="toggleDraftSelection(row, ($event.target as HTMLInputElement).checked)" /></td>
            <td class="min-w-0 p-3">
              <div class="flex gap-3">
                <img v-if="row.mainImage" :src="row.mainImage" class="size-12 rounded-lg object-cover" />
                <div v-else class="flex size-12 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-[10px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">无图</div>
                <div class="min-w-0">
                  <div class="truncate font-semibold text-accent-950 dark:text-white">{{ row.title || row.productTitle || row.productId || '-' }}</div>
                  <div class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400">{{ row.productTitle || row.productId }}</div>
                </div>
              </div>
            </td>
            <td class="p-3"><span class="badge-muted max-w-full truncate" :title="platformLabels[row.platform]">{{ platformLabels[row.platform] }}</span></td>
            <td class="p-3"><span class="inline-flex max-w-full truncate" :class="statusBadgeClass(row.status)" :title="workflowStatusLabel(row.status)">{{ workflowStatusLabel(row.status) }}</span></td>
            <td class="min-w-0 p-3">
              <div class="truncate font-semibold text-accent-950 dark:text-white" :title="row.sourcePlatform || '-'">{{ row.sourcePlatform || '-' }}</div>
              <div class="truncate text-xs text-accent-500 dark:text-accent-400" :title="row.sourceUrl">{{ row.sourceUrl }}</div>
            </td>
            <td class="p-3">
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading" @click="emit('editText', row)">编辑文本</button>
                <button class="btn btn-secondary whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading" @click="emit('editImages', row)">编辑图片</button>
                <button class="btn btn-primary whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading" @click="emit('goPublish', row)">发布预检</button>
                <button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading || !draftIdOf(row)" :title="draftIdOf(row) || '当前草稿暂无 ID'" @click="copyDraftId(row)">
                  {{ copiedDraftId === draftIdOf(row) ? '已复制' : '复制草稿id' }}
                </button>
                <button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs text-rose-600 hover:border-rose-300 hover:bg-rose-50 dark:text-rose-200 dark:hover:border-rose-500/50 dark:hover:bg-rose-500/10" :disabled="props.loading" @click="emit('deleteDraft', row)">删除</button>
              </div>
            </td>
          </tr>
          <tr v-if="!draftRows.length">
            <td class="p-6 text-center text-accent-500 dark:text-accent-300" colspan="6">暂无平台草稿。可先从商品库或发布预检页推到平台草稿箱。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

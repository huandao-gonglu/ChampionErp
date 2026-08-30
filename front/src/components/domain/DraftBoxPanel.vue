<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import DraftLanguageSelect from '@/components/domain/DraftLanguageSelect.vue'
import DraftMarketSelect from '@/components/domain/DraftMarketSelect.vue'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'
import type { DraftIndexItem, Marketplace, MarketplaceOption, MarketplaceTargetSite, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  drafts: DraftIndexItem[]
  platformOptions: MarketplaceOption[]
  storeConfig: UnknownRecord
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: []
  edit: [item: DraftIndexItem]
  duplicateDraft: [item: DraftIndexItem]
  deleteDraft: [item: DraftIndexItem]
  deleteDrafts: [items: DraftIndexItem[]]
  updateLanguage: [item: DraftIndexItem, language: string]
  updateTargets: [item: DraftIndexItem, targets: MarketplaceTargetSite[]]
}>()

const platformFilter = ref<'all' | Marketplace>('all')
const draftScope = ref<'active' | 'published' | 'all'>('active')
const selectedDraftIds = ref<string[]>([])
const isPublishedDraft = (item: DraftIndexItem) => String(item.status || '').trim().toLowerCase() === 'published'
const allDraftRows = computed(() => props.drafts.filter((item) => {
  if (platformFilter.value !== 'all' && !draftMatchesPlatform(item, platformFilter.value)) return false
  return true
}))

const draftRows = computed(() => allDraftRows.value.filter((row) => {
  if (draftScope.value === 'published') return isPublishedDraft(row)
  if (draftScope.value === 'all') return true
  return !isPublishedDraft(row)
}))

const activeDraftCount = computed(() => allDraftRows.value.filter((row) => !isPublishedDraft(row)).length)
const publishedDraftCount = computed(() => allDraftRows.value.filter(isPublishedDraft).length)
const selectedDrafts = computed(() => props.drafts.filter((item) => selectedDraftIds.value.includes(draftIdOf(item))))
const selectedCount = computed(() => selectedDrafts.value.length)
const visibleDraftIds = computed(() => draftRows.value.map(draftIdOf).filter(Boolean))
const allChecked = computed(() => visibleDraftIds.value.length > 0 && visibleDraftIds.value.every((id) => selectedDraftIds.value.includes(id)))

function draftIdOf(item: DraftIndexItem) {
  return String(item.draftId || '').trim()
}

function draftMatchesPlatform(item: DraftIndexItem, platform: Marketplace) {
  if (item.platform === platform || (item.platforms || []).includes(platform)) return true
  return (item.targetSites || []).some((target) => target.platform === platform)
}

function toggleDraftSelection(item: DraftIndexItem, checked: boolean) {
  const draftId = draftIdOf(item)
  if (!draftId) return
  const exists = selectedDraftIds.value.includes(draftId)
  if (checked && !exists) selectedDraftIds.value.push(draftId)
  if (!checked) selectedDraftIds.value = selectedDraftIds.value.filter((id) => id !== draftId)
}

function draftRowKey(item: DraftIndexItem) {
  return draftIdOf(item) || `${item.sourceProductId || item.productId}:${item.platform}:${item.createdAt}`
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

watch(() => props.drafts.map(draftIdOf), (draftIds) => {
  const existingIds = new Set(draftIds)
  selectedDraftIds.value = selectedDraftIds.value.filter((id) => existingIds.has(id))
})
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">草稿箱</p>
        <h2 class="mt-2 card-title">草稿箱</h2>
        <p class="muted mt-1">商品从母库复制到这里后独立编辑，来源商品只作为关联和参考。</p>
      </div>
      <div class="grid w-full gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:w-auto">
        <select v-model="platformFilter" class="input sm:w-48">
          <option value="all">全部平台</option>
          <option v-for="platform in props.platformOptions" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
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

    <div class="mt-5 overflow-visible rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full table-fixed text-left text-xs">
        <colgroup>
          <col class="w-10" />
          <col />
          <col class="w-[88px]" />
          <col class="w-[150px]" />
          <col class="w-[88px]" />
          <col class="w-[14%]" />
          <col class="w-[176px]" />
        </colgroup>
        <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
          <tr class="whitespace-nowrap">
            <th class="p-2"><input class="size-4 rounded border-accent-300 text-primary-600" type="checkbox" aria-label="全选当前草稿" :checked="allChecked" :disabled="props.loading || !visibleDraftIds.length" @change="selectVisibleDrafts(($event.target as HTMLInputElement).checked)" /></th>
            <th class="p-2">商品</th>
            <th class="p-2">语言</th>
            <th class="p-2">市场</th>
            <th class="p-2">状态</th>
            <th class="p-2">来源</th>
            <th class="p-2">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="row in draftRows" :key="draftRowKey(row)" class="whitespace-nowrap align-middle transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60">
            <td class="p-2"><input class="size-4 rounded border-accent-300 text-primary-600" type="checkbox" aria-label="勾选草稿" :checked="selectedDraftIds.includes(draftIdOf(row))" :disabled="props.loading || !draftIdOf(row)" @change="toggleDraftSelection(row, ($event.target as HTMLInputElement).checked)" /></td>
            <td class="min-w-0 p-2">
              <div class="flex items-center gap-2">
                <img v-if="row.mainImage" :src="row.mainImage" class="size-8 shrink-0 rounded-md object-cover" />
                <div v-else class="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent-100 text-[9px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">无图</div>
                <div class="min-w-0">
                  <div class="truncate font-semibold text-accent-950 dark:text-white">{{ row.title || row.productTitle || row.productId || '-' }}</div>
                </div>
              </div>
            </td>
            <td class="p-2">
              <DraftLanguageSelect
                compact
                :language="row.language"
                :platform-options="props.platformOptions"
                :loading="props.loading"
                @update-language="emit('updateLanguage', row, $event)"
              />
            </td>
            <td class="p-2">
              <DraftMarketSelect
                compact
                :language="row.language"
                :target-sites="row.targetSites"
                :platform-options="props.platformOptions"
                :store-config="props.storeConfig"
                :loading="props.loading"
                @update-targets="emit('updateTargets', row, $event)"
              />
            </td>
            <td class="min-w-0 p-2"><span class="inline-flex max-w-full truncate" :class="statusBadgeClass(row.status)" :title="workflowStatusLabel(row.status)">{{ workflowStatusLabel(row.status) }}</span></td>
            <td class="min-w-0 p-2">
              <div class="truncate font-mono text-xs text-accent-600 dark:text-accent-300" :title="`${row.sourceProductId || row.productId || '-'} · ${row.sourcePlatform || '-'} · ${row.sourceUrl || '-'}`">{{ row.sourceProductId || row.productId || '-' }} · {{ row.sourcePlatform || '-' }} · {{ row.sourceUrl || '-' }}</div>
            </td>
            <td class="p-2">
              <div class="flex flex-nowrap gap-1">
                <button class="btn btn-primary shrink-0 whitespace-nowrap px-1.5 py-1 text-xs" :disabled="props.loading || !draftIdOf(row)" @click="emit('edit', row)">编辑</button>
                <button data-testid="duplicate-draft-button" class="btn btn-outline shrink-0 whitespace-nowrap px-1.5 py-1 text-xs" :disabled="props.loading || !draftIdOf(row)" title="创建一份独立草稿副本" @click="emit('duplicateDraft', row)">复制草稿</button>
                <button class="btn btn-outline shrink-0 whitespace-nowrap px-1.5 py-1 text-xs text-rose-600 hover:border-rose-300 hover:bg-rose-50 dark:text-rose-200 dark:hover:border-rose-500/50 dark:hover:bg-rose-500/10" :disabled="props.loading" @click="emit('deleteDraft', row)">删除</button>
              </div>
            </td>
          </tr>
          <tr v-if="!draftRows.length">
            <td class="p-6 text-center text-accent-500 dark:text-accent-300" colspan="7">暂无草稿。可先从商品库勾选商品并推到草稿箱。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { draftListTitle } from '@/utils/draftDisplayTitle'
import { computed, ref, watch } from 'vue'
import { PhMagnifyingGlass, PhX } from '@phosphor-icons/vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import { useAiChatStore } from '@/stores/aiChat'
import { useAiPageContext } from '@/composables/useAiPageContext'
import DraftLanguageSelect from '@/components/domain/DraftLanguageSelect.vue'
import DraftMarketSelect from '@/components/domain/DraftMarketSelect.vue'
import { formatDateTime } from '@/utils/format'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'
import type { DraftIndexItem, DraftPublishState, Marketplace, MarketplaceOption, MarketplaceTargetSite, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  drafts: DraftIndexItem[]
  platformOptions: MarketplaceOption[]
  storeConfig: UnknownRecord
  openingDraftId?: string
  refreshing?: boolean
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

const PUBLISHED_STATUSES = new Set(['published', 'real_publish_success', 'success', 'imported'])
const PUBLISHING_STATUSES = new Set(['pending', 'queued', 'running', 'retrying', 'publishing', 'pending_confirmation'])
const FAILED_STATUSES = new Set(['failed', 'publish_failed', 'real_publish_failed', 'not_ready', 'interrupted', 'outcome_unknown', 'needs_attention', 'partial'])

const chatStore = useAiChatStore()
const platformFilter = ref<'all' | Marketplace>('all')
const draftScope = ref<'unpublished' | 'publishing' | 'publish_failed' | 'published' | 'all'>('unpublished')
const search = ref('')
const selectedDraftIds = ref<string[]>([])

function draftPublishStateOf(item: DraftIndexItem): DraftPublishState {
  if (item.publishState === 'unpublished' || item.publishState === 'publishing' || item.publishState === 'publish_failed' || item.publishState === 'published') {
    return item.publishState
  }
  const status = String(item.status || '').trim().toLowerCase()
  const publishStatus = String(item.publishStatus || '').trim().toLowerCase()
  if (PUBLISHING_STATUSES.has(status) || PUBLISHING_STATUSES.has(publishStatus)) return 'publishing'
  if (FAILED_STATUSES.has(status) || FAILED_STATUSES.has(publishStatus)) return 'publish_failed'
  if (PUBLISHED_STATUSES.has(status) || PUBLISHED_STATUSES.has(publishStatus)) return 'published'
  return 'unpublished'
}

const allDraftRows = computed(() => {
  const term = search.value.trim().toLowerCase()
  return props.drafts.filter((item) => {
    if (platformFilter.value !== 'all' && !draftMatchesPlatform(item, platformFilter.value)) return false
    if (!term) return true
    return (draftListTitle(item) || '').toLowerCase().includes(term)
      || (item.title || '').toLowerCase().includes(term)
      || (item.draftId || '').toLowerCase().includes(term)
      || (item.productId || '').toLowerCase().includes(term)
      || (item.sourceProductId || '').toLowerCase().includes(term)
      || (item.sourceUrl || '').toLowerCase().includes(term)
  })
})

const scopeCounts = computed(() => {
  let unpublished = 0
  let publishing = 0
  let publishFailed = 0
  let published = 0
  for (const row of allDraftRows.value) {
    const state = draftPublishStateOf(row)
    if (state === 'published') published++
    else if (state === 'publishing') publishing++
    else if (state === 'publish_failed') publishFailed++
    else unpublished++
  }
  return {
    all: allDraftRows.value.length,
    unpublished,
    publishing,
    publish_failed: publishFailed,
    published,
  }
})

const draftRows = computed(() => allDraftRows.value.filter((row) => {
  if (draftScope.value === 'all') return true
  return draftPublishStateOf(row) === draftScope.value
}))

const selectedDrafts = computed(() => props.drafts.filter((item) => selectedDraftIds.value.includes(draftIdOf(item))))
const selectedCount = computed(() => selectedDrafts.value.length)
useAiPageContext(() => ({
  page: 'drafts', selected_draft_ids: selectedDrafts.value.map(draftIdOf).filter(Boolean),
}), 1)
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
  <section class="space-y-6">
    <PageHeader title="草稿箱" description="分别维护各平台的内容、SKU 选品和售价；规格资料可沿用商品或在草稿中单独调整。">
      <template #actions>
        <div class="flex flex-wrap items-center gap-2">
          <div class="relative w-56 sm:w-64">
            <PhMagnifyingGlass :size="15" aria-hidden="true" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-accent-400" />
            <input
              v-model="search"
              type="search"
              aria-label="搜索草稿标题、ID 或来源链接"
              placeholder="搜索标题、草稿/商品 ID…"
              class="input h-9 !rounded-xl !py-0 !pl-8 !pr-7 text-xs"
            />
            <button
              v-if="search"
              type="button"
              aria-label="清空草稿搜索"
              class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-accent-400 hover:bg-accent-100 hover:text-accent-700 dark:hover:bg-dark-800 dark:hover:text-accent-200"
              @click="search = ''"
            >
              <PhX :size="12" aria-hidden="true" />
            </button>
          </div>
          <select v-model="platformFilter" aria-label="按平台筛选草稿" class="input h-9 !w-auto !rounded-xl !py-0 !pl-3 !pr-8 text-xs">
            <option value="all">全部平台</option>
            <option v-for="platform in props.platformOptions" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
          </select>
          <button class="btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm" :disabled="props.loading || props.refreshing" @click="emit('refresh')">{{ props.refreshing ? '刷新中…' : '刷新' }}</button>
        </div>
      </template>
    </PageHeader>

    <div class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2.5 border-b border-accent-200/80 pb-2.5 dark:border-dark-700">
        <nav class="flex items-center gap-1 overflow-x-auto" aria-label="按发布状态筛选草稿">
          <button type="button" data-testid="draft-tab-unpublished" class="draft-filter-tab" :aria-pressed="draftScope === 'unpublished'" @click="draftScope = 'unpublished'">
            待发布 <span class="draft-tab-badge tabular-nums">{{ scopeCounts.unpublished }}</span>
          </button>
          <button type="button" data-testid="draft-tab-publishing" class="draft-filter-tab" :aria-pressed="draftScope === 'publishing'" @click="draftScope = 'publishing'">
            发布中 <span class="draft-tab-badge tabular-nums">{{ scopeCounts.publishing }}</span>
          </button>
          <button type="button" data-testid="draft-tab-failed" class="draft-filter-tab" :aria-pressed="draftScope === 'publish_failed'" @click="draftScope = 'publish_failed'">
            发布失败 <span class="draft-tab-badge tabular-nums">{{ scopeCounts.publish_failed }}</span>
          </button>
          <button type="button" data-testid="draft-tab-published" class="draft-filter-tab" :aria-pressed="draftScope === 'published'" @click="draftScope = 'published'">
            已发布 <span class="draft-tab-badge tabular-nums">{{ scopeCounts.published }}</span>
          </button>
          <button type="button" data-testid="draft-tab-all" class="draft-filter-tab" :aria-pressed="draftScope === 'all'" @click="draftScope = 'all'">
            全部草稿 <span class="draft-tab-badge tabular-nums">{{ scopeCounts.all }}</span>
          </button>
        </nav>
      </div>
      <div class="rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="text-sm text-accent-500 dark:text-accent-400">
            当前显示：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ draftRows.length }}</span> 条
            <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
            待发布：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ scopeCounts.unpublished }}</span>
            <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
            发布中：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ scopeCounts.publishing }}</span>
            <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
            发布失败：<span class="font-semibold text-rose-600 dark:text-rose-300">{{ scopeCounts.publish_failed }}</span>
            <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
            已发布：<span class="font-semibold text-emerald-600 dark:text-emerald-300">{{ scopeCounts.published }}</span>
            <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
            已勾选：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ selectedCount }}</span> 个
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button class="btn btn-primary px-3 py-1.5 text-xs" data-testid="drafts-ai-prepare" :disabled="props.loading || !selectedCount" @click="chatStore.prepareDrafts(selectedDraftIds)">AI 准备所选草稿（{{ selectedCount }}）</button>
            <button class="btn btn-outline px-3 py-1.5 text-xs text-rose-600 hover:border-rose-300 hover:bg-rose-50 dark:text-rose-200 dark:hover:border-rose-500/50 dark:hover:bg-rose-500/10" :disabled="props.loading || !selectedCount" @click="deleteSelectedDrafts">批量删除选中</button>
          </div>
        </div>
      </div>
      <p class="muted mt-3 text-sm">AI 准备包含共用主图和 SKU 套图生成；图片有手动修改时会先确认保留或重做，原图保留。</p>
      <div v-if="props.error" class="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
        {{ props.error }}
      </div>

      <div class="mt-5 overflow-visible rounded-lg border border-accent-200 dark:border-dark-700">
        <table class="w-full table-fixed text-left text-xs">
          <colgroup>
            <col class="w-10" />
            <col />
            <col class="w-[150px]" />
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
              <th class="p-2">创建时间</th>
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
                  <img v-if="row.mainImage" loading="lazy" decoding="async" :src="row.mainImage" class="size-8 shrink-0 rounded-md object-cover" />
                  <div v-else class="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent-100 text-[9px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">无图</div>
                  <div class="min-w-0">
                    <div class="truncate font-semibold text-accent-950 dark:text-white">{{ draftListTitle(row) || '-' }}</div>
                  </div>
                </div>
              </td>
              <td data-testid="draft-created-at" class="p-2 text-accent-600 dark:text-accent-300" :title="row.createdAt || '-'">{{ formatDateTime(row.createdAt) }}</td>
              <td class="p-2">
                <DraftLanguageSelect
                  compact
                  :language="row.language"
                  :platform-options="props.platformOptions"
                  :loading="props.loading || !props.platformOptions.length"
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
                  :loading="props.loading || !props.platformOptions.length"
                  @update-targets="emit('updateTargets', row, $event)"
                />
              </td>
              <td class="min-w-0 p-2"><span class="inline-flex max-w-full truncate" :class="statusBadgeClass(row.status)" :title="workflowStatusLabel(row.status)">{{ workflowStatusLabel(row.status) }}</span></td>
              <td class="min-w-0 p-2">
                <div class="truncate font-mono text-xs text-accent-600 dark:text-accent-300" :title="`${row.sourceProductId || row.productId || '-'} · ${row.sourcePlatform || '-'} · ${row.sourceUrl || '-'}`">{{ row.sourceProductId || row.productId || '-' }} · {{ row.sourcePlatform || '-' }} · {{ row.sourceUrl || '-' }}</div>
              </td>
              <td class="p-2">
                <div class="flex flex-nowrap gap-1">
                  <button class="btn btn-primary shrink-0 whitespace-nowrap px-1.5 py-1 text-xs" :disabled="props.loading || !draftIdOf(row)" @click="emit('edit', row)">{{ props.openingDraftId === row.draftId ? '打开中…' : '编辑' }}</button>
                  <button data-testid="duplicate-draft-button" class="btn btn-outline shrink-0 whitespace-nowrap px-1.5 py-1 text-xs" :disabled="props.loading || !draftIdOf(row)" title="创建一份独立草稿副本" @click="emit('duplicateDraft', row)">复制草稿</button>
                  <button class="btn btn-outline shrink-0 whitespace-nowrap px-1.5 py-1 text-xs text-rose-600 hover:border-rose-300 hover:bg-rose-50 dark:text-rose-200 dark:hover:border-rose-500/50 dark:hover:bg-rose-500/10" :disabled="props.loading" @click="emit('deleteDraft', row)">删除</button>
                </div>
              </td>
            </tr>
            <tr v-if="!draftRows.length">
              <td class="p-6 text-center text-accent-500 dark:text-accent-300" colspan="8">{{ props.refreshing ? '正在读取草稿列表…' : '暂无草稿。可先从采集箱勾选商品并推到草稿箱。' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.draft-filter-tab {
  @apply inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium text-accent-600 transition hover:bg-accent-100 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-white/5 dark:hover:text-white;
}
.draft-filter-tab[aria-pressed='true'] {
  @apply bg-primary-50 font-semibold text-primary-700 dark:bg-primary-500/15 dark:text-primary-300;
}
.draft-tab-badge {
  @apply inline-flex min-w-4 items-center justify-center rounded-full bg-accent-100 px-1.5 py-0.5 text-[11px] font-semibold leading-none text-accent-600 dark:bg-dark-700 dark:text-accent-300;
}
.draft-filter-tab[aria-pressed='true'] .draft-tab-badge {
  @apply bg-primary-100 text-primary-800 dark:bg-primary-500/25 dark:text-primary-200;
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  PhArrowsClockwise,
  PhArrowSquareOut,
  PhCaretDown,
  PhCheck,
  PhCheckCircle,
  PhCopy,
  PhDownloadSimple,
  PhImage,
  PhMagnifyingGlass,
  PhPackage,
  PhPaperPlaneTilt,
  PhPencilSimple,
  PhSpinnerGap,
  PhTrash,
  PhUploadSimple,
  PhWarningCircle,
  PhX,
} from '@phosphor-icons/vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { formatDateTime } from '@/utils/format'
import LibraryMarketSelector from './LibraryMarketSelector.vue'
import type { Marketplace, MarketplaceOption, MarketplaceTargetSite, ProductIndexItem, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  items: ProductIndexItem[]
  selectedIds: string[]
  platformOptions: MarketplaceOption[]
  storeConfig: UnknownRecord
  refreshing?: boolean
  loading: boolean
  error?: string
  notice?: string
}>()

const emit = defineEmits<{
  refresh: []
  edit: [item: ProductIndexItem]
  toggle: [productId: string, checked: boolean]
  selectAll: [checked: boolean, productIds: string[]]
  deleteItem: [item: ProductIndexItem]
  deleteSelected: []
  exportProducts: [productIds: string[]]
  importProducts: [file: File]
  clearNotice: []
  claim: [productIds: string[], targets: MarketplaceTargetSite[]]
}>()

const stageFilter = ref<'all' | 'unclaimed' | 'claimed' | 'published'>('all')
const collectFilter = ref<'all' | 'collected' | 'not_collected'>('all')
const search = ref('')
const platformFilter = ref('')
const sortBy = ref<'updated' | 'title'>('updated')
const selectedTargets = ref<MarketplaceTargetSite[]>([])
const copiedKey = ref('')
const deleteTarget = ref<ProductIndexItem | 'batch' | null>(null)
const actionMenuOpen = ref(false)
const actionMenuRef = ref<HTMLElement>()
const importFileInput = ref<HTMLInputElement>()
const localError = ref('')
const collator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
const doneStatuses = new Set(['done', 'success', 'ready', 'ready_to_publish', 'published', 'completed', 'true', 'real_publish_success', 'collected'])

const platformNames: Record<Marketplace, string> = {
  yandex: 'Yandex',
  ozon: 'Ozon',
  mercadolibre: '美客多',
}

function isCollected(item: ProductIndexItem) {
  return item.collectStatus === 'success' || item.collectStatus === 'collected'
}

function itemLibraryStage(item: ProductIndexItem): 'unclaimed' | 'claimed' | 'published' {
  if (item.libraryStage === 'unclaimed' || item.libraryStage === 'claimed' || item.libraryStage === 'published') {
    return item.libraryStage
  }
  if ((item.publishedCount || 0) > 0 || (item.publishedPlatforms?.length || 0) > 0) return 'published'
  if ((item.draftCount || 0) > 0 || (item.platforms?.length || 0) > 0) return 'claimed'
  return 'unclaimed'
}

function isPlatformPublished(item: ProductIndexItem, platform: Marketplace) {
  return (item.publishedPlatforms || []).includes(platform)
}

function itemDisplayPlatforms(item: ProductIndexItem): Marketplace[] {
  return Array.from(new Set([...(item.platforms || []), ...(item.publishedPlatforms || [])]))
}

const sourcePlatforms = computed(() => {
  const set = new Set<string>()
  for (const item of props.items) {
    if (item.sourcePlatform) set.add(item.sourcePlatform)
  }
  return [...set].sort()
})

const searchMatched = computed(() => {
  const term = search.value.trim().toLowerCase()
  return props.items.filter((item) => {
    if (platformFilter.value && item.sourcePlatform !== platformFilter.value) return false
    if (collectFilter.value === 'collected' && !isCollected(item)) return false
    if (collectFilter.value === 'not_collected' && isCollected(item)) return false
    if (!term) return true
    return (item.title || '').toLowerCase().includes(term)
      || (item.productId || '').toLowerCase().includes(term)
      || (item.sourceUrl || '').toLowerCase().includes(term)
      || (item.sourcePlatform || '').toLowerCase().includes(term)
  })
})

const tabCounts = computed(() => {
  let unclaimed = 0
  let claimed = 0
  let published = 0
  for (const item of searchMatched.value) {
    const stage = itemLibraryStage(item)
    if (stage === 'published') published++
    else if (stage === 'claimed') claimed++
    else unclaimed++
  }
  return { all: searchMatched.value.length, unclaimed, claimed, published }
})

const filteredItems = computed(() => {
  const rows = searchMatched.value.filter((item) => {
    if (stageFilter.value === 'all') return true
    return itemLibraryStage(item) === stageFilter.value
  })
  if (sortBy.value === 'title') {
    return [...rows].sort((a, b) => collator.compare(a.title || a.productId, b.title || b.productId))
  }
  return rows
})

const hasActiveFilters = computed(() => Boolean(search.value.trim() || stageFilter.value !== 'all' || collectFilter.value !== 'all' || platformFilter.value))
const selectedVisibleIds = computed(() => filteredItems.value.filter(item => props.selectedIds.includes(item.productId)).map(item => item.productId))
const allChecked = computed(() => filteredItems.value.length > 0 && selectedVisibleIds.value.length === filteredItems.value.length)
const selectedCount = computed(() => props.selectedIds.length)
const claimDisabledHint = computed(() => !selectedTargets.value.length ? '请先在上方勾选目标市场' : '')
const displayError = computed(() => localError.value || props.error || '')

function requestClaim(productIds: string[]) {
  if (props.loading || !productIds.length || !selectedTargets.value.length) return
  emit('claim', [...productIds], selectedTargets.value.map((target) => ({ ...target })))
}

function clearFilters() {
  search.value = ''
  stageFilter.value = 'all'
  collectFilter.value = 'all'
  platformFilter.value = ''
}

function triggerImportPicker() {
  actionMenuOpen.value = false
  localError.value = ''
  emit('clearNotice')
  if (importFileInput.value) {
    importFileInput.value.value = ''
    importFileInput.value.click()
  }
}

function triggerExportSelected() {
  actionMenuOpen.value = false
  localError.value = ''
  if (props.loading || !selectedCount.value) return
  emit('exportProducts', [...props.selectedIds])
}

function handleImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  localError.value = ''
  const name = String(file.name || '').toLowerCase()
  const type = String(file.type || '').toLowerCase()
  const isZip = name.endsWith('.zip')
    || type === 'application/zip'
    || type === 'application/x-zip-compressed'
    || type === 'application/octet-stream'
  if (!isZip) {
    localError.value = '不支持的导入文件格式，请选择从采集箱导出的商品 ZIP 压缩包。'
    return
  }
  emit('importProducts', file)
}

function handleDocumentPointerDown(event: PointerEvent) {
  if (!actionMenuOpen.value) return
  const target = event.target as Node | null
  if (target && actionMenuRef.value?.contains(target)) return
  actionMenuOpen.value = false
}

function handleDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && actionMenuOpen.value) {
    actionMenuOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  document.addEventListener('keydown', handleDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  document.removeEventListener('keydown', handleDocumentKeydown)
})

async function copyText(key: string, value: string) {
  if (!value) return
  try {
    await navigator.clipboard?.writeText(value)
    copiedKey.value = key
    window.setTimeout(() => {
      if (copiedKey.value === key) copiedKey.value = ''
    }, 1500)
  } catch {
    // 忽略剪贴板权限拒绝
  }
}

function safeHost(url: string) {
  try { return new URL(url).hostname } catch { return url }
}

function statusMeta(value: string): { label: string; tone: 'success' | 'danger' | 'warning' | 'info' | 'muted' } {
  const status = String(value || '').toLowerCase()
  if (doneStatuses.has(status)) return { label: '已采集', tone: 'success' }
  if (status === 'partial') return { label: '部分采集', tone: 'warning' }
  if (status === 'failed' || status === 'not_ready' || status === 'false') return { label: '采集失败', tone: 'danger' }
  if (status === 'pending' || status === 'running') return { label: '待处理', tone: 'info' }
  return { label: status ? status : '待补全', tone: 'muted' }
}

function confirmDeleteAction() {
  if (!deleteTarget.value) return
  if (deleteTarget.value === 'batch') emit('deleteSelected')
  else emit('deleteItem', deleteTarget.value)
  deleteTarget.value = null
}
</script>

<template>
  <section class="space-y-4">
    <PageHeader title="采集箱">
      <template #actions>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            class="btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm"
            :disabled="props.loading || props.refreshing"
            @click="emit('refresh')"
          >
            <PhArrowsClockwise :size="15" :class="{ 'animate-spin': props.refreshing }" aria-hidden="true" />{{ props.refreshing ? '刷新中…' : '刷新采集箱' }}
          </button>
          <button
            type="button"
            data-testid="library-claim-selected"
            class="btn btn-primary !min-h-9 !rounded-xl !px-3.5 !py-1.5 text-sm tabular-nums"
            :disabled="props.loading || !selectedCount || !selectedTargets.length"
            :title="claimDisabledHint || (!selectedCount ? '请先勾选要推送的商品' : '将已勾选商品推送到所选目标市场草稿')"
            @click="requestClaim(props.selectedIds)"
          >
            <PhPaperPlaneTilt :size="15" weight="fill" aria-hidden="true" />推到草稿<span v-if="selectedCount">（{{ selectedCount }} 个商品）</span>
          </button>
        </div>
      </template>
    </PageHeader>

    <div class="rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-800/70">
      <div
        v-if="displayError"
        role="alert"
        class="mb-3 flex items-start justify-between gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
      >
        <div class="flex min-w-0 items-start gap-2">
          <PhWarningCircle :size="17" weight="fill" class="mt-0.5 shrink-0 text-rose-500 dark:text-rose-400" aria-hidden="true" />
          <span class="min-w-0 break-words">{{ displayError }}</span>
        </div>
        <button v-if="localError" type="button" aria-label="关闭错误提示" class="shrink-0 rounded-md p-0.5 opacity-70 hover:opacity-100" @click="localError = ''">
          <PhX :size="14" aria-hidden="true" />
        </button>
      </div>

      <div
        v-if="props.notice && !displayError"
        role="status"
        class="mb-3 flex items-center justify-between gap-2 rounded-xl border border-primary-200/80 bg-primary-50/80 px-3.5 py-2 text-xs font-medium text-primary-800 dark:border-primary-500/25 dark:bg-primary-500/10 dark:text-primary-200"
      >
        <div class="flex min-w-0 items-center gap-2">
          <PhCheckCircle :size="15" weight="fill" class="shrink-0 text-primary-600 dark:text-primary-400" aria-hidden="true" />
          <span>{{ props.notice }}</span>
        </div>
        <button type="button" aria-label="关闭通知" class="shrink-0 rounded-md p-0.5 opacity-70 hover:opacity-100" @click="emit('clearNotice')">
          <PhX :size="13" aria-hidden="true" />
        </button>
      </div>

      <!-- 紧凑可折叠目标市场选择器 -->
      <LibraryMarketSelector
        v-model="selectedTargets"
        :platform-options="props.platformOptions"
        :store-config="props.storeConfig"
        :loading="props.loading"
      />

      <!-- 筛选标签 + 搜索 + 采集态/来源筛选 + 排序 + 操作下拉 -->
      <div class="mt-3.5 flex flex-wrap items-center justify-between gap-2.5 border-b border-accent-200/80 pb-2.5 dark:border-dark-700">
        <nav class="flex items-center gap-1 overflow-x-auto" aria-label="按草稿与发布状态筛选">
          <button type="button" data-testid="library-tab-all" class="filter-tab" :aria-pressed="stageFilter === 'all'" @click="stageFilter = 'all'">
            全部商品 <span class="tab-badge tabular-nums">{{ tabCounts.all }}</span>
          </button>
          <button type="button" data-testid="library-tab-unclaimed" class="filter-tab" :aria-pressed="stageFilter === 'unclaimed'" @click="stageFilter = 'unclaimed'">
            未推草稿 <span class="tab-badge tabular-nums">{{ tabCounts.unclaimed }}</span>
          </button>
          <button type="button" data-testid="library-tab-claimed" class="filter-tab" :aria-pressed="stageFilter === 'claimed'" @click="stageFilter = 'claimed'">
            已推草稿 <span class="tab-badge tabular-nums">{{ tabCounts.claimed }}</span>
          </button>
          <button type="button" data-testid="library-tab-published" class="filter-tab" :aria-pressed="stageFilter === 'published'" @click="stageFilter = 'published'">
            已发布 <span class="tab-badge tabular-nums">{{ tabCounts.published }}</span>
          </button>
        </nav>

        <div class="flex flex-wrap items-center gap-2">
          <div class="relative w-56 sm:w-64">
            <PhMagnifyingGlass :size="15" aria-hidden="true" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-accent-400" />
            <input
              v-model="search"
              type="search"
              aria-label="搜索商品标题、ID 或来源链接"
              placeholder="搜索标题、商品 ID 或链接…"
              class="input h-8 !rounded-xl !py-0 !pl-8 !pr-7 text-xs"
            />
            <button
              v-if="search"
              type="button"
              aria-label="清空搜索"
              class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-accent-400 hover:bg-accent-100 hover:text-accent-700 dark:hover:bg-dark-800 dark:hover:text-accent-200"
              @click="search = ''"
            >
              <PhX :size="12" aria-hidden="true" />
            </button>
          </div>

          <select
            v-model="collectFilter"
            aria-label="按采集状态筛选"
            class="input h-8 !w-auto !rounded-xl !py-0 !pl-2.5 !pr-7 text-xs"
          >
            <option value="all">全部采集态</option>
            <option value="collected">已采集</option>
            <option value="not_collected">待补全</option>
          </select>

          <select
            v-if="sourcePlatforms.length > 1"
            v-model="platformFilter"
            aria-label="按来源平台筛选"
            class="input h-8 !w-auto !rounded-xl !py-0 !pl-2.5 !pr-7 text-xs"
          >
            <option value="">全部来源</option>
            <option v-for="platform in sourcePlatforms" :key="platform" :value="platform">{{ platform }}</option>
          </select>

          <select
            v-model="sortBy"
            aria-label="商品排序方式"
            class="input h-8 !w-auto !rounded-xl !py-0 !pl-2.5 !pr-7 text-xs"
          >
            <option value="updated">默认排序</option>
            <option value="title">按标题排序</option>
          </select>

          <!-- 操作下拉（导入 / 导出 ZIP） -->
          <div ref="actionMenuRef" class="relative">
            <input
              ref="importFileInput"
              type="file"
              accept=".zip,application/zip,application/x-zip-compressed"
              class="sr-only"
              aria-label="选择导入的商品 ZIP 文件"
              @change="handleImportFile"
            />
            <button
              type="button"
              data-testid="library-actions-trigger"
              aria-haspopup="menu"
              :aria-expanded="actionMenuOpen"
              :disabled="props.loading"
              class="inline-flex h-8 items-center gap-1.5 rounded-xl border border-accent-300 bg-white px-3 text-xs font-semibold text-accent-700 transition hover:bg-accent-50 hover:text-accent-950 disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-600 dark:bg-dark-900 dark:text-accent-200 dark:hover:bg-dark-800 dark:hover:text-white"
              @click="actionMenuOpen = !actionMenuOpen"
            >
              <span>操作</span>
              <PhCaretDown :size="12" class="transition-transform" :class="actionMenuOpen ? 'rotate-180' : ''" aria-hidden="true" />
            </button>
            <div
              v-if="actionMenuOpen"
              role="menu"
              aria-label="采集箱商品导入导出操作"
              class="absolute right-0 z-20 mt-1.5 w-52 overflow-hidden rounded-xl border border-accent-200 bg-white p-1 shadow-lg dark:border-dark-600 dark:bg-dark-900"
            >
              <button
                type="button"
                role="menuitem"
                data-testid="library-action-import"
                :disabled="props.loading"
                class="flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition hover:bg-accent-100/80 disabled:cursor-not-allowed disabled:opacity-45 dark:hover:bg-white/5"
                @click="triggerImportPicker"
              >
                <PhUploadSimple :size="15" class="mt-0.5 shrink-0 text-primary-600 dark:text-primary-400" aria-hidden="true" />
                <span class="min-w-0">
                  <span class="block text-xs font-semibold text-accent-900 dark:text-white">导入</span>
                  <span class="block text-[11px] text-accent-500 dark:text-accent-400">从 ZIP 压缩包导入商品数据</span>
                </span>
              </button>
              <button
                type="button"
                role="menuitem"
                data-testid="library-action-export"
                :disabled="props.loading || !selectedCount"
                class="flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition hover:bg-accent-100/80 disabled:cursor-not-allowed disabled:opacity-45 dark:hover:bg-white/5"
                @click="triggerExportSelected"
              >
                <PhDownloadSimple :size="15" class="mt-0.5 shrink-0 text-primary-600 dark:text-primary-400" aria-hidden="true" />
                <span class="min-w-0">
                  <span class="block text-xs font-semibold text-accent-900 dark:text-white">导出<span v-if="selectedCount" class="ml-1 tabular-nums text-primary-700 dark:text-primary-300">（{{ selectedCount }}）</span></span>
                  <span class="block text-[11px] text-accent-500 dark:text-accent-400">
                    {{ selectedCount ? `导出已勾选的 ${selectedCount} 个商品为 ZIP` : '请先勾选要导出的商品' }}
                  </span>
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 勾选批量操作栏 -->
      <div
        v-if="selectedCount"
        class="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-primary-200/80 bg-primary-50/70 px-3 py-2 text-xs dark:border-primary-500/25 dark:bg-primary-500/10"
      >
        <div class="flex items-center gap-2 font-medium text-primary-900 dark:text-primary-200">
          <PhCheckCircle :size="15" weight="fill" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
          <span class="tabular-nums">已勾选 {{ selectedCount }} 个商品</span>
          <span v-if="!selectedTargets.length" class="text-amber-700 dark:text-amber-300">（请先在上方勾选目标市场即可批量推送）</span>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-white px-2.5 py-1 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:opacity-45 dark:border-rose-500/30 dark:bg-dark-800 dark:text-rose-300 dark:hover:bg-rose-500/15"
            :disabled="props.loading"
            @click="deleteTarget = 'batch'"
          >
            <PhTrash :size="13" aria-hidden="true" />批量删除
          </button>
          <button
            type="button"
            class="text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300"
            :disabled="props.loading"
            @click="emit('selectAll', false, props.selectedIds)"
          >
            清空勾选
          </button>
        </div>
      </div>

      <!-- 商品表格 -->
      <div class="mt-3 overflow-x-auto rounded-xl border border-accent-200/90 bg-white dark:border-dark-700 dark:bg-dark-900/30">
        <table class="w-full min-w-[740px] table-fixed text-left text-xs">
          <colgroup>
            <col class="w-10" />
            <col />
            <col class="w-[184px]" />
            <col class="w-[92px]" />
            <col class="w-[136px]" />
            <col class="w-[186px]" />
          </colgroup>
          <thead class="border-b border-accent-200/80 bg-accent-50/90 text-xs font-semibold text-accent-600 dark:border-dark-700 dark:bg-dark-900/80 dark:text-accent-300">
            <tr class="whitespace-nowrap">
              <th class="py-2.5 text-center">
                <input
                  class="size-4 cursor-pointer align-middle accent-primary-600"
                  type="checkbox"
                  aria-label="全选当前商品"
                  :checked="allChecked"
                  :indeterminate="selectedVisibleIds.length > 0 && !allChecked"
                  :disabled="props.loading || !filteredItems.length"
                  @change="emit('selectAll', ($event.target as HTMLInputElement).checked, filteredItems.map((item) => item.productId))"
                />
              </th>
              <th class="px-2 py-2.5">商品信息</th>
              <th class="px-2 py-2.5">来源链接</th>
              <th class="px-2 py-2.5">采集状态</th>
              <th class="px-2 py-2.5">更新时间</th>
              <th class="px-3 py-2.5 text-right">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-accent-200/70 dark:divide-dark-700/70">
            <tr
              v-for="item in filteredItems"
              :key="item.productId"
              class="group align-middle transition-colors hover:bg-accent-50/80 dark:hover:bg-white/[0.04]"
              :class="props.selectedIds.includes(item.productId) ? 'bg-primary-50/60 dark:bg-primary-500/10' : ''"
            >
              <td class="py-2.5 text-center">
                <input
                  class="size-4 cursor-pointer align-middle accent-primary-600"
                  type="checkbox"
                  aria-label="勾选商品"
                  :checked="props.selectedIds.includes(item.productId)"
                  :disabled="props.loading"
                  @change="emit('toggle', item.productId, ($event.target as HTMLInputElement).checked)"
                />
              </td>

              <!-- 商品信息：48px 主图 + 可点击标题 + ID 复制 + 已建草稿平台 -->
              <td class="min-w-0 px-2 py-2.5">
                <div class="flex items-center gap-3">
                  <a
                    v-if="item.mainImage"
                    :href="item.mainImage"
                    target="_blank"
                    rel="noopener noreferrer"
                    title="点击查看商品主图"
                    class="shrink-0 overflow-hidden rounded-xl border border-accent-200/80 bg-accent-50 transition hover:opacity-90 dark:border-dark-600 dark:bg-dark-800"
                  >
                    <img :src="item.mainImage" loading="lazy" decoding="async" referrerpolicy="no-referrer" :alt="item.title || '商品主图'" class="size-12 object-cover" />
                  </a>
                  <div v-else class="grid size-12 shrink-0 place-items-center rounded-xl border border-accent-200/80 bg-accent-100/70 text-accent-400 dark:border-dark-700 dark:bg-dark-800">
                    <PhImage :size="18" aria-hidden="true" />
                  </div>

                  <div class="min-w-0 flex-1">
                    <button
                      type="button"
                      class="block max-w-full truncate text-left text-[13px] font-semibold text-accent-950 transition hover:text-primary-700 dark:text-white dark:hover:text-primary-300"
                      :title="item.title || item.productId"
                      :disabled="props.loading"
                      @click="emit('edit', item)"
                    >
                      {{ item.title || '未命名商品' }}
                    </button>
                    <div class="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-accent-500 dark:text-accent-400">
                      <span class="truncate font-mono">{{ item.productId }}</span>
                      <button
                        v-if="item.productId"
                        type="button"
                        class="inline-flex items-center rounded p-0.5 text-accent-400 transition hover:bg-accent-200/60 hover:text-accent-800 dark:hover:bg-white/10 dark:hover:text-white"
                        :title="copiedKey === `id:${item.productId}` ? '已复制商品 ID' : '复制商品 ID'"
                        :aria-label="`复制商品 ID ${item.productId}`"
                        @click="copyText(`id:${item.productId}`, item.productId)"
                      >
                        <PhCheck v-if="copiedKey === `id:${item.productId}`" :size="11" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
                        <PhCopy v-else :size="11" aria-hidden="true" />
                      </button>
                      <span
                        v-for="platform in itemDisplayPlatforms(item)"
                        :key="platform"
                        class="rounded px-1.5 py-0.5 text-[10px] font-medium"
                        :class="isPlatformPublished(item, platform)
                          ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300'
                          : 'bg-accent-100 text-accent-600 dark:bg-dark-700 dark:text-accent-200'"
                        :title="isPlatformPublished(item, platform) ? `${platformNames[platform] || platform} 已发布` : `${platformNames[platform] || platform} 已建草稿`"
                      >
                        {{ platformNames[platform] || platform }}<template v-if="isPlatformPublished(item, platform)"> · 已发布</template>
                      </span>
                      <span
                        v-if="itemLibraryStage(item) === 'unclaimed'"
                        class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
                      >
                        未推草稿
                      </span>
                    </div>
                  </div>
                </div>
              </td>

              <!-- 来源链接：平台标签 + 可点击短链 + 一键复制 -->
              <td class="min-w-0 px-2 py-2.5">
                <div class="flex items-center gap-1.5">
                  <span class="inline-flex max-w-full truncate rounded-md bg-accent-100 px-1.5 py-0.5 text-[11px] font-medium text-accent-700 dark:bg-dark-700 dark:text-accent-200">
                    {{ item.sourcePlatform || (item.sourceUrl ? safeHost(item.sourceUrl) : '手工创建') }}
                  </span>
                </div>
                <div v-if="item.sourceUrl" class="mt-1 flex items-center gap-1">
                  <a
                    :href="item.sourceUrl"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="inline-flex min-w-0 flex-1 items-center gap-1 truncate font-mono text-[11px] text-accent-500 transition hover:text-primary-700 hover:underline dark:text-accent-400 dark:hover:text-primary-300"
                    :title="item.sourceUrl"
                  >
                    <span class="truncate">{{ item.sourceUrl.replace(/^https?:\/\//i, '') }}</span>
                    <PhArrowSquareOut :size="11" class="shrink-0 opacity-70" aria-hidden="true" />
                  </a>
                  <button
                    type="button"
                    class="shrink-0 rounded p-0.5 text-accent-400 transition hover:bg-accent-200/60 hover:text-accent-800 dark:hover:bg-white/10 dark:hover:text-white"
                    :title="copiedKey === `url:${item.productId}` ? '已复制链接' : '复制来源链接'"
                    :aria-label="`复制来源链接 ${item.title || item.productId}`"
                    @click="copyText(`url:${item.productId}`, item.sourceUrl)"
                  >
                    <PhCheck v-if="copiedKey === `url:${item.productId}`" :size="11" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
                    <PhCopy v-else :size="11" aria-hidden="true" />
                  </button>
                </div>
                <span v-else class="mt-1 block text-[11px] text-accent-400 dark:text-accent-500">暂无来源链接</span>
              </td>

              <!-- 采集状态（中文化胶囊 + 彩色圆点） -->
              <td class="px-2 py-2.5">
                <span class="library-status" :data-tone="statusMeta(item.collectStatus).tone" :title="item.collectStatus || '待补全'">
                  <span class="status-dot" aria-hidden="true" />
                  {{ statusMeta(item.collectStatus).label }}
                </span>
              </td>

              <!-- 更新时间 -->
              <td class="px-2 py-2.5 text-[11px] tabular-nums text-accent-500 dark:text-accent-400">
                {{ formatDateTime(item.updatedAt || item.createdAt) }}
              </td>

              <!-- 行操作 -->
              <td class="px-3 py-2.5 text-right">
                <div class="inline-flex flex-nowrap items-center justify-end gap-1.5">
                  <button
                    type="button"
                    class="row-btn"
                    :disabled="props.loading"
                    title="编辑商品"
                    @click="emit('edit', item)"
                  >
                    <PhPencilSimple :size="13" aria-hidden="true" />编辑
                  </button>
                  <button
                    type="button"
                    data-testid="library-claim-row"
                    class="row-btn row-btn--primary"
                    :disabled="props.loading || !selectedTargets.length"
                    :title="claimDisabledHint || '推送该商品到已选目标市场草稿'"
                    @click="requestClaim([item.productId])"
                  >
                    推到草稿
                  </button>
                  <button
                    type="button"
                    class="row-btn-icon"
                    :disabled="props.loading"
                    title="删除商品"
                    :aria-label="`删除 ${item.title || item.productId}`"
                    @click="deleteTarget = item"
                  >
                    <PhTrash :size="13" aria-hidden="true" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- 空状态 -->
        <div v-if="!filteredItems.length" class="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
          <div class="grid size-11 place-items-center rounded-2xl bg-accent-100 text-accent-400 dark:bg-dark-800">
            <PhSpinnerGap v-if="props.refreshing && !props.items.length" :size="22" class="animate-spin text-primary-500" aria-hidden="true" />
            <PhPackage v-else :size="22" aria-hidden="true" />
          </div>
          <p class="text-sm font-semibold text-accent-800 dark:text-accent-100">
            {{ props.refreshing && !props.items.length ? '正在读取采集箱…' : props.items.length ? '没有匹配的商品' : '采集箱暂无商品' }}
          </p>
          <p v-if="!props.refreshing || props.items.length" class="max-w-xs text-xs leading-relaxed text-accent-500 dark:text-accent-400">
            {{ props.items.length ? '尝试更换搜索关键词、来源平台或状态标签。' : '在「采集」页采集商品或通过右上「操作 → 导入」导入 ZIP 压缩包后，商品会自动保存到这里。' }}
          </p>
          <button
            v-if="props.items.length && hasActiveFilters"
            type="button"
            class="btn btn-outline mt-1 !min-h-8 !rounded-xl !px-3 !py-1.5 text-xs"
            @click="clearFilters"
          >
            清空筛选条件
          </button>
        </div>
      </div>

      <!-- 底部统计摘要 -->
      <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-accent-500 tabular-nums dark:text-accent-400">
        <span>共显示 <strong class="font-semibold text-accent-800 dark:text-accent-100">{{ filteredItems.length }}</strong> / {{ props.items.length }} 件商品</span>
        <span v-if="selectedTargets.length" class="text-primary-700 dark:text-primary-300">
          推送目标已就绪（{{ selectedTargets.length }} 个市场）
        </span>
      </div>
    </div>

    <!-- 删除确认弹窗 -->
    <WorkspaceDialog
      v-if="deleteTarget"
      :open="true"
      :title="deleteTarget === 'batch' ? '批量删除商品' : '删除商品'"
      width="30rem"
      :close-disabled="props.loading"
      @close="deleteTarget = null"
    >
      <template #close>取消</template>
      <template #header-actions>
        <button
          type="button"
          class="btn !min-h-9 !rounded-xl bg-rose-600 !px-3.5 !py-1.5 text-sm text-white hover:bg-rose-700 dark:bg-rose-500 dark:hover:bg-rose-400"
          :disabled="props.loading"
          @click="confirmDeleteAction"
        >
          确认删除
        </button>
      </template>
      <div class="space-y-3 text-sm">
        <div class="flex items-start gap-2.5 rounded-xl border border-rose-200/80 bg-rose-50/70 p-3 text-rose-900 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-200">
          <PhTrash :size="17" class="mt-0.5 shrink-0 text-rose-600 dark:text-rose-400" aria-hidden="true" />
          <p v-if="deleteTarget === 'batch'">
            确认从采集箱移除已勾选的 <strong>{{ selectedCount }}</strong> 个商品吗？关联的草稿与商品主档将安全保留；未建草稿的商品会被彻底删除。
          </p>
          <p v-else>
            确认从采集箱移除商品「<strong>{{ deleteTarget.title || deleteTarget.productId || '该商品' }}</strong>」吗？关联的草稿与商品主档将安全保留；未建草稿的商品会被彻底删除。
          </p>
        </div>
      </div>
    </WorkspaceDialog>
  </section>
</template>

<style scoped>
.filter-tab {
  @apply inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium text-accent-600 transition hover:bg-accent-100 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-white/5 dark:hover:text-white;
}
.filter-tab[aria-pressed='true'] {
  @apply bg-primary-50 font-semibold text-primary-700 dark:bg-primary-500/15 dark:text-primary-300;
}
.tab-badge {
  @apply inline-flex min-w-4 items-center justify-center rounded-full bg-accent-100 px-1.5 py-0.5 text-[11px] font-semibold leading-none text-accent-600 dark:bg-dark-700 dark:text-accent-300;
}
.filter-tab[aria-pressed='true'] .tab-badge {
  @apply bg-primary-100 text-primary-800 dark:bg-primary-500/25 dark:text-primary-200;
}
.library-status {
  @apply inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-accent-100 px-2 py-0.5 text-[11px] font-medium text-accent-700 dark:bg-dark-700 dark:text-accent-200;
}
.status-dot {
  @apply size-1.5 shrink-0 rounded-full bg-accent-400;
}
.library-status[data-tone='success'] {
  @apply bg-primary-100/90 text-primary-800 dark:bg-primary-500/15 dark:text-primary-300;
}
.library-status[data-tone='success'] .status-dot {
  @apply bg-primary-500;
}
.library-status[data-tone='danger'] {
  @apply bg-rose-100/90 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300;
}
.library-status[data-tone='danger'] .status-dot {
  @apply bg-rose-500;
}
.library-status[data-tone='warning'] {
  @apply bg-amber-100/90 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300;
}
.library-status[data-tone='warning'] .status-dot {
  @apply bg-amber-500;
}
.library-status[data-tone='info'] {
  @apply bg-blue-100/90 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300;
}
.library-status[data-tone='info'] .status-dot {
  @apply bg-blue-500;
}
.row-btn {
  @apply inline-flex items-center gap-1 whitespace-nowrap rounded-lg border border-accent-200 bg-white px-2.5 py-1 text-xs font-semibold text-accent-700 transition hover:border-accent-300 hover:bg-accent-50 hover:text-accent-950 disabled:cursor-not-allowed disabled:opacity-45 dark:border-dark-600 dark:bg-dark-800 dark:text-accent-200 dark:hover:bg-dark-700;
}
.row-btn--primary {
  @apply border-primary-300/90 bg-primary-50/70 text-primary-800 hover:border-primary-400 hover:bg-primary-100 dark:border-primary-500/35 dark:bg-primary-500/15 dark:text-primary-200 dark:hover:bg-primary-500/25;
}
.row-btn-icon {
  @apply grid size-7 shrink-0 place-items-center rounded-lg border border-accent-200 bg-white text-accent-500 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-45 dark:border-dark-600 dark:bg-dark-800 dark:text-accent-400 dark:hover:border-rose-500/30 dark:hover:bg-rose-500/10 dark:hover:text-rose-300;
}
</style>

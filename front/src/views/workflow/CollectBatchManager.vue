<script setup lang="ts">
import { computed } from 'vue'
import {
  PhArrowsClockwise,
  PhArrowsDownUp,
  PhBrowsers,
  PhCheckCircle,
  PhLightning,
  PhListBullets,
  PhMagnifyingGlass,
  PhPlay,
  PhPlus,
  PhSlidersHorizontal,
  PhSortAscending,
  PhSortDescending,
  PhSpinnerGap,
  PhTrash,
  PhX,
} from '@phosphor-icons/vue'
import { collectBatchStatusLabels } from '@/utils/collectQueue'
import { formatDateTime } from '@/utils/format'
import type { CollectionLink, LinkMode } from '@/api/workflow/collectionLinks'
import type { CollectBatchStatus } from '@/types/workflow'

const props = defineProps<{
  rows: CollectionLink[]
  total: number
  counts: Partial<Record<CollectBatchStatus, number>>
  selectedId: string
  checkedIds: ReadonlySet<string>
  busy: boolean
  ready: boolean
}>()
const emit = defineEmits<{
  select: [id: string]
  check: [id: string, checked: boolean]
  checkAll: [checked: boolean]
  clearChecks: []
  run: [row: CollectionLink]
  runBatch: [rows: CollectionLink[]]
  changeModeChecked: []
  removeChecked: []
  requestAdd: []
  openProduct: [id: string]
}>()
const search = defineModel<string>('search', { required: true })
const status = defineModel<CollectBatchStatus | ''>('status', { required: true })
const modeFilter = defineModel<LinkMode | ''>('modeFilter', { default: '' })
const sort = defineModel<'none' | 'ascending' | 'descending'>('sort', { required: true })
const timeSort = defineModel<'newest' | 'oldest'>('timeSort', { default: 'newest' })

const matchCount = computed(() => Object.values(props.counts).reduce((sum, count) => sum + count, 0))
const checkedRows = computed(() => props.rows.filter(row => props.checkedIds.has(row.id)))
const checkedCount = computed(() => checkedRows.value.length)
const allChecked = computed(() => checkedCount.value > 0 && checkedCount.value === props.rows.length)
const checkedPendingRows = computed(() => checkedRows.value.filter(row => row.status === 'pending' || row.status === 'waiting_verification'))
const checkedFailedRows = computed(() => checkedRows.value.filter(row => row.status === 'failed'))
const checkedEditableCount = computed(() => checkedRows.value.filter(row => row.status !== 'running').length)
const hasActiveFilters = computed(() => Boolean(search.value.trim() || status.value || modeFilter.value))

function cycleSort() {
  sort.value = sort.value === 'none' ? 'ascending' : sort.value === 'ascending' ? 'descending' : 'none'
}
function toggleTimeSort() {
  if (sort.value !== 'none') {
    sort.value = 'none'
    timeSort.value = 'newest'
    return
  }
  timeSort.value = timeSort.value === 'newest' ? 'oldest' : 'newest'
}
function hostname(url: string) {
  try { return new URL(url).hostname } catch { return url }
}
function clearFilters() {
  search.value = ''
  status.value = ''
  modeFilter.value = ''
}
function statusCountTone(key: CollectBatchStatus, count: number) {
  if (!count) return 'tab-badge--zero'
  if (key === 'failed') return 'tab-badge--danger'
  if (key === 'waiting_verification' || key === 'unconfirmed') return 'tab-badge--warning'
  if (key === 'running') return 'tab-badge--info'
  if (key === 'success') return 'tab-badge--success'
  return ''
}
</script>

<template>
  <section class="min-w-0">
    <!-- 紧凑单层筛选与搜索栏（搜索框为组件内首个 input） -->
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-accent-200/80 pb-2 dark:border-dark-700">
      <nav class="flex min-w-0 items-center gap-0.5 overflow-x-auto text-sm" aria-label="按状态筛选">
        <button type="button" class="filter-tab" :aria-pressed="status === ''" @click="status = ''">
          全部 <span class="tab-badge tabular-nums">{{ matchCount }}</span>
        </button>
        <button
          v-for="(label, key) in collectBatchStatusLabels"
          :key="key"
          type="button"
          class="filter-tab"
          :aria-pressed="status === key"
          @click="status = key"
        >
          {{ label }} <span class="tab-badge tabular-nums" :class="statusCountTone(key, counts[key] || 0)">{{ counts[key] || 0 }}</span>
        </button>
      </nav>

      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <div class="relative w-48 sm:w-56">
          <PhMagnifyingGlass aria-hidden="true" :size="15" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-accent-400" />
          <input
            v-model="search"
            type="search"
            aria-label="搜索 URL 或商品名称"
            placeholder="搜索 URL 或商品名称"
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
          v-model="modeFilter"
          aria-label="按采集方式筛选"
          class="input h-8 !w-auto !rounded-xl !py-0 !pl-2.5 !pr-7 text-xs"
        >
          <option value="">全部方式</option>
          <option value="browser">浏览器</option>
          <option value="api">1688 API</option>
        </select>
      </div>
    </div>

    <!-- 勾选批量操作栏（统一仅作用于当前筛选中的勾选项） -->
    <div
      v-if="checkedIds.size"
      class="mt-2.5 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-primary-200/80 bg-primary-50/70 px-3 py-1.5 text-xs dark:border-primary-500/25 dark:bg-primary-500/10"
    >
      <div class="flex items-center gap-2 font-medium text-primary-900 dark:text-primary-200">
        <PhCheckCircle :size="15" weight="fill" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
        <span class="tabular-nums">当前筛选已选 {{ checkedCount }} 条</span>
        <span v-if="checkedCount !== checkedIds.size" class="text-primary-700/75 tabular-nums dark:text-primary-300/75">
          （另 {{ checkedIds.size - checkedCount }} 条不在当前筛选中，不受批量操作影响）
        </span>
      </div>
      <div class="flex flex-wrap items-center gap-1.5">
        <button
          v-if="checkedPendingRows.length"
          type="button"
          class="selection-btn selection-btn--primary tabular-nums"
          :disabled="busy"
          @click="emit('runBatch', checkedPendingRows)"
        >
          <PhPlay :size="12" weight="fill" aria-hidden="true" />采集已选（{{ checkedPendingRows.length }}）
        </button>
        <button
          v-if="checkedFailedRows.length"
          type="button"
          class="selection-btn tabular-nums"
          :disabled="busy"
          @click="emit('runBatch', checkedFailedRows)"
        >
          <PhArrowsClockwise :size="13" aria-hidden="true" />重试已选（{{ checkedFailedRows.length }}）
        </button>
        <button
          type="button"
          class="selection-btn"
          :disabled="busy || !checkedEditableCount"
          @click="emit('changeModeChecked')"
        >
          <PhSlidersHorizontal :size="13" aria-hidden="true" />改方式
        </button>
        <button
          type="button"
          class="selection-btn selection-btn--danger"
          :disabled="busy || !checkedEditableCount"
          @click="emit('removeChecked')"
        >
          <PhTrash :size="13" aria-hidden="true" />批量移除
        </button>
        <button type="button" class="row-action px-1" @click="emit('clearChecks')">清空勾选</button>
      </div>
    </div>

    <!-- 链接列表表格（优化 1280-1440px 固定列宽，把空间留给商品 / URL） -->
    <div class="mt-2.5 overflow-x-auto rounded-xl border border-accent-200/90 bg-white dark:border-dark-700 dark:bg-dark-900/30">
      <table class="w-full min-w-[27rem] table-fixed text-left text-sm sm:min-w-[31rem]">
        <caption class="sr-only">采集链接和状态</caption>
        <thead class="bg-accent-50/90 text-xs font-semibold text-accent-600 dark:bg-dark-900/80 dark:text-accent-300">
          <tr>
            <th scope="col" class="w-9 py-2.5 text-center">
              <input
                type="checkbox"
                class="link-check"
                aria-label="全选当前筛选结果"
                :checked="allChecked"
                :indeterminate="checkedCount > 0 && !allChecked"
                :disabled="!rows.length"
                @change="emit('checkAll', ($event.target as HTMLInputElement).checked)"
              />
            </th>
            <th scope="col" class="px-2 py-2.5" :aria-sort="sort">
              <button
                type="button"
                class="inline-flex max-w-full items-center gap-1 rounded-md px-1 py-0.5 text-left transition hover:bg-accent-200/60 hover:text-accent-950 dark:hover:bg-white/10 dark:hover:text-white"
                aria-label="按商品名称或 URL 排序"
                @click="cycleSort"
              >
                <span class="truncate">商品 / URL</span>
                <component
                  :is="sort === 'ascending' ? PhSortAscending : sort === 'descending' ? PhSortDescending : PhArrowsDownUp"
                  :size="13"
                  class="shrink-0"
                  :class="sort !== 'none' ? 'text-primary-600 dark:text-primary-400' : 'opacity-50'"
                  aria-hidden="true"
                />
              </button>
            </th>
            <th scope="col" class="hidden w-[70px] py-2.5 sm:table-cell">方式</th>
            <th scope="col" class="w-[96px] py-2.5">状态</th>
            <th scope="col" class="w-[132px] py-2.5 pr-1.5">
              <button
                type="button"
                class="inline-flex max-w-full items-center gap-1 rounded-md px-1 py-0.5 text-left transition hover:bg-accent-200/60 hover:text-accent-950 dark:hover:bg-white/10 dark:hover:text-white"
                :aria-label="timeSort === 'newest' ? '按添加时间排序（当前最新在前）' : '按添加时间排序（当前最早在前）'"
                :title="timeSort === 'newest' ? '当前最新在前，点击切为最早在前' : '当前最早在前，点击切为最新在前'"
                @click="toggleTimeSort"
              >
                <span>添加时间</span>
                <component
                  :is="sort === 'none' ? (timeSort === 'newest' ? PhSortDescending : PhSortAscending) : PhArrowsDownUp"
                  :size="13"
                  class="shrink-0"
                  :class="sort === 'none' ? 'text-primary-600 dark:text-primary-400' : 'opacity-45'"
                  aria-hidden="true"
                />
              </button>
            </th>
            <th scope="col" class="w-[72px] py-2.5 pr-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="group cursor-pointer transition-colors hover:bg-accent-50/80 dark:hover:bg-white/[0.04]"
            :class="{ 'selected-row': selectedId === row.id }"
            @click="emit('select', row.id)"
          >
            <td class="py-2.5 text-center">
              <input
                type="checkbox"
                class="link-check"
                :aria-label="`选择 ${row.title || row.url}`"
                :checked="checkedIds.has(row.id)"
                @click.stop
                @change="emit('check', row.id, ($event.target as HTMLInputElement).checked)"
              />
            </td>
            <td class="px-2 py-2.5">
              <button
                type="button"
                class="block w-full text-left focus:outline-none"
                data-collect-row
                :aria-pressed="selectedId === row.id"
                @click.stop="emit('select', row.id)"
              >
                <strong class="block truncate text-[13px] font-semibold text-accent-900 transition-colors group-hover:text-primary-800 dark:text-accent-50 dark:group-hover:text-primary-200">
                  {{ row.title || hostname(row.url) }}
                </strong>
                <span class="mt-0.5 block truncate text-[11px] text-accent-500 dark:text-accent-400" :title="row.url">{{ row.url }}</span>
                <span
                  v-if="row.error && (row.status === 'failed' || row.status === 'unconfirmed')"
                  class="mt-0.5 block truncate text-[11px] font-medium text-rose-600 dark:text-rose-400"
                  :title="row.error"
                >
                  {{ row.error }}
                </span>
              </button>
            </td>
            <td class="hidden py-2.5 sm:table-cell">
              <span class="mode-chip" :data-mode="row.mode">
                <PhLightning v-if="row.mode === 'api'" :size="11" aria-hidden="true" />
                <PhBrowsers v-else :size="11" aria-hidden="true" />
                {{ row.mode === 'api' ? 'API' : '浏览器' }}
              </span>
            </td>
            <td class="py-2.5">
              <span class="link-status" :data-status="row.status">
                <span class="status-dot" aria-hidden="true" />
                {{ collectBatchStatusLabels[row.status] }}
              </span>
            </td>
            <td class="py-2.5 pr-1.5 text-[11px] leading-tight tabular-nums text-accent-500 dark:text-accent-400">
              <time v-if="row.createdAt" :datetime="row.createdAt">{{ formatDateTime(row.createdAt) }}</time>
              <span v-else>-</span>
            </td>
            <td class="py-2.5 pr-2 text-right">
              <button
                v-if="row.status === 'success' && row.productId"
                type="button"
                class="row-action-pill row-action-pill--success"
                :disabled="busy"
                @click.stop="emit('openProduct', row.productId)"
              >
                查看商品
              </button>
              <button
                v-else-if="row.status !== 'running'"
                type="button"
                class="row-action-pill"
                :class="row.status === 'failed' ? 'row-action-pill--danger' : ''"
                :disabled="busy"
                @click.stop="emit('run', row)"
              >
                {{ row.status === 'pending' ? '采集' : row.status === 'waiting_verification' ? '继续' : '重试' }}
              </button>
              <span v-else class="inline-flex items-center gap-1 text-[11px] font-medium text-blue-600 dark:text-blue-400">
                <PhSpinnerGap :size="12" class="animate-spin" aria-hidden="true" />进行中
              </span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 空状态 -->
      <div v-if="!rows.length" class="flex flex-col items-center justify-center gap-2.5 px-6 py-12 text-center">
        <div class="grid size-11 place-items-center rounded-2xl bg-accent-100 text-accent-400 dark:bg-dark-800 dark:text-accent-400">
          <PhSpinnerGap v-if="!ready" :size="22" class="animate-spin text-primary-500" aria-hidden="true" />
          <PhListBullets v-else :size="22" aria-hidden="true" />
        </div>
        <p class="text-sm font-semibold text-accent-800 dark:text-accent-100">
          {{ !ready ? '正在读取采集列表' : total ? '没有匹配的链接' : '还没有采集链接' }}
        </p>
        <p v-if="ready" class="max-w-xs text-xs leading-relaxed text-accent-500 dark:text-accent-400">
          {{ total ? '尝试更换搜索关键词、采集方式或状态标签。' : '在上方粘贴商品链接或 1688 分享文案即可加入队列，粘贴多条时会自动展开批量输入框。' }}
        </p>
        <div v-if="ready" class="mt-1">
          <button
            v-if="total && hasActiveFilters"
            type="button"
            class="btn btn-outline !min-h-8 !rounded-xl !px-3 !py-1.5 text-xs"
            @click="clearFilters"
          >
            清空筛选条件
          </button>
          <button
            v-else-if="!total"
            type="button"
            class="btn btn-primary !min-h-8 !rounded-xl !px-3.5 !py-1.5 text-xs"
            :disabled="busy"
            @click="emit('requestAdd')"
          >
            <PhPlus :size="14" weight="bold" aria-hidden="true" />添加第一条链接
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.filter-tab {
  @apply inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg px-2 py-1.5 text-xs font-medium text-accent-600 transition hover:bg-accent-100 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-white/5 dark:hover:text-white;
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
.tab-badge--zero {
  @apply bg-transparent px-0.5 font-normal text-accent-400 dark:bg-transparent dark:text-accent-500;
}
.tab-badge--danger {
  @apply bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300;
}
.tab-badge--warning {
  @apply bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300;
}
.tab-badge--info {
  @apply bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300;
}
.tab-badge--success {
  @apply bg-primary-100/80 text-primary-800 dark:bg-primary-500/15 dark:text-primary-300;
}
tbody td {
  @apply border-t border-accent-200/80 dark:border-dark-700/80;
}
.selected-row,
.selected-row:hover {
  @apply bg-primary-50/90 dark:bg-primary-500/10;
}
.selected-row > td:first-child {
  @apply shadow-[inset_3px_0_0_rgb(var(--color-primary-500))];
}
.row-action {
  @apply whitespace-nowrap text-xs font-semibold text-primary-700 hover:underline disabled:opacity-50 dark:text-primary-300;
}
.row-action-pill {
  @apply inline-flex items-center justify-center whitespace-nowrap rounded-lg border border-accent-200 bg-white px-2 py-1 text-xs font-semibold text-primary-700 transition hover:border-primary-300 hover:bg-primary-50 disabled:cursor-not-allowed disabled:opacity-45 dark:border-dark-600 dark:bg-dark-800 dark:text-primary-300 dark:hover:border-primary-500/40 dark:hover:bg-primary-500/15;
}
.row-action-pill--success {
  @apply border-primary-200/90 bg-primary-50/70 text-primary-800 hover:bg-primary-100 dark:border-primary-500/30 dark:bg-primary-500/15 dark:text-primary-200 dark:hover:bg-primary-500/25;
}
.row-action-pill--danger {
  @apply border-rose-200 text-rose-700 hover:border-rose-300 hover:bg-rose-50 dark:border-rose-500/30 dark:text-rose-300 dark:hover:bg-rose-500/15;
}
.selection-btn {
  @apply inline-flex items-center gap-1 rounded-lg border border-accent-200 bg-white px-2.5 py-1 text-xs font-semibold text-accent-700 transition hover:bg-accent-50 disabled:opacity-50 dark:border-dark-600 dark:bg-dark-800 dark:text-accent-200 dark:hover:bg-dark-700;
}
.selection-btn--primary {
  @apply border-primary-600 bg-primary-600 text-white hover:bg-primary-700 dark:border-primary-500 dark:bg-primary-500 dark:text-primary-950 dark:hover:bg-primary-400;
}
.selection-btn--danger {
  @apply border-rose-200 text-rose-700 hover:bg-rose-50 dark:border-rose-500/30 dark:text-rose-300 dark:hover:bg-rose-500/15;
}
.link-check {
  @apply size-4 cursor-pointer align-middle accent-primary-600;
}
.mode-chip {
  @apply inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-accent-100/90 px-1.5 py-0.5 text-[11px] font-medium text-accent-700 dark:bg-dark-700/80 dark:text-accent-200;
}
.mode-chip[data-mode='api'] {
  @apply bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-200/80 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/25;
}
.link-status {
  @apply inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-accent-100 px-2 py-0.5 text-[11px] font-medium text-accent-700 dark:bg-dark-700 dark:text-accent-200;
}
.status-dot {
  @apply size-1.5 shrink-0 rounded-full bg-accent-400;
}
.link-status[data-status='success'] {
  @apply bg-primary-100/90 text-primary-800 dark:bg-primary-500/15 dark:text-primary-300;
}
.link-status[data-status='success'] .status-dot {
  @apply bg-primary-500;
}
.link-status[data-status='failed'] {
  @apply bg-rose-100/90 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300;
}
.link-status[data-status='failed'] .status-dot {
  @apply bg-rose-500;
}
.link-status[data-status='running'] {
  @apply bg-blue-100/90 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300;
}
.link-status[data-status='running'] .status-dot {
  @apply animate-pulse bg-blue-500;
}
.link-status[data-status='waiting_verification'],
.link-status[data-status='unconfirmed'] {
  @apply bg-amber-100/90 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300;
}
.link-status[data-status='waiting_verification'] .status-dot {
  @apply animate-pulse bg-amber-500;
}
.link-status[data-status='unconfirmed'] .status-dot {
  @apply bg-amber-500;
}
</style>

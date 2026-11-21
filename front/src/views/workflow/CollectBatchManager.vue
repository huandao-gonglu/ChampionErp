<script setup lang="ts">
import { computed } from 'vue'
import { PhArrowsDownUp, PhListBullets, PhMagnifyingGlass, PhSortAscending, PhSortDescending } from '@phosphor-icons/vue'
import { collectBatchStatusLabels } from '@/utils/collectQueue'
import { formatDateTime } from '@/utils/format'
import type { CollectionLink } from '@/api/workflow/collectionLinks'
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
  openProduct: [id: string]
}>()
const search = defineModel<string>('search', { required: true })
const status = defineModel<CollectBatchStatus | ''>('status', { required: true })
const sort = defineModel<'none' | 'ascending' | 'descending'>('sort', { required: true })

const matchCount = computed(() => Object.values(props.counts).reduce((sum, count) => sum + count, 0))
const checkedCount = computed(() => props.rows.filter(row => props.checkedIds.has(row.id)).length)
const allChecked = computed(() => checkedCount.value > 0 && checkedCount.value === props.rows.length)

function cycleSort() {
  sort.value = sort.value === 'none' ? 'ascending' : sort.value === 'ascending' ? 'descending' : 'none'
}
function hostname(url: string) { return new URL(url).hostname }
</script>

<template>
  <section class="min-w-0">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h2 class="flex items-baseline gap-2 text-lg font-bold">URL 列表 <span class="text-sm font-normal text-accent-500 dark:text-accent-300">{{ total }}</span></h2>
      <label class="relative w-full max-w-[296px]">
        <PhMagnifyingGlass aria-hidden="true" :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-accent-500 dark:text-accent-300" />
        <input v-model="search" type="search" aria-label="搜索 URL 或商品名称" placeholder="搜索 URL 或商品名称" class="input h-9 !rounded-lg !py-0 !pl-9 text-sm" />
      </label>
    </div>

    <nav class="mt-2 flex gap-4 overflow-x-auto border-b border-accent-200 text-sm dark:border-dark-700" aria-label="按状态筛选">
      <button type="button" class="filter-tab" :aria-pressed="status === ''" @click="status = ''">全部 {{ matchCount }}</button>
      <button v-for="(label, key) in collectBatchStatusLabels" :key="key" type="button" class="filter-tab" :aria-pressed="status === key" @click="status = key">{{ label }} {{ counts[key] || 0 }}</button>
    </nav>

    <div v-if="checkedIds.size" class="mt-3 flex items-center gap-3 text-xs text-accent-600 dark:text-accent-300">
      <span>已选 {{ checkedIds.size }} 条</span>
      <button type="button" class="row-action" @click="emit('clearChecks')">清空勾选</button>
    </div>

    <div class="mt-4 overflow-x-auto rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full min-w-[28rem] table-fixed text-left text-sm sm:min-w-[32rem]">
        <caption class="sr-only">采集链接和状态</caption>
        <thead class="bg-accent-50 text-xs font-semibold text-accent-700 dark:bg-dark-900 dark:text-accent-200">
          <tr>
            <th scope="col" class="w-9 py-3 text-center">
              <input
                type="checkbox" class="link-check" aria-label="全选当前筛选结果"
                :checked="allChecked" :indeterminate="checkedCount > 0 && !allChecked" :disabled="!rows.length"
                @change="emit('checkAll', ($event.target as HTMLInputElement).checked)"
              />
            </th>
            <th scope="col" class="px-2 py-3" :aria-sort="sort">
              <button type="button" class="inline-flex w-full items-center gap-1 text-left" aria-label="按商品名称或 URL 排序" @click="cycleSort">
                <span class="truncate">商品 / URL</span>
                <component :is="sort === 'ascending' ? PhSortAscending : sort === 'descending' ? PhSortDescending : PhArrowsDownUp" :size="14" class="shrink-0" aria-hidden="true" />
              </button>
            </th>
            <th scope="col" class="hidden w-20 sm:table-cell">方式</th>
            <th scope="col" class="w-24">状态</th>
            <th scope="col" class="w-36 pr-2">添加时间</th>
            <th scope="col" class="w-[72px]">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id" class="cursor-pointer hover:bg-accent-50 dark:hover:bg-white/5" :class="{ 'selected-row': selectedId === row.id }" @click="emit('select', row.id)">
            <td class="text-center">
              <input
                type="checkbox" class="link-check" :aria-label="`选择 ${row.title || row.url}`"
                :checked="checkedIds.has(row.id)" @click.stop @change="emit('check', row.id, ($event.target as HTMLInputElement).checked)"
              />
            </td>
            <td class="px-2 py-2.5">
              <button type="button" class="block w-full text-left" data-collect-row :aria-pressed="selectedId === row.id" @click.stop="emit('select', row.id)">
                <strong class="block truncate">{{ row.title || hostname(row.url) }}</strong>
                <span class="mt-0.5 block truncate text-xs text-accent-600 dark:text-accent-300">{{ row.url }}</span>
              </button>
            </td>
            <td class="hidden text-xs text-accent-600 dark:text-accent-300 sm:table-cell">{{ row.mode === 'api' ? 'API' : '浏览器' }}</td>
            <td><span class="link-status" :data-status="row.status">{{ collectBatchStatusLabels[row.status] }}</span></td>
            <td class="pr-2 text-xs tabular-nums text-accent-600 dark:text-accent-300">
              <time v-if="row.createdAt" :datetime="row.createdAt">{{ formatDateTime(row.createdAt) }}</time>
              <span v-else>-</span>
            </td>
            <td>
              <button v-if="row.status === 'success' && row.productId" type="button" class="row-action" :disabled="busy" @click.stop="emit('openProduct', row.productId)">查看商品</button>
              <button v-else-if="row.status !== 'running'" type="button" class="row-action" :disabled="busy" @click.stop="emit('run', row)">{{ row.status === 'pending' ? '采集' : row.status === 'waiting_verification' ? '继续' : '重试' }}</button>
              <span v-else class="text-xs text-accent-500 dark:text-accent-300">进行中</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="grid justify-items-center gap-2 px-4 py-10 text-center">
        <PhListBullets :size="28" class="text-accent-400" aria-hidden="true" />
        <p class="font-semibold">{{ !ready ? '正在读取采集列表' : total ? '没有匹配的链接' : '还没有采集链接' }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.filter-tab { @apply shrink-0 whitespace-nowrap border-b-2 border-transparent px-0.5 py-2.5 text-accent-600 dark:text-accent-300; }
.filter-tab[aria-pressed='true'] { @apply border-primary-500 font-semibold text-primary-700 dark:text-primary-300; }
tbody td { @apply border-t border-accent-200 dark:border-dark-700; }
.selected-row, .selected-row:hover { @apply bg-primary-50 dark:bg-primary-500/10; }
.selected-row > td:first-child { @apply shadow-[inset_3px_0_0_rgb(var(--color-primary-500))]; }
.row-action { @apply whitespace-nowrap text-xs font-semibold text-primary-700 hover:underline disabled:opacity-50 dark:text-primary-300; }
.link-check { @apply size-4 align-middle accent-primary-600; }
.link-status { @apply inline-flex whitespace-nowrap rounded-full bg-accent-100 px-2 py-1 text-xs text-accent-700 dark:bg-dark-700 dark:text-accent-200; }
.link-status[data-status=success] { @apply bg-primary-100 text-primary-800 dark:bg-primary-500/15 dark:text-primary-300; }
.link-status[data-status=failed] { @apply bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300; }
.link-status[data-status=running] { @apply bg-blue-100 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300; }
.link-status[data-status=waiting_verification], .link-status[data-status=unconfirmed] { @apply bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300; }
</style>

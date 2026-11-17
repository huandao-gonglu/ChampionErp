<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhLink, PhMagnifyingGlass, PhListBullets, PhPlay, PhArrowClockwise } from '@phosphor-icons/vue'
import { collectBatchStatusLabels } from '@/utils/collectQueue'
import type { CollectionLink } from '@/api/workflow/collectionLinks'
import type { CollectBatchStatus } from '@/types/workflow'

const props = defineProps<{ rows: CollectionLink[]; selectedId: string; busy: boolean; ready: boolean }>()
const emit = defineEmits<{ select: [id: string]; run: [rows: CollectionLink[]]; add: []; openProduct: [id: string] }>()
const search = ref('')
const status = ref<CollectBatchStatus | ''>('')
const matching = computed(() => {
  const term = search.value.trim().toLowerCase()
  return props.rows.filter(row => !term || (row.title || '').toLowerCase().includes(term) || row.url.toLowerCase().includes(term))
})
const visible = computed(() => matching.value.filter(row => !status.value || row.status === status.value))
const pending = computed(() => visible.value.filter(row => ['pending', 'waiting_verification'].includes(row.status)))
const failed = computed(() => visible.value.filter(row => row.status === 'failed'))
const counts = computed(() => {
  const result: Partial<Record<CollectBatchStatus, number>> = {}
  for (const row of matching.value) result[row.status] = (result[row.status] || 0) + 1
  return result
})
watch([visible, () => props.selectedId], () => {
  if (!visible.value.some(row => row.id === props.selectedId)) emit('select', visible.value[0]?.id || '')
}, { immediate: true })
function hostname(url: string) { return new URL(url).hostname }
</script>

<template>
  <section class="min-w-0">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h2 class="flex items-baseline gap-2 text-lg font-bold">URL 列表 <span class="text-sm font-normal text-slate-500">{{ rows.length }}</span></h2>
      <label class="relative w-full sm:w-64">
        <PhMagnifyingGlass aria-hidden="true" class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input v-model="search" type="search" aria-label="搜索 URL 或商品名称" placeholder="搜索 URL 或商品名称" class="input !py-2 !pl-9 text-sm" />
      </label>
    </div>
    <nav class="mt-3 flex gap-4 overflow-x-auto border-b border-slate-200 text-sm dark:border-dark-700" aria-label="按状态筛选">
      <button class="filter-tab" :aria-pressed="status === ''" @click="status = ''">全部 {{ matching.length }}</button>
      <button v-for="(label, key) in collectBatchStatusLabels" :key="key" class="filter-tab" :aria-pressed="status === key" @click="status = key">{{ label }} {{ counts[key] || 0 }}</button>
    </nav>
    <div class="my-4 flex flex-wrap gap-2">
      <button class="btn btn-primary" :disabled="busy || !pending.length" @click="emit('run', pending)"><PhPlay weight="fill" />开始待采集（{{ pending.length }}）</button>
      <button class="btn btn-outline" :disabled="busy || !failed.length" @click="emit('run', failed)"><PhArrowClockwise />重试失败（{{ failed.length }}）</button>
    </div>
    <div class="overflow-x-auto rounded-xl border border-slate-200 dark:border-dark-700">
      <table class="w-full table-fixed text-left text-sm">
        <caption class="sr-only">采集链接和状态</caption>
        <thead class="bg-slate-50 text-xs text-slate-600 dark:bg-dark-900 dark:text-accent-300">
          <tr><th class="px-4 py-3">商品 / URL</th><th class="hidden w-16 sm:table-cell">方式</th><th class="w-24">状态</th><th class="w-16">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in visible" :key="row.id" class="cursor-pointer border-t border-slate-200 hover:bg-slate-50 dark:border-dark-700 dark:hover:bg-dark-800" :class="{ 'selected-row': selectedId === row.id }" @click="emit('select', row.id)">
            <td class="px-3 py-2.5">
              <button class="flex w-full items-center gap-3 text-left" :aria-pressed="selectedId === row.id" @click.stop="emit('select', row.id)">
                <img v-if="row.image" :src="row.image" alt="" class="size-11 shrink-0 rounded-md bg-white object-cover" loading="lazy" />
                <span v-else class="grid size-11 shrink-0 place-items-center rounded-md bg-slate-100 text-slate-400 dark:bg-dark-700"><PhLink :size="21" /></span>
                <span class="min-w-0"><strong class="block truncate">{{ row.title || hostname(row.url) }}</strong><span class="mt-1 block truncate text-xs text-slate-500 dark:text-accent-300">{{ row.url }}</span></span>
              </button>
            </td>
            <td class="hidden text-xs text-slate-500 dark:text-accent-300 sm:table-cell">{{ row.mode === 'api' ? 'API' : '浏览器' }}</td>
            <td><span class="link-status" :data-status="row.status">{{ collectBatchStatusLabels[row.status] }}</span></td>
            <td>
              <button v-if="row.status === 'success' && row.productId" class="row-action" :disabled="busy" @click.stop="emit('openProduct', row.productId)">查看商品</button>
              <button v-else-if="row.status !== 'running'" class="row-action" :disabled="busy" @click.stop="emit('select', row.id); emit('run', [row])">{{ row.status === 'pending' ? '采集' : row.status === 'waiting_verification' ? '继续' : '重试' }}</button>
              <span v-else class="text-slate-400">进行中</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!visible.length" class="grid justify-items-center gap-2 px-4 py-12 text-center">
        <PhListBullets :size="32" class="text-slate-400" />
        <p class="font-semibold">{{ !ready ? '正在读取采集列表' : rows.length ? '没有匹配的链接' : '还没有采集链接' }}</p>
        <button v-if="ready && !rows.length" class="row-action" @click="emit('add')">添加商品链接</button>
      </div>
    </div>
  </section>
</template>
<style scoped>
.filter-tab { @apply shrink-0 border-b-2 border-transparent py-2.5 text-slate-500 dark:text-accent-300; }
.filter-tab[aria-pressed=true] { @apply border-emerald-500 font-semibold text-emerald-700 dark:text-emerald-300; }
.selected-row { @apply bg-emerald-50 dark:bg-emerald-500/10; box-shadow: inset 3px 0 #10b981; }
.row-action { @apply text-xs font-semibold text-emerald-700 hover:underline disabled:opacity-50 dark:text-emerald-300; }
.link-status { @apply inline-flex rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600 dark:bg-dark-700 dark:text-accent-200; }
.link-status[data-status=success] { @apply bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300; }
.link-status[data-status=failed] { @apply bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300; }
.link-status[data-status=running] { @apply bg-blue-100 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300; }
.link-status[data-status=waiting_verification], .link-status[data-status=unconfirmed] { @apply bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300; }
</style>

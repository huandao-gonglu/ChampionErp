<script setup lang="ts">
import { computed } from 'vue'
import type { MercadoLibreRemoteItem } from '@/types/workflow'

const props = defineProps<{
  items: MercadoLibreRemoteItem[]
  status: string
  page: number
  perPage: number
  total: number
  totalPages: number
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: [status: string, page?: number, perPage?: number]
  closeItem: [item: MercadoLibreRemoteItem]
}>()

const statusLabels: Record<string, string> = {
  active: '在售',
  paused: '已暂停',
  closed: '已结束',
  all: '全部',
}

const pageCount = computed(() => Math.max(1, props.totalPages || Math.ceil(props.total / Math.max(1, props.perPage)) || 1))
const pageStart = computed(() => props.total ? ((props.page - 1) * props.perPage) + 1 : 0)
const pageEnd = computed(() => Math.min(props.total, (props.page - 1) * props.perPage + props.items.length))

function badgeClass(status: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'active') return 'badge-success'
  if (value === 'paused') return 'badge-info'
  if (value === 'closed') return 'badge-muted'
  return 'badge-muted'
}

function requestClose(item: MercadoLibreRemoteItem) {
  const confirmed = window.confirm(`确认下架 Mercado Libre 商品 ${item.id}？`)
  if (confirmed) emit('closeItem', item)
}

function refreshStatus(status: string) {
  emit('refresh', status, 1, props.perPage)
}

function refreshPerPage(value: string) {
  const next = Number.parseInt(value, 10)
  emit('refresh', props.status, 1, Number.isFinite(next) ? next : props.perPage)
}

function goPage(page: number) {
  emit('refresh', props.status, Math.min(Math.max(1, page), pageCount.value), props.perPage)
}
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">ML 已发布</p>
        <h2 class="mt-2 card-title">Mercado Libre 已发布商品</h2>
        <p class="muted mt-1">从 Mercado Libre 账号实时读取商品列表；下架会调用 API 暂停或结束发布。</p>
      </div>
      <div class="grid w-full gap-3 sm:grid-cols-3 lg:w-auto">
        <select
          :value="props.status"
          class="input sm:w-40"
          :disabled="props.loading"
          @change="refreshStatus(($event.target as HTMLSelectElement).value)"
        >
          <option value="active">在售</option>
          <option value="paused">已暂停</option>
          <option value="closed">已结束</option>
          <option value="all">全部</option>
        </select>
        <select
          :value="props.perPage"
          class="input sm:w-32"
          :disabled="props.loading"
          @change="refreshPerPage(($event.target as HTMLSelectElement).value)"
        >
          <option :value="25">25 条/页</option>
          <option :value="50">50 条/页</option>
          <option :value="100">100 条/页</option>
        </select>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('refresh', props.status, props.page, props.perPage)">
          {{ props.loading ? '刷新中...' : '刷新' }}
        </button>
      </div>
    </div>

    <div v-if="props.error" class="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
      {{ props.error }}
    </div>

    <div class="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent-200 bg-accent-50 p-3 text-sm text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
      <span>当前筛选：{{ statusLabels[props.status] || props.status }}，第 {{ props.page }} / {{ pageCount }} 页，显示 {{ pageStart }}-{{ pageEnd }}，共 {{ props.total }} 条。</span>
      <div class="flex items-center gap-2">
        <button class="btn btn-outline py-1.5" :disabled="props.loading || props.page <= 1" @click="goPage(props.page - 1)">上一页</button>
        <button class="btn btn-outline py-1.5" :disabled="props.loading || props.page >= pageCount" @click="goPage(props.page + 1)">下一页</button>
      </div>
    </div>
    <div class="mt-5 overflow-hidden rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[30%]" />
          <col class="w-[13%]" />
          <col class="w-[10%]" />
          <col class="w-[9%]" />
          <col class="w-[10%]" />
          <col class="w-[12%]" />
          <col class="w-[16%]" />
        </colgroup>
        <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
          <tr>
            <th class="p-3">商品</th>
            <th class="p-3">状态</th>
            <th class="p-3">价格</th>
            <th class="p-3">库存 / 已售</th>
            <th class="p-3">SKU</th>
            <th class="p-3">更新时间</th>
            <th class="p-3">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="item in props.items" :key="item.id" class="align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60">
            <td class="min-w-0 p-3">
              <div class="flex gap-3">
                <img v-if="item.thumbnail" :src="item.thumbnail" class="size-12 shrink-0 rounded-lg object-cover" />
                <div v-else class="flex size-12 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-[10px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">无图</div>
                <div class="min-w-0">
                  <div class="truncate font-semibold text-accent-950 dark:text-white" :title="item.title || item.id">{{ item.title || item.id }}</div>
                  <div class="mt-1 truncate font-mono text-xs text-accent-500 dark:text-accent-400" :title="item.id">{{ item.id }}</div>
                  <div class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400" :title="item.categoryId">{{ item.categoryId }}</div>
                </div>
              </div>
            </td>
            <td class="min-w-0 p-3">
              <span class="inline-flex max-w-full truncate" :class="badgeClass(item.status)" :title="statusLabels[item.status] || item.status || '-'">{{ statusLabels[item.status] || item.status || '-' }}</span>
              <div v-if="item.subStatus.length" class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400" :title="item.subStatus.join(', ')">{{ item.subStatus.join(', ') }}</div>
            </td>
            <td class="p-3 font-semibold text-accent-950 dark:text-white"><span class="block truncate" :title="`${item.price || '-'} ${item.currencyId}`">{{ item.price || '-' }} {{ item.currencyId }}</span></td>
            <td class="p-3 text-accent-700 dark:text-accent-200"><span class="block truncate" :title="`${item.availableQuantity} / ${item.soldQuantity}`">{{ item.availableQuantity }} / {{ item.soldQuantity }}</span></td>
            <td class="p-3 font-mono text-xs text-accent-700 dark:text-accent-200"><span class="block truncate" :title="item.sellerSku || '-'">{{ item.sellerSku || '-' }}</span></td>
            <td class="p-3 text-xs text-accent-500 dark:text-accent-400"><span class="block truncate" :title="item.lastUpdated || item.dateCreated || '-'">{{ item.lastUpdated || item.dateCreated || '-' }}</span></td>
            <td class="p-3">
              <div class="flex flex-wrap gap-2">
                <a v-if="item.permalink" class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs" :href="item.permalink" target="_blank" rel="noreferrer">官网</a>
                <button
                  class="btn btn-secondary whitespace-nowrap px-3 py-1.5 text-xs"
                  :disabled="props.loading || item.status === 'closed' || item.status === 'paused'"
                  @click="requestClose(item)"
                >
                  {{ item.status === 'closed' || item.status === 'paused' ? '已下架' : '下架商品' }}
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!props.items.length">
            <td class="p-6 text-center text-accent-500 dark:text-accent-300" colspan="7">暂无远程商品，或当前状态筛选下没有商品。</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="mt-4 flex flex-wrap items-center justify-end gap-2 text-sm text-accent-500 dark:text-accent-400">
      <button class="btn btn-outline py-1.5" :disabled="props.loading || props.page <= 1" @click="goPage(props.page - 1)">上一页</button>
      <span>第 {{ props.page }} / {{ pageCount }} 页</span>
      <button class="btn btn-outline py-1.5" :disabled="props.loading || props.page >= pageCount" @click="goPage(props.page + 1)">下一页</button>
    </div>
  </section>
</template>

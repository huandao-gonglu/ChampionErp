<script setup lang="ts">
import type { MercadoLibreRemoteItem } from '@/types/workflow'

const props = defineProps<{
  items: MercadoLibreRemoteItem[]
  status: string
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: [status: string]
  closeItem: [item: MercadoLibreRemoteItem]
}>()

const statusLabels: Record<string, string> = {
  active: '在售',
  paused: '已暂停',
  closed: '已结束',
  all: '全部',
}

function badgeClass(status: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'active') return 'badge-success'
  if (value === 'paused') return 'badge-info'
  if (value === 'closed') return 'badge-muted'
  return 'badge-muted'
}

function requestClose(item: MercadoLibreRemoteItem) {
  const confirmed = window.confirm(`确认删除/结束 Mercado Libre 商品 ${item.id}？\n\n该操作会把商品状态改为 closed，通常不可恢复。`)
  if (confirmed) emit('closeItem', item)
}
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">Mercado Libre 已发布商品</h2>
        <p class="muted mt-1">从 Mercado Libre 账号实时读取商品列表；删除会调用 API 结束发布。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <select
          :value="props.status"
          class="input w-40"
          :disabled="props.loading"
          @change="emit('refresh', ($event.target as HTMLSelectElement).value)"
        >
          <option value="active">在售</option>
          <option value="paused">已暂停</option>
          <option value="closed">已结束</option>
          <option value="all">全部</option>
        </select>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('refresh', props.status)">
          {{ props.loading ? '刷新中...' : '刷新' }}
        </button>
      </div>
    </div>

    <div v-if="props.error" class="mt-4 rounded-2xl bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
      {{ props.error }}
    </div>

    <div class="mt-4 text-sm text-slate-500">当前筛选：{{ statusLabels[props.status] || props.status }}，共 {{ props.items.length }} 条。</div>
    <div class="mt-4 overflow-auto rounded-2xl border border-slate-200">
      <table class="w-full text-left text-sm">
        <thead class="bg-slate-50 text-xs text-slate-500">
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
        <tbody>
          <tr v-for="item in props.items" :key="item.id" class="border-t align-top">
            <td class="max-w-md p-3">
              <div class="flex gap-3">
                <img v-if="item.thumbnail" :src="item.thumbnail" class="size-12 shrink-0 rounded object-cover" />
                <div v-else class="size-12 shrink-0 rounded bg-slate-100 text-center text-[10px] leading-[48px] text-slate-500">无图</div>
                <div class="min-w-0">
                  <div class="font-semibold text-slate-950">{{ item.title || item.id }}</div>
                  <div class="mt-1 font-mono text-xs text-slate-500">{{ item.id }}</div>
                  <div class="mt-1 text-xs text-slate-500">{{ item.categoryId }}</div>
                </div>
              </div>
            </td>
            <td class="p-3">
              <span :class="badgeClass(item.status)">{{ statusLabels[item.status] || item.status || '-' }}</span>
              <div v-if="item.subStatus.length" class="mt-1 text-xs text-slate-500">{{ item.subStatus.join(', ') }}</div>
            </td>
            <td class="p-3 font-semibold">{{ item.price || '-' }} {{ item.currencyId }}</td>
            <td class="p-3">{{ item.availableQuantity }} / {{ item.soldQuantity }}</td>
            <td class="p-3 font-mono text-xs">{{ item.sellerSku || '-' }}</td>
            <td class="p-3 text-xs text-slate-500">{{ item.lastUpdated || item.dateCreated || '-' }}</td>
            <td class="p-3">
              <div class="flex flex-wrap gap-2">
                <a v-if="item.permalink" class="btn btn-outline py-1.5" :href="item.permalink" target="_blank" rel="noreferrer">官网</a>
                <button
                  class="btn btn-secondary py-1.5"
                  :disabled="props.loading || item.status === 'closed'"
                  @click="requestClose(item)"
                >
                  删除商品
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!props.items.length">
            <td class="p-6 text-center text-slate-500" colspan="7">暂无远程商品，或当前状态筛选下没有商品。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

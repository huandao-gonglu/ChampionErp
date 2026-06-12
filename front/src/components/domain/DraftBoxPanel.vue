<script setup lang="ts">
import { computed, ref } from 'vue'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'
import type { Marketplace, ProductIndexItem, WorkflowStatus } from '@/types/workflow'

const props = defineProps<{
  items: ProductIndexItem[]
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: []
  editText: [item: ProductIndexItem, platform: Marketplace]
  editImages: [item: ProductIndexItem, platform: Marketplace]
  goPublish: [item: ProductIndexItem, platform: Marketplace]
}>()

const platformFilter = ref<'all' | Marketplace>('all')
const draftStatusSet = new Set(['claimed', 'copy_ready', 'images_ready', 'ready_to_publish', 'published', 'failed', 'not_ready'])
const platformLabels: Record<Marketplace, string> = {
  mercadolibre: 'Mercado Libre',
  wildberries: 'Wildberries',
  ozon: 'Ozon',
}

const draftRows = computed(() => props.items.flatMap((item) => {
  return (Object.entries(item.draftStatuses) as Array<[Marketplace, WorkflowStatus]>)
    .filter(([platform, status]) => {
      if (platformFilter.value !== 'all' && platform !== platformFilter.value) return false
      return draftStatusSet.has(String(status || ''))
    })
    .map(([platform, status]) => ({ item, platform, status }))
}))
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">平台草稿箱</h2>
        <p class="muted mt-1">展示已经认领到平台草稿状态机的商品，可继续编辑并进入发布预检。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <select v-model="platformFilter" class="input w-48">
          <option value="all">全部平台</option>
          <option value="mercadolibre">Mercado Libre</option>
          <option value="wildberries">Wildberries</option>
          <option value="ozon">Ozon</option>
        </select>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('refresh')">刷新</button>
      </div>
    </div>

    <div class="mt-4 text-sm text-slate-500">草稿：{{ draftRows.length }} 条。</div>
    <div v-if="props.error" class="mt-4 rounded-2xl bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
      {{ props.error }}
    </div>

    <div class="mt-4 overflow-auto rounded-2xl border border-slate-200">
      <table class="w-full text-left text-sm">
        <thead class="bg-slate-50 text-xs text-slate-500">
          <tr>
            <th class="p-3">商品</th>
            <th class="p-3">平台</th>
            <th class="p-3">草稿状态</th>
            <th class="p-3">来源</th>
            <th class="p-3">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in draftRows" :key="`${row.item.productId}-${row.platform}`" class="border-t align-top">
            <td class="max-w-lg p-3">
              <div class="flex gap-3">
                <img v-if="row.item.mainImage" :src="row.item.mainImage" class="size-12 rounded object-cover" />
                <div v-else class="size-12 shrink-0 rounded bg-slate-100 text-center text-[10px] leading-[48px] text-slate-500">无图</div>
                <div class="min-w-0">
                  <div class="font-semibold text-slate-950">{{ row.item.title || row.item.productId || '-' }}</div>
                  <div class="mt-1 truncate text-xs text-slate-500">{{ row.item.productId }}</div>
                </div>
              </div>
            </td>
            <td class="p-3"><span class="badge-muted">{{ platformLabels[row.platform] }}</span></td>
            <td class="p-3"><span :class="statusBadgeClass(row.status)">{{ workflowStatusLabel(row.status) }}</span></td>
            <td class="p-3">
              <div class="font-semibold">{{ row.item.sourcePlatform || '-' }}</div>
              <div class="max-w-xs truncate text-xs text-slate-500">{{ row.item.sourceUrl }}</div>
            </td>
            <td class="p-3">
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('editText', row.item, row.platform)">编辑文本</button>
                <button class="btn btn-secondary py-1.5" :disabled="props.loading" @click="emit('editImages', row.item, row.platform)">编辑图片</button>
                <button class="btn btn-primary py-1.5" :disabled="props.loading" @click="emit('goPublish', row.item, row.platform)">发布预检</button>
              </div>
            </td>
          </tr>
          <tr v-if="!draftRows.length">
            <td class="p-6 text-center text-slate-500" colspan="5">暂无平台草稿。可先从商品库或发布预检页推到平台草稿箱。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

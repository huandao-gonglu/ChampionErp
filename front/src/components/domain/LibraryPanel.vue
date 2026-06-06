<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Marketplace, ProductIndexItem } from '@/types/workflow'

const props = defineProps<{
  items: ProductIndexItem[]
  selectedIds: string[]
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: []
  load: [item: ProductIndexItem]
  editImages: [item: ProductIndexItem]
  toggle: [productId: string, checked: boolean]
  selectAll: [checked: boolean, productIds: string[]]
  deleteItem: [item: ProductIndexItem]
  deleteSelected: []
  claim: []
  generateCopy: []
  generateImagePrompt: []
  publishSelected: []
  goPublish: []
}>()

const platformFilter = ref<'all' | Marketplace>('all')
const workflowFilter = ref('all')

const filteredItems = computed(() => props.items.filter((item) => {
  const platformOk = platformFilter.value === 'all' || item.platforms.includes(platformFilter.value)
  const workflowOk = workflowFilter.value === 'all'
    || (workflowFilter.value === 'ready_to_publish' ? item.workflowStatus === 'ready_to_publish' : item.workflowStatus !== 'ready_to_publish')
  return platformOk && workflowOk
}))

const allChecked = computed(() => filteredItems.value.length > 0 && filteredItems.value.every((item) => props.selectedIds.includes(item.productId)))
const selectedCount = computed(() => props.selectedIds.length)

function confirmDelete(item: ProductIndexItem) {
  const title = item.title || item.productId || '该商品'
  if (window.confirm(`确认删除商品「${title}」吗？删除后不可恢复。`)) emit('deleteItem', item)
}

function confirmDeleteSelected() {
  if (!selectedCount.value) return
  if (window.confirm(`确认批量删除已勾选的 ${selectedCount.value} 个商品吗？删除后不可恢复。`)) emit('deleteSelected')
}

function statusClass(value: string) {
  if (['done', 'success', 'ready_to_publish', 'published'].includes(String(value))) return 'badge-success'
  if (['failed', 'not_ready'].includes(String(value))) return 'badge-danger'
  return 'badge-muted'
}
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">商品库 / 平台采集箱</h2>
        <p class="muted mt-1">来自后端 SQLite 商品母库，可批量认领、生成文案、生图任务包和发布。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <select v-model="platformFilter" class="input w-48">
          <option value="all">全部平台</option>
          <option value="mercadolibre">Mercado Libre</option>
          <option value="wildberries">Wildberries</option>
          <option value="ozon">Ozon</option>
        </select>
        <select v-model="workflowFilter" class="input w-44">
          <option value="all">全部流程</option>
          <option value="ready_to_publish">仅看校验通过</option>
          <option value="not_ready">未校验通过</option>
        </select>
      </div>
    </div>

    <div class="mt-4 flex flex-wrap gap-2 text-sm">
      <button class="btn btn-secondary" :disabled="props.loading" @click="emit('claim')">批量认领到平台草稿箱</button>
      <button class="btn btn-primary" :disabled="props.loading" @click="emit('generateCopy')">批量 AI 生成标题描述</button>
      <button class="btn btn-secondary" :disabled="props.loading" @click="emit('generateImagePrompt')">生成 GPT 生图任务包</button>
      <button class="btn btn-primary" :disabled="props.loading" @click="emit('publishSelected')">选中商品发布入队</button>
      <button class="btn btn-outline text-rose-700" :disabled="props.loading || !selectedCount" @click="confirmDeleteSelected">批量删除选中</button>
      <button class="btn btn-outline" :disabled="props.loading" @click="emit('refresh')">刷新商品库</button>
      <button class="btn btn-outline" @click="emit('goPublish')">发布预检</button>
    </div>

    <div class="mt-4 text-sm text-slate-500">本地商品库：{{ filteredItems.length }} / {{ props.items.length }} 条记录。</div>
    <div v-if="props.error" class="mt-4 rounded-2xl bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
      {{ props.error }}
    </div>

    <div class="mt-4 overflow-auto rounded-2xl border border-slate-200">
      <table class="w-full text-left text-sm">
        <thead class="bg-slate-50 text-xs text-slate-500">
          <tr>
            <th class="p-3"><input type="checkbox" :checked="allChecked" @change="emit('selectAll', ($event.target as HTMLInputElement).checked, filteredItems.map((item) => item.productId))" /></th>
            <th class="p-3">主图</th>
            <th class="p-3">来源</th>
            <th class="p-3">标题 / 平台草稿箱</th>
            <th class="p-3">采集</th>
            <th class="p-3">流程</th>
            <th class="p-3">AI 文案</th>
            <th class="p-3">生图</th>
            <th class="p-3">类目</th>
            <th class="p-3">属性</th>
            <th class="p-3">价格</th>
            <th class="p-3">预检</th>
            <th class="p-3">发布</th>
            <th class="p-3">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in filteredItems" :key="item.productId" class="border-t align-top">
            <td class="p-3"><input type="checkbox" :checked="props.selectedIds.includes(item.productId)" @change="emit('toggle', item.productId, ($event.target as HTMLInputElement).checked)" /></td>
            <td class="p-3"><img v-if="item.mainImage" :src="item.mainImage" class="size-12 rounded object-cover" /><div v-else class="size-12 rounded bg-slate-100 text-center text-[10px] leading-[48px] text-slate-500">无图</div></td>
            <td class="p-3"><div class="font-semibold">{{ item.sourcePlatform || '-' }}</div><div class="max-w-40 truncate text-xs text-slate-500">{{ item.sourceUrl }}</div></td>
            <td class="max-w-sm p-3"><div class="font-semibold text-slate-950">{{ item.title || '-' }}</div><div class="mt-2 flex flex-wrap gap-1"><span v-for="platform in item.platforms" :key="platform" class="badge-muted">{{ platform }}</span></div></td>
            <td class="p-3"><span :class="statusClass(item.collectStatus)">{{ item.collectStatus || '-' }}</span></td>
            <td class="p-3"><span :class="statusClass(item.workflowStatus)">{{ item.workflowStatus || '-' }}</span></td>
            <td class="p-3"><span :class="statusClass(item.aiCopyStatus)">{{ item.aiCopyStatus || '-' }}</span></td>
            <td class="p-3"><span :class="statusClass(item.imageStatus)">{{ item.imageStatus || '-' }}</span></td>
            <td class="p-3"><span :class="statusClass(item.categoryStatus)">{{ item.categoryStatus || '-' }}</span></td>
            <td class="p-3"><span :class="statusClass(item.attributesStatus)">{{ item.attributesStatus || '-' }}</span></td>
            <td class="p-3"><span :class="statusClass(item.pricingStatus)">{{ item.pricingStatus || '-' }}</span></td>
            <td class="p-3"><span :class="statusClass(String(item.precheckStatus))">{{ item.precheckStatus || '-' }}</span></td>
            <td class="p-3"><span :class="statusClass(item.publishStatus)">{{ item.publishStatus || '-' }}</span></td>
            <td class="p-3">
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('load', item)">编辑文本</button>
                <button class="btn btn-secondary py-1.5" :disabled="props.loading" @click="emit('editImages', item)">编辑图片</button>
                <button class="btn btn-outline py-1.5 text-rose-700" :disabled="props.loading" @click="confirmDelete(item)">删除</button>
              </div>
            </td>
          </tr>
          <tr v-if="!filteredItems.length"><td class="p-6 text-center text-slate-500" colspan="14">当前筛选下暂无商品。可在采集页手动导入或采集后自动加入商品库。</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

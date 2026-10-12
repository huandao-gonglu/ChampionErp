<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { searchProductResearchKeywords } from '@/api/workflow/research'
import type { BackendProductResearchKeywordResponse } from '@/types/workflow'

const props = defineProps<{ keyword: string; marketId: string; disabled: boolean }>()
const emit = defineEmits<{ select: [keyword: string] }>()
const busy = ref(false)
const error = ref('')
const result = ref<BackendProductResearchKeywordResponse | null>(null)
const page = ref(0)
const filter = ref('')
const sort = ref('volume')
let marketVersion = 0
const normalized = (value: string) => value.trim().replace(/\s+/g, ' ').toLowerCase()
const sameQuery = computed(() => result.value?.market_id === props.marketId
  && normalized(result.value.keyword) === normalized(props.keyword))
const disabled = computed(() => busy.value || props.disabled || !props.keyword.trim())
const items = computed(() => {
  const query = filter.value.trim().toLowerCase()
  const rows = (result.value?.items || []).filter(item =>
    `${item.keyword} ${item.chinese_name}`.toLowerCase().includes(query))
  if (sort.value === 'volume') rows.sort((a, b) => (b.search_volume ?? -Infinity) - (a.search_volume ?? -Infinity))
  if (sort.value === 'change') rows.sort((a, b) => (b.rank_change ?? -Infinity) - (a.rank_change ?? -Infinity))
  return rows
})
const visibleItems = computed(() => items.value.slice(page.value * 10, page.value * 10 + 10))
const pages = computed(() => Math.max(1, Math.ceil(items.value.length / 10)))
const receipts = computed(() => result.value?.quota_receipts || [])
watch([filter, sort], () => { page.value = 0 })
watch(() => props.marketId, () => { marketVersion++; result.value = null; error.value = ''; page.value = 0 })

function metric(value: number | null) {
  return value == null ? '暂无' : value.toLocaleString('zh-CN')
}
function change(value: number | null) {
  return value == null ? '暂无' : value > 0 ? `上升 ${metric(value)}` : value < 0 ? `下降 ${metric(-value)}` : '持平'
}
function date(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
async function search(refresh = false) {
  if (disabled.value) return
  const marketId = props.marketId
  const version = marketVersion
  const keyword = props.keyword.trim()
  busy.value = true
  error.value = ''
  result.value = null
  try {
    const response = await searchProductResearchKeywords({ market_id: marketId, keyword, refresh })
    if (marketVersion !== version) return
    result.value = response
    error.value = response.ok ? '' : response.error || '相关热搜词查询失败'
    page.value = 0
    filter.value = ''
    sort.value = 'volume'
  } catch (exc) {
    if (marketVersion === version) error.value = exc instanceof Error ? exc.message : '相关热搜词查询失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="space-y-3">
    <button type="button" class="btn btn-secondary w-full" :disabled="disabled" @click="search()">
      {{ busy ? '正在查询热搜词…' : '找热搜词 · 预计 5 额度' }}
    </button>
    <p class="muted text-xs">相同条件 24 小时内复用结果，缓存命中不消耗额度。</p>
    <p v-if="error" role="alert" class="text-sm text-danger-700 dark:text-danger-200">{{ error }}</p>
    <div v-if="result" class="space-y-3 rounded-lg border border-accent-200 p-3 dark:border-dark-700" aria-live="polite">
      <p class="text-sm font-semibold">相关热搜词 · {{ result.keyword }}</p>
      <p v-if="result.cached" class="muted text-xs">使用缓存，本次消耗 0 额度。</p>
      <p v-for="(receipt, index) in receipts" :key="index" class="muted text-xs">
        本次消耗 {{ receipt.request_consumed ?? '未知' }} 额度 · 回执剩余 {{ receipt.request_left ?? '未知' }}
      </p>
      <p v-if="!result.cached && !receipts.length" class="muted text-xs">本次扣费未知，请核对账户额度。</p>
      <template v-if="result.ok">
        <p class="muted text-xs">查询于 {{ date(result.fetched_at) }} · 默认上一 ABA 周数据</p>
        <p v-if="!sameQuery" class="muted text-xs">以下为此前查询结果；点击上方按钮查询当前输入。</p>
        <div v-if="result.items.length" class="space-y-2">
          <input v-model="filter" class="input" aria-label="筛选已获取热搜词" placeholder="筛选已获取的词（不消耗额度）" />
          <select v-model="sort" class="input" aria-label="热搜词排序">
            <option value="source">数据源顺序</option>
            <option value="volume">搜索量从高到低</option>
            <option value="change">排名上升优先</option>
          </select>
          <ul class="max-h-96 space-y-2 overflow-y-auto">
            <li v-for="item in visibleItems" :key="item.keyword">
              <button
                type="button"
                class="w-full rounded-lg border border-accent-200 p-2 text-left hover:bg-accent-50 disabled:opacity-50 dark:border-dark-700 dark:hover:bg-dark-800"
                :disabled="props.disabled || busy"
                :aria-label="`使用关键词 ${item.keyword}`"
                @click="emit('select', item.keyword)"
              >
                <span class="block break-words text-sm font-semibold">{{ item.keyword }}</span>
                <span v-if="item.chinese_name" class="muted block text-xs">{{ item.chinese_name }}</span>
                <span class="muted mt-1 block text-xs">30 天搜索量 {{ metric(item.search_volume) }}</span>
                <span class="muted block text-xs">周排名 {{ metric(item.rank) }} · {{ change(item.rank_change) }}</span>
                <span v-if="item.data_updated_at" class="muted block text-xs">数据更新 {{ item.data_updated_at }}</span>
              </button>
            </li>
          </ul>
          <p v-if="!items.length" class="muted text-sm">没有匹配的已获取关键词。</p>
          <div class="flex items-center justify-between gap-2 text-xs">
            <button type="button" class="btn btn-secondary" :disabled="page === 0" @click="page--">上一批</button>
            <span>{{ page + 1 }} / {{ pages }}</span>
            <button type="button" class="btn btn-secondary" :disabled="page + 1 >= pages" @click="page++">下一批</button>
          </div>
          <p class="muted text-xs">共 {{ result.items.length }} 条，翻批与筛选不消耗额度；选词后点击“生成候选”查询商品。</p>
        </div>
        <p v-else class="muted text-sm">此方向暂无相关热搜词，可以调整输入后再查询。</p>
        <button v-if="sameQuery" type="button" class="btn btn-secondary w-full" :disabled="disabled" @click="search(true)">重新获取 · 预计 5 额度</button>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  createProductResearchHotProductRun,
  fetchProductResearchSettings,
} from '@/api/workflow'
import {
  productResearchMarketLabel,
  productResearchStrategyLabel,
} from '@/utils/productResearchLabels'
import type {
  HotProductCandidate,
  ProductResearchConfig,
  ProductResearchResponse,
  ProductResearchTargetMarket,
} from '@/types/workflow'

const form = ref({
  targetMarket: 'amazon-us',
  keywordText: '',
  limit: 12,
})

const loading = ref(false)
const error = ref('')
const result = ref<ProductResearchResponse | null>(null)
const researchConfig = ref<ProductResearchConfig | null>(null)
const selectedCandidateId = ref('')

const candidates = computed(() => result.value?.items || [])
const targetMarketOptions = computed(() => {
  const markets = researchConfig.value?.targetMarkets || []
  return markets.length ? markets : [{ id: 'amazon-us', displayName: 'Amazon US', platform: 'amazon', site: 'amazon.com', raw: {} }]
})
const selectedTargetMarket = computed<ProductResearchTargetMarket>(() => {
  return targetMarketOptions.value.find((item) => item.id === form.value.targetMarket) || targetMarketOptions.value[0]
})
const selectedCandidate = computed(() => {
  return candidates.value.find((item) => item.id === selectedCandidateId.value) || candidates.value[0] || null
})
const searchKeywords = computed(() => parseKeywords(form.value.keywordText))
const canRunSearch = computed(() => Boolean(searchKeywords.value.length && selectedTargetMarket.value?.id))
const averageHotScore = computed(() => {
  if (!candidates.value.length) return 0
  return candidates.value.reduce((sum, item) => sum + item.hotScore, 0) / candidates.value.length
})
const successfulSources = computed(() => (result.value?.sourceStatus || []).filter((item) => item.status === 'success').length)

function parseKeywords(value: string) {
  return Array.from(
    new Set(
      value
        .split(/[\n,，;；]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  )
}

function buildPayload() {
  return {
    search_mode: 'target_only',
    markets: {
      target_markets: [selectedTargetMarket.value.id],
      reference_markets: [],
    },
    keywords: searchKeywords.value,
    result_options: {
      limit: form.value.limit,
      sort_by: 'rank',
    },
  }
}

async function loadSettings() {
  researchConfig.value = await fetchProductResearchSettings()
  if (!targetMarketOptions.value.some((market) => market.id === form.value.targetMarket)) {
    form.value.targetMarket = targetMarketOptions.value[0]?.id || 'amazon-us'
  }
}

async function runSearch() {
  loading.value = true
  error.value = ''
  try {
    if (!searchKeywords.value.length) {
      throw new Error('请先输入关键词')
    }
    const next = await createProductResearchHotProductRun(buildPayload())
    result.value = next
    selectedCandidateId.value = next.items[0]?.id || ''
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '选品搜索失败'
  } finally {
    loading.value = false
  }
}

function scoreTone(score: number) {
  if (score >= 90) return 'text-success-700 dark:text-success-300'
  if (score >= 75) return 'text-info-700 dark:text-info-300'
  return 'text-warning-700 dark:text-warning-300'
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    success: '成功',
    cached: '缓存',
    empty: '无数据',
    skipped: '跳过',
    configuration_required: '待配置',
    failed: '失败',
  }
  return labels[value] || value || '-'
}

function runStatusLabel(value: string) {
  const labels: Record<string, string> = {
    completed: '已完成',
    running: '运行中',
    queued: '排队中',
    failed: '失败',
    idle: '待运行',
  }
  return labels[value] || '待运行'
}

function statusClass(value: string) {
  if (value === 'success') return 'badge-success'
  if (value === 'configuration_required') return 'badge-info'
  if (value === 'failed') return 'badge-danger'
  return 'badge-muted'
}

function priceLabel(item: HotProductCandidate | null) {
  if (!item?.price) return '-'
  return `${item.price.currency} ${item.price.amount.toFixed(2)}`
}

function candidateMarketLabel(item: HotProductCandidate) {
  return productResearchMarketLabel({
    id: item.marketId,
    displayName: item.marketId,
    platform: item.platform,
    site: item.site,
  })
}

function sourceLabel(value: string) {
  const labels: Record<string, string> = {
    market_hot_products: '市场候选数据',
  }
  return labels[value] || value || '-'
}

onMounted(async () => {
  try {
    await loadSettings()
  } catch {
    researchConfig.value = null
  }
})
</script>

<template>
  <section class="space-y-6">
    <div class="grid gap-4 md:grid-cols-4">
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">热点商品</p>
        <p class="mt-2 text-2xl font-black text-accent-950 dark:text-white">{{ candidates.length }}</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">平均热度</p>
        <p class="mt-2 text-2xl font-black" :class="scoreTone(averageHotScore)">{{ averageHotScore.toFixed(1) }}</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">正常来源</p>
        <p class="mt-2 text-2xl font-black text-success-700 dark:text-success-300">{{ successfulSources }}</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">运行状态</p>
        <p class="mt-3"><span class="badge-success">{{ runStatusLabel(result?.run.status || 'idle') }}</span></p>
      </div>
    </div>

    <div class="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)_360px] 2xl:grid-cols-[360px_minmax(0,1fr)_420px]">
      <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 class="card-title">搜索条件</h2>
            <p class="muted mt-1">选择平台站点，输入本次关注的关键词。</p>
          </div>
          <span class="badge-info">临时结果</span>
        </div>

        <div class="mt-5 space-y-5">
          <label class="block">
            <span class="mb-2 block text-sm font-semibold text-accent-700 dark:text-accent-200">目标市场</span>
            <select v-model="form.targetMarket" class="input">
              <option v-for="market in targetMarketOptions" :key="market.id" :value="market.id">
                {{ productResearchMarketLabel(market) }}
              </option>
            </select>
          </label>

          <label class="block">
            <div class="mb-2 flex items-center justify-between gap-3">
              <span class="text-sm font-semibold text-accent-700 dark:text-accent-200">关键词</span>
              <span class="text-xs text-accent-500 dark:text-accent-400">{{ searchKeywords.length }} 个</span>
            </div>
            <textarea
              v-model="form.keywordText"
              class="input min-h-[104px] resize-y leading-6"
              placeholder="例如：pet storage&#10;mahjong gift&#10;custom name stamp"
            ></textarea>
          </label>

          <label class="block">
            <span class="mb-2 block text-sm font-semibold text-accent-700 dark:text-accent-200">结果数量</span>
            <input v-model.number="form.limit" class="input" min="1" max="50" type="number" />
          </label>

          <button class="btn btn-primary w-full" :disabled="loading || !canRunSearch" @click="runSearch">
            {{ loading ? '生成中' : '生成候选' }}
          </button>
          <p v-if="error" class="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700 dark:border-danger-500/30 dark:bg-danger-500/10 dark:text-danger-200">{{ error }}</p>
        </div>
      </section>

      <section class="space-y-4">
        <div class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="card-title">热点商品候选</h2>
              <p class="muted mt-1">{{ result?.run.runId || '等待运行' }}</p>
            </div>
            <span class="badge-info">按 rank 排序</span>
          </div>

          <div class="mt-4 space-y-3">
            <button
              v-for="item in candidates"
              :key="item.id"
              type="button"
              class="w-full rounded-lg border p-3 text-left transition"
              :class="selectedCandidate?.id === item.id ? 'border-primary-300 bg-primary-50/70 dark:border-primary-500/40 dark:bg-primary-500/10' : 'border-accent-200 bg-white hover:bg-accent-50 dark:border-dark-700 dark:bg-dark-900/70 dark:hover:bg-dark-800/70'"
              @click="selectedCandidateId = item.id"
            >
              <div class="flex gap-3">
                <img :src="item.imageUrl" :alt="item.title" class="h-24 w-24 flex-none rounded-lg object-cover" />
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="badge-success">#{{ item.rank }}</span>
                    <span class="badge-info">{{ candidateMarketLabel(item) }}</span>
                  </div>
                  <h3 class="mt-2 line-clamp-2 text-base font-bold text-accent-950 dark:text-white">{{ item.title }}</h3>
                  <div class="mt-2 flex flex-wrap gap-2 text-xs text-accent-500 dark:text-accent-400">
                    <span>{{ priceLabel(item) }}</span>
                    <span>评分 {{ item.rating.toFixed(1) }}</span>
                    <span>评论 {{ item.reviewCount }}</span>
                  </div>
                </div>
                <div class="w-16 flex-none text-right">
                  <p class="text-xl font-black" :class="scoreTone(item.hotScore)">{{ item.hotScore.toFixed(1) }}</p>
                  <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">热度</p>
                </div>
              </div>
            </button>

            <div v-if="!candidates.length" class="rounded-lg border border-dashed border-accent-300 p-8 text-center text-sm text-accent-500 dark:border-dark-700 dark:text-accent-300">
              选择目标市场并输入关键词后，生成一组临时热点商品。
            </div>
          </div>
        </div>

        <div class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div>
            <h2 class="card-title">来源状态</h2>
            <p class="muted mt-1">本次运行只返回临时数据，不保存候选商品。</p>
          </div>
          <div class="mt-4 overflow-auto rounded-lg border border-accent-200 dark:border-dark-700">
            <table class="w-full min-w-[560px] text-left text-sm">
              <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
                <tr>
                  <th class="p-3">来源</th>
                  <th class="p-3">市场</th>
                  <th class="p-3">策略</th>
                  <th class="p-3">状态</th>
                  <th class="p-3 text-right">数量</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
                <tr v-for="status in result?.sourceStatus || []" :key="`${status.sourceId}-${status.market}`">
                  <td class="p-3 font-semibold text-accent-950 dark:text-white">{{ sourceLabel(status.source) }}</td>
                  <td class="whitespace-nowrap p-3 text-accent-700 dark:text-accent-200">{{ productResearchMarketLabel(status.market) }}</td>
                  <td class="whitespace-nowrap p-3 text-accent-700 dark:text-accent-200">{{ productResearchStrategyLabel(status.providerStrategy) }}</td>
                  <td class="whitespace-nowrap p-3"><span :class="statusClass(status.status)">{{ statusLabel(status.status) }}</span></td>
                  <td class="whitespace-nowrap p-3 text-right font-semibold text-accent-900 dark:text-white">{{ status.itemsFound }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <aside class="space-y-4">
        <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div>
            <h2 class="card-title">商品详情</h2>
            <p class="muted mt-1">{{ selectedCandidate?.sourceName || '-' }}</p>
          </div>

          <template v-if="selectedCandidate">
            <img :src="selectedCandidate.imageUrl" :alt="selectedCandidate.title" class="mt-5 aspect-square w-full rounded-lg object-cover" />
            <div class="mt-4 flex flex-wrap items-center gap-2">
              <span class="badge-success">#{{ selectedCandidate.rank }}</span>
              <span class="badge-info">{{ selectedCandidate.keyword }}</span>
              <span class="badge-muted">{{ selectedCandidate.site }}</span>
            </div>
            <h3 class="mt-4 text-lg font-bold text-accent-950 dark:text-white">{{ selectedCandidate.title }}</h3>

            <div class="mt-5 grid gap-3 sm:grid-cols-2 2xl:grid-cols-1">
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">价格</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ priceLabel(selectedCandidate) }}</p>
              </div>
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">热度</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ selectedCandidate.hotScore.toFixed(1) }}</p>
              </div>
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">评分</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ selectedCandidate.rating.toFixed(1) }}</p>
              </div>
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">评论数</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ selectedCandidate.reviewCount }}</p>
              </div>
            </div>

            <a class="btn btn-outline mt-5 w-full" :href="selectedCandidate.sourceUrl" target="_blank" rel="noreferrer">查看来源</a>
            <p class="mt-3 break-all text-xs text-accent-500 dark:text-accent-400">{{ selectedCandidate.sourceUrl }}</p>
          </template>

          <div v-else class="mt-5 rounded-lg border border-dashed border-accent-300 p-6 text-center text-sm text-accent-500 dark:border-dark-700 dark:text-accent-300">
            暂无商品。
          </div>
        </section>
      </aside>
    </div>
  </section>
</template>

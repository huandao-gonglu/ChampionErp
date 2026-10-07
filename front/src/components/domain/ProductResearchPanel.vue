<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  createProductResearchHotProductRun,
  fetchActiveProductResearchHotProductRun,
  fetchLatestProductResearchHotProductRun,
  fetchProductResearchHotProductRun,
  fetchProductResearchSettings,
} from '@/api/workflow/research'
import {
  productResearchMarketLabel,
  productResearchStrategyLabel,
} from '@/utils/productResearchLabels'
import ProductResearchSourcingPanel from './ProductResearchSourcingPanel.vue'
import type {
  HotProductCandidate,
  ProductResearchConfig,
  ProductResearchResponse,
  ProductResearchTargetMarket,
} from '@/types/workflow'

const form = ref({
  targetMarket: 'amazon-us',
  limit: 12,
  keyword: 'kitchen organizer',
})

const loading = ref(false)
const error = ref('')
const result = ref<ProductResearchResponse | null>(null)
const researchConfig = ref<ProductResearchConfig | null>(null)
const selectedCandidateId = ref('')
const imageLoadErrors = ref<Record<string, boolean>>({})
let pollTimer: number | null = null
let polling = false
const LAST_RUN_STORAGE_KEY = 'champion.erp.productResearch.lastRunId'

const candidates = computed(() => result.value?.items || [])
const targetMarketOptions = computed(() => {
  const markets = (researchConfig.value?.targetMarkets || []).filter(item => item.platform === 'amazon' && item.site === 'amazon.com')
  return markets.length ? markets : [{ id: 'amazon-us', displayName: 'Amazon US', platform: 'amazon', site: 'amazon.com', searchMethods: [], raw: {} }]
})
const selectedTargetMarket = computed<ProductResearchTargetMarket>(() => {
  return targetMarketOptions.value.find((item) => item.id === form.value.targetMarket) || targetMarketOptions.value[0]
})
const selectedCandidate = computed(() => {
  return candidates.value.find((item) => item.id === selectedCandidateId.value) || candidates.value[0] || null
})
const canRunSearch = computed(() => Boolean(selectedTargetMarket.value?.id && form.value.keyword.trim()))
const runDescription = computed(() => {
  const status = result.value?.run.status || ''
  const description = result.value?.run.description || result.value?.description || ''
  const progressDescription = result.value?.run.progressDescription || ''
  if (isTerminalRunStatus(status) && progressDescription && progressDescription !== description) {
    return `${description}\n最近进度：${progressDescription.replace(/^正在返回结果：/, '')}`
  }
  return progressDescription || description
})
type QuotaReceipt = { request_consumed?: number; request_left?: number }
function quotaReceipts(value: unknown): QuotaReceipt[] { return Array.isArray(value) ? value : [] }
const successfulSources = computed(() => (result.value?.sourceStatus || []).filter((item) => item.status === 'success').length)

function buildPayload() {
  return {
    search_mode: 'target_only',
    keywords: [form.value.keyword.trim()],
    markets: {
      target_markets: [selectedTargetMarket.value.id],
      reference_markets: [],
    },
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

function isTerminalRunStatus(status: string) {
  return ['completed', 'failed'].includes(status)
}

function applyRunResponse(next: ProductResearchResponse) {
  result.value = next
  if (next.run.runId) {
    rememberLastRunId(next.run.runId)
  }
  const currentIds = new Set(next.items.map((item) => item.id))
  imageLoadErrors.value = Object.fromEntries(
    Object.entries(imageLoadErrors.value).filter(([id]) => currentIds.has(id)),
  )
  if (!next.items.some((item) => item.id === selectedCandidateId.value)) {
    selectedCandidateId.value = next.items[0]?.id || ''
  }
}

function readLastRunId() {
  try {
    return window.sessionStorage.getItem(LAST_RUN_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

function rememberLastRunId(runId: string) {
  try {
    window.sessionStorage.setItem(LAST_RUN_STORAGE_KEY, runId)
  } catch {
    // 浏览器存储不可用时只跳过本地恢复，后端活跃任务查询仍可兜底。
  }
}

function forgetLastRunId() {
  try {
    window.sessionStorage.removeItem(LAST_RUN_STORAGE_KEY)
  } catch {
    // ignore
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollRun(runId: string) {
  if (!runId || polling || document.hidden) return
  polling = true
  try {
    const next = await fetchProductResearchHotProductRun(runId)
    applyRunResponse(next)
    if (isTerminalRunStatus(next.run.status)) {
      stopPolling()
      loading.value = false
    }
  } catch (exc) {
    stopPolling()
    loading.value = false
    forgetLastRunId()
    error.value = exc instanceof Error ? exc.message : '读取选品运行状态失败'
  } finally {
    polling = false
  }
}

function startPolling(runId: string) {
  stopPolling()
  pollTimer = window.setInterval(() => {
    void pollRun(runId)
  }, 2000)
  void pollRun(runId)
}

async function runSearch() {
  stopPolling()
  loading.value = true
  error.value = ''
  try {
    const payload = buildPayload()
    const next = await createProductResearchHotProductRun(payload)
    applyRunResponse(next)
    if (next.run.runId && !isTerminalRunStatus(next.run.status)) {
      startPolling(next.run.runId)
      return
    }
    loading.value = false
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '选品搜索失败'
    loading.value = false
  }
}

async function loadLatestRun() {
  error.value = ''
  try {
    const latest = await fetchLatestProductResearchHotProductRun()
    if (latest) {
      applyRunResponse(latest)
      if (!isTerminalRunStatus(latest.run.status)) {
        loading.value = true
        startPolling(latest.run.runId)
      }
    }
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '读取最近调研失败'
  }
}

async function restoreRunAfterRefresh() {
  const storedRunId = readLastRunId()
  if (storedRunId) {
    try {
      const storedRun = await fetchProductResearchHotProductRun(storedRunId)
      applyRunResponse(storedRun)
      if (!isTerminalRunStatus(storedRun.run.status)) {
        loading.value = true
        startPolling(storedRun.run.runId)
      }
      return
    } catch {
      forgetLastRunId()
    }
  }
  try {
    const activeRun = await fetchActiveProductResearchHotProductRun() || await fetchLatestProductResearchHotProductRun()
    if (!activeRun?.run.runId) return
    applyRunResponse(activeRun)
    if (!isTerminalRunStatus(activeRun.run.status)) {
      loading.value = true
      startPolling(activeRun.run.runId)
    }
  } catch {
    // 恢复运行状态失败不阻断页面使用，用户仍可手动重新生成。
  }
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

function runStatusClass(value: string) {
  if (value === 'completed') return 'badge-success'
  if (value === 'running' || value === 'queued') return 'badge-info'
  if (value === 'failed') return 'badge-danger'
  return 'badge-muted'
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
  return value || '-'
}

function hasCandidateImage(item: HotProductCandidate | null) {
  return Boolean(item?.imageUrl && !imageLoadErrors.value[item.id])
}

function markCandidateImageFailed(id: string) {
  if (!id) return
  imageLoadErrors.value = {
    ...imageLoadErrors.value,
    [id]: true,
  }
}

onMounted(async () => {
  try {
    await loadSettings()
    await restoreRunAfterRefresh()
  } catch {
    researchConfig.value = null
  }
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <section class="space-y-6">
    <div class="grid gap-4 md:grid-cols-4">
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">商品候选</p>
        <p class="mt-2 text-2xl font-black text-accent-950 dark:text-white">{{ candidates.length }}</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">数据来源</p>
        <p class="mt-2 text-2xl font-black">Sorftime</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">正常来源</p>
        <p class="mt-2 text-2xl font-black text-success-700 dark:text-success-300">{{ successfulSources }}</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">运行状态</p>
        <p class="mt-3"><span :class="runStatusClass(result?.run.status || 'idle')">{{ runStatusLabel(result?.run.status || 'idle') }}</span></p>
      </div>
    </div>

    <div class="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)_360px] 2xl:grid-cols-[360px_minmax(0,1fr)_420px]">
      <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 class="card-title">搜索条件</h2>
            <p class="muted mt-1">输入商品关键词，查询真实商品及市场指标。</p>
          </div>
          <span class="badge-info">调研记录</span>
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

          <label class="block"><span class="mb-2 block text-sm font-semibold">商品关键词（美国站建议英文）</span><input v-model="form.keyword" class="input" maxlength="200" :disabled="loading" /></label>
          <p class="muted">查询第一页预计消耗 2 个额度，不自动翻页或补足数量。</p>
          <label class="block">
            <span class="mb-2 block text-sm font-semibold text-accent-700 dark:text-accent-200">结果数量</span>
            <input v-model.number="form.limit" class="input" min="1" max="50" type="number" />
          </label>

          <button class="btn btn-primary w-full" :disabled="loading || !canRunSearch" @click="runSearch">
            {{ loading ? '运行中' : '生成候选' }}
          </button>
          <button class="btn btn-secondary w-full" :disabled="loading" @click="loadLatestRun">读取最近调研（不消耗额度）</button>
          <p
            v-if="runDescription"
            class="max-h-28 overflow-auto whitespace-pre-wrap rounded-lg border border-accent-200 bg-accent-50/70 px-3 py-2 text-xs leading-5 text-accent-500 dark:border-dark-700 dark:bg-dark-950/50 dark:text-accent-400"
          >
            {{ runDescription }}
          </p>
          <p v-if="error" class="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700 dark:border-danger-500/30 dark:bg-danger-500/10 dark:text-danger-200">{{ error }}</p>
        </div>
      </section>

      <section class="space-y-4">
        <div class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="card-title">商品候选</h2>
              <p class="muted mt-1">{{ result?.run.runId || '等待运行' }}</p>
            </div>
            <span class="badge-info">数据源返回顺序</span>
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
                <img
                  v-if="hasCandidateImage(item)"
                  :src="item.imageUrl"
                  :alt="item.title"
                  class="h-24 w-24 flex-none rounded-lg object-cover"
                  @error="markCandidateImageFailed(item.id)"
                />
                <div v-else class="flex h-24 w-24 flex-none items-center justify-center rounded-lg border border-dashed border-accent-300 bg-accent-50 text-xs font-semibold text-accent-400 dark:border-dark-700 dark:bg-dark-950/50 dark:text-accent-500">
                  暂无图片
                </div>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="badge-success">#{{ item.rank }}</span>
                    <span class="badge-info">{{ candidateMarketLabel(item) }}</span>
                  </div>
                  <h3 class="mt-2 line-clamp-2 text-base font-bold text-accent-950 dark:text-white">{{ item.title }}</h3>
                  <div class="mt-2 flex flex-wrap gap-2 text-xs text-accent-500 dark:text-accent-400">
                    <span>{{ priceLabel(item) }}</span>
                    <span>评分 {{ item.rating?.toFixed(1) ?? '暂无' }}</span>
                    <span>评分数 {{ item.reviewCount ?? '暂无' }}</span>
                  </div>
                </div>
                <div class="w-16 flex-none text-right">
                  <p class="text-xl font-black">{{ item.monthlySales ?? '暂无' }}</p>
                  <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">预估月销量</p>
                </div>
              </div>
            </button>

            <div v-if="!candidates.length" class="rounded-lg border border-dashed border-accent-300 p-8 text-center text-sm text-accent-500 dark:border-dark-700 dark:text-accent-300">
              选择市场并输入关键词后开始查询。
            </div>
          </div>
        </div>

        <div class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div>
            <h2 class="card-title">来源状态</h2>
            <p class="muted mt-1">调研结果自动保存；确认货源后才写入商品库。</p>
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
          <p v-for="status in result?.sourceStatus || []" :key="status.sourceId" class="muted mt-2">
            {{ status.errorMessage || status.diagnosticMessage }}
            <span v-for="(receipt, index) in quotaReceipts(status.raw.quota_receipts)" :key="index">本次消耗 {{ receipt.request_consumed ?? '未知' }}，剩余 {{ receipt.request_left ?? '未知' }}。</span>
          </p>
        </div>
      </section>

      <aside class="space-y-4">
        <ProductResearchSourcingPanel v-if="selectedCandidate && result && selectedCandidate.sourceName === 'Sorftime'" :key="`${result.run.runId}:${selectedCandidate.id}`" :candidate="selectedCandidate" :run-id="result.run.runId" @imported="pollRun(result.run.runId)" />
        <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div>
            <h2 class="card-title">商品详情</h2>
            <p class="muted mt-1">{{ selectedCandidate?.sourceName || '-' }}</p>
          </div>

          <template v-if="selectedCandidate">
            <img
              v-if="hasCandidateImage(selectedCandidate)"
              :src="selectedCandidate.imageUrl"
              :alt="selectedCandidate.title"
              class="mt-5 aspect-square w-full rounded-lg object-cover"
              @error="markCandidateImageFailed(selectedCandidate.id)"
            />
            <div v-else class="mt-5 flex aspect-square w-full items-center justify-center rounded-lg border border-dashed border-accent-300 bg-accent-50 text-sm font-semibold text-accent-400 dark:border-dark-700 dark:bg-dark-950/50 dark:text-accent-500">
              暂无图片
            </div>
            <div class="mt-4 flex flex-wrap items-center gap-2">
              <span class="badge-success">#{{ selectedCandidate.rank }}</span>
              <span v-if="selectedCandidate.keyword" class="badge-info">{{ selectedCandidate.keyword }}</span>
              <span class="badge-muted">{{ selectedCandidate.site }}</span>
            </div>
            <h3 class="mt-4 text-lg font-bold text-accent-950 dark:text-white">{{ selectedCandidate.title }}</h3>

            <div class="mt-5 grid gap-3 sm:grid-cols-2 2xl:grid-cols-1">
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">价格</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ priceLabel(selectedCandidate) }}</p>
              </div>
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">预估月销量</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ selectedCandidate.monthlySales ?? '暂无' }}</p>
              </div>
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">评分</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ selectedCandidate.rating?.toFixed(1) ?? '暂无' }}</p>
              </div>
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">评分数量</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ selectedCandidate.reviewCount ?? '暂无' }}</p>
              </div>
            </div>

            <a class="btn btn-outline mt-5 w-full" :href="selectedCandidate.sourceUrl" target="_blank" rel="noreferrer">查看来源</a>
            <p class="muted mt-3">月销量为 Sorftime 的 Listing 级估算，可能包含多个变体。采集时间：{{ selectedCandidate.collectedAt }}；数据更新时间：{{ selectedCandidate.raw.data_updated_at || '未提供' }}。</p>
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

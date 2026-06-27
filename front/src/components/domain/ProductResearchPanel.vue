<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  createProductResearchSearchTask,
  fetchProductResearchSearchTask,
  fetchProductResearchSettings,
} from '@/api/workflow'
import type {
  ProductResearchCandidate,
  ProductResearchConfig,
  ProductResearchResponse,
  ProductResearchSearchMode,
  ProductResearchSourceRegistryItem,
  ProductResearchTargetMarket,
} from '@/types/workflow'

const searchModes: Array<{ value: ProductResearchSearchMode; label: string }> = [
  { value: 'target_only', label: '目标市场' },
  { value: 'target_plus_reference', label: '目标 + 相近' },
  { value: 'global_scan', label: '全球扫描' },
]

const riskOptions = [
  { value: 'food', label: '食品' },
  { value: 'battery', label: '电池' },
  { value: 'children_product', label: '儿童' },
  { value: 'medical_device', label: '医疗' },
  { value: 'cosmetics', label: '化妆品' },
  { value: 'liquid', label: '液体' },
]

const form = ref({
  searchMode: 'target_plus_reference' as ProductResearchSearchMode,
  targetMarket: 'US',
  keywordText: 'mahjong\ncustom name stamp\nred envelope\noriental home decor',
  excludeRisks: ['food', 'battery', 'children_product', 'medical_device', 'cosmetics', 'liquid'],
  limit: 12,
})

const loading = ref(false)
const error = ref('')
const result = ref<ProductResearchResponse | null>(null)
const researchConfig = ref<ProductResearchConfig | null>(null)
const sourceRegistry = ref<ProductResearchSourceRegistryItem[]>([])
const selectedCandidateId = ref('')

const candidates = computed(() => result.value?.items || [])
const targetMarketOptions = computed(() => {
  const markets = researchConfig.value?.targetMarkets.filter((item) => item.enabled) || []
  return markets.length ? markets : [{ market: 'US', name: 'United States', enabled: true, language: 'en', currency: 'USD', referenceMarkets: ['GB', 'CA', 'AU'], providerIds: [], raw: {} }]
})
const selectedTargetMarket = computed<ProductResearchTargetMarket>(() => {
  return targetMarketOptions.value.find((item) => item.market === form.value.targetMarket) || targetMarketOptions.value[0]
})
const boundProviderIds = computed(() => selectedTargetMarket.value?.providerIds || [])
const boundProviders = computed(() => {
  const providers = researchConfig.value?.searchProviders || sourceRegistry.value
  return boundProviderIds.value
    .map((id) => providers.find((provider) => provider.id === id || provider.platform === id))
    .filter(Boolean) as ProductResearchSourceRegistryItem[]
})
const activeReferenceMarkets = computed(() => {
  if (form.value.searchMode === 'target_only') return []
  return selectedTargetMarket.value?.referenceMarkets || []
})
const selectedCandidate = computed(() => {
  return candidates.value.find((item) => item.candidateId === selectedCandidateId.value) || candidates.value[0] || null
})

const averageScore = computed(() => {
  if (!candidates.value.length) return 0
  return candidates.value.reduce((sum, item) => sum + item.opportunityScore, 0) / candidates.value.length
})

const successfulSources = computed(() => (result.value?.sourceStatus || []).filter((item) => ['success', 'cached'].includes(item.status)).length)
const blockedSources = computed(() => (result.value?.sourceStatus || []).filter((item) => !['success', 'cached'].includes(item.status)).length)
const searchKeywords = computed(() => parseKeywords(form.value.keywordText))

function toggleSelection(listName: 'excludeRisks', value: string) {
  const set = new Set(form.value[listName])
  if (set.has(value)) set.delete(value)
  else set.add(value)
  form.value[listName] = Array.from(set)
}

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
    search_mode: form.value.searchMode,
    markets: {
      target_markets: [selectedTargetMarket.value.market],
      reference_markets: activeReferenceMarkets.value,
    },
    keywords: searchKeywords.value,
    product_intent: {
      keyword_required: true,
    },
    filters: {
      upgrade_types: [],
      exclude_risks: form.value.excludeRisks,
    },
    sources: {
      demand_sources: boundProviderIds.value,
    },
    result_options: {
      limit: form.value.limit,
      sort_by: 'opportunity_score',
    },
  }
}

async function loadSettings() {
  researchConfig.value = await fetchProductResearchSettings()
  sourceRegistry.value = researchConfig.value.sourceRegistry
  if (!targetMarketOptions.value.some((market) => market.market === form.value.targetMarket)) {
    form.value.targetMarket = targetMarketOptions.value[0]?.market || 'US'
  }
}

async function runSearch() {
  loading.value = true
  error.value = ''
  try {
    if (!searchKeywords.value.length) {
      throw new Error('请先输入关键词')
    }
    const next = await createProductResearchSearchTask(buildPayload())
    result.value = next
    selectedCandidateId.value = next.items[0]?.candidateId || ''
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '选品搜索失败'
  } finally {
    loading.value = false
  }
}

async function refreshTask() {
  if (!result.value?.task.taskId) return
  loading.value = true
  error.value = ''
  try {
    result.value = await fetchProductResearchSearchTask(result.value.task.taskId)
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '刷新任务失败'
  } finally {
    loading.value = false
  }
}

function scoreTone(score: number) {
  if (score >= 85) return 'text-success-700 dark:text-success-300'
  if (score >= 75) return 'text-info-700 dark:text-info-300'
  return 'text-warning-700 dark:text-warning-300'
}

function bandLabel(value: string) {
  const labels: Record<string, string> = {
    high: '高',
    medium: '中',
    low: '低',
  }
  return labels[value] || value || '-'
}

function actionLabel(value: string) {
  const labels: Record<string, string> = {
    priority_manual_research: '重点人工研究',
    candidate_pool: '进入候选池',
    watch: '观察',
    drop: '放弃',
  }
  return labels[value] || value || '-'
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    success: '成功',
    cached: '缓存',
    skipped: '跳过',
    configuration_required: '待配置',
    failed: '失败',
  }
  return labels[value] || value || '-'
}

function taskStatusLabel(value: string) {
  const labels: Record<string, string> = {
    completed: '已完成',
    running: '运行中',
    queued: '排队中',
    failed: '失败',
    idle: '待运行',
  }
  return labels[value] || value || '待运行'
}

function statusClass(value: string) {
  if (['success', 'cached'].includes(value)) return 'badge-success'
  if (value === 'configuration_required') return 'badge-info'
  if (value === 'failed') return 'badge-danger'
  return 'badge-muted'
}

function scoreBreakdownLabel(value: string) {
  const labels: Record<string, string> = {
    search_interest: '搜索热度',
    china_element_fit: '关键词匹配',
    wait_tolerance: '等待度',
    local_scarcity: '稀缺度',
    upgrade_space: '差异化空间',
    logistics_fit: '物流适配',
    compliance_fit: '合规适配',
  }
  return labels[value] || value
}

function sourceRegistryItem(source: string, sourceId = '') {
  const sourceKey = source.toLowerCase()
  const idKey = sourceId.toLowerCase()
  return sourceRegistry.value.find((item) => {
    return item.id.toLowerCase() === idKey
      || item.id.toLowerCase() === sourceKey
      || item.platform.toLowerCase() === sourceKey
      || item.name.toLowerCase() === sourceKey
  })
}

function providerDisplayName(provider: ProductResearchSourceRegistryItem) {
  return provider.name || provider.id || provider.platform
}

onMounted(async () => {
  try {
    await loadSettings()
  } catch {
    researchConfig.value = null
    sourceRegistry.value = []
  }
  await runSearch()
})
</script>

<template>
  <section class="space-y-6">
    <div class="grid gap-4 md:grid-cols-4">
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">候选数</p>
        <p class="mt-2 text-2xl font-black text-accent-950 dark:text-white">{{ candidates.length }}</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">平均评分</p>
        <p class="mt-2 text-2xl font-black" :class="scoreTone(averageScore)">{{ averageScore.toFixed(1) }}</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">正常来源</p>
        <p class="mt-2 text-2xl font-black text-success-700 dark:text-success-300">{{ successfulSources }}</p>
      </div>
      <div class="rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <p class="text-xs font-semibold uppercase text-accent-500 dark:text-accent-400">需关注</p>
        <p class="mt-2 text-2xl font-black text-info-700 dark:text-info-300">{{ blockedSources }}</p>
      </div>
    </div>

    <div class="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)_360px] 2xl:grid-cols-[360px_minmax(0,1fr)_420px]">
      <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 class="card-title">搜索条件</h2>
            <p class="muted mt-1">目标市场为主，相近市场辅助。</p>
          </div>
          <span class="badge-info">{{ form.searchMode === 'target_only' ? '正式' : form.searchMode === 'global_scan' ? '灵感' : '扩词' }}</span>
        </div>

        <div class="mt-5 space-y-5">
          <div>
            <label class="mb-2 block text-sm font-semibold text-accent-700 dark:text-accent-200">搜索模式</label>
            <div class="grid grid-cols-3 gap-2">
              <button
                v-for="mode in searchModes"
                :key="mode.value"
                type="button"
                class="btn min-h-11 px-2 text-xs"
                :class="form.searchMode === mode.value ? 'btn-primary' : 'btn-outline'"
                @click="form.searchMode = mode.value"
              >
                {{ mode.label }}
              </button>
            </div>
          </div>

          <div class="grid gap-3 sm:grid-cols-2 2xl:grid-cols-1">
            <label class="block">
              <span class="mb-2 block text-sm font-semibold text-accent-700 dark:text-accent-200">目标市场</span>
              <select v-model="form.targetMarket" class="input">
                <option v-for="market in targetMarketOptions" :key="market.market" :value="market.market">
                  {{ market.market }} · {{ market.name || market.currency }}
                </option>
              </select>
            </label>
            <label class="block">
              <span class="mb-2 block text-sm font-semibold text-accent-700 dark:text-accent-200">相近市场</span>
              <input class="input" :value="activeReferenceMarkets.join(', ')" disabled />
            </label>
          </div>

          <div>
            <label class="block">
              <div class="mb-2 flex items-center justify-between gap-3">
                <span class="text-sm font-semibold text-accent-700 dark:text-accent-200">关键词</span>
                <span class="text-xs text-accent-500 dark:text-accent-400">{{ searchKeywords.length }} 个</span>
              </div>
              <textarea
                v-model="form.keywordText"
                class="input min-h-[104px] resize-y leading-6"
                placeholder="mahjong lamp, custom name stamp&#10;red envelope"
              ></textarea>
            </label>
          </div>

          <div>
            <div class="mb-2 flex items-center justify-between gap-3">
              <span class="text-sm font-semibold text-accent-700 dark:text-accent-200">绑定搜索手段</span>
              <span class="text-xs text-accent-500 dark:text-accent-400">启用 {{ boundProviderIds.length }}</span>
            </div>
            <div v-if="boundProviders.length" class="grid grid-cols-1 gap-2">
              <div
                v-for="provider in boundProviders"
                :key="provider.id"
                class="rounded-lg border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-100"
              >
                <div class="font-semibold">{{ providerDisplayName(provider) }}</div>
                <div class="mt-1 text-xs opacity-80">{{ provider.platform }} · {{ provider.providerStrategy || 'configured_api' }}</div>
              </div>
            </div>
            <div v-else class="rounded-lg border border-dashed border-accent-300 p-3 text-sm text-accent-500 dark:border-dark-700 dark:text-accent-300">
              当前市场未绑定搜索手段。
            </div>
          </div>

          <div>
            <span class="mb-2 block text-sm font-semibold text-accent-700 dark:text-accent-200">排除风险</span>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="item in riskOptions"
                :key="item.value"
                type="button"
                class="badge"
                :class="form.excludeRisks.includes(item.value) ? 'badge-danger' : 'badge-muted'"
                @click="toggleSelection('excludeRisks', item.value)"
              >
                {{ item.label }}
              </button>
            </div>
          </div>

          <label class="block">
            <span class="mb-2 block text-sm font-semibold text-accent-700 dark:text-accent-200">结果数量</span>
            <input v-model.number="form.limit" class="input" min="1" max="50" type="number" />
          </label>

          <div class="grid gap-2 sm:grid-cols-2 2xl:grid-cols-1">
            <button class="btn btn-primary" :disabled="loading" @click="runSearch">{{ loading ? '搜索中' : '生成候选' }}</button>
            <button class="btn btn-outline" :disabled="loading || !result?.task.taskId" @click="refreshTask">刷新任务</button>
          </div>
          <p v-if="error" class="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700 dark:border-danger-500/30 dark:bg-danger-500/10 dark:text-danger-200">{{ error }}</p>
        </div>
      </section>

      <section class="space-y-4">
        <div class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="card-title">候选机会</h2>
              <p class="muted mt-1">{{ result?.task.taskId || '等待任务' }}</p>
            </div>
            <span class="badge-success">{{ taskStatusLabel(result?.task.status || 'idle') }}</span>
          </div>
          <div class="mt-4 space-y-3">
            <button
              v-for="item in candidates"
              :key="item.candidateId"
              type="button"
              class="w-full rounded-lg border p-4 text-left transition"
              :class="selectedCandidate?.candidateId === item.candidateId ? 'border-primary-300 bg-primary-50/70 dark:border-primary-500/40 dark:bg-primary-500/10' : 'border-accent-200 bg-white hover:bg-accent-50 dark:border-dark-700 dark:bg-dark-900/70 dark:hover:bg-dark-800/70'"
              @click="selectedCandidateId = item.candidateId"
            >
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <h3 class="max-w-xl truncate text-base font-bold text-accent-950 dark:text-white">{{ item.overseasKeyword }}</h3>
                    <span class="badge-info">{{ item.targetMarket }}</span>
                  </div>
                  <div class="mt-2 flex flex-wrap gap-2">
                    <span v-for="source in item.relatedSources" :key="source" class="badge-muted">{{ source }}</span>
                  </div>
                </div>
                <div class="text-right">
                  <p class="text-2xl font-black" :class="scoreTone(item.opportunityScore)">{{ item.opportunityScore.toFixed(1) }}</p>
                  <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ actionLabel(item.recommendedAction) }}</p>
                </div>
              </div>
              <div class="mt-4 grid gap-3 sm:grid-cols-3">
                <div class="rounded-lg bg-accent-50 p-3 dark:bg-dark-950/70">
                  <p class="text-xs text-accent-500 dark:text-accent-400">关键词匹配</p>
                  <p class="mt-1 font-semibold text-accent-900 dark:text-white">{{ bandLabel(item.chinaElementStrength) }}</p>
                </div>
                <div class="rounded-lg bg-accent-50 p-3 dark:bg-dark-950/70">
                  <p class="text-xs text-accent-500 dark:text-accent-400">等待度</p>
                  <p class="mt-1 font-semibold text-accent-900 dark:text-white">{{ bandLabel(item.waitTolerance) }}</p>
                </div>
                <div class="rounded-lg bg-accent-50 p-3 dark:bg-dark-950/70">
                  <p class="text-xs text-accent-500 dark:text-accent-400">稀缺度</p>
                  <p class="mt-1 font-semibold text-accent-900 dark:text-white">{{ bandLabel(item.localScarcity) }}</p>
                </div>
              </div>
            </button>
            <div v-if="!candidates.length" class="rounded-lg border border-dashed border-accent-300 p-8 text-center text-sm text-accent-500 dark:border-dark-700 dark:text-accent-300">
              暂无候选。
            </div>
          </div>
        </div>

        <div class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="card-title">数据源状态</h2>
              <p class="muted mt-1">Provider 策略、市场和采集质量。</p>
            </div>
          </div>
          <div class="mt-4 overflow-auto rounded-lg border border-accent-200 dark:border-dark-700">
            <table class="w-full min-w-[760px] text-left text-sm">
              <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
                <tr>
                  <th class="p-3">来源</th>
                  <th class="p-3">市场</th>
                  <th class="p-3">策略</th>
                  <th class="p-3">状态</th>
                  <th class="p-3 text-right">Items</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
                <tr v-for="status in result?.sourceStatus || []" :key="`${status.sourceId}-${status.market}`">
                  <td class="p-3">
                    <div class="font-semibold text-accent-950 dark:text-white">{{ sourceRegistryItem(status.source, status.sourceId)?.name || status.source }}</div>
                    <div v-if="status.errorMessage" class="mt-1 max-w-md truncate text-xs text-accent-500 dark:text-accent-400">{{ status.errorMessage }}</div>
                  </td>
                  <td class="whitespace-nowrap p-3 text-accent-700 dark:text-accent-200">{{ status.market }}</td>
                  <td class="whitespace-nowrap p-3 text-accent-700 dark:text-accent-200">{{ status.providerStrategy || '-' }}</td>
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
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="card-title">机会详情</h2>
              <p class="muted mt-1">{{ selectedCandidate?.productType || '-' }}</p>
            </div>
            <span class="badge-success">{{ selectedCandidate ? actionLabel(selectedCandidate.recommendedAction) : '-' }}</span>
          </div>

          <template v-if="selectedCandidate">
            <div class="mt-5">
              <p class="text-sm font-semibold text-accent-700 dark:text-accent-200">采购关键词</p>
              <div class="mt-2 flex flex-wrap gap-2">
                <span v-for="keyword in selectedCandidate.chinesePurchaseKeywords" :key="keyword" class="badge-info">{{ keyword }}</span>
              </div>
            </div>

            <div class="mt-5">
              <p class="text-sm font-semibold text-accent-700 dark:text-accent-200">评分拆解</p>
              <div class="mt-3 space-y-3">
                <div v-for="(score, key) in selectedCandidate.scoreBreakdown" :key="key">
                  <div class="mb-1 flex justify-between gap-3 text-xs text-accent-500 dark:text-accent-400">
                    <span>{{ scoreBreakdownLabel(String(key)) }}</span>
                    <span>{{ score.toFixed(1) }}</span>
                  </div>
                  <div class="h-2 rounded-full bg-accent-100 dark:bg-dark-800">
                    <div class="h-2 rounded-full bg-primary-500" :style="{ width: `${Math.min(100, Math.max(6, score * 5))}%` }" />
                  </div>
                </div>
              </div>
            </div>

            <div class="mt-5 grid gap-3 sm:grid-cols-2 2xl:grid-cols-1">
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">物流风险</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ selectedCandidate.logisticsRisks.join(', ') || '-' }}</p>
              </div>
              <div class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
                <p class="text-xs text-accent-500 dark:text-accent-400">合规排除</p>
                <p class="mt-1 text-sm font-semibold text-accent-800 dark:text-white">{{ selectedCandidate.complianceRisks.join(', ') || '-' }}</p>
              </div>
            </div>
          </template>
        </section>

        <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div>
            <h2 class="card-title">证据信号</h2>
            <p class="muted mt-1">{{ selectedCandidate?.evidenceSignals.length || 0 }} 条信号</p>
          </div>
          <div class="mt-4 space-y-3">
            <div v-for="signal in selectedCandidate?.evidenceSignals || []" :key="`${signal.sourceId}-${signal.market}-${signal.keyword}-${signal.dataType}`" class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="badge-muted">{{ signal.source }} · {{ signal.market }}</span>
                <span class="text-xs text-accent-500 dark:text-accent-400">{{ signal.dataType }}</span>
              </div>
              <p class="mt-3 font-semibold text-accent-950 dark:text-white">{{ signal.title || signal.keyword }}</p>
              <div class="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div v-if="signal.metrics.searchInterest" class="rounded bg-accent-50 p-2 dark:bg-dark-950/70">搜索 {{ signal.metrics.searchInterest }}</div>
                <div v-if="signal.metrics.reviewCount" class="rounded bg-accent-50 p-2 dark:bg-dark-950/70">评论 {{ signal.metrics.reviewCount }}</div>
                <div v-if="signal.metrics.rating" class="rounded bg-accent-50 p-2 dark:bg-dark-950/70">评分 {{ signal.metrics.rating }}</div>
                <div v-if="signal.metrics.contentHeat" class="rounded bg-accent-50 p-2 dark:bg-dark-950/70">热度 {{ signal.metrics.contentHeat }}</div>
              </div>
            </div>
          </div>
        </section>
      </aside>
    </div>
  </section>
</template>

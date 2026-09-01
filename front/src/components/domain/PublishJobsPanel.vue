<script setup lang="ts">
import { computed, ref } from 'vue'
import { statusBadgeClass } from '@/utils/status'
import type {
  Marketplace,
  MarketplaceOption,
  MarketplaceSiteToSell,
  PublishJobListItem,
  PublishJobMarketResultSummary,
  PublishJobPlatformSummary,
  UnknownRecord,
} from '@/types/workflow'

const props = defineProps<{
  jobs: PublishJobListItem[]
  selectedJobId: string
  selectedJobStatus: UnknownRecord | null
  loading: boolean
  nextCursor: string
  lastUpdated: string
  precheckOk: boolean
  activeMarketplace: Marketplace
  platformOptions: MarketplaceOption[]
  busy: boolean
}>()

const emit = defineEmits<{
  refresh: []
  select: [jobId: string]
  loadMore: []
  enqueue: []
  publishDirect: []
  reconcile: [jobId: string, platform: Marketplace]
}>()

const statusFilter = ref('')
const platformFilter = ref('')
const searchQuery = ref('')

const MERCADOLIBRE_SHIPPING_MODE_NOT_SUPPORTED = 'MERCADOLIBRE_SHIPPING_MODE_NOT_SUPPORTED'
const MERCADOLIBRE_LEGACY_CATEGORY_LOGISTICS_ERROR = 'MERCADOLIBRE_CATEGORY_MARKET_LOGISTICS_UNSUPPORTED'
const MERCADOLIBRE_MARKET_NOT_OPERABLE = 'MERCADOLIBRE_MARKET_NOT_OPERABLE'
const MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED = 'MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED'
const MERCADOLIBRE_LOCAL_RATE_LIMITED = 'MERCADOLIBRE_LOCAL_RATE_LIMITED'

const mercadoLibreErrorGuidance: Record<string, { summary: string; nextAction: string }> = {
  [MERCADOLIBRE_SHIPPING_MODE_NOT_SUPPORTED]: {
    summary: '当前发布方式不支持该市场的跨境物流。',
    nextAction: '检查店铺、销售市场与物流能力；确认不支持时移除该市场。',
  },
  [MERCADOLIBRE_MARKET_NOT_OPERABLE]: {
    summary: '该销售市场当前不支持国际跨境直发。',
    nextAction: '移除该销售市场，平台重新开放后再添加。',
  },
  [MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED]: {
    summary: '发货包装的尺寸或重量超过当前物流限制。',
    nextAction: '按实际发货外包装修正长、宽、高和重量，然后重新核价与预检。',
  },
  [MERCADOLIBRE_LOCAL_RATE_LIMITED]: {
    summary: '平台当前请求受限，暂时无法完成该市场发布。',
    nextAction: '等待限流窗口恢复后重试该市场。',
  },
}
const mercadoLibreKnownErrorCodes = new Set(Object.keys(mercadoLibreErrorGuidance))

interface PublishErrorSource {
  error?: string
  errorCode?: string
  nextAction?: string
}

interface PublishErrorPresentation {
  code: string
  summary: string
  nextAction: string
}

const statusLabels: Record<string, string> = {
  queued: '排队中',
  running: '发布中',
  success: '发布成功',
  failed: '发布失败',
  partial: '部分成功',
  outcome_unknown: '结果待对账',
}

const stageLabels: Record<string, string> = {
  queued: '等待执行',
  resuming: '恢复执行',
  resolving_category: '解析类目',
  validating: '校验商品',
  validating_required_attributes: '校验必填属性',
  publishing: '提交平台',
  publishing_approved_payload: '提交已确认 Payload',
  waiting_platform_confirmation: '等待平台确认',
  retrying: '等待重试',
  finished: '已结束',
  failed: '已结束',
  partial: '已结束',
  outcome_unknown: '停止重放，等待对账',
}

const filterPlatforms = computed(() => Array.from(new Set(
  props.jobs.flatMap((job) => job.platforms.map((item) => item.platform)),
)).sort())

const filteredJobs = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return props.jobs.filter((job) => {
    if (statusFilter.value && job.status !== statusFilter.value) return false
    if (platformFilter.value && !job.platforms.some((item) => item.platform === platformFilter.value)) return false
    if (!query) return true
    return [
      job.jobId,
      job.productId,
      job.productName,
      job.draftId,
      job.error,
      job.errorCode,
      job.nextAction,
      ...job.platforms.flatMap((item) => [item.error, item.errorCode, item.nextAction]),
      ...job.platforms.flatMap((item) => (item.marketResults || []).flatMap((market) => [
        market.siteId,
        market.itemId,
        market.error,
        market.errorCode,
      ])),
    ]
      .some((value) => String(value || '').toLowerCase().includes(query))
  })
})

const selectedJob = computed(() => (
  props.jobs.find((job) => job.jobId === props.selectedJobId) || null
))
const selectedJobErrorPresentation = computed(() => (
  selectedJob.value ? jobErrorPresentation(selectedJob.value) : null
))

const detailForDisplay = computed(() => {
  if (!props.selectedJobStatus) return null
  const detail = { ...props.selectedJobStatus }
  delete detail.product
  return detail
})

function statusLabel(status: string) {
  return statusLabels[status] || status || '未知'
}

function stageLabel(stage: string) {
  return stageLabels[stage] || stage || '-'
}

function mercadoLibreErrorCode(explicitCode: string, rawError: string) {
  const upper = `${explicitCode} ${rawError}`.toUpperCase()
  const lower = rawError.toLowerCase()
  if (
    upper.includes(MERCADOLIBRE_SHIPPING_MODE_NOT_SUPPORTED)
    || upper.includes(MERCADOLIBRE_LEGACY_CATEGORY_LOGISTICS_ERROR)
    || lower.includes('item.shipping.mode.not_supported')
    || lower.includes("can't send the product in this kind of shipment")
  ) return MERCADOLIBRE_SHIPPING_MODE_NOT_SUPPORTED
  if (
    upper.includes(MERCADOLIBRE_MARKET_NOT_OPERABLE)
    || lower.includes('site.not_operable')
    || lower.includes('currently unavailable for international dropshipping')
  ) return MERCADOLIBRE_MARKET_NOT_OPERABLE
  if (
    upper.includes(MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED)
    || /item\.package_[a-z_]+\.over_max/.test(lower)
  ) return MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED
  if (
    upper.includes(MERCADOLIBRE_LOCAL_RATE_LIMITED)
    || lower.includes('local_rate_limited')
  ) return MERCADOLIBRE_LOCAL_RATE_LIMITED
  return explicitCode
}

function publishErrorPresentation(source: PublishErrorSource): PublishErrorPresentation | null {
  const rawError = String(source.error || '').trim()
  const explicitCode = String(source.errorCode || '').trim().toUpperCase()
  const code = mercadoLibreErrorCode(explicitCode, rawError)
  const stableGuidance = mercadoLibreErrorGuidance[code]
  const nextAction = String(source.nextAction || '').trim()
  const hasLegacyCategoryDiagnosis = (
    explicitCode === MERCADOLIBRE_LEGACY_CATEGORY_LOGISTICS_ERROR
    || rawError.toUpperCase().includes(MERCADOLIBRE_LEGACY_CATEGORY_LOGISTICS_ERROR)
    || /CBT\d+|\u5171\u4eab\s*CBT\s*\u7c7b\u76ee|Global Item/.test(nextAction)
  )
  if (stableGuidance) {
    return {
      code,
      summary: stableGuidance.summary,
      nextAction: hasLegacyCategoryDiagnosis
        ? stableGuidance.nextAction
        : nextAction || stableGuidance.nextAction,
    }
  }
  if (!rawError && !code && !nextAction) return null
  const readableSummary = /^[\u3400-\u9fff\d\s，。；、！？：]+$/.test(rawError)
    ? rawError
    : '发布失败，平台未返回可直接展示的原因。'
  return {
    code,
    summary: readableSummary,
    nextAction: nextAction || '请重新执行上架预检；若仍失败，在“查看技术详情”中核对平台返回。',
  }
}

function platformErrorPresentation(item: PublishJobPlatformSummary) {
  const marketError = (item.marketResults || [])
    .map(marketErrorPresentation)
    .find((error): error is PublishErrorPresentation => Boolean(error))
  return marketError || publishErrorPresentation(item)
}

function marketErrorPresentation(item: PublishJobMarketResultSummary) {
  const code = String(item.errorCode || '').trim()
  const rawError = String(item.error || '').trim()
  const summary = code || rawError
  if (!summary) return null
  return {
    code,
    summary,
    nextAction: '',
  }
}

function jobErrorPresentation(job: PublishJobListItem) {
  const marketError = job.platforms
    .flatMap((item) => item.marketResults || [])
    .map(marketErrorPresentation)
    .find((error): error is PublishErrorPresentation => Boolean(error))
  if (marketError) return marketError
  const platformErrors = job.platforms
    .map(platformErrorPresentation)
    .filter((error): error is PublishErrorPresentation => Boolean(error))
  const platformKnownError = platformErrors
    .find((error) => mercadoLibreKnownErrorCodes.has(error.code))
  return platformKnownError || publishErrorPresentation(job) || platformErrors[0] || null
}

function platformOption(platform: Marketplace) {
  const platformKey = String(platform || '').trim().toLowerCase()
  return props.platformOptions.find((option) => (
    String(option.key || '').trim().toLowerCase() === platformKey
  ))
}

function platformLabel(platform: Marketplace) {
  return platformOption(platform)?.label || String(platform || '').trim() || '-'
}

function siteLabel(option: MarketplaceOption | undefined, site: string) {
  const siteCode = String(site || '').trim()
  if (!siteCode) return ''
  const siteOption = option?.sites.find((item) => (
    item.code.toLowerCase() === siteCode.toLowerCase()
    || item.key.toLowerCase() === siteCode.toLowerCase()
  ))
  return siteOption ? `${siteOption.label}（${siteOption.code}）` : siteCode
}

function platformTargetLabel(
  platform: Marketplace,
  site: string,
  sitesToSell: MarketplaceSiteToSell[],
) {
  const option = platformOption(platform)
  const platformName = option?.label || String(platform || '').trim() || '-'
  const parentLabel = siteLabel(option, site)
  const baseLabel = parentLabel ? `${platformName} · ${parentLabel}` : platformName
  const parentKey = String(site || '').trim().toLowerCase()
  const salesLabels = sitesToSell.flatMap((target) => {
    const targetKey = String(target.siteId || '').trim().toLowerCase()
    if (!targetKey || targetKey === parentKey) return []
    return [siteLabel(option, target.siteId)]
  })
  return salesLabels.length
    ? `${baseLabel} → ${salesLabels.join('、')}`
    : baseLabel
}

function formatTime(value: string) {
  return String(value || '').replace('T', ' ').replace(/Z$/, '').slice(0, 19) || '-'
}

function selectJob(jobId: string) {
  emit('select', jobId)
}
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">发布任务</h2>
        <p class="muted mt-1">每次发布入队生成一条独立任务，运行中的任务会自动刷新。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-outline" :disabled="loading" @click="emit('refresh')">刷新列表</button>
        <button class="btn btn-primary" :disabled="busy || !precheckOk" @click="emit('enqueue')">发布入队</button>
        <button class="btn btn-outline" :disabled="busy || activeMarketplace === 'mercadolibre' || !precheckOk" @click="emit('publishDirect')">非 ML 直接发布</button>
      </div>
    </div>

    <div class="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.75fr)]">
      <section class="min-w-0 rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <div class="grid gap-3 md:grid-cols-[minmax(0,1fr)_160px_160px]">
          <input v-model="searchQuery" class="input" placeholder="搜索 Job ID、商品或错误" />
          <select v-model="statusFilter" class="input">
            <option value="">全部状态</option>
            <option value="queued">排队中</option>
            <option value="running">发布中</option>
            <option value="success">发布成功</option>
            <option value="failed">发布失败</option>
            <option value="partial">部分成功</option>
            <option value="outcome_unknown">结果待对账</option>
          </select>
          <select v-model="platformFilter" class="input">
            <option value="">全部平台</option>
            <option v-for="platform in filterPlatforms" :key="platform" :value="platform">{{ platformLabel(platform) }}</option>
          </select>
        </div>

        <div class="mt-4 overflow-x-auto rounded-lg border border-accent-200 dark:border-dark-700">
          <table class="min-w-[1160px] w-full table-fixed text-left text-sm">
            <colgroup>
              <col class="w-[145px]" />
              <col class="w-[190px]" />
              <col class="w-[190px]" />
              <col class="w-[280px]" />
              <col class="w-[110px]" />
              <col class="w-[110px]" />
              <col class="w-[70px]" />
              <col />
            </colgroup>
            <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
              <tr>
                <th class="p-3">创建时间</th>
                <th class="p-3">Job ID</th>
                <th class="p-3">商品</th>
                <th class="p-3">平台</th>
                <th class="p-3">状态</th>
                <th class="p-3">阶段</th>
                <th class="p-3">重试</th>
                <th class="p-3">结果摘要</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
              <tr
                v-for="job in filteredJobs"
                :key="job.jobId"
                class="cursor-pointer align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60"
                :class="job.jobId === selectedJobId ? 'bg-accent-50 dark:bg-dark-800/80' : ''"
                @click="selectJob(job.jobId)"
              >
                <td class="p-3 text-accent-600 dark:text-accent-300">{{ formatTime(job.createdAt) }}</td>
                <td class="p-3 font-mono text-xs font-semibold text-accent-950 dark:text-white">
                  <button class="text-left hover:underline" :title="job.jobId" @click.stop="selectJob(job.jobId)">{{ job.jobId }}</button>
                </td>
                <td class="p-3">
                  <span class="block truncate font-medium text-accent-950 dark:text-white" :title="job.productName || job.productId">{{ job.productName || job.productId || '-' }}</span>
                  <span class="mt-1 block truncate text-xs text-accent-500 dark:text-accent-400">{{ job.productId || '-' }}</span>
                </td>
                <td class="p-3">
                  <div class="flex flex-wrap gap-1.5">
                    <span
                      v-for="item in job.platforms"
                      :key="`${item.platform}:${item.site}`"
                      class="badge-info"
                      :title="platformTargetLabel(item.platform, item.site, item.sitesToSell)"
                    >{{ platformTargetLabel(item.platform, item.site, item.sitesToSell) }}</span>
                  </div>
                </td>
                <td class="p-3"><span :class="statusBadgeClass(job.status)">{{ statusLabel(job.status) }}</span></td>
                <td class="p-3 text-accent-700 dark:text-accent-200">{{ stageLabel(job.stage) }}</td>
                <td class="p-3 text-center text-accent-700 dark:text-accent-200">{{ job.attempts }}</td>
                <td class="p-3 text-accent-700 dark:text-accent-200"><span class="block truncate" :title="jobErrorPresentation(job)?.summary || '-'">{{ jobErrorPresentation(job)?.summary || '-' }}</span></td>
              </tr>
              <tr v-if="!filteredJobs.length"><td colspan="8" class="p-8 text-center text-accent-500 dark:text-accent-300">暂无匹配的发布任务。</td></tr>
            </tbody>
          </table>
        </div>

        <div class="mt-3 flex items-center justify-between gap-3 text-xs text-accent-500 dark:text-accent-400">
          <span>已显示 {{ filteredJobs.length }} / {{ jobs.length }} 条<span v-if="lastUpdated"> · 更新于 {{ formatTime(lastUpdated) }}</span></span>
          <button v-if="nextCursor" class="btn btn-outline px-3 py-1.5 text-xs" :disabled="loading" @click="emit('loadMore')">加载更多</button>
        </div>
      </section>

      <aside class="min-w-0 rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <template v-if="selectedJob">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="text-xs text-accent-500 dark:text-accent-400">任务详情</p>
              <h3 class="mt-1 break-all font-mono text-sm font-semibold text-accent-950 dark:text-white">{{ selectedJob.jobId }}</h3>
            </div>
            <span :class="statusBadgeClass(selectedJob.status)">{{ statusLabel(selectedJob.status) }}</span>
          </div>

          <dl class="mt-4 grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
            <dt class="text-accent-500 dark:text-accent-400">商品</dt><dd class="break-words text-accent-950 dark:text-white">{{ selectedJob.productName || selectedJob.productId || '-' }}</dd>
            <dt class="text-accent-500 dark:text-accent-400">草稿</dt><dd class="break-all text-accent-700 dark:text-accent-200">{{ selectedJob.draftId || '-' }}</dd>
            <dt class="text-accent-500 dark:text-accent-400">创建</dt><dd class="text-accent-700 dark:text-accent-200">{{ formatTime(selectedJob.createdAt) }}</dd>
            <dt class="text-accent-500 dark:text-accent-400">更新</dt><dd class="text-accent-700 dark:text-accent-200">{{ formatTime(selectedJob.updatedAt) }}</dd>
            <dt class="text-accent-500 dark:text-accent-400">重试</dt><dd class="text-accent-700 dark:text-accent-200">{{ selectedJob.attempts }} 次</dd>
          </dl>

          <div v-if="selectedJobErrorPresentation" data-testid="publish-job-error-guidance" class="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900/70 dark:bg-red-950/30 dark:text-red-200">
            <p class="font-semibold">失败原因</p>
            <p class="mt-1 break-words"><span class="font-medium">原因：</span>{{ selectedJobErrorPresentation.summary }}</p>
            <p v-if="selectedJobErrorPresentation.nextAction" class="mt-2 break-words font-medium">处理建议：{{ selectedJobErrorPresentation.nextAction }}</p>
          </div>

          <div class="mt-4 space-y-2">
            <article v-for="item in selectedJob.platforms" :key="`${item.platform}:${item.site}`" class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
              <div class="flex items-center justify-between gap-2">
                <span class="font-semibold text-accent-950 dark:text-white">{{ platformTargetLabel(item.platform, item.site, item.sitesToSell) }}</span>
                <span :class="statusBadgeClass(item.status)">{{ statusLabel(item.status) }}</span>
              </div>
              <p class="mt-2 text-xs text-accent-500 dark:text-accent-400">{{ stageLabel(item.stage) }} · 尝试 {{ item.attempts }} 次</p>
              <p class="mt-1 break-all text-xs text-accent-500 dark:text-accent-400">{{ item.draftId || '-' }}</p>
              <div v-if="platformErrorPresentation(item)" data-testid="publish-platform-error-guidance" class="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900/70 dark:bg-red-950/30 dark:text-red-200">
                <p class="break-words"><span class="font-medium">原因：</span>{{ platformErrorPresentation(item)?.summary }}</p>
                <p v-if="platformErrorPresentation(item)?.nextAction" class="mt-1 break-words font-medium">处理建议：{{ platformErrorPresentation(item)?.nextAction }}</p>
              </div>
              <div v-if="item.marketResults?.length" class="mt-3 space-y-2" data-testid="publish-market-results">
                <p class="text-xs font-medium text-accent-600 dark:text-accent-300">销售市场结果</p>
                <div
                  v-for="market in item.marketResults"
                  :key="`${market.siteId}:${market.logisticType}`"
                  class="rounded-md border border-accent-100 p-2 text-xs dark:border-dark-700"
                >
                  <div class="flex items-center justify-between gap-2">
                    <span class="font-medium text-accent-900 dark:text-white">{{ siteLabel(platformOption(item.platform), market.siteId) }}<span v-if="market.logisticType"> · {{ market.logisticType }}</span></span>
                    <span :class="statusBadgeClass(market.status)">{{ statusLabel(market.status) }}</span>
                  </div>
                  <p v-if="market.itemId" class="mt-1 break-all text-accent-500 dark:text-accent-400">{{ market.itemId }}</p>
                  <template v-if="marketErrorPresentation(market)">
                    <p class="mt-1 text-red-700 dark:text-red-200">原因：{{ marketErrorPresentation(market)?.summary }}</p>
                    <p v-if="marketErrorPresentation(market)?.nextAction" class="mt-1 text-red-700 dark:text-red-200">处理建议：{{ marketErrorPresentation(market)?.nextAction }}</p>
                  </template>
                </div>
              </div>
              <div v-if="item.status === 'outcome_unknown'" class="mt-3 rounded-md border border-amber-200 bg-amber-50 p-2 dark:border-amber-900/70 dark:bg-amber-950/30">
                <p class="text-xs text-amber-800 dark:text-amber-200">只读取已保存的远端 task 终态，不会再次提交创建或更新请求。</p>
                <button
                  data-testid="publish-job-reconcile"
                  type="button"
                  class="btn btn-outline mt-2 px-2.5 py-1.5 text-xs"
                  :disabled="loading || busy"
                  @click="emit('reconcile', selectedJob.jobId, item.platform)"
                >
                  只读对账
                </button>
              </div>
            </article>
          </div>

          <details v-if="detailForDisplay" class="mt-4">
            <summary class="cursor-pointer text-sm font-medium text-accent-700 dark:text-accent-200">查看技术详情</summary>
            <pre class="mt-2 max-h-80 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(detailForDisplay, null, 2) }}</pre>
          </details>
        </template>
        <p v-else class="text-sm text-accent-500 dark:text-accent-300">选择一条任务查看平台状态和错误详情。</p>
      </aside>
    </div>
  </section>
</template>

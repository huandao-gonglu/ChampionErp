<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  PhArrowsIn,
  PhArrowsOut,
  PhArrowsClockwise,
  PhChartLineUp,
  PhClock,
  PhCube,
  PhMoon,
  PhPackage,
  PhPlusCircle,
  PhPulse,
  PhSun,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import DashboardTrendChart from '@/components/dashboard/DashboardTrendChart.vue'
import DashboardOrderDonut from '@/components/dashboard/DashboardOrderDonut.vue'
import {
  fetchDashboardAnalytics,
  type DashboardAnalytics,
  type DashboardPlatformBreakdown,
  type DashboardWindowDays,
} from '@/api/dashboard'
import { useAppStore } from '@/stores/app'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { orderPlatformNames, type OrderPlatform, type OrderState } from '@/types/orders'
import type {
  MercadoLibreAuthChecklist,
  Product,
  ProductIndexItem,
  PublishJob,
} from '@/types/workflow'

const props = defineProps<{
  product: Product
  productsIndex: ProductIndexItem[]
  selectedIds: string[]
  progressPercent: number
  authChecklist: MercadoLibreAuthChecklist | null
  publishJob: PublishJob | null
  logs: string[]
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  navigate: [key: string]
  refreshProducts: []
  openProduct: [item: ProductIndexItem]
  editImages: [item: ProductIndexItem]
  collect: []
  publishSelected: []
}>()

const appStore = useAppStore()
const orderStore = useOrderNotificationsStore()

const windowDays = ref<DashboardWindowDays>(14)
const trendMode = ref<'orders' | 'catalog'>('orders')
const analytics = ref<DashboardAnalytics | null>(null)
const analyticsLoading = ref(false)
const analyticsError = ref('')
const isFullscreen = ref(false)
const nowText = ref('')
const dateText = ref('')

const windowOptions: { value: DashboardWindowDays; label: string }[] = [
  { value: 7, label: '近 7 天' },
  { value: 14, label: '近 14 天' },
  { value: 30, label: '近 30 天' },
]

const readyStatuses = new Set(['ready_to_publish', 'published'])

let clockTimer: ReturnType<typeof setInterval> | undefined
let refreshTimer: ReturnType<typeof setInterval> | undefined
let requestGeneration = 0

function updateClock() {
  const now = new Date()
  nowText.value = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(now)
  dateText.value = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
  }).format(now)
}

async function loadAnalytics(silent = false) {
  const gen = ++requestGeneration
  if (!silent) analyticsLoading.value = true
  try {
    const result = await fetchDashboardAnalytics(windowDays.value)
    if (gen !== requestGeneration) return
    analytics.value = result
    analyticsError.value = ''
  } catch (cause) {
    if (gen !== requestGeneration) return
    analyticsError.value = cause instanceof Error ? cause.message : '大屏聚合数据读取失败'
  } finally {
    if (gen === requestGeneration) analyticsLoading.value = false
  }
}

async function handleManualRefresh() {
  emit('refreshProducts')
  await Promise.all([loadAnalytics(false), orderStore.refreshSummary()])
}

async function toggleFullscreen() {
  if (isFullscreen.value) {
    isFullscreen.value = false
    if (document.fullscreenElement && typeof document.exitFullscreen === 'function') {
      try {
        await document.exitFullscreen()
      } catch {
        // Fallback overlay mode already exited.
      }
    }
    return
  }
  isFullscreen.value = true
  if (typeof document.documentElement.requestFullscreen === 'function') {
    try {
      await document.documentElement.requestFullscreen()
    } catch {
      // Graceful CSS fixed-overlay fullscreen when browser blocks native fullscreen.
    }
  }
}

function handleFullscreenChange() {
  if (!document.fullscreenElement && isFullscreen.value) {
    isFullscreen.value = false
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && isFullscreen.value) {
    void toggleFullscreen()
  }
}

watch(windowDays, () => {
  void loadAnalytics(false)
})

onMounted(() => {
  updateClock()
  clockTimer = setInterval(updateClock, 1000)
  refreshTimer = setInterval(() => {
    void loadAnalytics(true)
  }, 30000)
  document.addEventListener('fullscreenchange', handleFullscreenChange)
  window.addEventListener('keydown', handleKeydown)
  void loadAnalytics(false)
})

onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (refreshTimer) clearInterval(refreshTimer)
  document.removeEventListener('fullscreenchange', handleFullscreenChange)
  window.removeEventListener('keydown', handleKeydown)
})

// ---- Computed Business Telemetry ----

const orderSummary = computed(() => analytics.value?.order_summary ?? {
  total: Object.values(orderStore.summary.counts || {}).reduce((acc, n) => acc + Number(n || 0), 0),
  window_orders: 0,
  previous_window_orders: 0,
  window_orders_delta_pct: 0,
  counts: orderStore.summary.counts || {},
  pending_shipment: orderStore.pendingCount,
  processing: orderStore.summary.counts?.processing || 0,
  shipped: orderStore.summary.counts?.shipped || 0,
  delivered: orderStore.summary.counts?.delivered || 0,
  cancelled: orderStore.summary.counts?.cancelled || 0,
  fulfillment_rate: 0,
  overdue_count: 0,
  urgent_count: 0,
  unread_alerts: orderStore.summary.unread || 0,
  attention_count: orderStore.summary.attention_count || 0,
  window_gmv: {} as Record<string, number>,
  total_gmv: {} as Record<string, number>,
  sync_status: orderStore.summary.sync_status || [],
})

const catalogSummary = computed(() => analytics.value?.catalog_summary ?? {
  products_total: props.productsIndex.length,
  window_collected: 0,
  previous_window_collected: 0,
  window_collected_delta_pct: 0,
  drafts_total: 0,
  ready_drafts: 0,
  published_drafts: 0,
  drafts_by_platform: { mercadolibre: 0, ozon: 0, yandex: 0 },
})

const onlineSummary = computed(() => analytics.value?.online_summary ?? {
  total: 0,
  active: 0,
  paused: 0,
  attention: 0,
  by_platform: {
    mercadolibre: { total: 0, active: 0, paused: 0, attention: 0 },
    ozon: { total: 0, active: 0, paused: 0, attention: 0 },
    yandex: { total: 0, active: 0, paused: 0, attention: 0 },
  },
})

const platformBreakdown = computed<DashboardPlatformBreakdown[]>(() => {
  if (analytics.value?.platform_breakdown?.length) return analytics.value.platform_breakdown
  return (['yandex', 'ozon', 'mercadolibre'] as OrderPlatform[]).map((platform) => ({
    platform,
    total: 0,
    pending_shipment: 0,
    processing: 0,
    shipped: 0,
    delivered: 0,
    cancelled: 0,
    unknown: 0,
    window_orders: 0,
    gmv_by_currency: {},
    sync_status: { platform, status: 'idle', error: '', last_success_at: '', next_attempt: 0 },
  }))
})

const trends = computed(() => analytics.value?.trends ?? [])

const readyToPublishCount = computed(() => {
  const fromProducts = props.productsIndex.filter((item) => {
    if (item.publishQueueReady) return true
    return Object.values(item.draftStatuses || {}).some((status) => readyStatuses.has(String(status || '').toLowerCase()))
  }).length
  return Math.max(fromProducts, catalogSummary.value.ready_drafts)
})

const productsTotalCount = computed(() => Math.max(props.productsIndex.length, catalogSummary.value.products_total))

const trendStats = computed(() => {
  if (!trends.value.length) return { total: 0, avg: '0.0', peak: 0, peakLabel: '-' }
  const values = trends.value.map((item) => (trendMode.value === 'orders' ? item.orders : item.collected))
  const total = values.reduce((sum, n) => sum + n, 0)
  let peak = 0
  let peakLabel = '-'
  trends.value.forEach((item, idx) => {
    const val = values[idx] ?? 0
    if (val >= peak) {
      peak = val
      peakLabel = item.label
    }
  })
  return {
    total,
    avg: (total / trends.value.length).toFixed(1),
    peak,
    peakLabel,
  }
})

const currencySymbols: Record<string, string> = {
  RUB: '₽',
  USD: '$',
  CNY: '¥',
  RMB: '¥',
  EUR: '€',
  MXN: 'MX$',
  BRL: 'R$',
  CLP: 'CLP$',
  COP: 'COP$',
}

function formatGmvMap(gmv: Record<string, number>): { primary: string; secondary: string[] } {
  const entries = Object.entries(gmv || {}).filter(([, val]) => Number(val) > 0)
  if (!entries.length) return { primary: '0', secondary: [] }
  entries.sort((a, b) => b[1] - a[1])
  const formatted = entries.map(([currency, amount]) => {
    const symbol = currencySymbols[currency] || `${currency} `
    const num = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(amount)
    return `${symbol}${num}`
  })
  return { primary: formatted[0] || '0', secondary: formatted.slice(1, 3) }
}

const windowGmvFormatted = computed(() => formatGmvMap(orderSummary.value.window_gmv))
const totalGmvFormatted = computed(() => formatGmvMap(orderSummary.value.total_gmv))

const orderStateLegend = computed(() => [
  { key: 'pending_shipment' as OrderState, label: '待发货', count: orderSummary.value.pending_shipment, dot: 'bg-amber-400' },
  { key: 'processing' as OrderState, label: '处理中', count: orderSummary.value.processing, dot: 'bg-sky-400' },
  { key: 'shipped' as OrderState, label: '已发货', count: orderSummary.value.shipped, dot: 'bg-primary-500' },
  { key: 'delivered' as OrderState, label: '已送达', count: orderSummary.value.delivered, dot: 'bg-emerald-400' },
  { key: 'cancelled' as OrderState, label: '已取消', count: orderSummary.value.cancelled, dot: 'bg-rose-500' },
])

const lastUpdatedLabel = computed(() => {
  const iso = analytics.value?.generated_at
  if (!iso) return '等待同步'
  const ts = Date.parse(iso)
  if (!Number.isFinite(ts)) return iso
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(ts))
})

function formatDelta(value: number | null | undefined): { text: string; tone: string } {
  if (value === null || value === undefined) return { text: '0%', tone: 'text-accent-500 dark:text-slate-400' }
  if (value > 0) return { text: `+${value}%`, tone: 'text-primary-600 dark:text-primary-400' }
  if (value < 0) return { text: `${value}%`, tone: 'text-rose-600 dark:text-rose-400' }
  return { text: '持平', tone: 'text-accent-500 dark:text-slate-400' }
}

function platformAccent(platform: OrderPlatform) {
  if (platform === 'yandex') return { bar: 'from-cyan-500 to-sky-400', badge: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300', dot: 'bg-cyan-400' }
  if (platform === 'ozon') return { bar: 'from-blue-500 to-indigo-400', badge: 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300', dot: 'bg-blue-400' }
  return { bar: 'from-amber-500 to-yellow-400', badge: 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300', dot: 'bg-amber-400' }
}

function syncStatusMeta(status: string) {
  if (status === 'done') return { label: '已同步', dot: 'bg-primary-400', text: 'text-primary-600 dark:text-primary-300' }
  if (status === 'running' || status === 'queued') return { label: '同步中', dot: 'bg-cyan-400 animate-pulse', text: 'text-cyan-600 dark:text-cyan-300' }
  if (status === 'failed' || status === 'retry' || status === 'blocked') return { label: '异常', dot: 'bg-rose-500 animate-pulse', text: 'text-rose-600 dark:text-rose-300' }
  return { label: '空闲', dot: 'bg-slate-400 dark:bg-slate-500', text: 'text-accent-500 dark:text-slate-400' }
}

function platformSharePercent(total: number) {
  if (!orderSummary.value.total) return 0
  return Math.round((total / orderSummary.value.total) * 100)
}
</script>

<template>
  <div
    data-testid="dashboard-command-center"
    class="dashboard-stage relative rounded-2xl transition-all"
    :class="[
      isFullscreen
        ? 'fixed inset-0 z-[80] overflow-y-auto rounded-none p-4 sm:p-6'
        : 'p-4 sm:p-5',
      { 'dashboard-stage--dark': appStore.darkMode },
    ]"
  >
    <!-- Ambient Tech-Dark Grid & Emerald Radial Aura (dark mode only) -->
    <div aria-hidden="true" class="dashboard-grid-overlay pointer-events-none absolute inset-0 rounded-[inherit]" />
    <div aria-hidden="true" class="dashboard-aura pointer-events-none absolute inset-x-0 top-0 h-56 rounded-t-[inherit]" />

    <div class="relative z-10 space-y-4">
      <!-- Top Command Header Bar -->
      <header class="telemetry-panel flex flex-wrap items-center justify-between gap-4 px-5 py-3.5">
        <div class="flex min-w-0 items-center gap-3.5">
          <div class="relative flex size-10 shrink-0 items-center justify-center rounded-xl border border-primary-500/30 bg-primary-500/10 text-primary-600 dark:text-primary-400">
            <PhPulse :size="22" weight="duotone" />
            <span class="absolute -right-0.5 -top-0.5 flex size-2.5">
              <span class="absolute inline-flex size-full animate-ping rounded-full bg-primary-400 opacity-75" />
              <span class="relative inline-flex size-2.5 rounded-full bg-primary-500" />
            </span>
          </div>
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2.5">
              <h1 class="text-lg font-black tracking-tight text-accent-950 dark:text-white sm:text-xl">
                跨境运营指挥大屏
              </h1>
              <span class="inline-flex items-center gap-1.5 rounded-full border border-primary-500/30 bg-primary-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-primary-700 dark:text-primary-300">
                <span class="size-1.5 rounded-full bg-primary-500" />
                实时监控
              </span>
            </div>
            <div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-accent-500 dark:text-slate-400">
              <span>上次更新 {{ lastUpdatedLabel }}</span>
              <span class="hidden sm:inline">·</span>
              <span
                v-for="item in platformBreakdown"
                :key="item.platform"
                class="inline-flex items-center gap-1.5"
              >
                <span class="size-1.5 rounded-full" :class="syncStatusMeta(item.sync_status.status).dot" />
                <span class="font-medium text-accent-700 dark:text-slate-300">{{ orderPlatformNames[item.platform] }}</span>
                <span :class="syncStatusMeta(item.sync_status.status).text">{{ syncStatusMeta(item.sync_status.status).label }}</span>
              </span>
            </div>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2.5">
          <!-- Time Window Switcher -->
          <div
            role="group"
            aria-label="统计周期"
            class="inline-flex items-center rounded-xl border border-accent-200 bg-accent-100/80 p-1 dark:border-white/10 dark:bg-white/[0.04]"
          >
            <button
              v-for="opt in windowOptions"
              :key="opt.value"
              type="button"
              :aria-pressed="windowDays === opt.value"
              class="rounded-lg px-2.5 py-1 text-xs font-semibold transition"
              :class="windowDays === opt.value
                ? 'bg-primary-500 text-white shadow-sm shadow-primary-500/30'
                : 'text-accent-600 hover:text-accent-950 dark:text-slate-300 dark:hover:text-white'"
              @click="windowDays = opt.value"
            >
              {{ opt.label }}
            </button>
          </div>

          <!-- Digital Clock -->
          <div class="hidden items-center gap-2 rounded-xl border border-accent-200 bg-white/80 px-3 py-1.5 dark:border-white/10 dark:bg-white/[0.03] lg:flex">
            <PhClock :size="15" class="text-primary-600 dark:text-primary-400" />
            <div class="leading-tight">
              <p class="font-mono text-sm font-bold tabular-nums text-accent-950 dark:text-white">{{ nowText }}</p>
              <p class="text-[10px] text-accent-500 dark:text-slate-400">{{ dateText }}</p>
            </div>
          </div>

          <!-- Quick Actions -->
          <button
            type="button"
            class="cmd-btn cmd-btn--primary"
            :disabled="loading"
            @click="emit('collect')"
          >
            <PhPlusCircle :size="15" />
            <span>采集商品</span>
          </button>

          <button
            type="button"
            class="cmd-btn"
            :disabled="loading || analyticsLoading"
            title="刷新大屏数据"
            @click="handleManualRefresh"
          >
            <PhArrowsClockwise :size="15" :class="{ 'animate-spin': loading || analyticsLoading }" />
            <span class="hidden sm:inline">刷新</span>
          </button>

          <button
            type="button"
            class="cmd-btn"
            :title="appStore.darkMode ? '切换浅色模式' : '切换科技暗黑模式'"
            :aria-label="appStore.darkMode ? '切换浅色模式' : '切换科技暗黑模式'"
            @click="appStore.toggleTheme()"
          >
            <PhSun v-if="appStore.darkMode" :size="15" />
            <PhMoon v-else :size="15" />
          </button>

          <button
            type="button"
            data-testid="dashboard-fullscreen-toggle"
            class="cmd-btn"
            :class="isFullscreen ? 'border-primary-500/50 bg-primary-500/15 text-primary-700 dark:text-primary-300' : ''"
            @click="toggleFullscreen"
          >
            <PhArrowsIn v-if="isFullscreen" :size="15" />
            <PhArrowsOut v-else :size="15" />
            <span>{{ isFullscreen ? '退出大屏' : '大屏全屏' }}</span>
          </button>
        </div>
      </header>

      <!-- Error Notice -->
      <div
        v-if="error || analyticsError || orderStore.summaryError"
        role="alert"
        class="flex items-center gap-2.5 rounded-xl border border-rose-500/35 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-700 dark:text-rose-200"
      >
        <PhWarningCircle :size="18" class="shrink-0" />
        <span>{{ error || analyticsError || orderStore.summaryError }}</span>
      </div>

      <!-- Tier 1: Hero KPI Telemetry Strip -->
      <section aria-label="核心运营指标" class="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-5">
        <!-- KPI 1: Pending Shipment -->
        <button
          type="button"
          class="telemetry-card telemetry-card--amber group text-left"
          @click="emit('navigate', 'orders')"
        >
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold tracking-wide text-accent-600 dark:text-slate-300">待发货订单</span>
            <span class="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-700 dark:text-amber-300">
              <span v-if="orderSummary.pending_shipment > 0" class="size-1.5 animate-pulse rounded-full bg-amber-400" />
              {{ orderSummary.overdue_count > 0 ? `${orderSummary.overdue_count} 逾期` : orderSummary.urgent_count > 0 ? `${orderSummary.urgent_count} 急单` : '实时履约' }}
            </span>
          </div>
          <p class="mt-2.5 text-3xl font-black tabular-nums tracking-tight text-accent-950 dark:text-white xl:text-4xl">
            {{ orderSummary.pending_shipment }}
          </p>
          <div class="mt-2 flex items-center justify-between text-xs text-accent-500 dark:text-slate-400">
            <span>处理中 {{ orderSummary.processing }} 单</span>
            <span class="font-semibold text-amber-600 transition group-hover:translate-x-0.5 dark:text-amber-300">进入订单中心 →</span>
          </div>
        </button>

        <!-- KPI 2: Window Orders -->
        <button
          type="button"
          class="telemetry-card telemetry-card--emerald group text-left"
          @click="emit('navigate', 'orders')"
        >
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold tracking-wide text-accent-600 dark:text-slate-300">近 {{ windowDays }} 天订单</span>
            <span
              class="rounded-full bg-accent-100 px-2 py-0.5 text-[11px] font-bold tabular-nums dark:bg-white/[0.06]"
              :class="formatDelta(orderSummary.window_orders_delta_pct).tone"
            >
              较上周期 {{ formatDelta(orderSummary.window_orders_delta_pct).text }}
            </span>
          </div>
          <p class="mt-2.5 text-3xl font-black tabular-nums tracking-tight text-accent-950 dark:text-white xl:text-4xl">
            {{ orderSummary.window_orders }}
          </p>
          <div class="mt-2 flex items-center justify-between text-xs text-accent-500 dark:text-slate-400">
            <span>累计总单 {{ orderSummary.total }}</span>
            <span class="font-semibold text-primary-600 dark:text-primary-400">履约率 {{ orderSummary.fulfillment_rate }}%</span>
          </div>
        </button>

        <!-- KPI 3: Window GMV -->
        <article class="telemetry-card telemetry-card--cyan">
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold tracking-wide text-accent-600 dark:text-slate-300">近 {{ windowDays }} 天成交额</span>
            <PhChartLineUp :size="16" class="text-cyan-600 dark:text-cyan-400" />
          </div>
          <p class="mt-2.5 truncate text-3xl font-black tabular-nums tracking-tight text-accent-950 dark:text-white xl:text-4xl" :title="windowGmvFormatted.primary">
            {{ windowGmvFormatted.primary }}
          </p>
          <div class="mt-2 flex flex-wrap items-center justify-between gap-1 text-xs text-accent-500 dark:text-slate-400">
            <span v-if="windowGmvFormatted.secondary.length" class="truncate tabular-nums">
              {{ windowGmvFormatted.secondary.join(' · ') }}
            </span>
            <span v-else class="truncate tabular-nums">
              累计 {{ totalGmvFormatted.primary }}
            </span>
            <span class="text-[11px] text-cyan-600 dark:text-cyan-300">不含取消单</span>
          </div>
        </article>

        <!-- KPI 4: Online Products Health -->
        <button
          type="button"
          class="telemetry-card telemetry-card--blue group text-left"
          @click="emit('navigate', 'onlineProducts')"
        >
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold tracking-wide text-accent-600 dark:text-slate-300">在线商品在售</span>
            <PhPackage :size="16" class="text-blue-600 dark:text-blue-400" />
          </div>
          <div class="mt-2.5 flex items-baseline gap-2">
            <p class="text-3xl font-black tabular-nums tracking-tight text-accent-950 dark:text-white xl:text-4xl">
              {{ onlineSummary.active }}
            </p>
            <span class="text-xs font-semibold tabular-nums text-accent-500 dark:text-slate-400">/ {{ onlineSummary.total }} 总刊登</span>
          </div>
          <div class="mt-2 flex items-center justify-between text-xs text-accent-500 dark:text-slate-400">
            <span>暂停 {{ onlineSummary.paused }}</span>
            <span
              class="font-semibold"
              :class="onlineSummary.attention > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-primary-600 dark:text-primary-400'"
            >
              {{ onlineSummary.attention > 0 ? `${onlineSummary.attention} 需关注` : '状态正常' }}
            </span>
          </div>
        </button>

        <!-- KPI 5: Catalog & Ready -->
        <button
          type="button"
          class="telemetry-card telemetry-card--emerald group text-left"
          @click="emit('navigate', 'drafts')"
        >
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold tracking-wide text-accent-600 dark:text-slate-300">商品库 / 可发布</span>
            <PhCube :size="16" class="text-primary-600 dark:text-primary-400" />
          </div>
          <div class="mt-2.5 flex items-baseline gap-2">
            <p class="text-3xl font-black tabular-nums tracking-tight text-accent-950 dark:text-white xl:text-4xl">
              {{ productsTotalCount }}
            </p>
            <span class="text-xs font-semibold tabular-nums text-primary-600 dark:text-primary-400">
              {{ readyToPublishCount }} 可发
            </span>
          </div>
          <div class="mt-2 flex items-center justify-between text-xs text-accent-500 dark:text-slate-400">
            <span>草稿 {{ catalogSummary.drafts_total }} · 近{{ windowDays }}天+{{ catalogSummary.window_collected }}</span>
            <span class="font-semibold text-primary-600 transition group-hover:translate-x-0.5 dark:text-primary-400">草稿箱 →</span>
          </div>
        </button>
      </section>

      <!-- Tier 2: Main Analytical Stage (Trend Chart + Platform & Fulfillment Matrix) -->
      <section class="grid gap-4 xl:grid-cols-12">
        <!-- Left 8 cols: Time-Series Trend Analysis -->
        <div class="telemetry-panel flex flex-col justify-between p-5 xl:col-span-8">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="text-base font-bold text-accent-950 dark:text-white">
                {{ trendMode === 'orders' ? '三平台订单时间趋势' : '商品采集与刊登产出趋势' }}
              </h2>
              <p class="mt-0.5 text-xs text-accent-500 dark:text-slate-400">
                {{ trendMode === 'orders' ? `近 ${windowDays} 天按日聚合订单走势（含 Yandex / Ozon / Mercado Libre 分平台曲线）` : `近 ${windowDays} 天每日新增采集商品、创建草稿与已发布草稿数量` }}
              </p>
            </div>

            <div class="flex flex-wrap items-center gap-2">
              <div
                role="tablist"
                aria-label="趋势维度切换"
                class="inline-flex rounded-xl border border-accent-200 bg-accent-100/80 p-1 dark:border-white/10 dark:bg-white/[0.04]"
              >
                <button
                  type="button"
                  role="tab"
                  :aria-selected="trendMode === 'orders'"
                  class="rounded-lg px-3 py-1 text-xs font-semibold transition"
                  :class="trendMode === 'orders'
                    ? 'bg-primary-500 text-white shadow-sm shadow-primary-500/30'
                    : 'text-accent-600 hover:text-accent-950 dark:text-slate-300 dark:hover:text-white'"
                  @click="trendMode = 'orders'"
                >
                  订单趋势
                </button>
                <button
                  type="button"
                  role="tab"
                  :aria-selected="trendMode === 'catalog'"
                  class="rounded-lg px-3 py-1 text-xs font-semibold transition"
                  :class="trendMode === 'catalog'
                    ? 'bg-primary-500 text-white shadow-sm shadow-primary-500/30'
                    : 'text-accent-600 hover:text-accent-950 dark:text-slate-300 dark:hover:text-white'"
                  @click="trendMode = 'catalog'"
                >
                  商品产出
                </button>
              </div>
            </div>
          </div>

          <!-- Trend Summary Telemetry Chips -->
          <div class="mt-3.5 grid grid-cols-3 gap-2.5">
            <div class="rounded-xl border border-accent-200/80 bg-accent-50/70 px-3.5 py-2 dark:border-white/[0.06] dark:bg-white/[0.025]">
              <p class="text-[11px] font-medium text-accent-500 dark:text-slate-400">周期总计</p>
              <p class="mt-0.5 text-lg font-black tabular-nums text-accent-950 dark:text-white">{{ trendStats.total }}</p>
            </div>
            <div class="rounded-xl border border-accent-200/80 bg-accent-50/70 px-3.5 py-2 dark:border-white/[0.06] dark:bg-white/[0.025]">
              <p class="text-[11px] font-medium text-accent-500 dark:text-slate-400">日均产出</p>
              <p class="mt-0.5 text-lg font-black tabular-nums text-primary-600 dark:text-primary-400">{{ trendStats.avg }}</p>
            </div>
            <div class="rounded-xl border border-accent-200/80 bg-accent-50/70 px-3.5 py-2 dark:border-white/[0.06] dark:bg-white/[0.025]">
              <p class="text-[11px] font-medium text-accent-500 dark:text-slate-400">单日峰值 ({{ trendStats.peakLabel }})</p>
              <p class="mt-0.5 text-lg font-black tabular-nums text-cyan-600 dark:text-cyan-400">{{ trendStats.peak }}</p>
            </div>
          </div>

          <div class="mt-4">
            <DashboardTrendChart :trends="trends" :mode="trendMode" :dark="appStore.darkMode" />
          </div>
        </div>

        <!-- Right 4 cols: Order Status Donut + 3-Platform Matrix -->
        <div class="telemetry-panel flex flex-col justify-between p-5 xl:col-span-4">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-base font-bold text-accent-950 dark:text-white">订单履约与平台分布</h2>
              <p class="mt-0.5 text-xs text-accent-500 dark:text-slate-400">三平台实时订单状态与占比</p>
            </div>
            <span
              v-if="orderSummary.attention_count > 0"
              class="rounded-full border border-rose-500/40 bg-rose-500/15 px-2.5 py-0.5 text-[11px] font-bold text-rose-600 dark:text-rose-300"
            >
              {{ orderSummary.attention_count }} 同步异常
            </span>
          </div>

          <!-- Donut + Legend -->
          <div class="mt-4 flex flex-col items-center gap-4 sm:flex-row">
            <DashboardOrderDonut
              :counts="orderSummary.counts"
              :total="orderSummary.total"
              :fulfillment-rate="orderSummary.fulfillment_rate"
              :dark="appStore.darkMode"
            />
            <div class="grid w-full flex-1 grid-cols-2 gap-2 sm:grid-cols-1">
              <div
                v-for="row in orderStateLegend"
                :key="row.key"
                class="flex items-center justify-between rounded-lg border border-accent-200/70 bg-accent-50/60 px-2.5 py-1.5 text-xs dark:border-white/[0.06] dark:bg-white/[0.025]"
              >
                <span class="flex items-center gap-2 font-medium text-accent-700 dark:text-slate-300">
                  <span class="size-2 rounded-full" :class="row.dot" />
                  {{ row.label }}
                </span>
                <span class="font-bold tabular-nums text-accent-950 dark:text-white">{{ row.count }}</span>
              </div>
            </div>
          </div>

          <!-- 3-Platform Breakdown Bars -->
          <div class="mt-4 space-y-2.5 border-t border-accent-200/80 pt-3.5 dark:border-white/[0.08]">
            <div
              v-for="plat in platformBreakdown"
              :key="plat.platform"
              class="space-y-1.5"
            >
              <div class="flex items-center justify-between text-xs">
                <div class="flex items-center gap-2">
                  <span class="size-2 rounded-full" :class="platformAccent(plat.platform).dot" />
                  <span class="font-semibold text-accent-900 dark:text-white">{{ orderPlatformNames[plat.platform] }}</span>
                  <span
                    v-if="plat.pending_shipment > 0"
                    class="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold text-amber-700 dark:text-amber-300"
                  >
                    待发 {{ plat.pending_shipment }}
                  </span>
                </div>
                <div class="flex items-center gap-2 tabular-nums">
                  <span v-if="Object.keys(plat.gmv_by_currency).length" class="text-[11px] text-accent-500 dark:text-slate-400">
                    {{ formatGmvMap(plat.gmv_by_currency).primary }}
                  </span>
                  <span class="font-bold text-accent-950 dark:text-white">{{ plat.total }} 单</span>
                </div>
              </div>
              <div class="h-1.5 overflow-hidden rounded-full bg-accent-200/80 dark:bg-white/[0.07]">
                <div
                  class="h-full rounded-full bg-gradient-to-r transition-all duration-500"
                  :class="platformAccent(plat.platform).bar"
                  :style="{ width: `${platformSharePercent(plat.total)}%` }"
                />
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.dashboard-stage {
  @apply bg-accent-100/70 text-accent-950;
}

.dashboard-stage--dark {
  background: radial-gradient(circle at 50% 0%, rgba(16, 185, 129, 0.14), transparent 52%), #05080d;
  color: #f8fafc;
}

.dashboard-grid-overlay {
  opacity: 0;
}

.dashboard-stage--dark .dashboard-grid-overlay {
  opacity: 1;
  background-image:
    linear-gradient(rgba(34, 197, 94, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34, 197, 94, 0.045) 1px, transparent 1px);
  background-size: 36px 36px;
}

.dashboard-aura {
  opacity: 0;
}

.dashboard-stage--dark .dashboard-aura {
  opacity: 1;
  background: radial-gradient(ellipse 68% 85% at 50% -18%, rgba(34, 197, 94, 0.2), transparent 72%);
}

.telemetry-panel {
  @apply relative rounded-2xl border border-accent-200/90 bg-white/95 shadow-card backdrop-blur-md;
}

.dashboard-stage--dark .telemetry-panel {
  border-color: rgba(148, 163, 184, 0.15);
  background: linear-gradient(180deg, rgba(15, 23, 35, 0.9), rgba(9, 14, 22, 0.94));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.06),
    0 12px 32px rgba(0, 0, 0, 0.48);
}

.telemetry-card {
  @apply relative overflow-hidden rounded-2xl border border-accent-200/90 bg-white/95 p-4 shadow-card transition duration-200 hover:-translate-y-0.5 hover:border-primary-300;
}

.dashboard-stage--dark .telemetry-card {
  border-color: rgba(148, 163, 184, 0.16);
  background: linear-gradient(160deg, rgba(16, 24, 38, 0.92), rgba(9, 14, 22, 0.96));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.06),
    0 10px 28px rgba(0, 0, 0, 0.45);
}

.telemetry-card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: 2px;
  opacity: 0.85;
}

.telemetry-card--emerald::before {
  background: linear-gradient(90deg, transparent, #22c55e, transparent);
}

.telemetry-card--amber::before {
  background: linear-gradient(90deg, transparent, #f59e0b, transparent);
}

.telemetry-card--cyan::before {
  background: linear-gradient(90deg, transparent, #06b6d4, transparent);
}

.telemetry-card--blue::before {
  background: linear-gradient(90deg, transparent, #3b82f6, transparent);
}

.dashboard-stage--dark .telemetry-card:hover {
  border-color: rgba(34, 197, 94, 0.45);
  box-shadow:
    inset 0 1px 0 rgba(74, 222, 128, 0.18),
    0 0 28px rgba(34, 197, 94, 0.14),
    0 14px 34px rgba(0, 0, 0, 0.55);
}

.cmd-btn {
  @apply inline-flex items-center gap-1.5 rounded-xl border border-accent-200 bg-white px-3 py-1.5 text-xs font-semibold text-accent-700 transition hover:border-primary-300 hover:bg-accent-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-200 dark:hover:border-primary-500/40 dark:hover:bg-white/[0.08] dark:hover:text-white;
}

.cmd-btn--primary {
  @apply border-transparent bg-gradient-to-r from-primary-500 to-primary-600 text-white shadow-sm shadow-primary-500/25 hover:from-primary-600 hover:to-primary-700 dark:border-primary-400/30 dark:text-white;
}

.cmd-chip {
  @apply rounded-lg border border-accent-200 bg-accent-50 px-2.5 py-1 text-xs font-semibold text-accent-700 transition hover:border-primary-300 hover:text-primary-700 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-300 dark:hover:border-primary-500/40 dark:hover:text-primary-300;
}
</style>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type {
  Marketplace,
  MercadoLibreAuthChecklist,
  MercadoLibreOrderItem,
  MercadoLibreOrderNotification,
  MercadoLibreRemoteItem,
  Product,
  ProductIndexItem,
  PublishJob,
  PublishLogItem,
  PublishPrecheck,
} from '@/types/workflow'

const props = defineProps<{
  product: Product
  productsIndex: ProductIndexItem[]
  pendingItems: ProductIndexItem[]
  selectedIds: string[]
  progressPercent: number
  publishLogs: PublishLogItem[]
  orders: MercadoLibreOrderItem[]
  orderNotifications: MercadoLibreOrderNotification[]
  ordersTotal: number
  ordersCheckedAt: string
  notificationUrl: string
  remoteItems: MercadoLibreRemoteItem[]
  remoteTotal: number
  remoteStatus: string
  authChecklist: MercadoLibreAuthChecklist | null
  precheck: PublishPrecheck | null
  publishJob: PublishJob | null
  logs: string[]
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  navigate: [key: string]
  refreshProducts: []
  refreshLogs: []
  refreshOrders: []
  refreshRemote: []
  openProduct: [item: ProductIndexItem]
  editImages: [item: ProductIndexItem]
  openPrecheck: [item: ProductIndexItem, platform: Marketplace]
  claimSelected: []
  collect: []
  publishSelected: []
}>()

const blockingStatuses = new Set(['failed', 'not_ready', 'pending', 'partial'])
const readyStatuses = new Set(['ready_to_publish', 'published'])
const doneStatuses = new Set(['done', 'success', 'ready', 'ready_to_publish', 'published', 'completed'])
const activeDetailPanel = ref<'overview' | 'orders'>('overview')

const currentProductTitle = computed(() => (
  props.product.source.title
  || props.product.name
  || props.product.productId
  || '尚未选择商品'
))

const currentProductMeta = computed(() => {
  const parts = [
    props.product.source.sourcePlatform,
    props.product.source.price ? `${props.product.source.price} ${props.product.source.currency || ''}`.trim() : '',
    props.product.source.imagePool.length ? `${props.product.source.imagePool.length} 张图片` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' / ') : '等待采集或从商品库加载'
})

const recentProducts = computed(() => [...props.productsIndex]
  .sort((left, right) => Date.parse(right.updatedAt || right.createdAt || '') - Date.parse(left.updatedAt || left.createdAt || ''))
  .slice(0, 5))

const attentionItems = computed(() => props.pendingItems.slice(0, 5))

const readyToPublishCount = computed(() => props.productsIndex.filter((item) => {
  if (item.publishQueueReady) return true
  return Object.values(item.draftStatuses || {}).some((status) => readyStatuses.has(String(status || '').toLowerCase()))
}).length)

const failedCount = computed(() => props.productsIndex.filter((item) => [
  item.collectStatus,
  item.workflowStatus,
  item.aiCopyStatus,
  item.imageStatus,
  item.categoryStatus,
  item.attributesStatus,
  item.pricingStatus,
  item.precheckStatus,
  item.publishStatus,
].some((status) => String(status || '').toLowerCase() === 'failed')).length)

const aiReadyCount = computed(() => props.productsIndex.filter((item) => doneStatuses.has(String(item.aiCopyStatus || '').toLowerCase())).length)
const imageReadyCount = computed(() => props.productsIndex.filter((item) => doneStatuses.has(String(item.imageStatus || '').toLowerCase())).length)
const pricedCount = computed(() => props.productsIndex.filter((item) => doneStatuses.has(String(item.pricingStatus || '').toLowerCase())).length)
const precheckedCount = computed(() => props.productsIndex.filter((item) => doneStatuses.has(String(item.precheckStatus || '').toLowerCase())).length)

const dashboardMetrics = computed(() => [
  {
    label: '商品总量',
    value: props.productsIndex.length,
    detail: `${recentProducts.value.length ? '最近有更新' : '等待采集'}`,
    tone: 'border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200',
  },
  {
    label: '待处理',
    value: props.pendingItems.length,
    detail: failedCount.value ? `${failedCount.value} 个失败项` : '无失败项',
    tone: props.pendingItems.length
      ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200'
      : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200',
  },
  {
    label: '可发布',
    value: readyToPublishCount.value,
    detail: `${props.selectedIds.length} 个已选择`,
    tone: 'border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-200',
  },
  {
    label: 'ML 在线',
    value: props.remoteTotal || props.remoteItems.length,
    detail: props.remoteStatus || 'active',
    tone: 'border-slate-200 bg-white text-slate-700 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-200',
  },
])

const pipelineStats = computed(() => [
  { label: '文案', done: aiReadyCount.value, total: props.productsIndex.length },
  { label: '图片', done: imageReadyCount.value, total: props.productsIndex.length },
  { label: '核价', done: pricedCount.value, total: props.productsIndex.length },
  { label: '预检', done: precheckedCount.value, total: props.productsIndex.length },
])

const healthItems = computed(() => [
  {
    label: '平台授权',
    value: props.authChecklist?.tokenReady ? '已连接' : props.authChecklist?.readyForAuthLink ? '待授权' : '未配置',
    ok: Boolean(props.authChecklist?.tokenReady),
    action: 'auth',
  },
  {
    label: '当前预检',
    value: props.precheck?.ok ? '通过' : props.precheck ? '有缺项' : '未运行',
    ok: Boolean(props.precheck?.ok),
    action: 'category',
  },
  {
    label: '发布任务',
    value: props.publishJob?.status || '无任务',
    ok: props.publishJob?.status === 'completed' || props.publishJob?.status === 'running',
    action: 'publish',
  },
])

const latestLog = computed(() => props.logs[0] || '等待操作。')

const notificationEndpoint = computed(() => {
  if (props.notificationUrl.trim()) return props.notificationUrl.trim()
  if (typeof window === 'undefined') return '/api/mercadolibre/notifications'
  return `${window.location.origin}/api/mercadolibre/notifications`
})

function notificationOrder(notification: MercadoLibreOrderNotification): Record<string, unknown> {
  const rawOrder = notification.raw.order
  return rawOrder && typeof rawOrder === 'object' && !Array.isArray(rawOrder) ? rawOrder as Record<string, unknown> : {}
}

function orderFromNotificationIsWaitingShipment(notification: MercadoLibreOrderNotification) {
  if (notification.topic && notification.topic !== 'orders_v2') return false
  const order = notificationOrder(notification)
  const orderStatus = String(order.status || '').toLowerCase()
  const shippingStatus = String(order.shipping_status || order.shippingStatus || '').toLowerCase()
  const terminal = ['cancelled', 'canceled', 'shipped', 'delivered', 'not_delivered', 'closed']
  return !terminal.includes(orderStatus) && !terminal.includes(shippingStatus)
}

const pendingShipmentCount = computed(() => {
  const keys = new Set<string>()
  for (const notification of props.orderNotifications) {
    if (!orderFromNotificationIsWaitingShipment(notification)) continue
    const key = notification.orderId || notification.resource || `${notification.receivedAt}-${notification.sent}`
    if (key) keys.add(key)
  }
  return keys.size
})

const pendingShipmentNotifications = computed(() => props.orderNotifications.filter(orderFromNotificationIsWaitingShipment))

const orderNotificationDetails = computed(() => pendingShipmentNotifications.value.map((notification) => {
  const order = notificationOrder(notification)
  const rawItems = Array.isArray(order.items) ? order.items : []
  const itemTitles = rawItems
    .map((item) => {
      const record = item && typeof item === 'object' && !Array.isArray(item) ? item as Record<string, unknown> : {}
      return String(record.title || '').trim()
    })
    .filter(Boolean)
  return {
    key: notification.orderId || notification.resource || `${notification.receivedAt}-${notification.sent}`,
    orderId: notification.orderId || String(order.id || ''),
    topic: notification.topic || 'orders_v2',
    resource: notification.resource,
    status: String(order.status || (notification.error ? 'pending' : 'received') || ''),
    shippingStatus: String(order.shipping_status || order.shippingStatus || ''),
    receivedAt: notification.receivedAt,
    sent: notification.sent,
    itemTitle: itemTitles[0] || notification.resource || '订单通知',
    error: notification.error,
    raw: notification.raw,
  }
}))

const orderEvents = computed(() => {
  const fromOrders = props.orders.map((order) => ({
    key: `order-${order.id}`,
    title: order.itemTitles[0] || order.items[0]?.title || order.id || 'Mercado Libre 订单',
    subtitle: [order.buyerNickname, order.shippingStatus].filter(Boolean).join(' / '),
    status: order.status || 'order',
    amount: order.totalAmount || order.paidAmount,
    currency: order.currencyId,
    time: order.dateClosed || order.dateCreated || order.lastUpdated,
  }))
  const fromNotifications = props.orderNotifications.map((item) => ({
    key: `notification-${item.receivedAt}-${item.resource}`,
    title: item.orderId ? `订单 ${item.orderId}` : item.resource || '订单通知',
    subtitle: item.topic || 'orders_v2',
    status: item.error ? 'pending' : 'received',
    amount: 0,
    currency: '',
    time: item.receivedAt || item.sent,
  }))
  return [...fromOrders, ...fromNotifications].slice(0, 4)
})

function firstPlatform(item: ProductIndexItem): Marketplace {
  return item.platforms[0] || 'mercadolibre'
}

function statusClass(status: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'failed') return 'badge-danger'
  if (blockingStatuses.has(value)) return 'badge-info'
  if (doneStatuses.has(value)) return 'badge-success'
  return 'badge-muted'
}

function formatDate(value: string) {
  if (!value) return '-'
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestamp))
}

function formatMoney(amount: number, currency: string) {
  if (!amount) return ''
  return `${currency || ''} ${amount.toLocaleString('en-US', { maximumFractionDigits: 2 })}`.trim()
}

function showOrderDetails() {
  activeDetailPanel.value = 'orders'
}

function stringifyJson(value: unknown) {
  return JSON.stringify(value, null, 2)
}
</script>

<template>
  <div class="space-y-5">
    <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
      <div class="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div class="min-w-0">
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="min-w-0">
              <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">Champion ERP</p>
              <h1 class="mt-2 text-2xl font-black text-accent-950 dark:text-white">运营仪表盘</h1>
              <p class="mt-2 max-w-3xl text-sm leading-6 text-accent-600 dark:text-accent-300">
                {{ currentProductTitle }}
              </p>
              <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ currentProductMeta }}</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <button type="button" class="btn btn-primary" :disabled="loading" @click="emit('collect')">采集商品</button>
              <button type="button" class="btn btn-outline" :disabled="loading" @click="emit('refreshProducts')">刷新商品</button>
              <button type="button" class="btn btn-secondary" :disabled="loading || !selectedIds.length" @click="emit('publishSelected')">已选通过项入队</button>
            </div>
          </div>

          <div class="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <article
              v-for="metric in dashboardMetrics"
              :key="metric.label"
              class="rounded-lg border p-4"
              :class="metric.tone"
            >
              <p class="text-sm font-semibold">{{ metric.label }}</p>
              <p class="mt-2 text-3xl font-black">{{ metric.value }}</p>
              <p class="mt-1 text-xs opacity-80">{{ metric.detail }}</p>
            </article>
            <button
              type="button"
              class="rounded-lg border border-cyan-200 bg-cyan-50 p-4 text-left text-cyan-700 transition hover:border-cyan-400 hover:bg-cyan-100 focus:outline-none focus:ring-2 focus:ring-cyan-400 dark:border-cyan-500/30 dark:bg-cyan-500/10 dark:text-cyan-200 dark:hover:bg-cyan-500/20"
              :class="activeDetailPanel === 'orders' ? 'ring-2 ring-cyan-400' : ''"
              @click="showOrderDetails"
            >
              <p class="text-sm font-semibold">待发货</p>
              <p class="mt-2 text-3xl font-black">{{ pendingShipmentCount }}</p>
              <p class="mt-1 text-xs opacity-80">{{ orderNotifications.length ? '点击查看通知' : '等待 orders_v2' }}</p>
            </button>
          </div>
        </div>

        <aside class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="text-sm font-bold text-accent-950 dark:text-white">今日焦点</h2>
              <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ latestLog }}</p>
            </div>
            <span class="badge-info">{{ progressPercent }}%</span>
          </div>

          <div class="mt-4 space-y-3">
            <button
              v-for="item in healthItems"
              :key="item.label"
              type="button"
              class="flex w-full items-center justify-between rounded-lg border border-accent-200 bg-white px-3 py-2 text-left text-sm transition hover:border-primary-300 dark:border-dark-700 dark:bg-dark-900 dark:hover:border-primary-500/60"
              @click="emit('navigate', item.action)"
            >
              <span class="font-semibold text-accent-700 dark:text-accent-200">{{ item.label }}</span>
              <span :class="item.ok ? 'badge-success' : 'badge-muted'">{{ item.value }}</span>
            </button>
          </div>

          <div v-if="error" class="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
            {{ error }}
          </div>
        </aside>
      </div>
    </section>

    <section
      v-if="activeDetailPanel === 'orders'"
      class="rounded-lg border border-cyan-200 bg-white p-5 shadow-card dark:border-cyan-500/30 dark:bg-dark-900/80"
    >
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="card-title">待发货订单通知详情</h2>
          <p class="muted mt-1">数值来自 Mercado Libre `orders_v2` 回调通知，按订单号或 resource 去重。</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button type="button" class="btn btn-outline" @click="emit('refreshOrders')">刷新订单通知</button>
          <button type="button" class="btn btn-outline" @click="activeDetailPanel = 'overview'">收起</button>
        </div>
      </div>

      <div class="mt-4 rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
        <p class="text-xs font-semibold text-accent-500 dark:text-accent-400">Webhook URL</p>
        <p class="mt-1 break-all font-mono text-xs text-accent-800 dark:text-accent-100">{{ notificationEndpoint }}</p>
        <div class="mt-2 flex flex-wrap gap-2">
          <span class="badge-info">orders_v2</span>
          <span class="badge-muted">POST</span>
          <span class="badge-muted">{{ orderNotifications.length }} 条通知</span>
          <span class="badge-muted">{{ pendingShipmentCount }} 个待发货</span>
        </div>
      </div>

      <div class="mt-4 space-y-3">
        <article
          v-for="notification in orderNotificationDetails"
          :key="notification.key"
          class="rounded-lg border border-accent-200 p-4 dark:border-dark-700"
        >
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-accent-950 dark:text-white">{{ notification.itemTitle }}</p>
              <p class="mt-1 break-all text-xs text-accent-500 dark:text-accent-400">{{ notification.resource }}</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <span :class="statusClass(notification.status)">{{ notification.status || 'received' }}</span>
              <span v-if="notification.shippingStatus" class="badge-muted">{{ notification.shippingStatus }}</span>
            </div>
          </div>
          <dl class="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-400">订单号</dt>
              <dd class="mt-1 break-all text-accent-900 dark:text-accent-100">{{ notification.orderId || '-' }}</dd>
            </div>
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-400">Topic</dt>
              <dd class="mt-1 text-accent-900 dark:text-accent-100">{{ notification.topic }}</dd>
            </div>
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-400">收到时间</dt>
              <dd class="mt-1 text-accent-900 dark:text-accent-100">{{ formatDate(notification.receivedAt) }}</dd>
            </div>
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-400">发送时间</dt>
              <dd class="mt-1 text-accent-900 dark:text-accent-100">{{ formatDate(notification.sent) }}</dd>
            </div>
          </dl>
          <p v-if="notification.error" class="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            {{ notification.error }}
          </p>
          <details class="mt-3 rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
            <summary class="cursor-pointer text-sm font-semibold text-accent-700 dark:text-accent-200">原始通知 JSON</summary>
            <pre class="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs text-accent-700 dark:text-accent-200">{{ stringifyJson(notification.raw) }}</pre>
          </details>
        </article>
        <p v-if="!orderNotificationDetails.length" class="rounded-lg border border-dashed border-accent-300 p-6 text-center text-sm text-accent-500 dark:border-dark-600 dark:text-accent-300">
          暂无待发货通知。上线后 Mercado Libre `orders_v2` 回调到达时，这里会展示通知详情。
        </p>
      </div>
    </section>

    <section class="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_400px]">
      <div class="space-y-5">
        <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="card-title">最近商品</h2>
              <p class="muted mt-1">最近更新的本地商品记录。</p>
            </div>
            <button type="button" class="btn btn-outline" @click="emit('navigate', 'library')">进入商品库</button>
          </div>

          <div class="mt-4 overflow-auto">
            <table class="w-full min-w-[760px] text-left text-sm">
              <thead class="border-b border-accent-200 text-xs text-accent-500 dark:border-dark-700 dark:text-accent-400">
                <tr>
                  <th class="py-3 pr-3">商品</th>
                  <th class="px-3 py-3">平台</th>
                  <th class="px-3 py-3">文案</th>
                  <th class="px-3 py-3">图片</th>
                  <th class="px-3 py-3">预检</th>
                  <th class="px-3 py-3">更新</th>
                  <th class="py-3 pl-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
                <tr v-for="item in recentProducts" :key="item.productId">
                  <td class="max-w-md py-3 pr-3">
                    <div class="flex items-center gap-3">
                      <img v-if="item.mainImage" :src="item.mainImage" alt="" class="size-11 rounded-lg object-cover" />
                      <div v-else class="flex size-11 items-center justify-center rounded-lg bg-accent-100 text-xs font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">ERP</div>
                      <div class="min-w-0">
                        <p class="truncate font-semibold text-accent-950 dark:text-white">{{ item.title || item.productId || '-' }}</p>
                        <p class="truncate text-xs text-accent-500 dark:text-accent-400">{{ item.sourceUrl || item.productId }}</p>
                      </div>
                    </div>
                  </td>
                  <td class="px-3 py-3">{{ item.sourcePlatform || '-' }}</td>
                  <td class="px-3 py-3"><span :class="statusClass(item.aiCopyStatus)">{{ item.aiCopyStatus || '-' }}</span></td>
                  <td class="px-3 py-3"><span :class="statusClass(item.imageStatus)">{{ item.imageStatus || '-' }}</span></td>
                  <td class="px-3 py-3"><span :class="statusClass(item.precheckStatus)">{{ item.precheckStatus || '-' }}</span></td>
                  <td class="px-3 py-3 text-accent-500 dark:text-accent-400">{{ formatDate(item.updatedAt || item.createdAt) }}</td>
                  <td class="py-3 pl-3">
                    <div class="flex justify-end gap-2">
                      <button type="button" class="btn btn-outline py-1.5" @click="emit('openProduct', item)">编辑</button>
                      <button type="button" class="btn btn-primary py-1.5" @click="emit('openPrecheck', item, firstPlatform(item))">发布</button>
                    </div>
                  </td>
                </tr>
                <tr v-if="!recentProducts.length">
                  <td colspan="7" class="py-8 text-center text-accent-500 dark:text-accent-300">还没有商品记录。</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="card-title">流程产能</h2>
              <p class="muted mt-1">商品从文案到预检的准备情况。</p>
            </div>
            <button type="button" class="btn btn-outline" @click="emit('navigate', 'pending')">查看待处理</button>
          </div>

          <div class="mt-5 grid gap-4 md:grid-cols-4">
            <div v-for="item in pipelineStats" :key="item.label">
              <div class="flex items-center justify-between text-sm">
                <span class="font-semibold text-accent-700 dark:text-accent-200">{{ item.label }}</span>
                <span class="text-accent-500 dark:text-accent-400">{{ item.done }}/{{ item.total }}</span>
              </div>
              <div class="mt-2 h-2 rounded-full bg-accent-100 dark:bg-dark-800">
                <div
                  class="h-2 rounded-full bg-primary-500"
                  :style="{ width: item.total ? `${Math.round((item.done / item.total) * 100)}%` : '0%' }"
                />
              </div>
            </div>
          </div>
        </section>
      </div>

      <aside class="space-y-5">
        <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="card-title">待办队列</h2>
              <p class="muted mt-1">{{ pendingItems.length }} 个商品需要补齐。</p>
            </div>
            <button type="button" class="btn btn-outline py-2" @click="emit('navigate', 'pending')">全部</button>
          </div>

          <div class="mt-4 divide-y divide-accent-200 dark:divide-dark-700">
            <article v-for="item in attentionItems" :key="item.productId" class="py-3">
              <div class="flex items-start gap-3">
                <img v-if="item.mainImage" :src="item.mainImage" alt="" class="size-12 rounded-lg object-cover" />
                <div v-else class="flex size-12 items-center justify-center rounded-lg bg-accent-100 text-xs font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">ERP</div>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-sm font-semibold text-accent-950 dark:text-white">{{ item.title || item.productId || '-' }}</p>
                  <div class="mt-2 flex flex-wrap gap-1.5">
                    <span :class="statusClass(item.aiCopyStatus)">{{ item.aiCopyStatus || '文案' }}</span>
                    <span :class="statusClass(item.imageStatus)">{{ item.imageStatus || '图片' }}</span>
                    <span :class="statusClass(item.precheckStatus)">{{ item.precheckStatus || '预检' }}</span>
                  </div>
                </div>
              </div>
              <div class="mt-3 flex flex-wrap gap-2">
                <button type="button" class="btn btn-outline py-1.5" @click="emit('openProduct', item)">继续</button>
                <button type="button" class="btn btn-outline py-1.5" @click="emit('editImages', item)">图片</button>
                <button type="button" class="btn btn-primary py-1.5" @click="emit('openPrecheck', item, firstPlatform(item))">预检</button>
              </div>
            </article>
            <p v-if="!attentionItems.length" class="py-6 text-center text-sm text-accent-500 dark:text-accent-300">暂无待办商品。</p>
          </div>
        </section>

        <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="card-title">发布状态</h2>
              <p class="muted mt-1">最近发布记录和远程商品。</p>
            </div>
            <div class="flex gap-2">
              <button type="button" class="btn btn-outline py-2" @click="emit('refreshLogs')">日志</button>
              <button type="button" class="btn btn-outline py-2" @click="emit('refreshRemote')">ML</button>
            </div>
          </div>

          <div class="mt-4 space-y-3">
            <article v-for="log in publishLogs.slice(0, 3)" :key="`${log.jobId}-${log.startedAt}-${log.platform}`" class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
              <div class="flex items-center justify-between gap-3">
                <span class="truncate text-sm font-semibold text-accent-950 dark:text-white">{{ log.productId || log.platform || '发布记录' }}</span>
                <span :class="statusClass(log.status)">{{ log.status || '-' }}</span>
              </div>
              <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ formatDate(log.finishedAt || log.startedAt) }}</p>
              <p v-if="log.errorMessage" class="mt-2 line-clamp-2 text-xs text-rose-600 dark:text-rose-200">{{ log.errorMessage }}</p>
            </article>
            <p v-if="!publishLogs.length" class="rounded-lg border border-dashed border-accent-300 p-4 text-center text-sm text-accent-500 dark:border-dark-600 dark:text-accent-300">暂无发布日志。</p>
          </div>
        </section>

        <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="card-title">订单通知</h2>
              <p class="muted mt-1">{{ orderEvents.length ? '最近订单 / 回调' : '等待 orders_v2 回调' }}</p>
            </div>
            <button type="button" class="btn btn-outline py-2" @click="emit('refreshOrders')">刷新</button>
          </div>

          <div class="mt-4 rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
            <p class="text-xs font-semibold text-accent-500 dark:text-accent-400">Webhook URL</p>
            <p class="mt-1 break-all font-mono text-xs text-accent-800 dark:text-accent-100">{{ notificationEndpoint }}</p>
            <div class="mt-2 flex flex-wrap gap-2">
              <span class="badge-info">orders_v2</span>
              <span class="badge-muted">POST</span>
              <span v-if="ordersCheckedAt" class="badge-muted">{{ formatDate(ordersCheckedAt) }}</span>
            </div>
          </div>

          <div class="mt-4 space-y-3">
            <article v-for="event in orderEvents" :key="event.key" class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <p class="truncate text-sm font-semibold text-accent-950 dark:text-white">{{ event.title }}</p>
                  <p class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400">{{ event.subtitle || formatDate(event.time) }}</p>
                </div>
                <span :class="statusClass(event.status)">{{ event.status }}</span>
              </div>
              <div class="mt-2 flex items-center justify-between gap-3 text-xs text-accent-500 dark:text-accent-400">
                <span>{{ formatDate(event.time) }}</span>
                <span v-if="event.amount" class="font-semibold text-accent-800 dark:text-accent-100">{{ formatMoney(event.amount, event.currency) }}</span>
              </div>
            </article>
            <p v-if="!orderEvents.length" class="rounded-lg border border-dashed border-accent-300 p-4 text-center text-sm text-accent-500 dark:border-dark-600 dark:text-accent-300">
              暂无订单通知；上线后在 Mercado Libre App 通知配置里填入上方 URL。
            </p>
          </div>
        </section>
      </aside>
    </section>
  </div>
</template>

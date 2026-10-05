import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { fetchOrders, fetchOrderIntegrations, fetchOrderSummary, orderCommand } from '@/api/orders'
import { ORDER_PAGE_SIZE } from '@/types/orders'
import type { OrderIntegrations, OrdersPage, OrderSummary } from '@/types/orders'

export const useOrderNotificationsStore = defineStore('order-notifications', () => {
  const page = ref<OrdersPage>({
    ok: true,
    items: [],
    total: 0,
    counts: {},
    notifications: [],
    alerts: [],
    unread: 0,
    latest_alert_id: 0,
  })
  const integrations = ref<OrderIntegrations>({
    public_url: '',
    platforms: [],
  })
  const summary = ref<OrderSummary>({
    ok: true,
    counts: {},
    unread: 0,
    alerts: [],
    latest_alert_id: 0,
    attention_count: 0,
    recent: [],
  })
  const summaryError = ref('')
  const query = ref('')
  const listActive = ref(false)
  let summaryGeneration = 0
  const platform = ref('')
  const state = ref('')
  const offset = ref(0)
  const error = ref('')
  const busy = ref(false)
  const lastCheckedAt = ref('')
  const desktopEnabled = ref(false)
  const pendingCount = computed(() => summary.value.counts.pending_shipment || 0)
  let timer: ReturnType<typeof setTimeout> | undefined
  let running = false
  let generation = 0
  let lifecycle = 0
  let seenAlert: number | null = null

  async function refresh() {
    const current = ++generation
    try {
      const result = await fetchOrders({
        platform: platform.value,
        state: state.value,
        offset: offset.value,
        q: query.value,
      })
      if (current !== generation) return
      const lastOffset =
        Math.max(0, Math.ceil(result.total / ORDER_PAGE_SIZE) - 1) * ORDER_PAGE_SIZE
      if (offset.value > lastOffset) {
        offset.value = lastOffset
        await refresh()
        return
      }
      page.value = result
      error.value = ''
      lastCheckedAt.value = new Date().toISOString()
    } catch (exc) {
      if (current === generation)
        error.value = exc instanceof Error ? exc.message : '订单通知读取失败'
    }
  }
  async function refreshSummary() {
    const current = ++summaryGeneration
    try {
      const result = await fetchOrderSummary()
      if (current !== summaryGeneration) return
      summary.value = result
      summaryError.value = ''
      if (
        seenAlert !== null &&
        result.latest_alert_id > seenAlert &&
        desktopEnabled.value &&
        typeof Notification !== 'undefined' &&
        Notification.permission === 'granted'
      ) {
        const fresh = result.alerts.filter((item) => item.id > seenAlert!)
        if (fresh.length) {
          try {
            new Notification('有订单待发货', {
              body: `${fresh.length} 条新提醒：${fresh[0]?.title || ''}`,
              tag: 'erp-orders',
            })
          } catch {
            /* 页面内提醒仍保留。 */
          }
        }
      }
      seenAlert = Math.max(seenAlert || 0, result.latest_alert_id)
    } catch (exc) {
      if (current === summaryGeneration)
        summaryError.value = exc instanceof Error ? exc.message : '订单摘要读取失败'
    }
  }
  async function tick(epoch: number) {
    await Promise.all([refreshSummary(), ...(listActive.value ? [refresh()] : [])])
    if (running && epoch === lifecycle)
      timer = setTimeout(() => {
        void tick(epoch)
      }, 5000)
  }
  function start() {
    if (running) return
    running = true
    void tick(++lifecycle)
  }
  function stop() {
    running = false
    lifecycle++
    generation++
    summaryGeneration++
    if (timer) clearTimeout(timer)
  }
  async function filter(nextPlatform: string, nextState: string, nextOffset = 0) {
    platform.value = nextPlatform
    state.value = nextState
    offset.value = nextOffset
    await refresh()
  }
  async function loadIntegrations() {
    try {
      integrations.value = await fetchOrderIntegrations()
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : '回调配置读取失败'
    }
  }
  async function command(
    action: 'sync' | 'retry' | 'acknowledge' | 'configure',
    body: Record<string, unknown> = {}
  ) {
    if (busy.value) return
    busy.value = true
    try {
      await orderCommand(action, body)
      if (action === 'configure') await loadIntegrations()
      await Promise.all([refreshSummary(), ...(listActive.value ? [refresh()] : [])])
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : '订单通知操作失败'
    } finally {
      busy.value = false
    }
  }
  async function enableDesktop() {
    if (typeof Notification === 'undefined') {
      error.value = '当前环境不支持桌面通知，请使用页面内提醒'
      return
    }
    try {
      desktopEnabled.value = (await Notification.requestPermission()) === 'granted'
      if (!desktopEnabled.value) error.value = '桌面通知未获允许，页面内提醒仍可使用'
    } catch {
      error.value = '无法开启桌面通知，页面内提醒仍可使用'
    }
  }
  return {
    page,
    summary,
    summaryError,
    refreshSummary,
    query,
    listActive,
    integrations,
    platform,
    state,
    offset,
    error,
    busy,
    pendingCount,
    lastCheckedAt,
    desktopEnabled,
    refresh,
    start,
    stop,
    filter,
    loadIntegrations,
    command,
    enableDesktop,
  }
})

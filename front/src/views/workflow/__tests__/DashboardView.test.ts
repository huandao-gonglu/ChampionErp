import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import DashboardView from '../DashboardView.vue'
import * as dashboardApi from '@/api/dashboard'
import type { DashboardAnalytics } from '@/api/dashboard'
import { useAppStore } from '@/stores/app'

vi.mock('@/components/dashboard/DashboardTrendChart.vue', () => ({
  default: {
    name: 'DashboardTrendChart',
    props: ['trends', 'mode', 'dark'],
    template: '<div data-testid="trend-chart-stub">{{ mode }}:{{ trends.length }}</div>',
  },
}))

vi.mock('@/components/dashboard/DashboardOrderDonut.vue', () => ({
  default: {
    name: 'DashboardOrderDonut',
    props: ['counts', 'total', 'fulfillmentRate', 'dark'],
    template: '<div data-testid="order-donut-stub">{{ fulfillmentRate }}%</div>',
  },
}))

const sampleAnalytics: DashboardAnalytics = {
  ok: true,
  generated_at: '2026-11-21T06:00:00Z',
  window_days: 14,
  time_zone: 'Asia/Shanghai',
  order_summary: {
    total: 42,
    window_orders: 18,
    previous_window_orders: 12,
    window_orders_delta_pct: 50,
    counts: { pending_shipment: 5, processing: 3, shipped: 20, delivered: 12, cancelled: 2 },
    pending_shipment: 5,
    processing: 3,
    shipped: 20,
    delivered: 12,
    cancelled: 2,
    fulfillment_rate: 80,
    overdue_count: 1,
    urgent_count: 2,
    unread_alerts: 3,
    attention_count: 1,
    window_gmv: { RUB: 128500, USD: 450 },
    total_gmv: { RUB: 320000, USD: 1200 },
    sync_status: [
      { platform: 'yandex', status: 'done', error: '', last_success_at: '2026-11-21T05:50:00Z', next_attempt: 0 },
      { platform: 'ozon', status: 'done', error: '', last_success_at: '2026-11-21T05:50:00Z', next_attempt: 0 },
      { platform: 'mercadolibre', status: 'idle', error: '', last_success_at: '', next_attempt: 0 },
    ],
  },
  platform_breakdown: [
    {
      platform: 'yandex',
      total: 24,
      pending_shipment: 3,
      processing: 2,
      shipped: 12,
      delivered: 7,
      cancelled: 0,
      unknown: 0,
      window_orders: 10,
      gmv_by_currency: { RUB: 90000 },
      sync_status: { platform: 'yandex', status: 'done', error: '', last_success_at: '2026-11-21T05:50:00Z', next_attempt: 0 },
    },
    {
      platform: 'ozon',
      total: 12,
      pending_shipment: 2,
      processing: 1,
      shipped: 6,
      delivered: 3,
      cancelled: 0,
      unknown: 0,
      window_orders: 6,
      gmv_by_currency: { RUB: 38500 },
      sync_status: { platform: 'ozon', status: 'done', error: '', last_success_at: '2026-11-21T05:50:00Z', next_attempt: 0 },
    },
    {
      platform: 'mercadolibre',
      total: 6,
      pending_shipment: 0,
      processing: 0,
      shipped: 2,
      delivered: 2,
      cancelled: 2,
      unknown: 0,
      window_orders: 2,
      gmv_by_currency: { USD: 450 },
      sync_status: { platform: 'mercadolibre', status: 'idle', error: '', last_success_at: '', next_attempt: 0 },
    },
  ],
  catalog_summary: {
    products_total: 64,
    window_collected: 15,
    previous_window_collected: 10,
    window_collected_delta_pct: 50,
    drafts_total: 28,
    ready_drafts: 11,
    published_drafts: 9,
    drafts_by_platform: { yandex: 14, ozon: 10, mercadolibre: 4 },
  },
  online_summary: {
    total: 50,
    active: 44,
    paused: 4,
    attention: 2,
    by_platform: {
      yandex: { total: 30, active: 28, paused: 1, attention: 1 },
      ozon: { total: 15, active: 12, paused: 2, attention: 1 },
      mercadolibre: { total: 5, active: 4, paused: 1, attention: 0 },
    },
  },
  task_summary: { running: 2, queued: 1, completed: 18, failed: 1, attention: 0 },
  trends: [
    {
      date: '2026-11-20',
      label: '11-20',
      orders: 8,
      pending: 2,
      shipped: 6,
      cancelled: 0,
      by_platform: { yandex: 5, ozon: 2, mercadolibre: 1 },
      gmv_by_currency: { RUB: 60000 },
      collected: 6,
      drafts: 4,
      published: 2,
    },
    {
      date: '2026-11-21',
      label: '11-21',
      orders: 10,
      pending: 3,
      shipped: 7,
      cancelled: 0,
      by_platform: { yandex: 5, ozon: 4, mercadolibre: 1 },
      gmv_by_currency: { RUB: 68500 },
      collected: 9,
      drafts: 5,
      published: 3,
    },
  ],
  recent_orders: [
    {
      id: 'yandex:1::YA-1001',
      platform: 'yandex',
      order_id: 'YA-1001',
      title: '机械键盘 Pro',
      state: 'pending_shipment',
      amount: '5990',
      currency: 'RUB',
      updated_at: '2026-11-21T05:30:00Z',
      shipment_deadline: '2026-11-21T18:00:00Z',
      shop_name: 'Yandex 主店',
    },
    {
      id: 'ozon:1::OZ-2002',
      platform: 'ozon',
      order_id: 'OZ-2002',
      title: '无线耳机 Mini',
      state: 'shipped',
      amount: '3200',
      currency: 'RUB',
      updated_at: '2026-11-21T04:10:00Z',
      shipment_deadline: '',
      shop_name: 'Ozon 店',
    },
  ],
}

function mountDashboard() {
  return mount(DashboardView, {
    props: {
      product: {
        productId: '',
        name: '',
        source: { title: '', sourcePlatform: '', price: '', currency: '', imagePool: [] },
      } as never,
      productsIndex: [],
      selectedIds: [],
      progressPercent: 75,
      authChecklist: null,
      publishJob: null,
      logs: [],
      loading: false,
      error: '',
    },
    global: {
      stubs: {
        RouterLink: {
          props: ['to'],
          template: '<a><slot /></a>',
        },
      },
    },
  })
}

describe('DashboardView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const appStore = useAppStore()
    appStore.darkMode = true
    appStore.applyTheme()
    vi.clearAllMocks()
  })

  it('renders KPI telemetry, trends, platform matrix, and switches window/mode', async () => {
    const fetchSpy = vi.spyOn(dashboardApi, 'fetchDashboardAnalytics').mockResolvedValue(sampleAnalytics)
    const wrapper = mountDashboard()
    await flushPromises()

    expect(fetchSpy).toHaveBeenCalledWith(14)
    expect(wrapper.text()).toContain('跨境运营指挥大屏')
    expect(wrapper.text()).toContain('128,500')
    expect(wrapper.text()).toContain('44')
    expect(wrapper.text()).toContain('订单履约与平台分布')
    expect(wrapper.find('[data-testid="trend-chart-stub"]').text()).toBe('orders:2')

    // Switch trend mode to catalog
    await wrapper.findAll('[role="tab"]')[1].trigger('click')
    expect(wrapper.find('[data-testid="trend-chart-stub"]').text()).toBe('catalog:2')

    // Switch time window to 30 days
    const thirtyBtn = wrapper.findAll('button').find((btn) => btn.text() === '近 30 天')
    await thirtyBtn!.trigger('click')
    await flushPromises()
    expect(fetchSpy).toHaveBeenLastCalledWith(30)
  })

  it('toggles fullscreen immersive mode and exits on Escape', async () => {
    vi.spyOn(dashboardApi, 'fetchDashboardAnalytics').mockResolvedValue(sampleAnalytics)
    const wrapper = mountDashboard()
    await flushPromises()

    const root = wrapper.find('[data-testid="dashboard-command-center"]')
    const toggle = wrapper.find('[data-testid="dashboard-fullscreen-toggle"]')
    expect(root.classes()).toContain('dashboard-stage--dark')
    expect(root.classes()).not.toContain('fixed')

    const themeToggle = wrapper.find('button[aria-label="切换浅色模式"]')
    await themeToggle.trigger('click')
    expect(root.classes()).not.toContain('dashboard-stage--dark')

    await toggle.trigger('click')
    expect(root.classes()).toContain('fixed')
    expect(toggle.text()).toContain('退出大屏')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(root.classes()).not.toContain('fixed')
  })
})

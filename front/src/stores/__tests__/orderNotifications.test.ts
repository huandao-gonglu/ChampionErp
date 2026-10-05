import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { fetchOrders, fetchOrderIntegrations, fetchOrderSummary, orderCommand } from '@/api/orders'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import OrderCenterPanel from '@/components/domain/OrderCenterPanel.vue'
import type { OrdersPage } from '@/types/orders'

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/api/orders', () => ({
  fetchOrderSummary: vi.fn(),
  fetchOrders: vi.fn(),
  fetchOrderIntegrations: vi.fn(),
  orderCommand: vi.fn(),
}))
const page = (patch: Partial<OrdersPage> = {}): OrdersPage => ({
  ok: true,
  items: [],
  total: 0,
  counts: {},
  notifications: [],
  alerts: [],
  unread: 0,
  latest_alert_id: 0,
  ...patch,
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(fetchOrderSummary).mockResolvedValue({ ...page(), attention_count: 0, recent: [] })
  vi.mocked(fetchOrders).mockResolvedValue(page())
  vi.mocked(fetchOrderIntegrations).mockResolvedValue({ public_url: '', platforms: [] })
  vi.mocked(orderCommand).mockResolvedValue({ ok: true })
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('订单通知状态', () => {
  it('平台断开时保留上次列表并显示错误', async () => {
    const store = useOrderNotificationsStore()
    vi.mocked(fetchOrders).mockResolvedValueOnce(
      page({ counts: { pending_shipment: 55 }, total: 55 })
    )
    await store.refresh()
    vi.mocked(fetchOrders).mockRejectedValueOnce(new Error('网络不可用'))
    await store.refresh()
    expect(store.page.counts.pending_shipment).toBe(55)
    expect(store.error).toBe('网络不可用')
  })
  it('筛选请求后到达的旧响应不能覆盖新列表', async () => {
    const store = useOrderNotificationsStore()
    let finish!: (value: OrdersPage) => void
    vi.mocked(fetchOrders).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const old = store.refresh()
    vi.mocked(fetchOrders).mockResolvedValueOnce(page({ total: 7 }))
    await store.filter('yandex', 'pending_shipment')
    finish(page({ total: 99 }))
    await old
    expect(store.page.total).toBe(7)
    expect(fetchOrders).toHaveBeenLastCalledWith({
      platform: 'yandex',
      state: 'pending_shipment',
      offset: 0,
      q: '',
    })
  })
  it('轮询只读且停止后不再请求', async () => {
    vi.useFakeTimers()
    const store = useOrderNotificationsStore()
    store.start()
    store.start()
    await flushPromises()
    expect(fetchOrderSummary).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchOrderSummary).toHaveBeenCalledTimes(2)
    expect(orderCommand).not.toHaveBeenCalled()
    store.stop()
    await vi.advanceTimersByTimeAsync(20000)
    expect(fetchOrderSummary).toHaveBeenCalledTimes(2)
  })
  it('总数减少后自动退回最后有效页', async () => {
    const store = useOrderNotificationsStore()
    store.offset = 18
    vi.mocked(fetchOrders).mockResolvedValueOnce(page({ total: 10 }))
    vi.mocked(fetchOrders).mockResolvedValueOnce(page({ total: 10 }))
    await store.refresh()
    expect(store.offset).toBe(9)
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 9 }))
  })
  it('标记已读仅提交已展示的提醒游标', async () => {
    const store = useOrderNotificationsStore()
    await store.command('acknowledge', { through_id: 12 })
    expect(orderCommand).toHaveBeenCalledWith('acknowledge', { through_id: 12 })
  })
  it('首次加载不弹历史桌面提醒，新提醒只弹一次', async () => {
    const notify = vi.fn()
    Object.assign(notify, {
      permission: 'granted',
      requestPermission: vi.fn().mockResolvedValue('granted'),
    })
    vi.stubGlobal('Notification', notify)
    const store = useOrderNotificationsStore()
    await store.enableDesktop()
    vi.mocked(fetchOrderSummary).mockResolvedValueOnce({
      ...page({ latest_alert_id: 10 }),
      recent: [],
      attention_count: 0,
    })
    await store.refreshSummary()
    expect(notify).not.toHaveBeenCalled()
    vi.mocked(fetchOrderSummary).mockResolvedValue({
      ...page({
        latest_alert_id: 11,
        unread: 1,
        alerts: [{ id: 11, platform: 'ozon', order_id: '11', title: '测试商品', created_at: '' }],
      }),
      recent: [],
      attention_count: 0,
    })
    await store.refreshSummary()
    await store.refreshSummary()
    expect(notify).toHaveBeenCalledTimes(1)
  })
})

describe('订单通知页面', () => {
  it('展示三平台、服务端总数、未知状态和失败重试入口', async () => {
    const store = useOrderNotificationsStore()
    store.page = page({
      counts: { pending_shipment: 55 },
      total: 55,
      items: [
        {
          id: 'ozon:3:fbs:1',
          platform: 'ozon',
          account_id: '3',
          order_id: '1',
          fulfillment: 'fbs',
          title: '商品',
          status: 'new_status',
          shipping_status: '',
          state: 'unknown',
          amount: '',
          currency: '',
          updated_at: '',
          checked_at: '',
          items: [],
        },
      ],
      notifications: [
        {
          id: 7,
          platform: 'yandex',
          topic: 'ORDER_CREATED',
          status: 'failed',
          attempts: 8,
          error: '读取失败',
          received_at: '',
          next_attempt: 0,
        },
      ],
    })
    vi.mocked(fetchOrders).mockResolvedValue(store.page)
    const wrapper = mount(OrderCenterPanel, {
      global: { stubs: { teleport: true, RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Mercado Libre')
    expect(wrapper.text()).toContain('Ozon')
    expect(wrapper.text()).toContain('Yandex')
    expect(wrapper.text()).toContain('待发货 55')
    expect(wrapper.text()).toContain('状态待确认')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '通知 0')!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '重试')!
      .trigger('click')
    await flushPromises()
    expect(orderCommand).toHaveBeenCalledWith('retry', { event_id: 7 })
    wrapper.unmount()
  })
})

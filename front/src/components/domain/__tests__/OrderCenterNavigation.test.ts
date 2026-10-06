import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import OrderCenterPanel from '../OrderCenterPanel.vue'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { fetchOrderDetail, fetchOrders } from '@/api/orders'
import type { OrderSnapshot } from '@/types/orders'
vi.mock('@/api/orders', () => ({
  fetchOrderDetail: vi.fn(),
  fetchOrders: vi.fn(),
  fetchOrderSummary: vi.fn(),
  fetchOrderIntegrations: vi.fn(),
  orderCommand: vi.fn(),
  procurementCommand: vi.fn(),
}))
enableAutoUnmount(afterEach)
const orders: OrderSnapshot[] = Array.from({ length: 9 }, (_, index) => ({
  id: `ozon:shop:FBS:${index}`,
  platform: 'ozon',
  account_id: 'shop',
  order_id: String(index),
  fulfillment: 'FBS',
  title: '示例商品',
  status: 'awaiting_packaging',
  shipping_status: '',
  state: 'pending_shipment',
  amount: '24.00',
  currency: 'RUB',
  updated_at: '',
  checked_at: '',
  items: [
    {
      sku: `SKU-${index}`,
      title: '示例商品',
      quantity: 2,
      amount: '24.00',
      currency: 'RUB',
      image_url: `https://images.example/sku-${index}.jpg`,
    },
  ],
}))
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(fetchOrders).mockResolvedValue({
    ok: true,
    items: orders,
    total: 18,
    counts: { pending_shipment: 18 },
    notifications: [],
    alerts: [],
    unread: 0,
    latest_alert_id: 0,
  })
  vi.mocked(fetchOrderDetail).mockResolvedValue({
    ok: true,
    order: orders[0]!,
    lines: [],
  })
})
async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  await router.push('/?tab=orders')
  await router.isReady()
  const wrapper = mount(OrderCenterPanel, {
    global: { plugins: [router], stubs: { teleport: true } },
  })
  await flushPromises()
  return { wrapper, router }
}
describe('紧凑订单列表', () => {
  it.each([
    ['PROCESSING', 'STARTED', 'pending_shipment', '备货中'],
    ['PROCESSING', 'PACKAGING', 'pending_shipment', '打包中'],
    ['PROCESSING', 'READY_TO_SHIP', 'pending_shipment', '已备妥'],
    ['PROCESSING', 'NEW_STAGE', 'processing', '处理中'],
    ['DELIVERY', 'READY_TO_SHIP', 'shipped', '已发货'],
  ] as const)('Yandex %s / %s 在列表与详情显示真实阶段', async (status, shipping_status, state, label) => {
    const remoteOrder: OrderSnapshot = {
      ...orders[0]!, platform: 'yandex', status, shipping_status, state,
    }
    vi.mocked(fetchOrders).mockResolvedValue({
      ok: true, items: [remoteOrder], total: 1, counts: { [state]: 1 },
      notifications: [], alerts: [], unread: 0, latest_alert_id: 0,
    })
    vi.mocked(fetchOrderDetail).mockResolvedValue({ ok: true, order: remoteOrder, lines: [] })
    const store = useOrderNotificationsStore()
    store.state = state
    const { wrapper } = await render()
    expect(wrapper.get('tbody tr td:nth-child(4) .order-badge').text()).toBe(label)
    expect(store.page.counts[state]).toBe(1)
    await wrapper.get('a[aria-label="查看订单 0"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('dialog header .order-badge').text()).toBe(label)
    expect(wrapper.get('dialog').text().includes('等待交接发货')).toBe(label === '已备妥')
    expect(store.state).toBe(state)
  })
  it('详情在列表上打开，关闭后保持平台、状态、搜索和页码', async () => {
    const store = useOrderNotificationsStore()
    store.platform = 'ozon'
    store.state = 'pending_shipment'
    store.query = 'SKU'
    store.offset = 9
    const { wrapper, router } = await render()
    expect(wrapper.findAll('tbody tr')).toHaveLength(9)
    expect(wrapper.findAll('tbody img')).toHaveLength(9)
    expect(wrapper.get('tbody img').attributes('src')).toContain('sku-0.jpg')
    await wrapper.get('a[aria-label="查看订单 0"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.order).toBe(orders[0]!.id)
    expect(wrapper.findComponent(OrderDetailPanel).exists()).toBe(true)
    expect(wrapper.findAll('tbody tr')).toHaveLength(9)
    await wrapper.get('button[aria-label="关闭订单详情"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ tab: 'orders' })
    expect([store.platform, store.state, store.query, store.offset]).toEqual([
      'ozon',
      'pending_shipment',
      'SKU',
      9,
    ])
  })
  it('输入未提交的搜索词不影响轮询，提交后回到第一页', async () => {
    const store = useOrderNotificationsStore()
    store.offset = 9
    const { wrapper } = await render()
    await wrapper.get('input[type="search"]').setValue('  SKU-2  ')
    await store.refresh()
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.objectContaining({ q: '', offset: 9 }))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'SKU-2', offset: 0 }))
  })
})

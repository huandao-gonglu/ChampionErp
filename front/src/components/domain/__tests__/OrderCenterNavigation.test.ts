import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import OrderCenterPanel from '../OrderCenterPanel.vue'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { fetchOrderDetail, fetchOrders, fetchOrderSummary, orderCommand } from '@/api/orders'
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
  vi.mocked(orderCommand).mockResolvedValue({ ok: true })
  vi.mocked(fetchOrderSummary).mockResolvedValue({ ok: true, counts: {}, unread: 0, alerts: [], latest_alert_id: 0, attention_count: 0, recent: [] })
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
    lines: [], purchases: [],
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
    global: { plugins: [router], stubs: { teleport: true, OrderFulfillmentPanel: true, OrderReceivingWarehouse: true } },
  })
  await flushPromises()
  return { wrapper, router }
}
describe('紧凑订单列表', () => {
  it('默认查看全部店铺，切换店铺后搜索和分页保留范围，切换平台清除店铺', async () => {
    const store = useOrderNotificationsStore()
    vi.mocked(fetchOrders).mockResolvedValue({
      ok: true, items: orders, total: 18, counts: { pending_shipment: 18 },
      notifications: [], alerts: [], unread: 0, latest_alert_id: 0,
      shops: [
        { platform: 'yandex', account_id: '4', name: 'GUOO-Economy' },
        { platform: 'yandex', account_id: '6', name: 'GUOO-Express' },
        { platform: 'ozon', account_id: 'shop', name: 'Ozon 店铺' },
      ],
    })
    const { wrapper } = await render()
    const select = wrapper.get('select[aria-label="店铺筛选"]')
    expect((select.element as HTMLSelectElement).value).toBe('')
    expect(select.text()).toContain('GUOO-Express')
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.not.objectContaining({ shop: expect.anything() }))
    await select.setValue('yandex:6'); await flushPromises()
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.objectContaining({ shop: 'yandex:6', offset: 0 }))
    await store.filter(store.platform, store.state, 9)
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.objectContaining({ shop: 'yandex:6', offset: 9 }))
    await wrapper.get('input[type="search"]').setValue('SKU')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.objectContaining({ shop: 'yandex:6', q: 'SKU', offset: 0 }))
    await wrapper.get('select[aria-label="平台筛选"]').setValue('ozon'); await flushPromises()
    expect(store.filters.shop).toBeUndefined()
    expect(select.text()).not.toContain('GUOO-Express')
    expect(select.text()).toContain('Ozon 店铺')
    expect(orderCommand).not.toHaveBeenCalled()
  })

  it('订单显示真实店名，筛选另一店铺时仍显示平台级同步进度', async () => {
    vi.mocked(fetchOrders).mockResolvedValue({
      ok: true, items: [{ ...orders[0]!, shop_name: '快线店' }], total: 1,
      counts: { pending_shipment: 1 }, notifications: [], alerts: [], unread: 0, latest_alert_id: 0,
      sync_status: [{ platform: 'ozon', status: 'running', error: '', last_success_at: '', next_attempt: 0 }],
    })
    const { wrapper } = await render()
    expect(wrapper.get('tbody').text()).toContain('快线店')
    const sync = wrapper.findAll('button').find(button => button.text() === '同步中…')!
    expect(sync.attributes('disabled')).toBeDefined()
  })

  it('挂载、焦点恢复和本地刷新不外发同步，手动按钮仍可同步', async () => {
    vi.useFakeTimers()
    const store = useOrderNotificationsStore()
    try {
      store.start()
      const { wrapper } = await render()
      expect(orderCommand).not.toHaveBeenCalled()
      window.dispatchEvent(new Event('focus'))
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(600_000)
      expect(vi.mocked(fetchOrders).mock.calls.length).toBeGreaterThan(1)
      expect(orderCommand).not.toHaveBeenCalled()
      await wrapper.findAll('button').find(button => button.text() === '同步订单')!.trigger('click')
      await flushPromises()
      expect(orderCommand).toHaveBeenCalledTimes(1)
      expect(orderCommand).toHaveBeenCalledWith('sync', { platform: '' })
      wrapper.unmount()
      await render()
      expect(orderCommand).toHaveBeenCalledTimes(1)
    } finally { store.stop(); vi.useRealTimers() }
  })
  it.each([
    ['PROCESSING', 'STARTED', 'pending_shipment', '待发货', '平台备货中'],
    ['PROCESSING', 'PACKAGING', 'pending_shipment', '待发货', '平台打包中'],
    ['PROCESSING', 'READY_TO_SHIP', 'pending_shipment', '待发货', '平台已备妥，等待交接发货'],
    ['PROCESSING', 'NEW_STAGE', 'processing', '处理中', ''],
    ['DELIVERY', 'READY_TO_SHIP', 'shipped', '已发货', ''],
  ] as const)('Yandex %s / %s 在列表与详情区分平台主状态和附注', async (status, shipping_status, state, label, note) => {
    const remoteOrder: OrderSnapshot = {
      ...orders[0]!, platform: 'yandex', status, shipping_status, state,
    }
    vi.mocked(fetchOrders).mockResolvedValue({
      ok: true, items: [remoteOrder], total: 1, counts: { [state]: 1 },
      notifications: [], alerts: [], unread: 0, latest_alert_id: 0,
    })
    vi.mocked(fetchOrderDetail).mockResolvedValue({ ok: true, order: remoteOrder, lines: [], purchases: [] })
    const store = useOrderNotificationsStore()
    store.state = state
    const { wrapper } = await render()
    expect(wrapper.get('tbody tr td:nth-child(4) .order-badge').text()).toBe(label)
    expect(store.page.counts[state]).toBe(1)
    await wrapper.get('a[aria-label="查看订单 0"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('dialog header .order-badge').text()).toBe(label)
    if (note) {
      expect(wrapper.get('tbody tr td:nth-child(4) .order-muted').text()).toBe('来自平台同步')
      expect(wrapper.get('tbody tr td:nth-child(4)').text()).not.toContain(note)
      await wrapper.findAll('button').find(button => button.text() === '订单资料')!.trigger('click')
      expect(wrapper.get('button[aria-label="关闭订单资料"]').element.closest('dialog')!.textContent).toContain(note)
      await wrapper.get('button[aria-label="关闭订单资料"]').trigger('click')
    }
    expect(wrapper.get('dialog header .order-badge').text()).toBe(label)
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
    expect(wrapper.findAll('tbody a.order-link')).toHaveLength(9)
    expect(wrapper.find('a[aria-label*="跨境履约"]').exists()).toBe(false)
    expect(wrapper.get('thead').text()).toContain('ERP 处理进度')
    expect(wrapper.get('thead').text()).not.toContain('采购进度')
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
  it.each([
    ['unpurchased', false, '待采购登记', 'procurement', '商品与采购'],
    ['partial', false, '采购登记中', 'procurement', '商品与采购'],
    ['purchased', false, '等待买家收货', 'procurement', '商品与采购'],
    ['purchased', true, '待报单', 'fulfillment', '跨境履约'],
  ] as const)('%s / 运单=%s：点击 %s 直达对应详情', async (procurement_status, has_waybill, label, target, targetLabel) => {
    const order: OrderSnapshot = {
      ...orders[0]!, procurement_status,
      purchase_tracking: {
        orders: [{ order_number: '123', status: 'waitbuyerreceive', status_label: '等待买家收货' }],
        unknown_count: 0, has_waybill, stale: false,
      },
    }
    vi.mocked(fetchOrders).mockResolvedValue({ ok: true, items: [order], total: 1, counts: {}, notifications: [], alerts: [], unread: 0, latest_alert_id: 0 })
    vi.mocked(fetchOrderDetail).mockResolvedValue({ ok: true, order, lines: [], purchases: [] })
    const { wrapper, router } = await render()
    const link = wrapper.get('a.order-progress-link')
    expect(link.text()).toBe(label)
    expect(link.attributes('aria-label')).toContain(targetLabel)
    await link.trigger('click'); await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ order: order.id, orderTab: target })
    expect(wrapper.get('nav[aria-label="订单详情内容"] .order-primary').text()).toBe(targetLabel)
    expect(wrapper.find('order-fulfillment-panel-stub').exists()).toBe(target === 'fulfillment')
    const otherTab = target === 'fulfillment' ? '商品与采购' : '跨境履约'
    await wrapper.findAll('nav[aria-label="订单详情内容"] button').find(button => button.text() === otherTab)!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.orderTab).toBe(target === 'fulfillment' ? 'procurement' : 'fulfillment')
    await wrapper.get('button[aria-label="关闭订单详情"]').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ tab: 'orders' })
    await link.trigger('click'); await flushPromises()
    expect(wrapper.get('nav[aria-label="订单详情内容"] .order-primary').text()).toBe(targetLabel)
    await wrapper.get('button[aria-label="关闭订单详情"]').trigger('click'); await flushPromises()
    await wrapper.get('a[aria-label="查看订单 0"]').trigger('click'); await flushPromises()
    expect(wrapper.get('nav[aria-label="订单详情内容"] .order-primary').text()).toBe('商品与采购')
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
  it('提交组合筛选后分页和轮询沿用条件，清空后恢复全部订单且不触发同步', async () => {
    const store = useOrderNotificationsStore()
    store.offset = 9
    const { wrapper } = await render()
    await wrapper.get('select[aria-label="采购进度筛选"]').setValue('purchased')
    await wrapper.get('select[aria-label="报单进度筛选"]').setValue('unsubmitted')
    await wrapper.get('select[aria-label="国内运单筛选"]').setValue('yes')
    await wrapper.get('input[aria-label="国内快递公司筛选"]').setValue(' 圆通 ')
    await wrapper.get('select[aria-label="发货截止日期筛选"]').setValue('tomorrow')
    await store.refresh()
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.not.objectContaining({ procurement_status: 'purchased' }))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.objectContaining({
      offset: 0, procurement_status: 'purchased', declaration_status: 'unsubmitted', has_waybill: 'yes',
      domestic_carrier: '圆通', deadline_status: 'tomorrow',
    }))
    await store.filter(store.platform, store.state, 9)
    expect(fetchOrders).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 9, has_waybill: 'yes', deadline_status: 'tomorrow' }))
    await store.refresh()
    expect(store.filters.domestic_carrier).toBe('圆通')
    expect(wrapper.text()).toContain('已筛选 5 项')
    await wrapper.findAll('button').find(button => button.text() === '清空筛选')!.trigger('click'); await flushPromises()
    expect(store.filters).toEqual({})
    expect(fetchOrders).toHaveBeenLastCalledWith({ platform: '', state: '', offset: 0, q: '' })
    expect(orderCommand).not.toHaveBeenCalled()
  })
})

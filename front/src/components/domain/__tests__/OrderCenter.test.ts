import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import OrderProcurementLine from '../OrderProcurementLine.vue'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import OrderSummaryCard from '../OrderSummaryCard.vue'
import { procurementCommand, fetchOrderDetail } from '@/api/orders'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import type { OrderDetail, OrderSnapshot, ProcurementLine, ProcurementSource } from '@/types/orders'
vi.mock('@/api/orders', () => ({
  procurementCommand: vi.fn(),
  fetchOrderDetail: vi.fn(),
  fetchOrders: vi.fn(),
  fetchOrderSummary: vi.fn(),
  fetchOrderIntegrations: vi.fn(),
  orderCommand: vi.fn(),
}))
const source: ProcurementSource = {
  supplier: '供应商',
  source_platform: '1688',
  product_url: 'https://detail.1688.com/offer/123.html',
  source_sku_id: 'source-red',
  specification: '红色 M',
  sku_url: '',
  sku_url_verified: false,
}
const order: OrderSnapshot = {
  id: 'yandex:4:FBS:123',
  platform: 'yandex',
  account_id: '4',
  order_id: '123',
  fulfillment: 'FBS',
  title: '商品',
  status: 'PROCESSING',
  shipping_status: '',
  state: 'pending_shipment',
  amount: '74.00',
  currency: 'CNY',
  amount_breakdown: { payment: '48.10', subsidy: '25.90', cashback: '0.00' },
  updated_at: '',
  checked_at: '',
  items: [],
}
const item = (confirmed = true): ProcurementLine => ({
  line: { sku: 'SALE-1', title: '商品', quantity: 2, amount: '74.00', currency: 'CNY' },
  selection: {
    line_key: 'line-1',
    revision: confirmed ? 1 : 0,
    status: confirmed ? 'confirmed' : 'matched',
    source: { ...source },
    candidates: [
      {
        id: 'binding-1',
        product_id: 'p1',
        draft_id: 'd1',
        sku_id: 'internal-red',
        seller_sku: 'SALE-1',
        publication_id: 'job-1',
        source: { ...source },
      },
    ],
    reason: '',
  },
  records: [],
  purchased_quantity: 0,
  remaining_quantity: 2,
})
const detail = (): OrderDetail => ({ ok: true, order, lines: [item()] })
const stubs = {
  RouterLink: { props: ['to'], template: '<a :data-to="JSON.stringify(to)"><slot /></a>' },
}
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(procurementCommand).mockResolvedValue(detail())
  vi.mocked(fetchOrderDetail).mockResolvedValue(detail())
})
describe('订单采购交互', () => {
  it('商品链接不会冒充规格直达，打开链接不记录采购', async () => {
    const wrapper = mount(OrderProcurementLine, { props: { order, item: item() } })
    expect(wrapper.get('a').text()).toBe('打开采购商品')
    expect(wrapper.get('a').attributes('href')).toBe(source.product_url)
    expect(wrapper.text()).toContain('来源 SKU：source-red')
    expect(procurementCommand).not.toHaveBeenCalled()
    const verified = item()
    verified.selection.source = {
      ...source,
      sku_url: source.product_url + '?skuId=source-red',
      sku_url_verified: true,
    }
    await wrapper.setProps({ item: verified })
    expect(wrapper.get('a').text()).toBe('直达采购规格（人工确认）')
    expect(wrapper.get('a').attributes('href')).toContain('skuId=source-red')
  })
  it('确认来源提交候选身份和当前版本', async () => {
    const wrapper = mount(OrderProcurementLine, { props: { order, item: item(false) } })
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '确认此采购来源')!
      .trigger('click')
    await flushPromises()
    expect(procurementCommand).toHaveBeenCalledWith('select-source', {
      order_id: order.id,
      line_key: 'line-1',
      revision: 0,
      candidate_id: 'binding-1',
    })
    expect(wrapper.emitted('updated')).toHaveLength(1)
  })
  it('超时后重试复用请求身份', async () => {
    const wrapper = mount(OrderProcurementLine, { props: { order, item: item() } })
    const form = wrapper.findAll('form').at(-1)!
    await form.get('input[type="number"]').setValue(1)
    await form.findAll('input')[1]!.setValue('PO-123')
    vi.mocked(procurementCommand).mockRejectedValueOnce(new Error('网络超时'))
    await form.trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('网络超时')
    await form.trigger('submit')
    await flushPromises()
    const calls = vi.mocked(procurementCommand).mock.calls
    expect(calls[0]).toEqual(calls[1])
    expect(calls[0]![1]).toMatchObject({
      quantity: 1,
      purchase_order_number: 'PO-123',
      revision: 1,
    })
    expect(calls[0]![1].request_id).toBeTruthy()
  })
  it('取消的订单不能新增采购，历史记录显示原来源', () => {
    const data = item()
    data.records = [
      {
        id: 'r1',
        line_key: 'line-1',
        request_id: 'req',
        quantity: 1,
        purchase_order_number: 'PO-OLD',
        source: { ...source, specification: '原规格' },
        created_at: '2026-10-05T10:00:00Z',
        status: 'purchased',
        cancelled_at: '',
      },
    ]
    data.selection.source = { ...source, specification: '新规格' }
    const wrapper = mount(OrderProcurementLine, {
      props: { order: { ...order, state: 'cancelled' }, item: data },
    })
    expect(wrapper.text()).toContain('PO-OLD')
    expect(wrapper.text()).toContain('原规格')
    expect(wrapper.text()).not.toContain('记录已采购')
  })
  it('详情承载金额组成和采购操作', async () => {
    const wrapper = mount(OrderDetailPanel, { props: { orderId: order.id } })
    await flushPromises()
    expect(fetchOrderDetail).toHaveBeenCalledWith(order.id)
    expect(wrapper.text()).toContain('74.00 CNY')
    expect(wrapper.text()).toContain('付款 48.10 CNY')
    expect(wrapper.text()).toContain('平台补贴 25.90 CNY')
    expect(wrapper.text()).toContain('实际采购记录')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '返回订单中心')!
      .trigger('click')
    expect(wrapper.emitted('back')).toHaveLength(1)
  })
  it('仪表盘仅显示摘要和订单入口', () => {
    const store = useOrderNotificationsStore()
    store.summary = {
      ok: true,
      counts: { pending_shipment: 3 },
      unread: 2,
      latest_alert_id: 1,
      alerts: [],
      recent: [order],
      attention_count: 1,
    }
    const wrapper = mount(OrderSummaryCard, { global: { stubs } })
    expect(wrapper.text()).toContain('3 个待发货')
    expect(wrapper.text()).toContain('2 条未读提醒')
    expect(wrapper.text()).toContain('1 条同步异常')
    expect(wrapper.text()).not.toContain('采购来源')
    expect(wrapper.text()).not.toContain('回调')
    expect(
      wrapper.findAll('a').some((link) => link.attributes('data-to')?.includes(order.id))
    ).toBe(true)
  })
})

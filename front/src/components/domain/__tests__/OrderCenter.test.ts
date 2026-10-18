import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import OrderProcurementLine from '../OrderProcurementLine.vue'
import AlibabaSelfPurchaseDialog from '../AlibabaSelfPurchaseDialog.vue'
import OrderPurchaseDialog from '../OrderPurchaseDialog.vue'
import OrderSourceDialog from '../OrderSourceDialog.vue'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import OrderSummaryCard from '../OrderSummaryCard.vue'
import { fetchReceivingWarehouse } from '@/api/receivingWarehouse'
import type { ReceivingWarehouseView } from '@/api/receivingWarehouse'
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
vi.mock('@/api/receivingWarehouse', () => ({ fetchReceivingWarehouse: vi.fn() }))
enableAutoUnmount(afterEach)
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
  line: {
    sku: 'SALE-1',
    title: '商品',
    quantity: 2,
    amount: '74.00',
    currency: 'CNY',
    image_url: 'https://images.example/SALE-1.jpg',
  },
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
  product_id: 'product1',
  purchased_quantity: 0,
  remaining_quantity: 2,
})
const detail = (): OrderDetail => ({ ok: true, order, lines: [item()], purchases: [] })
const stubs = {
  RouterLink: {
    props: ['to'],
    template: '<a :data-to="JSON.stringify(to)"><slot /></a>',
  },
}
beforeEach(() => {
  vi.mocked(fetchReceivingWarehouse).mockResolvedValue({ warehouse: null, revision: 0, profiles: [], can_confirm: true, blocked_reason: '', locked: false } as unknown as ReceivingWarehouseView)
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(procurementCommand).mockResolvedValue(detail())
  vi.mocked(fetchOrderDetail).mockResolvedValue(detail())
})
describe('订单采购交互', () => {
  it('1688 采购入口绑定当前订单商品，创建回执后刷新详情', async () => {
    const wrapper = mount(OrderProcurementLine, {
      props: { order, item: item() },
      global: { stubs: { AlibabaSelfPurchaseDialog: true } },
    })
    await wrapper.findAll('button').find(b => b.text() === '1688 采购')!.trigger('click')
    const dialog = wrapper.getComponent(AlibabaSelfPurchaseDialog)
    expect(dialog.props()).toMatchObject({ orderId: order.id, lineKey: 'line-1', title: '商品' })
    expect(wrapper.emitted('lock')?.at(-1)).toEqual([true])
    dialog.vm.$emit('updated')
    await flushPromises()
    expect(fetchOrderDetail).toHaveBeenCalledWith(order.id)
    expect(wrapper.emitted('updated')?.at(-1)).toEqual([detail()])
    dialog.vm.$emit('close')
    await flushPromises()
    expect(wrapper.findComponent(AlibabaSelfPurchaseDialog).exists()).toBe(false)
    expect(wrapper.emitted('lock')?.at(-1)).toEqual([false])
  })
  it('未确认来源时仍可打开采购提示，保留来源确认入口', async () => {
    const wrapper = mount(OrderProcurementLine, {
      props: { order, item: item(false) }, global: { stubs: { AlibabaSelfPurchaseDialog: true } },
    })
    await wrapper.findAll('button').find(b => b.text() === '1688 采购')!.trigger('click')
    expect(wrapper.getComponent(AlibabaSelfPurchaseDialog).props('orderId')).toBe(order.id)
    await wrapper.get('.sku-source').trigger('click')
    expect(wrapper.text()).toContain('确认来源')
    expect(wrapper.findAll('button').some(b => b.text() === '记录采购')).toBe(false)
  })
  it('商品链接不会冒充规格直达，打开链接不记录采购', async () => {
    const wrapper = mount(OrderProcurementLine, {
      props: { order, item: item() },
    })
    await wrapper.get('.sku-source').trigger('click')
    expect(wrapper.get('a').text()).toBe('打开采购商品')
    expect(wrapper.get('a').attributes('href')).toBe(source.product_url)
    expect(wrapper.get('.order-source-facts').text()).toContain('source-red')
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
    const wrapper = mount(OrderProcurementLine, {
      props: { order, item: item(false) },
    })
    await wrapper.get('.sku-source').trigger('click')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '确认来源')!
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
    const wrapper = mount(OrderPurchaseDialog, {
      props: { order: detail() },
      global: { stubs: { teleport: true } },
    })
    const form = wrapper.findAll('form').at(-1)!
    await form.get('input[maxlength="200"]').setValue('PO-123')
    vi.mocked(procurementCommand).mockRejectedValueOnce(new Error('网络超时'))
    await form.trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('网络超时')
    await form.trigger('submit')
    await flushPromises()
    const calls = vi.mocked(procurementCommand).mock.calls
    expect(calls[0]).toEqual(calls[1])
    expect(calls[0]![1]).toMatchObject({
      items: [],
      purchase_order_number: 'PO-123',
      source_platform: '1688',
    })
    expect(calls[0]![1].request_id).toBeTruthy()
  })

  it('详情承载金额组成和采购操作', async () => {
    const wrapper = mount(OrderDetailPanel, {
      props: { orderId: order.id },
      global: { stubs: { teleport: true } },
    })
    await flushPromises()
    expect(fetchOrderDetail).toHaveBeenCalledWith(order.id)
    expect(wrapper.text()).toContain('74.00 CNY')
    expect(wrapper.text()).toContain('付款 48.10 CNY')
    expect(wrapper.text()).toContain('平台补贴 25.90 CNY')
    expect(wrapper.text()).toContain('商品与采购')
    expect(wrapper.get('.product-group-heading img').attributes('src')).toContain('SALE-1.jpg')
    expect(wrapper.get('.detail-task').text()).toContain('采购已登记 0/2 件')
    expect(wrapper.get('.sidebar-fact details').attributes('open')).toBeUndefined()
    expect(wrapper.get('.detail-task-footer').text()).toContain('登记采购单')
    expect(wrapper.get('dialog').attributes('style')).toContain('1120px')
    await wrapper
      .findAll('button')
      .find((b) => b.attributes('aria-label') === '关闭订单详情')!
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

describe('订单采购弹窗', () => {
  const global = { stubs: { teleport: true } }

  it('修改来源在弹窗保存，改变规格后清除直达确认', async () => {
    const data = item()
    data.selection.source = {
      ...source,
      sku_url: source.product_url + '?sku=red',
      sku_url_verified: true,
    }
    const wrapper = mount(OrderSourceDialog, {
      props: { orderId: order.id, item: data },
      global,
    })
    const labels = wrapper.findAll('label')
    await labels
      .find((label) => label.text() === '采购规格')!
      .get('input')
      .setValue('蓝色 L')
    expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(procurementCommand).toHaveBeenCalledWith(
      'select-source',
      expect.objectContaining({
        revision: 1,
        source: expect.objectContaining({
          specification: '蓝色 L',
          sku_url_verified: false,
        }),
      })
    )
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
  it('提交中的采购弹窗不能关闭，重复点击只提交一次', async () => {
    let finish!: (value: OrderDetail) => void
    vi.mocked(procurementCommand).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const wrapper = mount(OrderPurchaseDialog, {
      props: { order: detail() },
      global,
    })
    await flushPromises()
    await wrapper.get('input[maxlength="200"]').setValue('PO-1')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('dialog').trigger('cancel')
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(procurementCommand).toHaveBeenCalledTimes(1)
    finish(detail())
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

})

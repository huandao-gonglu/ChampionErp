import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { fetchOrderDetail } from '@/api/orders'
import type { OrderHandoverSnapshot, OrderSnapshot } from '@/types/orders'
import OrderHandoverPanel from '../OrderHandoverPanel.vue'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import { handoverDateTime } from '../orderPresentation'

vi.mock('@/api/orders', () => ({ fetchOrderDetail: vi.fn(), procurementCommand: vi.fn() }))
enableAutoUnmount(afterEach)
const order: OrderSnapshot = {
  id: 'yandex:4:FBS:123', platform: 'yandex', account_id: '4', order_id: '123',
  fulfillment: 'FBS', title: '测试商品', status: 'PROCESSING', shipping_status: 'STARTED',
  state: 'pending_shipment', amount: '', currency: '', updated_at: '', checked_at: '',
  shipment_deadline: '2026-10-13', items: [],
}
function result(): OrderHandoverSnapshot {
  return {
    state: 'ready', message: '', checked_at: '2026-10-07T00:00:00Z',
    shipments: [{
      shipment_id: '789', shipment_type: 'IMPORT', status: 'OUTBOUND_CREATED',
      planned_from: '2026-10-13T04:30:00+03:00', planned_to: '2026-10-13T15:00:00+03:00',
      origin: { id: '1', name: '卖家仓', address: '始发地址' },
      destination: { id: '2', name: '测试交货点', address: '测试市交货路23号' },
    }],
  }
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchOrderDetail).mockResolvedValue({ ok: true, order: { ...order, handover: result() }, lines: [] })
})

describe('平台交货信息', () => {
  it('直接展示订单快照中的交货点及发货单，支持复制地址', async () => {
    const copy = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: copy }, configurable: true })
    const wrapper = mount(OrderHandoverPanel, { props: { order: { ...order, handover: result() } } })
    expect(wrapper.text()).toContain('发货单 789')
    expect(wrapper.text()).toContain('批次准备中')
    expect(wrapper.text()).toContain('测试市交货路23号')
    expect(wrapper.text()).not.toContain('始发地址')
    expect(wrapper.text()).toContain('同步时间')
    expect(wrapper.text()).not.toContain('刷新交货信息')
    await wrapper.findAll('button').find(button => button.text() === '复制地址')!.trigger('click')
    expect(copy).toHaveBeenCalledWith('测试交货点\n测试市交货路23号')
  })

  it('平台没有目的地址时不给出卖家仓地址作为替代', () => {
    const value = result()
    value.shipments[0]!.destination = null
    const wrapper = mount(OrderHandoverPanel, { props: { order: { ...order, handover: value } } })
    expect(wrapper.text()).toContain('平台尚未提供地址')
    expect(wrapper.text()).not.toContain('始发地址')
    expect(wrapper.text()).not.toContain('复制地址')
  })

  it('上门揽收显示始发仓，未知交货方式不猜测地址', async () => {
    const value = result()
    value.shipments[0]!.shipment_type = 'WITHDRAW'
    const wrapper = mount(OrderHandoverPanel, { props: { order: { ...order, handover: value } } })
    expect(wrapper.text()).toContain('揽收地址')
    expect(wrapper.text()).toContain('始发地址')
    const next = result()
    next.shipments[0]!.shipment_type = 'NEW_TYPE'
    await wrapper.setProps({ order: { ...order, handover: next } })
    expect(wrapper.text()).toContain('平台未提供明确的交货方式')
    expect(wrapper.text()).not.toContain('始发地址')
  })

  it('旧订单未同步和平台未匹配到批次分别提示', async () => {
    const wrapper = mount(OrderHandoverPanel, { props: { order } })
    expect(wrapper.text()).toContain('交货信息尚未同步')
    expect(wrapper.find('button').exists()).toBe(false)
    await wrapper.setProps({ order: { ...order, handover: {
      state: 'unavailable', message: '尚未找到该订单的交货批次', checked_at: '', shipments: [],
    } } })
    expect(wrapper.text()).toContain('尚未找到该订单的交货批次')
    expect(wrapper.text()).not.toContain('尚未同步')
  })

  it('订单快照更新或切换后展示新数据', async () => {
    const wrapper = mount(OrderHandoverPanel, { props: { order: { ...order, handover: result() } } })
    const next = result()
    next.shipments[0]!.destination!.address = '新地址'
    await wrapper.setProps({ order: { ...order, handover: next } })
    expect(wrapper.text()).toContain('新地址')
    expect(wrapper.text()).not.toContain('测试市交货路23号')
    await wrapper.setProps({ order: { ...order, id: 'yandex:4:FBS:456' } })
    expect(wrapper.text()).not.toContain('新地址')
  })

  it('交货时间转换到北京时间并显示偏移', () => {
    const text = handoverDateTime('2026-10-13T15:00:00+03:00', 'Asia/Shanghai')
    expect(text).toContain('2026/10/13')
    expect(text).toContain('20:00')
    expect(text).toContain('GMT+8')
    expect(handoverDateTime('')).toBe('平台未提供')
  })

  it('模块位于采购列表下方，切换和重开详情只读取普通订单详情', async () => {
    function open() {
      return mount(OrderDetailPanel, {
        attachTo: document.body,
        props: { orderId: order.id },
        global: { stubs: { WorkspaceDialog: { template: '<div><slot /><slot name="footer" /></div>' }, OrderFulfillmentPanel: true } },
      })
    }
    const wrapper = open()
    await flushPromises()
    expect(wrapper.find('.order-product-list ~ [data-testid="order-handover"]').exists()).toBe(true)
    await wrapper.findAll('button').find(button => button.text() === '跨境履约')!.trigger('click')
    expect(wrapper.get('[data-testid="order-handover"]').isVisible()).toBe(false)
    await wrapper.findAll('button').find(button => button.text() === '商品与采购')!.trigger('click')
    expect(wrapper.get('[data-testid="order-handover"]').isVisible()).toBe(true)
    expect(fetchOrderDetail).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    const reopened = open()
    await flushPromises()
    expect(reopened.get('[data-testid="order-handover"]').text()).toContain('测试市交货路23号')
    expect(fetchOrderDetail).toHaveBeenCalledTimes(2)
  })
})

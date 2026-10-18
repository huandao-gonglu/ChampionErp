import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import OrderFulfillmentPanel from '../OrderFulfillmentPanel.vue'
import OrderPurchaseRecords from '../OrderPurchaseRecords.vue'
import { fulfillmentReadiness, procurementTask } from '../orderDetailPresentation'
import { fetchOrderDetail, syncPurchase } from '@/api/orders'
import { fetchFulfillment, fulfillmentCommand } from '@/api/fulfillment'
import { fetchReceivingWarehouse } from '@/api/receivingWarehouse'
import type { ReceivingWarehouse, ReceivingWarehouseView } from '@/api/receivingWarehouse'
import type { OrderDetail, PurchaseRecord } from '@/types/orders'
import type { DomesticParcel, FulfillmentDetail } from '@/types/fulfillment'

vi.mock('@/api/orders', () => ({ fetchOrderDetail: vi.fn(), syncPurchase: vi.fn(), procurementCommand: vi.fn() }))
vi.mock('@/api/fulfillment', () => ({ fetchFulfillment: vi.fn(), fulfillmentCommand: vi.fn(), uploadFulfillmentLabel: vi.fn() }))
vi.mock('@/api/receivingWarehouse', () => ({ fetchReceivingWarehouse: vi.fn() }))
enableAutoUnmount(afterEach)
const warehouse: ReceivingWarehouse = {
  revision: 1, section_id: 1, warehouse_id: 10, section_name: 'Yandex', warehouse_name: '福州合作仓',
  transport_service: 'GUOO(STA)', delivery_key: 'guoo', confirmed_at: '',
  address: { fullName: '收件人', mobile: '13800000000', phone: '', provinceText: '福建省', cityText: '福州市', areaText: '鼓楼区', townText: '', address: '采购收货路 1 号', postCode: '' },
}
const parcels = (): DomesticParcel[] => ['SF001', 'YT002'].map((tracking_number, index) => ({
  id: `parcel-${index}`, tracking_number, carrier: index ? '圆通' : '顺丰', quantity: index ? 1 : 3,
  description: index ? '宠物玩具' : '钻石画', image_url: '', source_platform: '1688', purchase_order_number: `PO${index}`, included: true,
}))
function purchase(index: number): PurchaseRecord {
  return {
    id: `purchase-${index}`, request_id: `request-${index}`, source_platform: '1688', purchase_order_number: `PO${index}`,
    status: 'purchased', created_at: '', cancelled_at: '', note: '', items: [], receiving_warehouse: warehouse,
    progress: { state: 'synced', attempted_at: '', error: '', message: '', data: {
      ok: true, record_id: `purchase-${index}`, order_number: `PO${index}`,
      checked_at: '2026-10-17T10:00:00Z',
      order: { order_number: `PO${index}`, status: 'waitbuyerreceive', status_label: '等待买家收货' }, products: [],
      logistics: [{ logistics_id: `LP${index}`, company: index ? '圆通' : '顺丰', tracking_number: index ? 'YT002' : 'SF001', status: 'TRANSPORT', status_label: '运输中', steps: [{ time: '2026-10-17 18:00', description: '已离开发货网点' }] }], logistics_warning: '',
    } },
  }
}
function order(): OrderDetail {
  return {
    ok: true,
    order: { id: 'yandex:shop:FBS:detail', platform: 'yandex', account_id: 'shop', order_id: 'DETAIL-001', fulfillment: 'FBS',
      title: '多商品订单', status: 'PROCESSING', shipping_status: 'STARTED', state: 'pending_shipment', amount: '600.00', currency: 'RUB', checked_at: '', updated_at: '', items: [], shipment_deadline: '2026-10-20',
      handover: { state: 'ready', message: '', checked_at: '', shipments: [{ shipment_id: 's1', shipment_type: 'IMPORT', status: 'OUTBOUND_CREATED', planned_from: '', planned_to: '', origin: null, destination: { id: 'handover', name: '义乌平台交货点', address: '浙江省义乌市交货路 2 号' } }] },
    },
    lines: ['图案 A', '图案 B', '黄色小狗款'].map((specification, index) => ({
      product_id: index === 2 ? 'toy' : 'painting', line: { sku: `SKU${index}`, title: index === 2 ? '宠物玩具' : '钻石画', quantity: index === 0 ? 2 : 1, amount: '', currency: '' },
      purchased_quantity: index === 0 ? 2 : 1, remaining_quantity: 0,
      selection: { line_key: `line-${index}`, revision: 1, status: 'confirmed', candidates: [], reason: '', source: { supplier: '', source_platform: '1688', specification, product_url: 'https://detail.1688.com/offer/1.html', source_sku_id: `source-${index}`, sku_url: '', sku_url_verified: false } },
    })),
    purchases: [purchase(0), purchase(1)],
  }
}
function fulfillment(): FulfillmentDetail {
  return {
    erp_order_id: order().order.id, revision: 8, busy: false, editing: false, editable: true, plan_editable: true,
    create_unknown: false, cancel_requested: false, cancel_rejected: false, crossborderbus_order_id: null, fulfillment_status: 'NEW',
    operation: '', error_message: '', label_error: '', blocked_reason: '本订单有 2 个国内包裹，请在报单方案中选择并确认仓库合单服务',
    last_attempt_at: '', last_synced_at: '', next_attempt: 0, platform_label: '', platform_tracking_number: '', label_fetch_supported: true,
    label_fetch_reason: '', label_attempt_at: '', country: 'RU', plan: { section_id: 1, warehouse_id: 10, service_ids: [2] },
    remark: '', override: true, rule: null, section_name: 'Yandex', warehouse_name: warehouse.warehouse_name, receiving_warehouse: warehouse,
    delivery: { fulfillment_model: 'FBS', warehouse_id: '', warehouse_name: '', method_id: '', method_name: '自提', carrier: '', country: 'RU', shipment_id: '', tracking_number: '', label_url: '' },
    parcels: parcels(), parcels_confirmed: false,
  }
}
function ready(): FulfillmentDetail {
  return { ...fulfillment(), plan: { section_id: 1, warehouse_id: 10, service_ids: [2, 3], consolidation_service_id: 3 }, platform_label: 'https://cdn.example/label.pdf', platform_tracking_number: 'LABEL001', parcels_confirmed: true, blocked_reason: '' }
}
const global = { plugins: [createPinia()], stubs: { RouterLink: { template: '<a><slot /></a>' } } }
function dom() { return new DOMWrapper(document.body) }
function button(title: string) { return dom().findAll('button').find(item => item.text() === title)! }
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchOrderDetail).mockResolvedValue(order())
  vi.mocked(fetchFulfillment).mockResolvedValue(fulfillment())
  vi.mocked(syncPurchase).mockResolvedValue(order())
  vi.mocked(fetchReceivingWarehouse).mockResolvedValue({ ok: true, order_id: order().order.id, revision: 1, warehouse, locked: true, can_confirm: false, reserved: false, has_purchases: true, binding_stale: false, needs_rebind: false, blocked_reason: '', delivery_key: 'guoo', delivery_name: '国欧', delivery_evidence: [], candidates: [], carrier: null, carrier_detected: null } satisfies ReceivingWarehouseView)
})

describe('订单详情的阶段与对象', () => {
  it('采购缺口优先于已有运单；无采购、无物流和有物流分别给出主任务', () => {
    const value = order()
    value.purchases = []
    value.lines.forEach(line => { line.remaining_quantity = line.line.quantity; line.purchased_quantity = 0 })
    expect(procurementTask(value)).toMatchObject({ title: '准备采购', action: 'purchase' })
    value.purchases = [purchase(0)]
    expect(procurementTask(value)).toMatchObject({ title: '继续采购', action: 'purchase' })
    value.lines.forEach(line => { line.remaining_quantity = 0; line.purchased_quantity = line.line.quantity })
    value.purchases[0]!.progress!.data!.logistics = []
    expect(procurementTask(value)).toMatchObject({ title: '跟进采购进度', action: 'refresh' })
    value.purchases = [purchase(0)]
    expect(procurementTask(value)).toMatchObject({ title: '准备跨境报单', action: 'fulfillment' })
  })

  it('两个商品保留三个 SKU；采购资料、任务栏和采购单摘要分层展示', async () => {
    mount(OrderDetailPanel, { props: { orderId: order().order.id }, global, attachTo: document.body })
    await flushPromises()
    const dialog = dom().get('dialog.order-detail')
    expect(dialog.findAll('.procurement-product')).toHaveLength(2)
    expect(dialog.findAll('.procurement-sku-row')).toHaveLength(3)
    expect(dialog.get('.detail-task').text()).toContain('准备跨境报单')
    expect(dialog.get('footer').text()).toContain('去跨境履约')
    expect(dialog.get('.procurement-sidebar').text()).toContain('福州合作仓')
    expect(dialog.get('.procurement-sidebar').text()).toContain('义乌平台交货点')
    expect(dialog.findAll('.purchase-record')).toHaveLength(2)
    expect(dialog.findAll('.purchase-record').every(record => record.attributes('open') === undefined)).toBe(true)
    expect(dialog.find('.order-line-source').exists()).toBe(false)
    expect(syncPurchase).not.toHaveBeenCalled()
    expect(fulfillmentCommand).not.toHaveBeenCalled()
  })

  it('无物流时底栏主操作顺序刷新采购单；打开详情不发起采购同步', async () => {
    const value = order()
    value.purchases.forEach(record => { record.progress!.data!.logistics = [] })
    vi.mocked(fetchOrderDetail).mockResolvedValue(value)
    vi.mocked(syncPurchase).mockResolvedValue(value)
    mount(OrderDetailPanel, { props: { orderId: value.order.id }, global, attachTo: document.body })
    await flushPromises()
    expect(syncPurchase).not.toHaveBeenCalled()
    const primary = dom().get('dialog.order-detail > footer .order-primary')
    expect(primary.text()).toBe('刷新采购进度')
    await primary.trigger('click'); await flushPromises()
    expect(vi.mocked(syncPurchase).mock.calls.map(call => call[1])).toEqual(['purchase-0', 'purchase-1'])
    expect(fulfillmentCommand).not.toHaveBeenCalled()
  })

  it('采购刷新失败停止后续请求，保留上次可信状态', async () => {
    vi.mocked(syncPurchase).mockRejectedValue(new Error('采购授权已失效'))
    const wrapper = mount(OrderPurchaseRecords, { props: { orderId: order().order.id, records: order().purchases, busy: false }, global: { stubs: { teleport: true } } })
    await wrapper.vm.refreshAll(); await flushPromises()
    expect(syncPurchase).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('采购授权已失效')
    expect(wrapper.text()).toContain('等待买家收货')
    expect(wrapper.emitted('lock')?.at(-1)).toEqual([false])
  })
})

describe('包裹与报单准备', () => {
  it('一次列出清单、合单服务和面单三个独立缺项；排除包裹不要求合单', () => {
    expect(fulfillmentReadiness(fulfillment(), true).filter(item => !item.ready).map(item => item.id)).toEqual(['parcels', 'services', 'label'])
    const single = ready()
    single.parcels[1]!.included = false
    single.plan!.consolidation_service_id = null
    expect(fulfillmentReadiness(single, true).every(item => item.ready)).toBe(true)
    single.blocked_reason = '请先在授权配置中完成跨境巴士账号授权。'
    expect(fulfillmentReadiness(single, true).at(-1)).toMatchObject({ id: 'validation', ready: false, note: single.blocked_reason })
  })

  it('已报单继续展示原方案，不把平台已发货或配置变化误报为提交待办', () => {
    const submitted = { ...ready(), crossborderbus_order_id: 99, fulfillment_status: 'SHIPPED' as const,
      receiving_binding_stale: true, editable: false, plan_editable: false, blocked_reason: '当前平台订单不可预报，请同步并核对平台状态。' }
    expect(fulfillmentReadiness(submitted, true).every(item => item.ready)).toBe(true)
  })

  it('报单页缺项可直接操作，任务按钮移入固定底栏；键盘可来回切换页签', async () => {
    mount(OrderDetailPanel, { props: { orderId: order().order.id, tab: 'fulfillment' }, global, attachTo: document.body })
    await flushPromises()
    expect(dom().get('dialog.order-detail > footer').text()).toContain('尚有 3 项待处理')
    expect(button('提交至合作仓').attributes('disabled')).toBeDefined()
    expect(dom().findAll('.readiness-item')).toHaveLength(3)
    expect(dom().get('.parcel-table tbody').findAll('tr')).toHaveLength(2)
    expect(button('确认清单')).toBeDefined()
    expect(button('选择合单服务')).toBeDefined()
    expect(button('获取平台面单')).toBeDefined()
    await button('查看未完成项').trigger('click')
    expect(document.activeElement).toBe(dom().get('[aria-label="报单准备"]').element)
    await dom().get('#order-fulfillment-tab').trigger('keydown', { key: 'ArrowLeft' }); await flushPromises()
    expect(dom().get('dialog.order-detail > footer').text()).toContain('去跨境履约')
    await dom().get('#order-procurement-tab').trigger('keydown', { key: 'End' }); await flushPromises()
    expect(dom().get('dialog.order-detail > footer').text()).toContain('提交至合作仓')
    expect(fulfillmentCommand).not.toHaveBeenCalled()
  })

  it('改变参与范围只保存完整包裹清单并撤销确认，不提交报单', async () => {
    const saved = { ...ready(), revision: 9, parcels_confirmed: false }
    saved.parcels[1]!.included = false
    vi.mocked(fetchFulfillment).mockResolvedValue(ready())
    vi.mocked(fulfillmentCommand).mockResolvedValue(saved)
    mount(OrderFulfillmentPanel, { props: { order: order() }, global, attachTo: document.body })
    await flushPromises()
    await dom().get('input[aria-label="运单 YT002 参与本次报单"]').setValue(false)
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('parcels', order().order.id, 8, {
      confirm_parcels: false,
      parcels: [expect.objectContaining({ id: 'parcel-0', included: true }), expect.objectContaining({ id: 'parcel-1', included: false })],
    })
    expect(dom().get('.parcel-selection').text()).toContain('已选 1 个 · 清单待确认')
    expect(button('提交至合作仓').attributes('disabled')).toBeDefined()
    expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
  })

  it('资料齐备后才可提交，等待回执期间阻止重复操作；未知结果只可核实', async () => {
    vi.mocked(fetchFulfillment).mockResolvedValue(ready())
    let finish!: (result: FulfillmentDetail) => void
    vi.mocked(fulfillmentCommand).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    mount(OrderFulfillmentPanel, { props: { order: order() }, global, attachTo: document.body })
    await flushPromises()
    await button('提交至合作仓').trigger('click')
    const submit = button('提交至合作仓')
    expect(submit.attributes('disabled')).toBeDefined()
    await submit.trigger('click')
    expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
    expect(fulfillmentCommand).toHaveBeenCalledWith('submit', order().order.id, 8, {})
    finish({ ...ready(), revision: 9, create_unknown: true, editable: false, plan_editable: false, error_message: '创建结果待确认' })
    await flushPromises()
    expect(dom().findAll('button').some(item => item.text() === '提交至合作仓')).toBe(false)
    expect(button('核实创建结果')).toBeDefined()
  })
})

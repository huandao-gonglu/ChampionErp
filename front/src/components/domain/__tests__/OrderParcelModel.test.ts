import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import type { OrderDetail, PurchaseRecord } from '@/types/orders'
import type { DomesticParcel, FulfillmentDetail } from '@/types/fulfillment'
import { fulfillmentCommand, fetchFulfillment } from '@/api/fulfillment'
import { procurementCommand } from '@/api/orders'
import FulfillmentParcelDialog from '../FulfillmentParcelDialog.vue'
import OrderPurchaseDialog from '../OrderPurchaseDialog.vue'
import OrderPurchaseRecords from '../OrderPurchaseRecords.vue'
vi.mock('@/api/fulfillment', () => ({ fulfillmentCommand: vi.fn(), fetchFulfillment: vi.fn() }))
vi.mock('@/api/orders', () => ({ procurementCommand: vi.fn(), syncPurchase: vi.fn() }))
enableAutoUnmount(afterEach)
const order: OrderDetail = {
  ok: true, purchases: [],
  order: { id: 'order1', order_id: '1', platform: 'yandex', account_id: 'shop', fulfillment: 'FBS', title: '多商品订单',
    status: 'PROCESSING', shipping_status: '', state: 'pending_shipment', amount: '', currency: '', checked_at: '', updated_at: '', items: [] },
  lines: [['shirt', 'red', '红色 M'], ['shirt', 'black', '黑色 L'], ['cup', 'white', '白色杯子']].map(([product, sku, title]) => ({
    product_id: product!, line: { sku: sku!, title: title!, quantity: 1, amount: '', currency: '' },
    selection: { line_key: sku!, revision: 0, status: 'unmatched', source: null, candidates: [], reason: '' },
    purchased_quantity: 1, remaining_quantity: 0,
  })),
}
function parcel(id = 'p1'): DomesticParcel {
  return { id, tracking_number: id.toUpperCase(), quantity: 1, carrier: '', description: '原包裹', image_url: '',
    source_platform: '', purchase_order_number: '', included: true }
}
function detail(parcels: DomesticParcel[] = []): FulfillmentDetail {
  return { erp_order_id: order.order.id, revision: 1, busy: false, editing: false, editable: true, plan_editable: true,
    create_unknown: false, cancel_requested: false, cancel_rejected: false, crossborderbus_order_id: null,
    fulfillment_status: 'NEW', operation: '', error_message: '', blocked_reason: '', last_attempt_at: '', last_synced_at: '',
    next_attempt: 0, platform_label: '', platform_tracking_number: '', label_fetch_supported: false, label_fetch_reason: '',
    label_error: '', label_attempt_at: '', country: '', remark: '', plan: null, override: false, rule: null,
    section_name: '', warehouse_name: '', delivery: { fulfillment_model: '', warehouse_id: '', warehouse_name: '', method_id: '',
      method_name: '', carrier: '', country: '', shipment_id: '', tracking_number: '', label_url: '' }, parcels, parcels_confirmed: false }
}
const global = { stubs: { teleport: true } }
const render = (parcels: DomesticParcel[] = []) => mount(FulfillmentParcelDialog, { props: { order, value: detail(parcels) }, global })
const button = (wrapper: ReturnType<typeof mount>, title: string) => wrapper.findAll('button').find(b => b.text() === title)!
beforeEach(() => { vi.clearAllMocks(); vi.mocked(fulfillmentCommand).mockResolvedValue(detail()); vi.mocked(procurementCommand).mockResolvedValue(order) })

it('无需商品关联，只填单号可先保存；未知件数不能确认报单', async () => {
  const wrapper = render()
  await wrapper.get('input[aria-label="国内快递单号"]').setValue(' sf123 ')
  await button(wrapper, '保存并确认清单').trigger('click')
  expect(wrapper.get('[role="alert"]').text()).toContain('商品件数')
  expect(fulfillmentCommand).not.toHaveBeenCalled()
  await button(wrapper, '保存包裹').trigger('click'); await flushPromises()
  expect(fulfillmentCommand).toHaveBeenCalledWith('parcels', 'order1', 1, {
    confirm_parcels: false, parcels: [expect.objectContaining({ tracking_number: 'SF123', quantity: null })],
  })
  const saved = vi.mocked(fulfillmentCommand).mock.calls[0]![3] as { parcels: DomesticParcel[] }
  expect(saved.parcels[0]).not.toHaveProperty('line_key')
  expect(saved.parcels[0]).not.toHaveProperty('purchase_record_id')
})
it('所有商品已采购也能直接新增补寄，不改变原包裹数量', async () => {
  const wrapper = render([parcel()])
  await button(wrapper, '新增包裹').trigger('click')
  await wrapper.findAll('input[aria-label="国内快递单号"]')[1]!.setValue('NEW')
  await wrapper.findAll('input[aria-label="商品件数"]')[1]!.setValue(5)
  await button(wrapper, '保存并确认清单').trigger('click'); await flushPromises()
  expect(fulfillmentCommand).toHaveBeenCalledWith('parcels', 'order1', 1, { confirm_parcels: true,
    parcels: [parcel(), expect.objectContaining({ tracking_number: 'NEW', quantity: 5 })] })
})
it('同一运单不重复登记，大小写和空格统一核对', async () => {
  const wrapper = render([parcel()]); await button(wrapper, '新增包裹').trigger('click')
  await wrapper.findAll('input[aria-label="国内快递单号"]')[1]!.setValue(' p1 ')
  await button(wrapper, '保存包裹').trigger('click')
  expect(wrapper.get('[role="alert"]').text()).toContain('已登记')
  expect(fulfillmentCommand).not.toHaveBeenCalled()
})
it('移出清单保留原包裹，可重新加入', async () => {
  const wrapper = render([parcel()]); await wrapper.get('input[type="checkbox"]').setValue(false)
  await button(wrapper, '保存包裹').trigger('click'); await flushPromises()
  expect(fulfillmentCommand).toHaveBeenLastCalledWith('parcels', 'order1', 1, { confirm_parcels: false, parcels: [{ ...parcel(), included: false }] })
  await wrapper.get('input[type="checkbox"]').setValue(true)
  expect(wrapper.findAll('input[aria-label="国内快递单号"]')).toHaveLength(1)
})
it('一个包裹可带入不同商品的说明，不建立 SKU 数量分配', async () => {
  const wrapper = render([parcel()])
  await wrapper.get('button[aria-label="包裹 1 更多资料"]').trigger('click')
  await wrapper.get('select[aria-label="带入商品资料"]').setValue('red')
  await wrapper.get('select[aria-label="带入商品资料"]').setValue('white')
  expect(wrapper.get('textarea[aria-label="货物说明"]').element).toHaveProperty('value', expect.stringContaining('白色杯子'))
  expect(wrapper.get('input[aria-label="商品件数"]').element).toHaveProperty('value', '1')
})
it('内部按下外部松开不关闭，正常遮罩点击关闭', async () => {
  const wrapper = render([parcel()]); const pointer = { isPrimary: true, button: 0, pointerId: 1 }
  await wrapper.get('input').trigger('pointerdown', pointer); await wrapper.get('dialog').trigger('pointerup', pointer)
  expect(wrapper.emitted('close')).toBeUndefined()
  await wrapper.get('dialog').trigger('pointerdown', pointer); await wrapper.get('dialog').trigger('pointerup', pointer)
  expect(wrapper.emitted('close')).toHaveLength(1)
})
it('版本冲突保留输入并停止重提，避免覆盖新清单', async () => {
  vi.mocked(fulfillmentCommand).mockRejectedValue(new Error('履约资料已更新，请刷新后再操作'))
  vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail([parcel('other')]), revision: 2 })
  const wrapper = render([parcel()]); await button(wrapper, '保存并确认清单').trigger('click'); await flushPromises()
  expect(wrapper.get('input[aria-label="国内快递单号"]').element).toHaveProperty('value', 'P1')
  expect(button(wrapper, '保存并确认清单').attributes('disabled')).toBeDefined()
  expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
})
it('采购登记无需先选 SKU 或确认采购来源', async () => {
  const wrapper = mount(OrderPurchaseDialog, { props: { order }, global })
  await wrapper.get('input[maxlength="200"]').setValue('PO2'); await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(procurementCommand).toHaveBeenCalledWith('record-purchase', expect.objectContaining({ order_id: 'order1', purchase_order_number: 'PO2', source_platform: '1688', items: [] }))
})
it('同一张采购单可关联多个商品与多个 SKU，并允许追加数量', async () => {
  const wrapper = mount(OrderPurchaseDialog, { props: { order }, global })
  await wrapper.get('input[maxlength="200"]').setValue('PO2')
  for (const input of wrapper.findAll('input[type="checkbox"]')) await input.setValue(true)
  await wrapper.findAll('input[type="number"]')[0]!.setValue(3)
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(procurementCommand).toHaveBeenCalledWith('record-purchase', expect.objectContaining({ items: [
    expect.objectContaining({ line_key: 'red', quantity: 3 }), expect.objectContaining({ line_key: 'black' }), expect.objectContaining({ line_key: 'white' }),
  ] }))
})
it('作废整单采购先确认，操作只作用于采购登记', async () => {
  const record: PurchaseRecord = { id: 'r1', request_id: 'req', source_platform: 'other', purchase_order_number: 'PO1', items: [], note: '', created_at: '', status: 'purchased', cancelled_at: '' }
  const wrapper = mount(OrderPurchaseRecords, { props: { orderId: 'order1', records: [record], busy: false }, global })
  await button(wrapper, '作废').trigger('click')
  expect(procurementCommand).not.toHaveBeenCalled()
  await button(wrapper, '确认作废').trigger('click'); await flushPromises()
  expect(procurementCommand).toHaveBeenCalledWith('cancel-purchase', { order_id: 'order1', record_id: 'r1' })
  expect(fulfillmentCommand).not.toHaveBeenCalled()
})

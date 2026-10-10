import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useAppStore } from '@/stores/app'
import FulfillmentParcelDialog from '../FulfillmentParcelDialog.vue'
import FulfillmentPlanFields from '../FulfillmentPlanFields.vue'
import OrderFulfillmentPanel from '../OrderFulfillmentPanel.vue'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import OrderPurchaseTracking from '../OrderPurchaseTracking.vue'
import { fetchOrderDetail, syncPurchase } from '@/api/orders'
import type { PurchaseLogisticsResults } from '@/types/fulfillment'
import { fetchBusServices, fetchFulfillment, fulfillmentCommand } from '@/api/fulfillment'
import type { FulfillmentDetail, FulfillmentPlan } from '@/types/fulfillment'
import type { OrderDetail } from '@/types/orders'

vi.mock('@/api/fulfillment', () => ({ fetchBusServices: vi.fn(), fetchBusSettings: vi.fn(), fetchFulfillment: vi.fn(), fulfillmentCommand: vi.fn(), uploadFulfillmentLabel: vi.fn() }))
vi.mock('@/api/orders', () => ({ fetchOrderDetail: vi.fn(), syncPurchase: vi.fn(), procurementCommand: vi.fn() }))
enableAutoUnmount(afterEach)
afterEach(() => vi.useRealTimers())
const source = { supplier: '供应商', source_platform: '1688', product_url: 'https://detail.1688.com/offer/1.html', source_sku_id: 'S1', specification: '银色', sku_url: '', sku_url_verified: false }
const order: OrderDetail = {
  ok: true,
  order: { id: 'yandex:4:FBS:123', platform: 'yandex', account_id: '4', order_id: '123', fulfillment: 'FBS', title: '支架', status: 'PROCESSING', state: 'pending_shipment', shipping_status: '', updated_at: '', checked_at: '', items: [], amount: '', currency: '' },
  lines: [{ line: { sku: 'SKU1', title: '支架', quantity: 2, amount: '', currency: '' }, selection: { line_key: 'line1', revision: 1, status: 'confirmed', source, candidates: [], reason: '' }, records: [{ id: 'purchase1', line_key: 'line1', request_id: 'r1', quantity: 2, purchase_order_number: 'PO1', source, created_at: '', status: 'purchased', cancelled_at: '' }], purchased_quantity: 2, remaining_quantity: 0 }],
}
function detail(): FulfillmentDetail {
  return { erp_order_id: order.order.id, revision: 1, busy: false, editing: false, editable: true, plan_editable: true, create_unknown: false, cancel_requested: false, cancel_rejected: false, crossborderbus_order_id: null, fulfillment_status: 'NEW', operation: '', error_message: '', blocked_reason: '请补齐全部商品的国内包裹及数量。', last_attempt_at: '', last_synced_at: '', next_attempt: 0, platform_label: '', platform_tracking_number: '', label_fetch_supported: true, label_fetch_reason: '', label_error: '', label_attempt_at: '', country: 'RU', remark: '', plan: { section_id: 1, warehouse_id: 10, service_ids: [2] }, override: false, rule: null, section_name: '合作渠道', warehouse_name: 'A仓', delivery: { fulfillment_model: 'FBS', warehouse_id: 'W1', warehouse_name: '平台仓', method_id: 'M1', method_name: '平台配送', carrier: '', country: 'RU', shipment_id: '', tracking_number: '', label_url: '' }, parcels: [] }
}
function logistics(multiple = false): PurchaseLogisticsResults {
  return { purchase1: { ok: true, record_id: 'purchase1', order_number: 'PO1', checked_at: '2026-10-08T04:00:00Z', order: null, logistics_warning: '', logistics: [
    { logistics_id: 'LP1', company: '圆通速递(YTO)', tracking_number: 'YT123', status: 'SIGN', status_label: '已签收', steps: [] },
    ...(multiple ? [{ logistics_id: 'LP2', company: '顺丰', tracking_number: 'SF456', status: 'TRANSPORT', status_label: '运输中', steps: [] }] : []),
  ] } }
}
function withProgress(results: PurchaseLogisticsResults): OrderDetail {
  const value = structuredClone(order)
  value.lines[0]!.records[0]!.progress = { state: 'pending_assignment', attempted_at: '', message: '请确认包裹分配', error: '', data: results.purchase1! }
  return value
}
const global = { stubs: { teleport: true, RouterLink: { template: '<a><slot /></a>' } } }
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(fetchFulfillment).mockResolvedValue(detail())
  vi.mocked(fulfillmentCommand).mockResolvedValue(detail())
  vi.mocked(fetchBusServices).mockResolvedValue({ core_data: [{ id: 2, name: '必选验货', gold: 0 }], optional_data: [{ id: 3, name: '代贴标', gold: 1 }] })
})

async function fillParcel(wrapper: ReturnType<typeof mount>) {
  await wrapper.findAll('select')[1]!.setValue('顺丰')
  await wrapper.get('input[type="number"]').setValue('2')
  await wrapper.get('input[maxlength="100"]').setValue('SF123')
}

describe('跨境履约操作', () => {
  it('已创建报单可关联平台订单，使用当前版本且关联成功后不重复提供按钮', async () => {
    const created = { ...detail(), crossborderbus_order_id: 99, plan_editable: false, platform_link_state: '' as const }
    vi.mocked(fetchFulfillment).mockResolvedValue(created)
    vi.mocked(fulfillmentCommand).mockResolvedValue({ ...created, platform_link_state: 'linked' })
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === '关联平台订单')!.trigger('click')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('associate', order.order.id, 1, {})
    expect(wrapper.text()).toContain('跨境巴士已返回关联成功')
    expect(wrapper.findAll('button').some(button => button.text() === '关联平台订单')).toBe(false)
  })

  it('关联结果未知时显示核实说明，不允许重复关联', async () => {
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), crossborderbus_order_id: 99,
      platform_link_state: 'unknown', platform_link_error: '请求结果尚未确认' })
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(wrapper.text()).toContain('关联结果待确认')
    expect(wrapper.text()).toContain('请求结果尚未确认')
    expect(wrapper.findAll('button').some(button => button.text() === '关联平台订单')).toBe(false)
    expect(fulfillmentCommand).not.toHaveBeenCalled()
  })

  it('刷新采购进度后切换页签仍显示保存的物流，重新打开也保留', async () => {
    vi.mocked(fetchOrderDetail).mockResolvedValue(order)
    vi.mocked(syncPurchase).mockResolvedValue(withProgress(logistics()))
    const wrapper = mount(OrderDetailPanel, { props: { orderId: order.order.id }, attachTo: document.body, global: { stubs: { RouterLink: global.stubs.RouterLink } } })
    await flushPromises()
    const body = new DOMWrapper(document.body)
    await body.findAll('button').find(button => button.text() === '刷新采购进度')!.trigger('click')
    await flushPromises()
    expect(body.text()).toContain('YT123')
    await body.findAll('button').find(button => button.text() === '跨境履约')!.trigger('click')
    await flushPromises()
    expect(body.text()).toContain('YT123')
    expect(body.text()).toContain('确认包裹分配')
    expect(wrapper.findComponent(FulfillmentParcelDialog).exists()).toBe(false)
    wrapper.unmount()
    vi.mocked(fetchOrderDetail).mockResolvedValue(withProgress(logistics()))
    mount(OrderDetailPanel, { props: { orderId: order.order.id }, attachTo: document.body, global: { stubs: { RouterLink: global.stubs.RouterLink } } })
    await flushPromises()
    expect(body.text()).toContain('YT123')
    expect(fulfillmentCommand).not.toHaveBeenCalled()
  })

  it('多个运单需选择，不能自动套用第一个；重复打开不重复添加已有包裹', async () => {
    const wrapper = mount(FulfillmentParcelDialog, { props: { order: withProgress(logistics(true)), value: detail() }, global })
    expect((wrapper.get('input[maxlength="100"]').element as HTMLInputElement).value).toBe('')
    await wrapper.get('select[aria-label="选择已查询运单"]').setValue('1')
    expect((wrapper.get('input[maxlength="100"]').element as HTMLInputElement).value).toBe('SF456')
    const saved = { ...detail(), parcels: [{ id: 'existing', line_key: 'line1', purchase_record_id: 'purchase1', carrier: '申通', tracking_number: 'MANUAL123', quantity: 2 }] }
    const reopened = mount(FulfillmentParcelDialog, { props: { order: withProgress(logistics()), value: saved }, global })
    expect(reopened.findAll('input[maxlength="100"]')).toHaveLength(1)
    expect((reopened.get('input[maxlength="100"]').element as HTMLInputElement).value).toBe('MANUAL123')
    expect(saved.parcels[0]!.tracking_number).toBe('MANUAL123')
  })

  it('失效采购记录和订单号不匹配的查询结果不带入；锁定资料不自动打开编辑', async () => {
    const stale = logistics()
    stale.purchase1!.order_number = 'another-order'
    const wrapper = mount(FulfillmentParcelDialog, { props: { order: withProgress(stale), value: detail() }, global })
    expect((wrapper.get('input[maxlength="100"]').element as HTMLInputElement).value).toBe('')
    const cancelledOrder = structuredClone(order)
    cancelledOrder.lines[0]!.records[0]!.status = 'cancelled'
    const cancelled = mount(FulfillmentParcelDialog, { props: { order: cancelledOrder, value: detail() }, global })
    expect(cancelled.find('input[maxlength="100"]').exists()).toBe(false)
    expect(cancelled.text()).toContain('尚无有效采购记录')
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), editable: false, busy: true })
    const panel = mount(OrderFulfillmentPanel, { props: { order: withProgress(logistics()) }, global })
    await flushPromises()
    expect(panel.findComponent(FulfillmentParcelDialog).exists()).toBe(false)
    expect(panel.findAll('button').some(button => button.text() === '确认包裹分配')).toBe(false)
  })

  it('多个采购刷新与包裹弹窗共用关闭锁，单个刷新结束不会提前解锁', async () => {
    const value = withProgress(logistics())
    value.lines[0]!.records.push({ ...value.lines[0]!.records[0]!, id: 'purchase2' })
    const panel = mount(OrderFulfillmentPanel, { props: { order: value }, global })
    await flushPromises()
    const queries = panel.findAllComponents(OrderPurchaseTracking)
    queries[0]!.vm.$emit('lock', true)
    queries[1]!.vm.$emit('lock', true)
    await flushPromises()
    queries[0]!.vm.$emit('lock', false)
    await flushPromises()
    expect(panel.emitted('lock')?.at(-1)).toEqual([true])
    await panel.findAll('button').find(button => button.text() === '确认包裹分配')!.trigger('click')
    queries[1]!.vm.$emit('lock', false)
    await flushPromises()
    expect(panel.emitted('lock')?.at(-1)).toEqual([true])
    panel.findComponent(FulfillmentParcelDialog).vm.$emit('close')
    await flushPromises()
    expect(panel.emitted('lock')?.at(-1)).toEqual([false])
  })

  it('没有国内单号不能推断供应商未发货；面单与包裹分别展示记录事实', async () => {
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    const facts = wrapper.get('[aria-label="已记录资料与仓库回执"]').text()
    expect(facts).toContain('2 / 2 件')
    expect(facts).toContain('未录入')
    expect(facts).toContain('待补充')
    expect(facts).toContain('尚未创建')
    expect(wrapper.text()).not.toContain('等待供应商发货')
    expect(wrapper.get('.order-row .order-badge').text()).toBe('采购状态待查询')
  })
  it('获取面单携带当前版本，成功后显示托管地址和箱号', async () => {
    vi.mocked(fulfillmentCommand).mockResolvedValue({ ...detail(), revision: 3, platform_label: 'https://cdn.example/label.pdf', platform_tracking_number: '123-1' })
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '获取平台面单')!.trigger('click')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('fetch-label', order.order.id, 1, {})
    expect(wrapper.get('a[href="https://cdn.example/label.pdf"]').text()).toBe('查看平台面单')
    expect(wrapper.text()).toContain('123-1')
    expect(wrapper.text()).toContain('重新获取面单')
  })

  it('获取期间禁止重复点击和编辑；失败原因保留在面单区域', async () => {
    let finish!: (value: FulfillmentDetail) => void
    vi.mocked(fulfillmentCommand).mockReturnValue(new Promise(resolve => { finish = resolve }))
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '获取平台面单')!.trigger('click')
    expect(wrapper.findAll('button').find(b => b.text() === '人工补充')!.attributes('disabled')).toBeDefined()
    finish({ ...detail(), label_error: 'Yandex 面单授权不足，请检查订单处理权限' })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('订单处理权限')
    expect(wrapper.text()).toContain('重新获取面单')
    expect(useAppStore().toasts.at(-1)?.message).toContain('订单处理权限')
    expect(wrapper.findAll('button').find(b => b.text() === '立即提交')!.attributes('disabled')).toBeDefined()
  })

  it('未接入的平台显示人工补充原因；后台获取时展示处理中', async () => {
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), label_fetch_supported: false, label_fetch_reason: '当前平台尚未接入面单自动获取，请人工上传' })
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(wrapper.findAll('button').some(b => b.text() === '获取平台面单')).toBe(false)
    expect(wrapper.text()).toContain('请人工上传')
    expect(wrapper.text()).toContain('人工补充')
    wrapper.unmount()
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), busy: true, editable: false, operation: 'fetch-label' })
    const downloading = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(downloading.text()).toContain('正在获取平台面单')
    expect(downloading.get('[role="status"]').text()).toContain('正在从平台下载')
  })

  it('取消被拒绝后显示原因并允许人工重新请求取消', async () => {
    const rejected = { ...detail(), crossborderbus_order_id: 99, fulfillment_status: 'WAITING_DOMESTIC_SHIPMENT' as const, cancel_requested: true, cancel_rejected: true, editable: false, error_message: '合作仓库拒绝取消' }
    vi.mocked(fetchFulfillment).mockResolvedValue(rejected)
    vi.mocked(fulfillmentCommand).mockResolvedValue(rejected)
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(wrapper.text()).toContain('合作仓库拒绝取消')
    expect(wrapper.text()).toContain('取消失败')
    await wrapper.findAll('button').find(b => b.text() === '重新请求取消')!.trigger('click')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('retry', order.order.id, 1, {})
  })
  it('包裹关联采购并提交实际数量；拖出弹窗不会关闭', async () => {
    const wrapper = mount(FulfillmentParcelDialog, { props: { value: detail(), order }, global })
    await flushPromises()
    await fillParcel(wrapper)
    const pointer = { isPrimary: true, button: 0, pointerId: 1 }
    await wrapper.get('input[type="number"]').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('parcels', order.order.id, 1, { parcels: [expect.objectContaining({ line_key: 'line1', purchase_record_id: 'purchase1', quantity: 2, carrier: '顺丰', tracking_number: 'SF123' })] })
    expect(wrapper.emitted('saved')).toHaveLength(1)
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('版本冲突刷新版本并保留包裹输入，复核后可再次保存', async () => {
    vi.mocked(fulfillmentCommand).mockRejectedValueOnce(new Error('履约资料已更新'))
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), revision: 5 })
    const wrapper = mount(FulfillmentParcelDialog, { props: { value: detail(), order }, global })
    await fillParcel(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('已刷新订单版本')
    expect((wrapper.get('input[maxlength="100"]').element as HTMLInputElement).value).toBe('SF123')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenLastCalledWith('parcels', order.order.id, 5, expect.anything())
  })

  it('包裹提交期间锁定关闭、删除及编辑', async () => {
    let finish!: (value: FulfillmentDetail) => void
    vi.mocked(fulfillmentCommand).mockReturnValue(new Promise(resolve => { finish = resolve }))
    const wrapper = mount(FulfillmentParcelDialog, { props: { value: detail(), order }, global })
    await fillParcel(wrapper)
    await wrapper.get('form').trigger('submit')
    await wrapper.get('dialog').trigger('cancel')
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined()
    finish(detail())
    await flushPromises()
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('覆盖只显示兼容仓；更换仓库重新读取服务并移除前仓服务', async () => {
    vi.mocked(fetchBusServices).mockResolvedValueOnce({ core_data: [{ id: 2, name: '验货', gold: 0 }], optional_data: [{ id: 3, name: '贴标', gold: 1 }] }).mockResolvedValueOnce({ core_data: [{ id: 8, name: '入库验货', gold: 0 }], optional_data: [] })
    const Host = defineComponent({ components: { FulfillmentPlanFields }, setup() { return { plan: ref<FulfillmentPlan>({ section_id: 1, warehouse_id: 10, service_ids: [2, 3] }), sections: [{ section_id: 1, section_name: '合作渠道', storehouse_list: [{ id: 10, name: 'A仓', code: 'A' }, { id: 20, name: 'B仓', code: 'B' }, { id: 30, name: '非兼容仓', code: 'C' }] }] } }, template: '<FulfillmentPlanFields v-model="plan" :sections="sections" section-locked :compatible-warehouse-ids="[10,20]" />' })
    const wrapper = mount(Host)
    await flushPromises()
    expect(wrapper.findAll('select')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('非兼容仓')
    expect(wrapper.get('input[type="radio"]').element).toHaveProperty('checked', true)
    await wrapper.get('select').setValue('20')
    await flushPromises()
    expect(fetchBusServices).toHaveBeenLastCalledWith(1, 20)
    expect(wrapper.vm.plan.service_ids).toEqual([8])
    expect(wrapper.text()).not.toContain('贴标')
  })

  it('订单详情的录入包裹按钮打开表单；创建待确认只允许核实', async () => {
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '录入包裹')!.trigger('click')
    expect(wrapper.get('dialog').text()).toContain('关联采购记录')
    wrapper.unmount()
    const unresolved = { ...detail(), create_unknown: true, editable: false, plan_editable: false, fulfillment_status: 'EXCEPTION' as const, error_message: '创建结果待确认' }
    vi.mocked(fetchFulfillment).mockResolvedValue(unresolved)
    vi.mocked(fulfillmentCommand).mockResolvedValue(unresolved)
    const unknown = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(unknown.text()).toContain('核实创建结果')
    expect(unknown.text()).not.toContain('立即提交')
    await unknown.findAll('button').find(b => b.text() === '核实创建结果')!.trigger('click')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('sync', order.order.id, 1, {})
  })
})


describe('跨境履约读取已保存状态与单笔手动同步', () => {
  it('进入和停留只读本地状态，单笔同步按钮才外发请求', async () => {
    vi.useFakeTimers()
    const saved = { ...detail(), crossborderbus_order_id: 99, fulfillment_status: 'PACKING' as const }
    vi.mocked(fetchFulfillment).mockResolvedValue(saved)
    vi.mocked(fulfillmentCommand).mockResolvedValue(saved)
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(180_000)
    expect(vi.mocked(fetchFulfillment).mock.calls.length).toBeGreaterThan(1)
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(b => b.text() === '同步')!.trigger('click')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
  })

  it('查询失败保留状态且不自动重试，手动同步可以恢复', async () => {
    vi.useFakeTimers()
    const saved = { ...detail(), crossborderbus_order_id: 99, fulfillment_status: 'PACKING' as const }
    vi.mocked(fetchFulfillment).mockResolvedValue(saved)
    vi.mocked(fulfillmentCommand).mockRejectedValueOnce(new Error('查询失败')).mockResolvedValue(saved)
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(b => b.text() === '同步')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('查询失败')
    expect(wrapper.text()).toContain('已打包')
    await vi.advanceTimersByTimeAsync(600_000)
    expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
    await wrapper.findAll('button').find(b => b.text() === '同步')!.trigger('click')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('查询失败')
  })

  it('后台面单失败通过本地刷新弹出提示，不自动重新获取', async () => {
    vi.useFakeTimers()
    vi.mocked(fetchFulfillment).mockResolvedValueOnce({ ...detail(), busy: true, editable: false, operation: 'fetch-label' })
      .mockResolvedValue({ ...detail(), label_error: '面单获取失败' })
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(useAppStore().toasts.at(-1)?.message).toContain('重新获取面单')
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === '重新获取面单')!.trigger('click')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('fetch-label', order.order.id, 1, {})
  })

  it('未关联订单只读本地；离开页面后迟到的读取不能触发外部同步', async () => {
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    wrapper.unmount()
    let resolve!: (value: FulfillmentDetail) => void
    vi.mocked(fetchFulfillment).mockReturnValue(new Promise(done => { resolve = done }))
    const exited = mount(OrderFulfillmentPanel, { props: { order }, global })
    exited.unmount()
    resolve({ ...detail(), crossborderbus_order_id: 99 })
    await flushPromises()
    expect(fulfillmentCommand).not.toHaveBeenCalled()
  })
})

it('详情跟随本地同步结果更新，编辑时暂停，不额外查询采购或仓库接口', async () => {
  vi.useFakeTimers()
  vi.mocked(fetchOrderDetail).mockResolvedValueOnce(order).mockResolvedValue(withProgress(logistics()))
  const wrapper = mount(OrderDetailPanel, { props: { orderId: order.order.id }, attachTo: document.body, global: { stubs: { RouterLink: global.stubs.RouterLink } } })
  await flushPromises()
  wrapper.findComponent(OrderPurchaseTracking).vm.$emit('lock', true)
  await flushPromises()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(fetchOrderDetail).toHaveBeenCalledTimes(1)
  wrapper.findComponent(OrderPurchaseTracking).vm.$emit('lock', false)
  await flushPromises()
  await vi.advanceTimersByTimeAsync(5000)
  expect(new DOMWrapper(document.body).text()).toContain('YT123')
  expect(syncPurchase).not.toHaveBeenCalled()
  expect(fulfillmentCommand).not.toHaveBeenCalled()
  wrapper.unmount()
  const reads = vi.mocked(fetchOrderDetail).mock.calls.length
  await vi.advanceTimersByTimeAsync(10_000)
  expect(fetchOrderDetail).toHaveBeenCalledTimes(reads)
})

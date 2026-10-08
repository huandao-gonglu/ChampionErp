import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useAppStore } from '@/stores/app'
import FulfillmentParcelDialog from '../FulfillmentParcelDialog.vue'
import FulfillmentPlanFields from '../FulfillmentPlanFields.vue'
import OrderFulfillmentPanel from '../OrderFulfillmentPanel.vue'
import { fetchBusServices, fetchFulfillment, fulfillmentCommand } from '@/api/fulfillment'
import type { FulfillmentDetail, FulfillmentPlan } from '@/types/fulfillment'
import type { OrderDetail } from '@/types/orders'

vi.mock('@/api/fulfillment', () => ({ fetchBusServices: vi.fn(), fetchBusSettings: vi.fn(), fetchFulfillment: vi.fn(), fulfillmentCommand: vi.fn(), uploadFulfillmentLabel: vi.fn() }))
enableAutoUnmount(afterEach)
afterEach(() => vi.useRealTimers())
const source = { supplier: '供应商', source_platform: '1688', product_url: 'https://detail.1688.com/offer/1.html', source_sku_id: 'S1', specification: '银色', sku_url: '', sku_url_verified: false }
const order: OrderDetail = {
  ok: true,
  order: { id: 'yandex:4:FBS:123', platform: 'yandex', account_id: '4', order_id: '123', fulfillment: 'FBS', title: '支架', status: 'PROCESSING', state: 'pending_shipment', shipping_status: '', updated_at: '', checked_at: '', items: [], amount: '', currency: '' },
  lines: [{ line: { sku: 'SKU1', title: '支架', quantity: 2, amount: '', currency: '' }, selection: { line_key: 'line1', revision: 1, status: 'confirmed', source, candidates: [], reason: '' }, records: [{ id: 'purchase1', line_key: 'line1', request_id: 'r1', quantity: 2, purchase_order_number: 'PO1', source, created_at: '', status: 'purchased', cancelled_at: '' }], purchased_quantity: 2, remaining_quantity: 0 }],
}
function detail(): FulfillmentDetail {
  return { erp_order_id: order.order.id, revision: 1, busy: false, editing: false, editable: true, plan_editable: true, create_unknown: false, cancel_requested: false, cancel_rejected: false, crossborderbus_order_id: null, fulfillment_status: 'NEW', operation: '', error_message: '', blocked_reason: '请补齐全部商品的国内包裹及数量。', last_attempt_at: '', last_synced_at: '', next_attempt: 0, platform_label: '', platform_tracking_number: '', label_fetch_supported: true, label_fetch_reason: '', label_error: '', label_attempt_at: '', country: 'RU', plan: { section_id: 1, warehouse_id: 10, service_ids: [2] }, override: false, rule: null, section_name: '合作渠道', warehouse_name: 'A仓', delivery: { fulfillment_model: 'FBS', warehouse_id: 'W1', warehouse_name: '平台仓', method_id: 'M1', method_name: '平台配送', carrier: '', country: 'RU', shipment_id: '', tracking_number: '', label_url: '' }, parcels: [] }
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
  it('没有国内单号不能推断供应商未发货；面单与包裹分别展示记录事实', async () => {
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    const facts = wrapper.get('[aria-label="已记录资料与仓库回执"]').text()
    expect(facts).toContain('2 / 2 件')
    expect(facts).toContain('未录入')
    expect(facts).toContain('待补充')
    expect(facts).toContain('尚未创建')
    expect(wrapper.text()).not.toContain('等待供应商发货')
    expect(wrapper.get('.order-row .order-badge').text()).toBe('待预报')
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
    expect(wrapper.get('input[disabled]').element).toHaveProperty('checked', true)
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


describe('跨境履约按页面同步', () => {
  it('进入时同步一次，停留只刷新本地，按钮和重新进入可再次同步', async () => {
    vi.useFakeTimers()
    const saved = { ...detail(), crossborderbus_order_id: 99, fulfillment_status: 'PACKING' as const }
    vi.mocked(fetchFulfillment).mockResolvedValue(saved)
    vi.mocked(fulfillmentCommand).mockResolvedValue(saved)
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(180_000)
    expect(vi.mocked(fetchFulfillment).mock.calls.length).toBeGreaterThan(1)
    expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
    await wrapper.findAll('button').find(b => b.text() === '同步')!.trigger('click')
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledTimes(3)
  })

  it('查询失败保留状态且不自动重试，手动同步可以恢复', async () => {
    vi.useFakeTimers()
    const saved = { ...detail(), crossborderbus_order_id: 99, fulfillment_status: 'PACKING' as const }
    vi.mocked(fetchFulfillment).mockResolvedValue(saved)
    vi.mocked(fulfillmentCommand).mockRejectedValueOnce(new Error('查询失败')).mockResolvedValue(saved)
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
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

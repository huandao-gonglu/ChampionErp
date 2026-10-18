import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useAppStore } from '@/stores/app'
import FulfillmentPlanFields from '../FulfillmentPlanFields.vue'
import OrderFulfillmentPanel from '../OrderFulfillmentPanel.vue'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import OrderPurchaseRecords from '../OrderPurchaseRecords.vue'
import OrderReceivingWarehouse from '../OrderReceivingWarehouse.vue'
import { fetchOrderDetail, syncPurchase } from '@/api/orders'
import { fetchBusServices, fetchFulfillment, fulfillmentCommand } from '@/api/fulfillment'
import type { FulfillmentDetail, FulfillmentPlan } from '@/types/fulfillment'
import type { OrderDetail } from '@/types/orders'

vi.mock('@/api/fulfillment', () => ({ fetchBusServices: vi.fn(), fetchBusSettings: vi.fn(), fetchFulfillment: vi.fn(), fulfillmentCommand: vi.fn(), uploadFulfillmentLabel: vi.fn() }))
vi.mock('@/api/orders', () => ({ fetchOrderDetail: vi.fn(), syncPurchase: vi.fn(), procurementCommand: vi.fn() }))
enableAutoUnmount(afterEach)
afterEach(() => vi.useRealTimers())
const receiving = { revision: 1, section_id: 1, warehouse_id: 10, section_name: 'Yandex', warehouse_name: 'A仓', transport_service: '普货', delivery_key: 'guoo', confirmed_at: '', address: { fullName: '收件人', mobile: '13800000000', phone: '', provinceText: '福建省', cityText: '福州市', areaText: '', townText: '', address: '收货路 1 号', postCode: '' } }
const source = { supplier: '供应商', source_platform: '1688', product_url: 'https://detail.1688.com/offer/1.html', source_sku_id: 'S1', specification: '银色', sku_url: '', sku_url_verified: false }
const order: OrderDetail = {
  ok: true, purchases: [],
  order: { id: 'yandex:4:FBS:123', platform: 'yandex', account_id: '4', order_id: '123', fulfillment: 'FBS', title: '支架', status: 'PROCESSING', state: 'pending_shipment', shipping_status: '', updated_at: '', checked_at: '', items: [], amount: '', currency: '' },
  lines: [{ line: { sku: 'SKU1', title: '支架', quantity: 2, amount: '', currency: '' }, selection: { line_key: 'line1', revision: 1, status: 'confirmed', source, candidates: [], reason: '' }, product_id: 'product1', purchased_quantity: 2, remaining_quantity: 0 }],
}
function detail(): FulfillmentDetail {
  return { receiving_warehouse: receiving, erp_order_id: order.order.id, revision: 1, busy: false, editing: false, editable: true, plan_editable: true, create_unknown: false, cancel_requested: false, cancel_rejected: false, crossborderbus_order_id: null, fulfillment_status: 'NEW', operation: '', error_message: '', blocked_reason: '请补齐全部商品的国内包裹及数量。', last_attempt_at: '', last_synced_at: '', next_attempt: 0, platform_label: '', platform_tracking_number: '', label_fetch_supported: true, label_fetch_reason: '', label_error: '', label_attempt_at: '', country: 'RU', remark: '', plan: { section_id: 1, warehouse_id: 10, service_ids: [2] }, override: true, rule: null, section_name: '合作渠道', warehouse_name: 'A仓', delivery: { fulfillment_model: 'FBS', warehouse_id: 'W1', warehouse_name: '平台仓', method_id: 'M1', method_name: '平台配送', carrier: '', country: 'RU', shipment_id: '', tracking_number: '', label_url: '' }, parcels: [], parcels_confirmed: false }
}
const global = { stubs: { teleport: true, RouterLink: { template: '<a><slot /></a>' } } }
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(fetchFulfillment).mockResolvedValue(detail())
  vi.mocked(fulfillmentCommand).mockResolvedValue(detail())
  vi.mocked(fetchBusServices).mockResolvedValue({ core_data: [{ id: 2, name: '必选验货', gold: 0 }], optional_data: [{ id: 3, name: '代贴标', gold: 1 }] })
})

describe('跨境履约操作', () => {
  it('作废后配置确实失效才展示重绑入口，重绑不重启旧单', async () => {
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), crossborderbus_order_id: 99,
      fulfillment_status: 'CANCELLED', receiving_binding_stale: true, plan_editable: true, editable: false })
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global: { stubs: { ...global.stubs, OrderReceivingWarehouse: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('已作废 · 99')
    expect(wrapper.text()).toContain('引用的仓库配置也已变化')
    expect(wrapper.findAll('button').some(button => ['提交至合作仓', '重新安排履约'].includes(button.text()))).toBe(false)
    const receiving = wrapper.getComponent(OrderReceivingWarehouse)
    expect(receiving.props('orderId')).toBe(order.order.id)
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), revision: 3, crossborderbus_order_id: 99,
      fulfillment_status: 'CANCELLED', editable: false, plan_editable: true, receiving_binding_stale: false })
    receiving.vm.$emit('updated')
    await flushPromises()
    expect(wrapper.text()).toContain('已作废 · 99')
    expect(wrapper.findComponent(OrderReceivingWarehouse).exists()).toBe(false)
    expect(wrapper.findAll('button').find(button => button.text() === '设置报单方案')!.attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('button').some(button => button.text() === '提交至合作仓')).toBe(false)
    expect(fulfillmentCommand).not.toHaveBeenCalled()
  })

  it('配置失效时直接提供重绑入口并禁止选服务、提交；重绑完成后刷新履约资料', async () => {
    vi.useFakeTimers()
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), receiving_binding_stale: true, blocked_reason: '原绑定已失效' })
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global: { stubs: { ...global.stubs, OrderReceivingWarehouse: true } } })
    await flushPromises()
    const receiving = wrapper.getComponent(OrderReceivingWarehouse)
    expect(receiving.props('orderId')).toBe(order.order.id)
    for (const label of ['设置报单方案', '提交至合作仓']) {
      expect(wrapper.findAll('button').find(button => button.text() === label)!.attributes('disabled')).toBeDefined()
    }
    receiving.vm.$emit('lock', true)
    await flushPromises()
    const reads = vi.mocked(fetchFulfillment).mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchFulfillment).toHaveBeenCalledTimes(reads)
    expect(wrapper.emitted('lock')?.at(-1)).toEqual([true])
    vi.mocked(fetchFulfillment).mockResolvedValue({ ...detail(), receiving_binding_stale: false })
    receiving.vm.$emit('lock', false)
    receiving.vm.$emit('updated')
    await flushPromises()
    expect(wrapper.findComponent(OrderReceivingWarehouse).exists()).toBe(false)
    expect(wrapper.emitted('updated')).toHaveLength(1)
    expect(fulfillmentCommand).not.toHaveBeenCalled()
  })
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

  it('没有国内单号不能推断供应商未发货；面单与包裹分别展示记录事实', async () => {
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    const facts = wrapper.get('[aria-label="报单准备"]').text()
    expect(facts).not.toContain('2 / 2 件')
    expect(wrapper.get('.parcel-table').text()).toContain('尚未登记国内包裹')
    expect(facts).toContain('待补充')
    expect(wrapper.get('.warehouse-activity').text()).toContain('尚未创建')
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
    expect(wrapper.findAll('button').find(b => b.text() === '提交至合作仓')!.attributes('disabled')).toBeDefined()
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
    expect(wrapper.get('dialog').text()).toContain('补寄可直接新增包裹')
    expect(wrapper.get('dialog').find('optgroup').exists()).toBe(false)
    wrapper.unmount()
    const unresolved = { ...detail(), create_unknown: true, editable: false, plan_editable: false, fulfillment_status: 'EXCEPTION' as const, error_message: '创建结果待确认' }
    vi.mocked(fetchFulfillment).mockResolvedValue(unresolved)
    vi.mocked(fulfillmentCommand).mockResolvedValue(unresolved)
    const unknown = mount(OrderFulfillmentPanel, { props: { order }, global })
    await flushPromises()
    expect(unknown.text()).toContain('核实创建结果')
    expect(unknown.text()).not.toContain('提交至合作仓')
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
    await wrapper.findAll('button').find(b => b.text() === '查询仓库状态')!.trigger('click')
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
    await wrapper.findAll('button').find(b => b.text() === '查询仓库状态')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('查询失败')
    expect(wrapper.text()).toContain('已打包')
    await vi.advanceTimersByTimeAsync(600_000)
    expect(fulfillmentCommand).toHaveBeenCalledTimes(1)
    await wrapper.findAll('button').find(b => b.text() === '查询仓库状态')!.trigger('click')
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
  vi.mocked(fetchOrderDetail).mockResolvedValueOnce(order).mockResolvedValue({ ...order, purchases: [{ id: 'updated', request_id: 'req', source_platform: '1688', purchase_order_number: 'UPDATED-PO', items: [], note: '', status: 'purchased', created_at: '', cancelled_at: '' }] })
  const wrapper = mount(OrderDetailPanel, { props: { orderId: order.order.id }, attachTo: document.body, global: { stubs: { RouterLink: global.stubs.RouterLink } } })
  await flushPromises()
  wrapper.findComponent(OrderPurchaseRecords).vm.$emit('lock', true)
  await flushPromises()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(fetchOrderDetail).toHaveBeenCalledTimes(1)
  wrapper.findComponent(OrderPurchaseRecords).vm.$emit('lock', false)
  await flushPromises()
  await vi.advanceTimersByTimeAsync(5000)
  expect(new DOMWrapper(document.body).text()).toContain('UPDATED-PO')
  expect(syncPurchase).not.toHaveBeenCalled()
  expect(fulfillmentCommand).not.toHaveBeenCalled()
  wrapper.unmount()
  const reads = vi.mocked(fetchOrderDetail).mock.calls.length
  await vi.advanceTimersByTimeAsync(10_000)
  expect(fetchOrderDetail).toHaveBeenCalledTimes(reads)
})


describe('取消履约与收货绑定独立', () => {
  it('取消后直接设置方案，打开和关闭不重绑、不重启，保存后才能提交', async () => {
    const cancelled = { ...detail(), crossborderbus_order_id: 99, fulfillment_status: 'CANCELLED' as const,
      cancel_requested: true, editable: false, receiving_binding_stale: false, blocked_reason: '',
      plan: { section_id: 1, warehouse_id: 10, service_ids: [] } }
    const next = { ...detail(), revision: 2, awaiting_resubmission: true, blocked_reason: '', platform_label: 'https://cdn.example/label.pdf', platform_tracking_number: 'TRACK-1', parcels_confirmed: true, parcels: [{ id: 'p1', included: true, tracking_number: 'SF1', quantity: 2, carrier: '顺丰', description: '', image_url: '', source_platform: '', purchase_order_number: '' }] }
    vi.mocked(fetchFulfillment).mockResolvedValue(cancelled)
    const wrapper = mount(OrderFulfillmentPanel, { props: { order }, global: { ...global, stubs: {
      ...global.stubs,
      FulfillmentPlanDialog: defineComponent({ emits: ['close', 'saved'], setup: () => ({ next }),
        template: `<div><button @click="$emit('close')">关闭方案</button><button @click="$emit('saved', next)">保存新方案</button></div>` }),
    } } })
    await flushPromises()
    expect(wrapper.text()).toContain('收货仓绑定保留')
    expect(wrapper.findComponent(OrderReceivingWarehouse).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('提交至合作仓')
    await wrapper.findAll('button').find(b => b.text() === '设置报单方案')!.trigger('click')
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(b => b.text() === '关闭方案')!.trigger('click')
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(b => b.text() === '设置报单方案')!.trigger('click')
    await wrapper.findAll('button').find(b => b.text() === '保存新方案')!.trigger('click')
    expect(wrapper.text()).toContain('提交至合作仓')
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(b => b.text() === '提交至合作仓')!.trigger('click')
    expect(fulfillmentCommand).toHaveBeenCalledWith('submit', order.order.id, 2, {})
  })
})

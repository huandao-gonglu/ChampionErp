import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import FulfillmentPlanDialog from '../FulfillmentPlanDialog.vue'
import { busCommand, fetchBusServices, fulfillmentCommand } from '@/api/fulfillment'
import { fetchOrderAddressNote, saveOrderAddressNote } from '@/api/orders'
import type { OrderSnapshot } from '@/types/orders'
import type { BusSettings, FulfillmentDetail } from '@/types/fulfillment'

vi.mock('@/api/fulfillment', () => ({ busCommand: vi.fn(), fetchBusServices: vi.fn(), fulfillmentCommand: vi.fn() }))
vi.mock('@/api/orders', () => ({ fetchOrderAddressNote: vi.fn(), saveOrderAddressNote: vi.fn() }))
enableAutoUnmount(afterEach)
// 使用真实 Teleport，避免测试替身在插槽更新时反复重建服务选择组件。
const global = {}
function dom() { return new DOMWrapper(document.body).get('dialog:last-of-type') }
function detail(): FulfillmentDetail {
  return { erp_order_id: 'order-1', revision: 3, plan: null, rule: null, warehouse_link: null,
    handover_target: { key: 'address-v1', warehouse_id: 'yandex-100', name: 'CEL 交货仓', address: '义乌市示例路 1 号', shipment_type: 'IMPORT', reason: '' },
  } as FulfillmentDetail
}
function order(): OrderSnapshot {
  return {
    id: 'order-1', platform: 'yandex', account_id: 'shop-1', order_id: '123',
    fulfillment: 'FBS', title: '', status: 'PROCESSING', shipping_status: '',
    state: 'pending_shipment', amount: '', currency: '', updated_at: '', checked_at: '', items: [],
    handover: { state: 'ready', message: '', checked_at: '', shipments: [{
      shipment_id: 'shipment-789', shipment_type: 'IMPORT', status: 'OUTBOUND_CREATED', planned_from: '', planned_to: '',
      origin: { id: 'seller-1', name: '卖家仓', address: '始发地址' },
      destination: { id: 'yandex-100', name: 'CEL 交货仓', address: '义乌市示例路 1 号' },
    }] },
  }
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(fetchOrderAddressNote).mockResolvedValue({ ok: true, address_key: 'shared-address', address: '义乌市示例路 1 号', note: '订单中心备注：实际交给义乌合作仓', revision: 2, updated_at: '' })
  vi.mocked(saveOrderAddressNote).mockImplementation(async body => ({ ok: true, address_key: body.address_key, address: '义乌市示例路 1 号', note: body.note, revision: body.revision + 1, updated_at: '' }))
  vi.mocked(busCommand).mockResolvedValue({ catalog: { sections: [
    { section_id: 143, section_name: 'Yandex', storehouse_list: [{ id: 54, name: '义乌合作仓', code: '122' }, { id: 95, name: '深圳合作仓', code: '100' }] },
    { section_id: 99, section_name: 'Ozon', storehouse_list: [{ id: 999, name: '其他平台仓', code: '' }] },
  ] } } as BusSettings)
  vi.mocked(fetchBusServices).mockResolvedValue({ core_data: [{ id: 338, name: '贴单', gold: 2 }, { id: 341, name: '合包', gold: 5 }], optional_data: [{ id: 339, name: '拍照', gold: 2 }] })
  vi.mocked(fulfillmentCommand).mockResolvedValue(detail())
})
async function choose() {
  await flushPromises()
  await dom().findAll('select').at(-1)!.setValue('54')
  await flushPromises()
  await dom().findAll('input[type="radio"]')[1]!.setValue(true)
}

describe('本单报单仓库与服务', () => {
  it('查看订单中心的同一份地址备注，编辑保存期间禁止关闭或提交报单', async () => {
    const wrapper = mount(FulfillmentPlanDialog, { props: { value: detail(), order: order() }, global, attachTo: document.body })
    await choose()
    await dom().findAll('input[type="checkbox"]').at(-1)!.setValue(true)
    expect(fetchOrderAddressNote).not.toHaveBeenCalled()
    await dom().get('button[aria-haspopup="dialog"]').trigger('click')
    await flushPromises()
    expect(fetchOrderAddressNote).toHaveBeenCalledWith('order-1', 'shipment-789', '义乌市示例路 1 号', expect.any(AbortSignal))
    expect((dom().get('textarea').element as HTMLTextAreaElement).value).toBe('订单中心备注：实际交给义乌合作仓')
    expect(dom().get('button[form="fulfillment-plan"]').attributes('disabled')).toBeDefined()
    expect(dom().get('button[aria-label="关闭选择报单仓库与服务"]').attributes('disabled')).toBeDefined()
    await dom().trigger('cancel')
    await dom().get('form').trigger('submit')
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    await dom().get('textarea').setValue('核对后的收件人和地址')
    let finish!: () => void
    vi.mocked(saveOrderAddressNote).mockImplementationOnce(body => new Promise(resolve => {
      finish = () => resolve({ ok: true, address_key: body.address_key, address: '义乌市示例路 1 号', note: body.note, revision: 3, updated_at: '' })
    }))
    await dom().get('textarea').trigger('keydown', { key: 'Escape' })
    await dom().trigger('cancel')
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(saveOrderAddressNote).toHaveBeenCalledWith({ order_id: 'order-1', shipment_id: 'shipment-789', address_key: 'shared-address', revision: 2, note: '核对后的收件人和地址' })
    finish()
    await flushPromises()
    expect(dom().find('textarea').exists()).toBe(false)
    expect(dom().get('button[form="fulfillment-plan"]').attributes('disabled')).toBeUndefined()
    await dom().trigger('cancel')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('揽收批次使用始发地址备注，不展示其他仓库或已取消批次的备注', async () => {
    const snapshot = order()
    const shipment = snapshot.handover!.shipments[0]!
    shipment.shipment_type = 'WITHDRAW'
    const value = { ...detail(), handover_target: { ...detail().handover_target!, shipment_type: 'WITHDRAW', warehouse_id: 'seller-1', address: '始发地址' } }
    const wrapper = mount(FulfillmentPlanDialog, { props: { value, order: snapshot }, global, attachTo: document.body })
    await flushPromises()
    await dom().get('button[aria-haspopup="dialog"]').trigger('click')
    await flushPromises()
    expect(fetchOrderAddressNote).toHaveBeenCalledWith('order-1', 'shipment-789', '始发地址', expect.any(AbortSignal))
    await dom().get('textarea').trigger('keydown', { key: 'Escape' })
    await wrapper.setProps({ value: { ...value, handover_target: { ...value.handover_target, address: '变更后地址' } } })
    expect(dom().find('button[aria-haspopup="dialog"]').exists()).toBe(false)
    await wrapper.setProps({ value, order: { ...snapshot, handover: { ...snapshot.handover!, shipments: [{ ...shipment, status: 'ERROR' }] } } })
    expect(dom().find('button[aria-haspopup="dialog"]').exists()).toBe(false)
  })

  it('实时读取合作仓，首次确认对应关系；基础单选和附加多选按真实 ID 保存', async () => {
    const wrapper = mount(FulfillmentPlanDialog, { props: { value: detail(), order: order() }, global, attachTo: document.body })
    await choose()
    expect(busCommand).toHaveBeenCalledWith('catalog')
    expect(fetchBusServices).toHaveBeenCalledWith(143, 54)
    expect(dom().text()).toContain('义乌市示例路 1 号')
    expect(dom().text()).not.toContain('其他平台仓')
    expect(dom().get('button[form="fulfillment-plan"]').attributes('disabled')).toBeDefined()
    await dom().findAll('input[type="checkbox"]')[0]!.setValue(true)
    await dom().findAll('input[type="checkbox"]').at(-1)!.setValue(true)
    await dom().get('form').trigger('submit'); await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('plan', 'order-1', 3, {
      plan: { section_id: 143, warehouse_id: 54, service_ids: [341, 339] },
      handover_key: 'address-v1', warehouse_link_revision: 0, confirm_warehouse: true,
    })
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('已确认的仓库固定，选择另一基础服务会替换原选择', async () => {
    const value = { ...detail(), plan: { section_id: 143, warehouse_id: 54, service_ids: [338] }, warehouse_link: { section_id: 143, warehouse_id: 54, revision: 2 } }
    mount(FulfillmentPlanDialog, { props: { value, order: order() }, global, attachTo: document.body })
    await flushPromises()
    expect(dom().get('select').attributes('disabled')).toBeDefined()
    await dom().findAll('input[type="radio"]')[1]!.setValue(true)
    await dom().get('form').trigger('submit'); await flushPromises()
    expect(fulfillmentCommand).toHaveBeenCalledWith('plan', 'order-1', 3, expect.objectContaining({
      plan: { section_id: 143, warehouse_id: 54, service_ids: [341] }, warehouse_link_revision: 2, confirm_warehouse: false,
    }))
  })

  it('地址变化或服务请求失败时不能保存', async () => {
    const wrapper = mount(FulfillmentPlanDialog, { props: { value: detail(), order: order() }, global, attachTo: document.body })
    await choose()
    await dom().findAll('input[type="checkbox"]').at(-1)!.setValue(true)
    await wrapper.setProps({ value: { ...detail(), handover_target: { ...detail().handover_target!, key: 'changed' } } })
    await dom().get('form').trigger('submit')
    expect(fulfillmentCommand).not.toHaveBeenCalled()
    expect(dom().text()).toContain('交货信息或仓库对应关系已变化')
    wrapper.unmount()
    vi.mocked(fetchBusServices).mockRejectedValue(new Error('仓库服务读取失败'))
    mount(FulfillmentPlanDialog, { props: { value: detail(), order: order() }, global, attachTo: document.body })
    await flushPromises()
    await dom().findAll('select').at(-1)!.setValue('54'); await flushPromises()
    expect(dom().text()).toContain('仓库服务读取失败')
    expect(dom().get('button[form="fulfillment-plan"]').attributes('disabled')).toBeDefined()
  })

  it('从内容拖到遮罩不关闭，正常遮罩按下松开可关闭', async () => {
    const wrapper = mount(FulfillmentPlanDialog, { props: { value: detail(), order: order() }, global, attachTo: document.body })
    await flushPromises()
    const pointer = { pointerId: 1, button: 0, isPrimary: true, clientX: -1, clientY: -1 }
    await dom().get('form').trigger('pointerdown', pointer)
    await dom().trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toBeUndefined()
    await dom().trigger('pointerdown', pointer)
    await dom().trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})

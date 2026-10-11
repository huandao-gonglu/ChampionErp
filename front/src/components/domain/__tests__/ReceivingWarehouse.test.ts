import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ReceivingWarehouseDialog from '../ReceivingWarehouseDialog.vue'
import { busCommand, fetchBusServices } from '@/api/fulfillment'
import { fetchReceivingWarehouse, saveReceivingWarehouse } from '@/api/receivingWarehouse'
import type { ReceivingWarehouseView } from '@/api/receivingWarehouse'
import type { BusSettings } from '@/types/fulfillment'
vi.mock('@/api/fulfillment', () => ({ busCommand: vi.fn(), fetchBusServices: vi.fn() }))
vi.mock('@/api/receivingWarehouse', () => ({ fetchReceivingWarehouse: vi.fn(), saveReceivingWarehouse: vi.fn() }))
enableAutoUnmount(afterEach)
const address = { fullName: '仓库收件人', mobile: '13800000000', phone: '', provinceText: '浙江省', cityText: '金华市', areaText: '义乌市', townText: '', address: '货代路 1 号', postCode: '' }
const warehouse = { revision: 1, warehouse_id: 54, warehouse_name: '义乌合作仓', section_id: 143, section_name: 'Yandex', address, address_id: 'addr', address_revision: 2, transport_service: 'Yandex / CEL', delivery_key: 'route', confirmed_at: '' }
function value(): ReceivingWarehouseView { return { ok: true, order_id: 'order-1', revision: 0, warehouse: null, delivery_key: 'route', delivery_name: 'CEL', delivery_evidence: ['CEL Yiwu Extra Small'], blocked_reason: '', locked: false, reserved: false, can_confirm: true, has_purchases: false,
  carrier_detected: 'CEL', carrier: 'CEL',
  candidates: [{ id: 'addr', revision: 2, warehouse_id: 54, warehouse_name: '义乌合作仓', name: '义乌收货点', address, section_id: 143, enabled: true, is_default: true, remark: '收货备注', rules: [], updated_at: '', migrated: false, frozen: false }] } }
function dom() { return new DOMWrapper(document.body).get('dialog:last-of-type') }
function open(v = value()) {
  vi.mocked(fetchReceivingWarehouse).mockResolvedValue(v)
  return mount(ReceivingWarehouseDialog, { props: { value: v }, attachTo: document.body })
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(busCommand).mockResolvedValue({} as BusSettings)
  vi.mocked(saveReceivingWarehouse).mockResolvedValue({ ...value(), revision: 1, warehouse })
})
describe('按物流设置选择采购地址', () => {
  it('默认地址不自动选中，选择并确认后提交地址版本而非自由文本', async () => {
    const wrapper = open(); await flushPromises()
    expect((dom().get('[aria-label="采购收货地址"]').element as HTMLSelectElement).value).toBe('')
    expect(dom().text()).toContain('平台物流商：CEL')
    expect(dom().find('[aria-label="平台物流商"]').exists()).toBe(false)
    expect(dom().find('[aria-label="运输服务"]').exists()).toBe(false)
    await dom().get('[aria-label="采购收货地址"]').setValue('addr:143')
    expect(dom().text()).toContain('仓库收件人'); expect(dom().text()).toContain('收货备注')
    expect(dom().find('[aria-label="详细地址"]').exists()).toBe(false)
    await dom().get('form').trigger('submit'); expect(saveReceivingWarehouse).not.toHaveBeenCalled()
    await dom().get('input[type="checkbox"]').setValue(true)
    await dom().get('form').trigger('submit'); await flushPromises()
    expect(saveReceivingWarehouse).toHaveBeenCalledWith({ order_id: 'order-1', revision: 0, section_id: 143, address_id: 'addr', address_revision: 2, delivery_key: 'route', confirmed: true, existing_purchase_confirmed: false })
    expect(wrapper.emitted('saved')).toHaveLength(1)
    expect(fetchBusServices).not.toHaveBeenCalled()
  })
  it('平台物流商未识别时不能手选绕过，提示同步订单', async () => {
    open({ ...value(), carrier: null, carrier_detected: null, delivery_name: '', candidates: [], can_confirm: false }); await flushPromises()
    expect(dom().text()).toContain('尚未识别平台物流商，请先同步平台交货信息')
    expect(dom().find('[aria-label="平台物流商"]').exists()).toBe(false)
    expect(fetchReceivingWarehouse).toHaveBeenCalledWith('order-1')
    await dom().get('form').trigger('submit'); expect(saveReceivingWarehouse).not.toHaveBeenCalled()
  })
  it('刷新订单后由服务端重新筛选，失效地址清除确认', async () => {
    open(); await flushPromises()
    await dom().get('[aria-label="采购收货地址"]').setValue('addr:143')
    await dom().get('input[type="checkbox"]').setValue(true)
    vi.mocked(fetchReceivingWarehouse).mockResolvedValue({ ...value(), carrier: 'UNI', carrier_detected: 'UNI', delivery_name: 'UNI', candidates: [] })
    await dom().findAll('button').find(b => b.text() === '刷新地址')!.trigger('click'); await flushPromises()
    expect(dom().text()).toContain('平台物流商：UNI')
    expect(dom().text()).toContain('暂无符合本单物流方案')
    await dom().get('form').trigger('submit'); expect(saveReceivingWarehouse).not.toHaveBeenCalled()
    expect(dom().get('a').attributes('href')).toBe('/auth?auth_section=crossborderbus&bus_section=rules')
  })
  it('已有采购补录要求核对包裹实际地址', async () => {
    open({ ...value(), has_purchases: true }); await flushPromises()
    await dom().get('[aria-label="采购收货地址"]').setValue('addr:143')
    await dom().findAll('input[type="checkbox"]')[1]!.setValue(true)
    await dom().get('form').trigger('submit'); expect(saveReceivingWarehouse).not.toHaveBeenCalled()
    await dom().findAll('input[type="checkbox"]')[0]!.setValue(true)
    await dom().get('form').trigger('submit'); await flushPromises()
    expect(saveReceivingWarehouse).toHaveBeenCalledWith(expect.objectContaining({ existing_purchase_confirmed: true }))
  })
  it('冻结采购地址不能切换到其他地址', async () => {
    const v = value(); v.candidates.push({ ...v.candidates[0]!, id: 'other' }); v.candidates[0]!.frozen = true
    open({ ...v, warehouse, revision: 1, locked: true, has_purchases: true }); await flushPromises()
    expect(dom().get('option[value="other:143"]').attributes('disabled')).toBeDefined()
    expect(dom().text()).toContain('采购时保存的收货地址')
  })
  it('内容区按下拖到遮罩不关闭，正常遮罩点击关闭', async () => {
    const wrapper = open(); await flushPromises()
    const pointer = { pointerId: 1, button: 0, isPrimary: true, clientX: -1, clientY: -1 }
    await dom().get('form').trigger('pointerdown', pointer); await dom().trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toBeUndefined()
    await dom().trigger('pointerdown', pointer); await dom().trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})

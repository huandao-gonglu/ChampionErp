import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import OrderSupplementDialog from '../OrderSupplementDialog.vue'
import { lookupSupplement, recordSupplement } from '@/api/orders'
import type { OrderSnapshot, ProcurementLine, OrderDetail } from '@/types/orders'
vi.mock('@/api/orders', () => ({ lookupSupplement: vi.fn(), recordSupplement: vi.fn() }))
vi.mock('@/api/receivingWarehouse', () => ({ fetchReceivingWarehouse: vi.fn() }))
enableAutoUnmount(afterEach)
const item = { line: { title: '蓝色商品', sku: 'SALE', quantity: 2 }, selection: { line_key: 'line', revision: 1 }, records: [], remaining_quantity: 0 } as unknown as ProcurementLine
const order = { id: 'order', platform: 'ozon' } as OrderSnapshot
const candidate = { key: 'candidate', title: '补采商品', specification: '蓝色', offer_id: '123', sku_id: '456', spec_id: '', quantity: 2, matched: false }
function render() { return mount(OrderSupplementDialog, { props: { item, order }, global: { stubs: { teleport: true } } }) }
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(lookupSupplement).mockResolvedValue({ ok: true, order_number: '555', order_status: '等待卖家发货', candidates: [candidate] })
  vi.mocked(recordSupplement).mockResolvedValue({ ok: true, lines: [] } as unknown as OrderDetail)
})
it('已采购齐也可读取补单，确认规格后一次登记并跟踪', async () => {
  const wrapper = render()
  await wrapper.get('input[inputmode="numeric"]').setValue('555')
  await wrapper.findAll('button').find(b => b.text() === '读取采购单')!.trigger('click')
  await flushPromises()
  expect(lookupSupplement).toHaveBeenCalledWith({ order_id: 'order', line_key: 'line', revision: 1, purchase_order_number: '555' })
  const save = wrapper.findAll('button').find(b => b.text() === '确认补单并跟踪')!
  expect(save.attributes('disabled')).toBeDefined()
  await wrapper.get('input[type="checkbox"]').setValue(true)
  await wrapper.findAll('button').find(b => b.text() === '确认补单并跟踪')!.trigger('click'); await flushPromises()
  expect(recordSupplement).toHaveBeenCalledWith(expect.objectContaining({ candidate_key: 'candidate', confirmed: true, purchase_order_number: '555' }))
  expect(wrapper.emitted('updated')).toHaveLength(1)
})
it('多规格不猜配，修改单号后废弃原读取结果', async () => {
  vi.mocked(lookupSupplement).mockResolvedValue({ ok: true, order_number: '555', order_status: '待发货', candidates: [candidate, { ...candidate, key: 'other', sku_id: '789' }] })
  const wrapper = render()
  await wrapper.get('input[inputmode="numeric"]').setValue('555')
  await wrapper.findAll('button').find(b => b.text() === '读取采购单')!.trigger('click'); await flushPromises()
  expect((wrapper.get('select').element as HTMLSelectElement).value).toBe('')
  await wrapper.get('select').setValue('other')
  await wrapper.get('input[type="checkbox"]').setValue(true)
  await wrapper.get('input[inputmode="numeric"]').setValue('556')
  expect(wrapper.find('select').exists()).toBe(false)
  expect(recordSupplement).not.toHaveBeenCalled()
})
it('内容按下遮罩松开不关闭，正常遮罩手势关闭', async () => {
  const wrapper = render()
  await flushPromises()
  const pointer = { pointerId: 1, button: 0, isPrimary: true, clientX: -1, clientY: -1 }
  await wrapper.get('input').trigger('pointerdown', pointer)
  await wrapper.get('dialog').trigger('pointerup', pointer)
  expect(wrapper.emitted('close')).toBeUndefined()
  await wrapper.get('dialog').trigger('pointerdown', pointer)
  await wrapper.get('dialog').trigger('pointerup', pointer)
  expect(wrapper.emitted('close')).toHaveLength(1)
})

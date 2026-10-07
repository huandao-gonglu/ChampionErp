import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { fetchOrderAddressNote, saveOrderAddressNote } from '@/api/orders'
import type { OrderAddressNote as Note } from '@/types/orders'
import OrderAddressNote from '../OrderAddressNote.vue'

vi.mock('@/api/orders', () => ({ fetchOrderAddressNote: vi.fn(), saveOrderAddressNote: vi.fn() }))
enableAutoUnmount(afterEach)
const pointer = { isPrimary: true, button: 0, pointerId: 1 }
function value(note = ''): Note {
  return { ok: true, address_key: 'a'.repeat(64), address: '平台原地址', note, revision: 1, updated_at: '' }
}
function render(orderId = 'order-1') {
  return mount(OrderAddressNote, { attachTo: document.body, props: { orderId, shipmentId: '789', address: '平台原地址' } })
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchOrderAddressNote).mockResolvedValue(value())
  vi.mocked(saveOrderAddressNote).mockImplementation(async body => ({ ...value(body.note), revision: body.revision + 1 }))
})

describe('地址备注气泡', () => {
  it('点击才读取本地备注，只展示输入框；关闭自动保存，再开读取已存内容', async () => {
    const wrapper = render()
    expect(fetchOrderAddressNote).not.toHaveBeenCalled()
    expect(wrapper.find('textarea').exists()).toBe(false)
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('input, textarea')).toHaveLength(1)
    expect(wrapper.findAll('button')).toHaveLength(1)
    await wrapper.get('textarea').setValue('实际地址\n收件人：CEL转ID203299\n电话：123')
    expect(saveOrderAddressNote).not.toHaveBeenCalled()
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(saveOrderAddressNote).toHaveBeenCalledWith({
      order_id: 'order-1', shipment_id: '789', address_key: 'a'.repeat(64), revision: 1,
      note: '实际地址\n收件人：CEL转ID203299\n电话：123',
    })
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.emitted('lock')?.at(-1)).toEqual([false])
    vi.mocked(fetchOrderAddressNote).mockResolvedValue(value('已保存的备注'))
    const anotherOrder = render('order-2')
    await anotherOrder.get('button').trigger('click')
    await flushPromises()
    expect(anotherOrder.get('textarea').element.value).toBe('已保存的备注')
    expect(fetchOrderAddressNote).toHaveBeenLastCalledWith('order-2', '789', '平台原地址', expect.any(AbortSignal))
  })

  it('输入框内部按下、外部松开不关闭；完整外部点击关闭并保存', async () => {
    const wrapper = render()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('选中文本不应丢失')
    await wrapper.get('textarea').trigger('pointerdown', pointer)
    await wrapper.get('.address-note-overlay').trigger('pointerup', pointer)
    expect(wrapper.find('textarea').exists()).toBe(true)
    expect(saveOrderAddressNote).not.toHaveBeenCalled()
    await wrapper.get('.address-note-overlay').trigger('pointerdown', pointer)
    await wrapper.get('.address-note-overlay').trigger('pointerup', pointer)
    await flushPromises()
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(saveOrderAddressNote).toHaveBeenCalledTimes(1)
  })

  it('中文输入法组合期间的 Esc 不关闭气泡', async () => {
    const wrapper = render()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape', isComposing: true })
    expect(wrapper.find('textarea').exists()).toBe(true)
    expect(saveOrderAddressNote).not.toHaveBeenCalled()
  })

  it('未修改不写入，清空备注也会保存', async () => {
    vi.mocked(fetchOrderAddressNote).mockResolvedValue(value('原备注'))
    const wrapper = render()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape' })
    expect(saveOrderAddressNote).not.toHaveBeenCalled()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('')
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(saveOrderAddressNote).toHaveBeenCalledWith(expect.objectContaining({ note: '' }))
  })

  it('提交期间保持锁定并防止重复写入，失败保留输入可再次关闭重试', async () => {
    let reject!: (error: Error) => void
    vi.mocked(saveOrderAddressNote).mockReturnValueOnce(new Promise((_, fail) => { reject = fail }))
    const wrapper = render()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('待保存地址')
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape' })
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape' })
    expect(saveOrderAddressNote).toHaveBeenCalledTimes(1)
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('lock')?.at(-1)).toEqual([true])
    reject(new Error('连接中断'))
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('待保存地址')
    expect(wrapper.get('[role="alert"]').text()).toContain('输入已保留')
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(wrapper.find('textarea').exists()).toBe(false)
  })

  it('并发修改时展示最新备注，保留草稿，核对后再关闭才覆盖', async () => {
    const wrapper = render()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('我的草稿')
    vi.mocked(saveOrderAddressNote).mockRejectedValueOnce(Object.assign(new Error('备注已修改'), { code: 'ADDRESS_NOTE_CONFLICT' }))
    vi.mocked(fetchOrderAddressNote).mockResolvedValue({ ...value('另一个订单写的'), revision: 2 })
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('我的草稿')
    expect(wrapper.text()).toContain('另一个订单写的')
    await wrapper.get('textarea').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(saveOrderAddressNote).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 2, note: '我的草稿' }))
  })

  it('关闭读取中的气泡后丢弃迟到结果；读取失败不能覆盖已有备注', async () => {
    let resolve!: (note: Note) => void
    vi.mocked(fetchOrderAddressNote).mockReturnValueOnce(new Promise(done => { resolve = done }))
    const wrapper = render()
    await wrapper.get('button').trigger('click')
    const signal = vi.mocked(fetchOrderAddressNote).mock.calls[0]![3]!
    await wrapper.get('.address-note-overlay').trigger('keydown', { key: 'Escape' })
    expect(signal.aborted).toBe(true)
    resolve(value('迟到的备注'))
    await flushPromises()
    expect(wrapper.find('textarea').exists()).toBe(false)
    vi.mocked(fetchOrderAddressNote).mockRejectedValueOnce(new Error('读取失败'))
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('读取失败')
    await wrapper.get('.address-note-overlay').trigger('keydown', { key: 'Escape' })
    expect(saveOrderAddressNote).not.toHaveBeenCalled()
  })
})

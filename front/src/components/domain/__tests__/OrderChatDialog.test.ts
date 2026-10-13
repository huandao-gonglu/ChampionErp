import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { fetchOrderChatHistory, sendOrderChatMessage } from '@/api/orderChat'
import OrderChatDialog from '../OrderChatDialog.vue'

vi.mock('@/api/orderChat', () => ({ fetchOrderChatHistory: vi.fn(), sendOrderChatMessage: vi.fn() }))
enableAutoUnmount(afterEach)
const pointer = { isPrimary: true, button: 0, pointerId: 1 }
const history = { ok: true, chat_id: '17', messages: [{ id: '1', sender: 'CUSTOMER', text: '什么时候发货？', created_at: '2026-10-13T10:00:00Z', attachments: [] }] }
const render = () => mount(OrderChatDialog, {
  props: { orderId: 'yandex:4:FBS:123', orderNumber: '123' },
  global: { stubs: { teleport: true } },
})
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(fetchOrderChatHistory).mockResolvedValue(history)
  vi.mocked(sendOrderChatMessage).mockResolvedValue({ ok: true, chat_id: '17' })
})
afterEach(() => vi.useRealTimers())

describe('订单买家聊天', () => {
  it('打开、经过时间、重新打开都不请求，只有点击获取才读取消息', async () => {
    vi.useFakeTimers()
    const wrapper = render()
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchOrderChatHistory).not.toHaveBeenCalled()
    expect(sendOrderChatMessage).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === '获取最新消息')!.trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchOrderChatHistory).toHaveBeenCalledTimes(1)
    expect(fetchOrderChatHistory).toHaveBeenCalledWith('yandex:4:FBS:123')
    expect(wrapper.text()).toContain('什么时候发货？')
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchOrderChatHistory).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    const reopened = render()
    expect(reopened.text()).not.toContain('什么时候发货？')
    expect(fetchOrderChatHistory).toHaveBeenCalledTimes(1)
  })

  it('发送只提交原文，成功清空草稿但不拉取历史', async () => {
    const wrapper = render()
    await wrapper.get('textarea').setValue('您好\n明天发货')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(sendOrderChatMessage).toHaveBeenCalledTimes(1)
    expect(sendOrderChatMessage).toHaveBeenCalledWith('yandex:4:FBS:123', '您好\n明天发货')
    expect(fetchOrderChatHistory).not.toHaveBeenCalled()
    expect(wrapper.get('textarea').element.value).toBe('')
    expect(wrapper.text()).toContain('消息已发送')
  })

  it('发送失败保留草稿并显示原因，不重试或自动读取', async () => {
    vi.mocked(sendOrderChatMessage).mockRejectedValue(new Error('发送结果未知，请核对消息'))
    const wrapper = render()
    await wrapper.get('textarea').setValue('订单说明')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('订单说明')
    expect(wrapper.get('[role="alert"]').text()).toContain('发送结果未知')
    expect(sendOrderChatMessage).toHaveBeenCalledTimes(1)
    expect(fetchOrderChatHistory).not.toHaveBeenCalled()
  })

  it('读取失败保留当前记录，等待下一次手动获取', async () => {
    const wrapper = render()
    const refresh = wrapper.findAll('button').find(button => button.text() === '获取最新消息')!
    await refresh.trigger('click')
    await flushPromises()
    vi.mocked(fetchOrderChatHistory).mockRejectedValue(new Error('平台暂时不可用'))
    await refresh.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('什么时候发货？')
    expect(wrapper.get('[role="alert"]').text()).toBe('平台暂时不可用')
    expect(fetchOrderChatHistory).toHaveBeenCalledTimes(2)
  })

  it('空白及超长消息不发送，按 Unicode 字符计算长度', async () => {
    const wrapper = render()
    for (const value of ['  \n', '字'.repeat(4097)]) {
      await wrapper.get('textarea').setValue(value)
      await wrapper.get('form').trigger('submit')
    }
    expect(sendOrderChatMessage).not.toHaveBeenCalled()
    await wrapper.get('textarea').setValue('😀'.repeat(4096))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(sendOrderChatMessage).toHaveBeenCalledTimes(1)
  })

  it('提交期间锁定关闭和重复发送，结束后正常关闭', async () => {
    let finish!: (value: { ok: boolean; chat_id: string }) => void
    vi.mocked(sendOrderChatMessage).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const wrapper = render()
    await wrapper.get('textarea').setValue('订单说明')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('dialog').trigger('cancel')
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(sendOrderChatMessage).toHaveBeenCalledTimes(1)
    finish({ ok: true, chat_id: '17' })
    await flushPromises()
    await wrapper.get('dialog').trigger('cancel')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('内容区按下遮罩松开不关闭，正常遮罩点击关闭', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.get('textarea').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})

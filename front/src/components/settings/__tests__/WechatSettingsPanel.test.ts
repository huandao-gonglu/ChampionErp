import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import WechatSettingsPanel from '../WechatSettingsPanel.vue'
import { fetchWechatStatus, saveWechatForwardTypes, startWechatLogin, unbindWechat, verifyWechatLogin } from '@/api/wechat'
import type { BackendWechatChannelStatus } from '@/types/workflow.generated'

vi.mock('@/api/wechat', () => ({ fetchWechatStatus: vi.fn(), saveWechatForwardTypes: vi.fn(), startWechatLogin: vi.fn(), unbindWechat: vi.fn(), verifyWechatLogin: vi.fn() }))
enableAutoUnmount(afterEach)
const conversationId = 'conversation_global_chat_' + 'a'.repeat(32)
const status = (extra: Partial<BackendWechatChannelStatus> = {}): BackendWechatChannelStatus => ({
  ok: true, bound: false, connected: false, status: 'unbound', conversation_id: '', qr_image: '', error: '', pending_deliveries: 0,
  forward_types: ['reply', 'progress', 'approval', 'error', 'command'], ...extra,
})
const qrStatus = () => status({ status: 'waiting_scan', qr_image: 'data:image/png;base64,cXI=' })
beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  vi.mocked(fetchWechatStatus).mockResolvedValue(status())
})
afterEach(() => vi.useRealTimers())
const open = () => mount(WechatSettingsPanel, { global: {  } })
const qrDialog = () => new DOMWrapper(document.body.querySelector('dialog')!)
const dialogOpen = () => document.body.querySelector<HTMLDialogElement>('dialog')?.open ?? false
const pointer = { isPrimary: true, button: 0, pointerId: 1 }

describe('微信连接', () => {
  it('列出五类转发选项，未绑定时也能保存选择', async () => {
    vi.mocked(saveWechatForwardTypes).mockResolvedValue(status({ forward_types: ['reply', 'approval', 'error', 'command'] }))
    const wrapper = open(); await flushPromises()
    const choices = wrapper.findAll('input[type="checkbox"]')
    expect(choices).toHaveLength(5)
    expect(choices.every(node => (node.element as HTMLInputElement).checked)).toBe(true)
    expect(wrapper.text()).toContain('回复与结果')
    expect(wrapper.text()).toContain('中途说明')
    expect(wrapper.text()).toContain('失败或中断提示')
    expect(wrapper.text()).toContain('关闭此提示仍会执行 /new')
    await wrapper.get('[data-testid="wechat-forward-progress"]').setValue(false)
    await wrapper.get('[data-testid="wechat-forwarding"]').trigger('submit'); await flushPromises()
    expect(saveWechatForwardTypes).toHaveBeenCalledWith(['reply', 'approval', 'error', 'command'])
    expect(wrapper.get('[data-testid="wechat-save-forwarding"]').attributes('disabled')).toBeDefined()
  })

  it('状态轮询不覆盖尚未保存的勾选，保存失败保留选择', async () => {
    const wrapper = open(); await flushPromises()
    await wrapper.get('[data-testid="wechat-forward-approval"]').setValue(false)
    await vi.advanceTimersByTimeAsync(2000); await flushPromises()
    expect((wrapper.get('[data-testid="wechat-forward-approval"]').element as HTMLInputElement).checked).toBe(false)
    vi.mocked(saveWechatForwardTypes).mockRejectedValueOnce(new Error('保存失败'))
    await wrapper.get('[data-testid="wechat-forwarding"]').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('保存失败')
    expect((wrapper.get('[data-testid="wechat-forward-approval"]').element as HTMLInputElement).checked).toBe(false)
    expect(wrapper.get('[data-testid="wechat-save-forwarding"]').attributes('disabled')).toBeUndefined()
  })

  it('支持全部取消，并在重新进入时回显已保存的设置', async () => {
    vi.mocked(saveWechatForwardTypes).mockResolvedValue(status({ forward_types: [] }))
    const wrapper = open(); await flushPromises()
    for (const checkbox of wrapper.findAll('input[type="checkbox"]')) await checkbox.setValue(false)
    expect(wrapper.text()).toContain('微信发送的文字仍会交给 ERP 处理')
    await wrapper.get('[data-testid="wechat-forwarding"]').trigger('submit'); await flushPromises()
    expect(saveWechatForwardTypes).toHaveBeenCalledWith([])
    wrapper.unmount()
    vi.mocked(fetchWechatStatus).mockResolvedValue(status({ forward_types: [] }))
    const reopened = open(); await flushPromises()
    expect(reopened.findAll('input[type="checkbox"]').every(node => !(node.element as HTMLInputElement).checked)).toBe(true)
  })

  it('没有已有对话也能扫码，点击立即弹出加载窗口，取码期间禁止关闭', async () => {
    let finish!: (value: BackendWechatChannelStatus) => void
    vi.mocked(startWechatLogin).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const wrapper = open(); await flushPromises()
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.get('[data-testid="wechat-login"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="wechat-login"]').trigger('click'); await flushPromises()
    expect(startWechatLogin).toHaveBeenCalledWith()
    expect(dialogOpen()).toBe(true)
    expect(qrDialog().text()).toContain('正在获取二维码')
    await qrDialog().trigger('cancel')
    expect(dialogOpen()).toBe(true)
    finish(qrStatus()); await flushPromises()
    expect(qrDialog().get('img').attributes('src')).toBe('data:image/png;base64,cXI=')
    expect(wrapper.text()).toContain('绑定成功后自动创建 ERP 对话')
    expect(wrapper.text()).toContain('/new')
  })

  it('异步取码后通过轮询显示二维码', async () => {
    vi.mocked(startWechatLogin).mockResolvedValue(status({ status: 'logging_in' }))
    const wrapper = open(); await flushPromises()
    await wrapper.get('[data-testid="wechat-login"]').trigger('click'); await flushPromises()
    vi.mocked(fetchWechatStatus).mockResolvedValue(qrStatus())
    await vi.advanceTimersByTimeAsync(2000); await flushPromises()
    expect(dialogOpen()).toBe(true)
    expect(qrDialog().get('img').attributes('src')).toBe(qrStatus().qr_image)
  })

  it('提交配对码，绑定成功自动关闭窗口，支持解绑', async () => {
    vi.mocked(fetchWechatStatus).mockResolvedValue(status({ ...qrStatus(), status: 'need_verifycode' }))
    vi.mocked(verifyWechatLogin).mockResolvedValue(status({ bound: true, status: 'connecting', conversation_id: conversationId }))
    vi.mocked(unbindWechat).mockResolvedValue(status())
    const wrapper = open(); await flushPromises()
    expect(dialogOpen()).toBe(true)
    await qrDialog().get('input').setValue('123456')
    await qrDialog().get('form').trigger('submit'); await flushPromises()
    expect(verifyWechatLogin).toHaveBeenCalledWith('123456')
    expect(dialogOpen()).toBe(false)
    await wrapper.get('[data-testid="wechat-unbind"]').trigger('click'); await flushPromises()
    expect(unbindWechat).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('未绑定')
  })

  it('内容区按下后在遮罩松开不关闭，正常遮罩点击关闭', async () => {
    vi.mocked(fetchWechatStatus).mockResolvedValue(qrStatus())
    open(); await flushPromises()
    await qrDialog().get('img').trigger('pointerdown', pointer)
    await qrDialog().trigger('pointerup', pointer)
    expect(dialogOpen()).toBe(true)
    await qrDialog().trigger('pointerdown', pointer)
    await qrDialog().trigger('pointerup', pointer)
    expect(dialogOpen()).toBe(false)
  })

  it('离开设置取消轮询', async () => {
    const wrapper = open(); await flushPromises()
    const signal = vi.mocked(fetchWechatStatus).mock.calls[0]![0]!
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(5000)
    expect(signal.aborted).toBe(true)
    expect(fetchWechatStatus).toHaveBeenCalledOnce()
  })

  it('扫码操作后旧轮询的迟到结果不能清除新二维码', async () => {
    const wrapper = open(); await flushPromises()
    let finish!: (value: BackendWechatChannelStatus) => void
    vi.mocked(fetchWechatStatus).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await vi.advanceTimersByTimeAsync(2000)
    vi.mocked(startWechatLogin).mockResolvedValue(qrStatus())
    await wrapper.get('[data-testid="wechat-login"]').trigger('click'); await flushPromises()
    finish(status()); await flushPromises()
    expect(qrDialog().get('img').attributes('src')).toBe(qrStatus().qr_image)
  })

  it('读取失败可重试，扫码错误在窗口内可见', async () => {
    vi.mocked(fetchWechatStatus).mockRejectedValueOnce(new Error('读取失败'))
    const wrapper = open(); await flushPromises()
    expect(wrapper.text()).toContain('读取失败')
    await wrapper.get('[role="alert"] button').trigger('click'); await flushPromises()
    vi.mocked(startWechatLogin).mockRejectedValueOnce(new Error('取码失败'))
    await wrapper.get('[data-testid="wechat-login"]').trigger('click'); await flushPromises()
    expect(dialogOpen()).toBe(true)
    expect(qrDialog().text()).toContain('取码失败')
    expect(qrDialog().text()).toContain('重新获取二维码')
  })
})

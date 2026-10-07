import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ExternalRequestControlPanel from '../ExternalRequestControlPanel.vue'
import { fetchRequestControl, recoverRequestBlock } from '@/api/externalRequests'
import type { RequestControlStatus } from '@/api/externalRequests'
vi.mock('@/api/externalRequests', () => ({ fetchRequestControl: vi.fn(), recoverRequestBlock: vi.fn() }))
enableAutoUnmount(afterEach)
const now = 1800000000
const status = (): RequestControlStatus => ({
  ok: true, server_time: now, total: 2,
  blocks: [{ id: 'block-1', platform: 'yandex', account: 'shop', scope: 'interface', http_status: 0, interface: '/orders', code: 'EXTERNAL_TRANSIENT_FAILURE', message: '网络暂时中断', created_at: now - 60, blocked_count: 4, resume_at: now + 60, recovery_mode: 'probe' }],
  history: ['manual', 'background'].map((trigger, i) => ({ id: String(i), platform: 'yandex', interface: '/orders', trigger, created_at: now - i, code: 'EXTERNAL_NETWORK_INTERRUPTED', message: '网络中断', local_rejection: false })),
})
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchRequestControl).mockResolvedValue(status())
  vi.mocked(recoverRequestBlock).mockResolvedValue({ ok: true, message: '已允许下一次只读请求检查恢复情况' })
})
afterEach(() => vi.useRealTimers())
async function render() {
  const wrapper = mount(ExternalRequestControlPanel, { global: { stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}
describe('授权页统一中断恢复', () => {
  it('展示手动和后台中断，倒计时逐秒减少', async () => {
    vi.useFakeTimers()
    const wrapper = await render()
    expect(wrapper.text()).toContain('1 分 0 秒')
    expect(wrapper.text()).toContain('共 2 条中断记录')
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.text()).toContain('0 分 59 秒')
  })
  it('手动恢复只提交所选阻断，不冒充平台已恢复', async () => {
    const wrapper = await render()
    await wrapper.findAll('button').find(b => b.text() === '手动恢复')!.trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(recoverRequestBlock).toHaveBeenCalledWith('block-1', '')
    expect(wrapper.text()).toContain('已允许下一次只读请求检查恢复情况')
  })
  it('权限中断要求原因，恢复失败保留输入', async () => {
    const value = status()
    value.blocks[0]!.recovery_mode = 'confirm'
    value.blocks[0]!.resume_at = 0
    vi.mocked(fetchRequestControl).mockResolvedValue(value)
    vi.mocked(recoverRequestBlock).mockRejectedValue(new Error('状态已变化'))
    const wrapper = await render()
    await wrapper.findAll('button').find(b => b.text() === '已处理原因，恢复请求')!.trigger('click')
    expect(wrapper.get('form button').attributes('disabled')).toBeDefined()
    await wrapper.get('input').setValue('已开通订单权限')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(recoverRequestBlock).toHaveBeenCalledWith('block-1', '已开通订单权限')
    expect(wrapper.get('input').element.value).toBe('已开通订单权限')
    expect(wrapper.get('form').text()).toContain('状态已变化')
  })
  it('内部按下外部松开不关闭，正常遮罩点击关闭', async () => {
    const wrapper = await render()
    await wrapper.findAll('button').find(b => b.text() === '手动恢复')!.trigger('click')
    const pointer = { pointerId: 1, button: 0, isPrimary: true }
    await wrapper.get('form').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.get('dialog').element.open).toBe(true)
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    await flushPromises()
    expect(wrapper.get('dialog').element.open).toBe(false)
  })
  it('写入未知只提示核对回执，不提供重放按钮', async () => {
    const value = status()
    value.blocks[0]!.recovery_mode = 'verify_result'
    value.blocks[0]!.resume_at = 0
    vi.mocked(fetchRequestControl).mockResolvedValue(value)
    const wrapper = await render()
    expect(wrapper.text()).toContain('先在原功能核对业务回执')
    expect(wrapper.findAll('button').some(b => b.text() === '手动恢复')).toBe(false)
  })
  it('AI 明确拒绝显示原请求范围和恢复按钮，不冒充整体冷却或未知写入', async () => {
    const value = status()
    Object.assign(value.blocks[0]!, { platform: 'ai:deepseek', scope: 'request', http_status: 402, code: 'AI_DEEPSEEK_REQUEST_INVALID', recovery_mode: 'confirm_request', resume_at: 0 })
    vi.mocked(fetchRequestControl).mockResolvedValue(value)
    const wrapper = await render()
    const card = wrapper.findAll('article')[0]!
    expect(card.text()).toContain('HTTP 402')
    expect(card.text()).toContain('不影响新操作')
    expect(card.text()).not.toContain('结果未知')
    expect(card.text()).not.toContain('冷却倒计时：')
    await card.get('button').trigger('click')
    expect(wrapper.get('form').text()).toContain('不会自动重新发送')
    expect(wrapper.get('form button').attributes('disabled')).toBeDefined()
    await wrapper.get('input').setValue('拒绝原因已处理，新的 AI 请求已正常')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(recoverRequestBlock).toHaveBeenCalledWith('block-1', '拒绝原因已处理，新的 AI 请求已正常')
  })
})

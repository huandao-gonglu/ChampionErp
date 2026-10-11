import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { routeLocationKey } from 'vue-router'
import ExternalRequestControlPanel from '../ExternalRequestControlPanel.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { clearRequestHistory, fetchRequestControl, recoverRequestBlock } from '@/api/externalRequests'
import type { RequestControlStatus } from '@/api/externalRequests'
vi.mock('@/api/externalRequests', () => ({ fetchRequestControl: vi.fn(), recoverRequestBlock: vi.fn(), clearRequestHistory: vi.fn() }))
enableAutoUnmount(afterEach)
const now = 1800000000
const status = (): RequestControlStatus => ({
  ok: true, server_time: now, total: 2, cleanup_before: now, clearable_count: 1,
  blocks: [{ id: 'block-1', platform: 'yandex', account: 'shop', scope: 'interface', http_status: 0, interface: '/orders', code: 'EXTERNAL_TRANSIENT_FAILURE', message: '网络暂时中断', created_at: now - 60, last_created_at: now - 60, count: 1, occurrences: [], blocked_count: 4, resume_at: now + 60, recovery_mode: 'probe' }],
  history: ['manual', 'background'].map((trigger, i) => ({ id: String(i), platform: 'yandex', interface: '/orders', trigger, created_at: now - i, code: 'EXTERNAL_NETWORK_INTERRUPTED', message: '网络中断', local_rejection: false })),
})
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchRequestControl).mockResolvedValue(status())
  vi.mocked(recoverRequestBlock).mockResolvedValue({ ok: true, message: '已允许下一次只读请求检查恢复情况' })
  vi.mocked(clearRequestHistory).mockResolvedValue({ ok: true, cleared_count: 1, message: '已清除 1 条历史中断记录' })
})
afterEach(() => vi.useRealTimers())
async function render() {
  const wrapper = mount(ExternalRequestControlPanel, { global: { stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}
describe('系统设置统一中断恢复', () => {
  it('清理全部分页中的旧记录需确认，完成后回到第一页并保留当前阻断', async () => {
    vi.mocked(fetchRequestControl).mockResolvedValue({ ...status(), total: 150, clearable_count: 120 })
    const wrapper = await render()
    await wrapper.findAll('button').find(b => b.text() === '下一页')!.trigger('click')
    await flushPromises()
    expect(fetchRequestControl).toHaveBeenLastCalledWith(50)
    await wrapper.findAll('button').find(b => b.text() === '清除已处理记录')!.trigger('click')
    expect(clearRequestHistory).not.toHaveBeenCalled()
    expect(wrapper.get('form').text()).toContain('全部历史分页中可清理的 120 条')
    expect(wrapper.get('form').text()).toContain('不会解除限制或重试请求')
    vi.mocked(fetchRequestControl).mockResolvedValue({ ...status(), total: 30, clearable_count: 0 })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(clearRequestHistory).toHaveBeenCalledWith(now)
    expect(fetchRequestControl).toHaveBeenLastCalledWith(0)
    expect(recoverRequestBlock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('网络暂时中断')
    expect(wrapper.text()).toContain('共 30 条中断记录')
  })
  it('清理确认框支持取消及完整遮罩手势，取消不删除记录', async () => {
    const wrapper = await render()
    const open = () => wrapper.findAll('button').find(b => b.text() === '清除已处理记录')!.trigger('click')
    await open()
    const pointer = { pointerId: 1, button: 0, isPrimary: true }
    await wrapper.get('form').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.get('dialog').element.open).toBe(true)
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    await flushPromises()
    expect(wrapper.get('dialog').element.open).toBe(false)
    await open()
    await wrapper.findAll('button').find(b => b.text() === '取消')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('dialog').element.open).toBe(false)
    expect(clearRequestHistory).not.toHaveBeenCalled()
  })
  it('没有可清理记录时禁用清理按钮', async () => {
    vi.mocked(fetchRequestControl).mockResolvedValue({ ...status(), clearable_count: 0 })
    const wrapper = await render()
    expect(wrapper.findAll('button').find(b => b.text() === '清除已处理记录')!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('可清理 0 条')
  })
  it('清理失败保留列表和确认框，执行期间不重复提交或关闭', async () => {
    let reject!: (error: Error) => void
    vi.mocked(clearRequestHistory).mockImplementation(() => new Promise((_, fail) => { reject = fail }))
    // 使用真实 Teleport，避免替身在 pending 更新时重建原生 dialog 节点。
    const wrapper = mount(ExternalRequestControlPanel, { attachTo: document.body })
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '清除已处理记录')!.trigger('click')
    await flushPromises()
    const dialog = new DOMWrapper(document.body.querySelector('dialog')!)
    expect(dialog.element.open).toBe(true)
    await dialog.get('form').trigger('submit')
    expect(dialog.get('form').text()).toContain('清理中…')
    await dialog.get('form').trigger('submit')
    expect(wrapper.findComponent(WorkspaceDialog).props('closeDisabled')).toBe(true)
    await dialog.trigger('cancel')
    const pointer = { pointerId: 1, button: 0, isPrimary: true }
    await dialog.trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    expect(dialog.element.open).toBe(true)
    expect(clearRequestHistory).toHaveBeenCalledTimes(1)
    reject(new Error('清理失败，请重试'))
    await flushPromises()
    expect(dialog.get('form').text()).toContain('清理失败，请重试')
    expect(wrapper.text()).toContain('共 2 条中断记录')
  })
  it('同类拒绝显示次数与时间范围，展开明细并按组确认恢复', async () => {
    const value = status()
    Object.assign(value.blocks[0]!, {
      platform: 'gw.open.1688.com', scope: 'request', count: 2,
      recovery_mode: 'confirm_request', http_status: 200, resume_at: 0,
      last_created_at: now, occurrences: [
        { id: 'one', created_at: now - 60, blocked_count: 3 },
        { id: 'two', created_at: now, blocked_count: 1 },
      ],
    })
    vi.mocked(fetchRequestControl).mockResolvedValue(value)
    const wrapper = await render()
    const card = wrapper.findAll('article')[0]!
    expect(card.text()).toContain('同类拒绝 · 2 条请求记录')
    expect(card.text()).toContain('首次')
    expect(card.text()).toContain('最近')
    expect(card.text()).toContain('响应已收到，业务未成功')
    expect(card.get('summary').text()).toBe('查看 2 条明细')
    expect(card.findAll('li')).toHaveLength(2)
    await card.get('button').trigger('click')
    expect(wrapper.get('form').text()).toContain('所选的 2 条请求限制')
    await wrapper.get('input').setValue('已处理接口业务错误')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(recoverRequestBlock).toHaveBeenCalledWith('block-1', '已处理接口业务错误')
  })
  it('提醒链接指定的平台置顶并高亮，路由改变后更新定位', async () => {
    const value = status()
    value.blocks.push({ ...value.blocks[0]!, id: 'ozon-block', platform: 'ozon' })
    vi.mocked(fetchRequestControl).mockResolvedValue(value)
    const route = reactive({ query: { request_platform: ['ozon'] } })
    const wrapper = mount(ExternalRequestControlPanel, { global: {
      stubs: { teleport: true }, provide: { [routeLocationKey as symbol]: route },
    } })
    await flushPromises()
    expect(wrapper.findAll('article')[0]!.text()).toContain('Ozon')
    expect(wrapper.get('[data-highlighted="true"]').text()).toContain('Ozon')
    route.query.request_platform = ['yandex']
    await flushPromises()
    expect(wrapper.findAll('article')[0]!.text()).toContain('Yandex')
    expect(wrapper.get('[data-highlighted="true"]').text()).toContain('Yandex')
  })
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

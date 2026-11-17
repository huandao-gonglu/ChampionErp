import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { desktopLaunch, dismissDesktopLaunch, launchDesktop, receivedDesktopHandoff, acknowledgeDesktopHandoff, retryDesktopLaunch, useDesktopHandoffContext } from '../desktopHandoff'
import { modelOperationReason, setOperationAvailability } from '../desktop'
import { vDesktop } from '../desktopHandoffDirective'
import * as api from '@/api/desktopHandoff'

vi.mock('@/api/desktopHandoff', () => ({
  createDesktopHandoff: vi.fn(), fetchDesktopHandoffStatus: vi.fn(), confirmDesktopHandoff: vi.fn(),
  desktopBackendOrigin: () => 'https://erp.example.test',
}))

describe('网页直接打开桌面并等待客户端确认', () => {
  const navigate = vi.fn()
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    vi.stubGlobal('window', { location: { pathname: '/', search: '?tab=drafts', href: 'https://erp.example.test/?tab=drafts', assign: navigate } })
    vi.mocked(api.createDesktopHandoff).mockResolvedValue({ ok: true, request_id: 'a'.repeat(43), expires_in: 600 })
    vi.mocked(api.fetchDesktopHandoffStatus).mockResolvedValue({ ok: true, opened: false })
  })
  afterEach(() => { dismissDesktopLaunch(); receivedDesktopHandoff.value = null; vi.useRealTimers(); vi.unstubAllGlobals() })

  it('直接唤起，未确认时提供重试，迟到的确认仍可结束等待', async () => {
    await launchDesktop()
    expect(navigate).toHaveBeenCalledWith(expect.stringContaining('champion-erp://continue?request='))
    expect(desktopLaunch.value?.status).toBe('opening')
    await vi.advanceTimersByTimeAsync(5500)
    expect(desktopLaunch.value?.status).toBe('waiting')
    retryDesktopLaunch()
    expect(navigate).toHaveBeenCalledTimes(2)
    expect(api.createDesktopHandoff).toHaveBeenCalledTimes(1)
    vi.mocked(api.fetchDesktopHandoffStatus).mockResolvedValue({ ok: true, opened: true })
    await vi.advanceTimersByTimeAsync(1500)
    expect(desktopLaunch.value?.status).toBe('opened')
    await vi.advanceTimersByTimeAsync(4000)
    expect(desktopLaunch.value).toBeNull()
  })

  it.each([true, false])('桌面版保持在当前窗口，执行器连接状态为 %s', async connected => {
    window.championDesktop = { localOperations: true, openLicenses: vi.fn() }
    setOperationAvailability({
      desktop_reason: connected ? '' : '请从桌面端发起。',
      blocked_use_cases: connected ? {} : { 'copy.generate': '请从桌面端发起。' },
      desktop_use_cases: connected ? [] : ['copy.generate'],
    })
    const reason = modelOperationReason('copy.generate')
    if (connected) expect(reason).toBe('')
    else expect(reason).toContain('重新登录')
    const action = vi.fn()
    const wrapper = mount({ directives: { desktop: vDesktop }, template: `<button v-desktop="'copy.generate'" :disabled="!!reason" @click="action">生成</button>`, setup: () => ({ reason, action }) })
    wrapper.get('button').element.click()
    expect(action).toHaveBeenCalledTimes(connected ? 1 : 0)
    await launchDesktop()
    desktopLaunch.value = { requestId: 'old', url: 'champion-erp://continue', status: 'waiting' }
    retryDesktopLaunch()
    expect(api.createDesktopHandoff).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('重复点击只创建一次请求，关闭后迟到的响应不能重新唤起', async () => {
    let resolve!: (value: Awaited<ReturnType<typeof api.createDesktopHandoff>>) => void
    vi.mocked(api.createDesktopHandoff).mockImplementation(() => new Promise(done => { resolve = done }))
    const pending = launchDesktop()
    await launchDesktop()
    dismissDesktopLaunch()
    resolve({ ok: true, request_id: 'b'.repeat(43), expires_in: 600 })
    await pending
    expect(api.createDesktopHandoff).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
    expect(desktopLaunch.value).toBeNull()
  })

  it('过期后重试仍交接原草稿，保留来源页面的编辑快照', async () => {
    const scope = effectScope()
    scope.run(() => useDesktopHandoffContext(() => ({ target: '/?tab=drafts', state: { selectedDraft: 'draft-a' } })))
    await launchDesktop()
    scope.stop()
    vi.mocked(api.fetchDesktopHandoffStatus).mockRejectedValue({ status: 410 })
    await vi.advanceTimersByTimeAsync(1000)
    expect(desktopLaunch.value?.status).toBe('error')
    retryDesktopLaunch()
    await Promise.resolve()
    expect(api.createDesktopHandoff).toHaveBeenLastCalledWith('/?tab=drafts', { selectedDraft: 'draft-a' })
  })

  it('所有页面部分恢复完成后才确认，确认可安全重复', async () => {
    receivedDesktopHandoff.value = { requestId: 'parts', target: '/', state: { workflow: {}, chat: { text: '继续', attachments: [] } } }
    await acknowledgeDesktopHandoff('parts', 'workflow')
    expect(api.confirmDesktopHandoff).not.toHaveBeenCalled()
    await acknowledgeDesktopHandoff('parts', 'chat')
    expect(api.confirmDesktopHandoff).toHaveBeenCalledWith('parts')
    await acknowledgeDesktopHandoff('parts', 'chat')
    expect(api.confirmDesktopHandoff).toHaveBeenCalledTimes(1)
  })

  it('本机模型按钮接管网页点击，避免向业务入口提交一次失败请求', async () => {
    setOperationAvailability({ desktop_reason: '需要本机', blocked_use_cases: { 'copy.generate': '需要本机模型' }, desktop_use_cases: ['copy.generate'] })
    const action = vi.fn()
    const wrapper = mount({ directives: { desktop: vDesktop }, template: `<button v-desktop="'copy.generate'" @click="action">生成</button>`, setup: () => ({ action }) })
    wrapper.get('button').element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    await flushPromises()
    expect(action).not.toHaveBeenCalled()
    expect(api.createDesktopHandoff).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})

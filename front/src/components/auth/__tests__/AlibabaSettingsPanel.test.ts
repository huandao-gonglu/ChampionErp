import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import AlibabaSettingsPanel from '../AlibabaSettingsPanel.vue'
import * as api from '@/api/alibabaAuthorization'
import { hasLocalOperations } from '@/utils/desktop'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const account = vi.hoisted(() => ({ isAdmin: false }))
vi.mock('@/stores/account', () => ({ useAccountStore: () => account }))
vi.mock('@/api/alibabaAuthorization', () => ({ bindOneBoundBuyerToken: vi.fn(), completeAlibabaAuthorization: vi.fn(), fetchAlibabaAuthorization: vi.fn(), startAlibabaAuthorization: vi.fn() }))
vi.mock('@/utils/desktop', () => ({ hasLocalOperations: vi.fn() }))
enableAutoUnmount(afterEach)
const status: BackendAlibabaAuthorizationStatus = {
  ok: true, active: true, application_type: 'alibaba_crossborder', application_configured: true, can_authorize: true, authorized: true, member_id: 'buyer-a',
  account_name: '采购甲', attempt: 'idle', expires_at: 0, refreshable: true, needs_reauthorization: false,
}
const url = 'https://auth.1688.com/auth/authorize.htm?state=opaque'
const tab = { location: { replace: vi.fn() }, close: vi.fn(), opener: window }
async function panel(provider: BackendAlibabaAuthorizationStatus['application_type'] = 'alibaba_crossborder') {
  const wrapper = mount(AlibabaSettingsPanel, { props: { provider } })
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  account.isAdmin = false
  vi.useFakeTimers()
  vi.clearAllMocks()
  vi.mocked(hasLocalOperations).mockReturnValue(false)
  vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
  vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue(status)
  vi.mocked(api.startAlibabaAuthorization).mockResolvedValue({ ok: true, url })
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('1688 浏览器授权', () => {
  it('普通用户能看到万邦自己的授权状态', async () => {
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, application_type: 'onebound', can_authorize: false, authorized: false })
    const wrapper = await panel('onebound')
    expect(wrapper.text()).toContain('暂未接入万邦的买家登录')
    expect(wrapper.find('input, button').exists()).toBe(false)
    expect(api.fetchAlibabaAuthorization).toHaveBeenCalledWith('onebound')
  })

  it('管理员在万邦卡片绑定自己的买家 Token', async () => {
    account.isAdmin = true
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, application_type: 'onebound', active: false, can_authorize: false, authorized: false })
    vi.mocked(api.bindOneBoundBuyerToken).mockResolvedValue({ ...status, application_type: 'onebound', authorized: true })
    const wrapper = await panel('onebound')
    await wrapper.get('input').setValue('personal-token')
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(api.bindOneBoundBuyerToken).toHaveBeenCalledWith('personal-token')
    expect(wrapper.text()).toContain('已连接')
    expect(wrapper.get('input').element.value).toBe('')
    expect(api.startAlibabaAuthorization).not.toHaveBeenCalled()
  })

  it('自用应用显示独立的授权说明', async () => {
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, application_type: 'alibaba' })
    const wrapper = await panel('alibaba')
    expect(wrapper.text()).toContain('企业自用应用 · 1688 授权')
    expect(wrapper.text()).not.toContain('请先在 1688 订购本服务')
  })

  it('服务器完成回调后自动更新账号', async () => {
    const wrapper = await panel()
    expect(wrapper.text()).toContain('采购甲')
    expect(wrapper.findAll('button')).toHaveLength(1)
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(api.startAlibabaAuthorization).toHaveBeenCalledWith('alibaba_crossborder')
    expect(tab.opener).toBeNull()
    expect(tab.location.replace).toHaveBeenCalledWith(url)
    expect(wrapper.find('a, input, form').exists()).toBe(false)
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValueOnce({ ...status, attempt: 'waiting' })
    await vi.advanceTimersByTimeAsync(2500)
    expect(wrapper.text()).toContain('等待浏览器完成授权')
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, account_name: '采购乙' })
    await vi.advanceTimersByTimeAsync(2500)
    expect(api.fetchAlibabaAuthorization).toHaveBeenLastCalledWith('alibaba_crossborder')
    expect(wrapper.text()).toContain('采购乙')
    expect(wrapper.text()).not.toContain('等待浏览器完成授权')
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.fetchAlibabaAuthorization).toHaveBeenCalledTimes(3)
  })

  it('管理员提交回调后显示授权账号并清空输入', async () => {
    account.isAdmin = true
    const wrapper = await panel()
    const callback = 'http://localhost:5050/auth/alibaba/callback?code=c&state=s'
    const input = wrapper.get('input')
    await input.setValue(callback)
    vi.mocked(api.completeAlibabaAuthorization).mockResolvedValue({ ...status, account_name: '管理员买家' })
    await wrapper.get('input').trigger('keydown.enter')
    await flushPromises()
    expect(api.completeAlibabaAuthorization).toHaveBeenCalledWith(callback, 'alibaba_crossborder')
    expect(wrapper.text()).toContain('管理员买家')
    expect((input.element as HTMLInputElement).value).toBe('')
    expect(window.open).not.toHaveBeenCalled()
  })

  it('桌面端交给系统浏览器打开官方地址', async () => {
    vi.mocked(hasLocalOperations).mockReturnValue(true)
    const openAlibabaAuthorization = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('championDesktop', { openAlibabaAuthorization })
    const wrapper = await panel()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(openAlibabaAuthorization).toHaveBeenCalledWith(url)
    expect(window.open).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('等待浏览器完成授权')
  })

  it('桌面浏览器启动失败后显示错误，服务端待回调不会覆盖提示', async () => {
    vi.mocked(hasLocalOperations).mockReturnValue(true)
    vi.stubGlobal('championDesktop', { openAlibabaAuthorization: vi.fn().mockRejectedValue(new Error()) })
    const wrapper = await panel()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, attempt: 'waiting' })
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('无法打开系统浏览器')
    expect(wrapper.text()).not.toContain('等待浏览器完成授权')
  })

  it('弹窗被拦截时允许重试', async () => {
    vi.mocked(window.open).mockReturnValue(null)
    const wrapper = await panel()
    await wrapper.get('button').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('允许此站点打开弹窗')
    expect(api.startAlibabaAuthorization).not.toHaveBeenCalled()
  })

  it('重新进入设置页后继续等待，失败时保留原账号', async () => {
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValueOnce({ ...status, attempt: 'waiting' })
    const wrapper = await panel()
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, attempt: 'failed' })
    await vi.advanceTimersByTimeAsync(2500)
    expect(wrapper.text()).toContain('采购甲')
    expect(wrapper.text()).toContain('本次授权未完成')
  })
})

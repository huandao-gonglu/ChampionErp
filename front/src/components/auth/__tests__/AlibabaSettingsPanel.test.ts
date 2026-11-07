import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import zh from '@/i18n/locales/zh'
import AlibabaSettingsPanel from '../AlibabaSettingsPanel.vue'
import * as api from '@/api/alibabaAuthorization'
import { hasLocalOperations } from '@/utils/desktop'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const account = vi.hoisted(() => ({ isAdmin: false }))
vi.mock('@/stores/account', () => ({ useAccountStore: () => account }))
vi.mock('@/api/alibabaAuthorization', () => ({ completeAlibabaAuthorization: vi.fn(), fetchAlibabaAuthorization: vi.fn(), startAlibabaAuthorization: vi.fn() }))
vi.mock('@/utils/desktop', () => ({ hasLocalOperations: vi.fn() }))
enableAutoUnmount(afterEach)
const status: BackendAlibabaAuthorizationStatus = {
  ok: true, application_configured: true, can_authorize: true, authorized: true, member_id: 'buyer-a',
  account_name: '采购甲', attempt: 'idle', expires_at: 0, refreshable: true, needs_reauthorization: false,
}
const url = 'https://auth.1688.com/auth/authorize.htm?state=opaque'
const tab = { location: { replace: vi.fn() }, close: vi.fn(), opener: window }
async function panel() {
  const wrapper = mount(AlibabaSettingsPanel, { global: {
    plugins: [createI18n({ legacy: false, locale: 'zh', messages: { zh } })],
  } })
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
  it('服务器完成回调后自动更新账号', async () => {
    const wrapper = await panel()
    expect(wrapper.text()).toContain('采购甲')
    expect(wrapper.findAll('button')).toHaveLength(1)
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(api.startAlibabaAuthorization).toHaveBeenCalledWith()
    expect(tab.opener).toBeNull()
    expect(tab.location.replace).toHaveBeenCalledWith(url)
    expect(wrapper.find('a, input, form').exists()).toBe(false)
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValueOnce({ ...status, attempt: 'waiting' })
    await vi.advanceTimersByTimeAsync(2500)
    expect(wrapper.text()).toContain('等待浏览器完成授权')
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, account_name: '采购乙' })
    await vi.advanceTimersByTimeAsync(2500)
    expect(api.fetchAlibabaAuthorization).toHaveBeenLastCalledWith()
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
    await wrapper.get('button[type="button"]').trigger('click')
    await flushPromises()
    expect(api.completeAlibabaAuthorization).toHaveBeenCalledWith(callback)
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

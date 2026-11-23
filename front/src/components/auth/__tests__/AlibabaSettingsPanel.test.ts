import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import AlibabaSettingsPanel from '../AlibabaSettingsPanel.vue'
import * as api from '@/api/alibabaAuthorization'
import { hasLocalOperations } from '@/utils/desktop'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const account = vi.hoisted(() => ({ isAdmin: false }))
vi.mock('@/stores/account', () => ({ useAccountStore: () => account }))
vi.mock('@/api/alibabaAuthorization', () => ({
  bindSelfUseToken: vi.fn(),
  completeAlibabaAuthorization: vi.fn(),
  fetchAlibabaAuthorization: vi.fn(),
  fetchOneBoundQuota: vi.fn(),
  restoreOneBoundKey: vi.fn(),
  startAlibabaAuthorization: vi.fn(),
}))
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
  vi.mocked(api.fetchOneBoundQuota).mockResolvedValue({
    ok: true, configured: true, is_admin: false, active_provider: 'onebound', authorized: true,
    buyer: { member_id: 'buyer-a', account_name: '采购甲', balance: 185, granted: 200, earned: 10, spent_total: 25, spent_trade: 5, spent_auth: 2, spent_collect: 18 },
    buyers: [], key: null, entries: [],
  })
  vi.mocked(api.startAlibabaAuthorization).mockResolvedValue({ ok: true, url })
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('1688 浏览器授权', () => {
  it.each([{ isAdmin: false, role: '普通用户' }, { isAdmin: true, role: '管理员' }])('$role 使用回跳地址完成万邦自助授权', async ({ isAdmin }) => {
    account.isAdmin = isAdmin
    const onebound = { ...status, application_type: 'onebound' as const, refreshable: false }
    const link = 'https://o0b.cn/9vXze?state=vendor-key'
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...onebound, authorized: false, account_name: '' })
    vi.mocked(api.startAlibabaAuthorization).mockResolvedValue({ ok: true, url: link })
    const wrapper = await panel('onebound')
    expect(api.fetchAlibabaAuthorization).toHaveBeenCalledWith('onebound')
    expect(wrapper.find('input').exists()).toBe(false)
    const connect = wrapper.get('[data-testid="connect-1688"]')
    expect(connect.text()).toBe('连接 1688 账号')
    await connect.trigger('click')
    await flushPromises()
    expect(api.startAlibabaAuthorization).toHaveBeenCalledWith('onebound')
    expect(tab.location.replace).toHaveBeenCalledWith(link)
    expect(wrapper.text()).toContain('回跳页可能空白')
    expect(wrapper.findAll('input').map(input => input.attributes('id'))).toEqual(['onebound-callback-url'])
    const callback = 'https://dc.dev.1buyo.com/api/Accredit1688/callback_1688?state=vendor-key&code=return-code'
    const input = wrapper.get('#onebound-callback-url')
    await input.setValue(callback)
    vi.mocked(api.completeAlibabaAuthorization).mockResolvedValue({ ...onebound, account_name: '个人买家' })
    await input.trigger('keydown.enter')
    await flushPromises()
    expect(api.completeAlibabaAuthorization).toHaveBeenCalledWith(callback, 'onebound')
    expect(wrapper.text()).toContain('已连接')
    expect(wrapper.text()).toContain('个人买家')
    expect(wrapper.find('input').exists()).toBe(false)
    expect(api.bindSelfUseToken).not.toHaveBeenCalled()
  })

  it('自用应用显示独立的授权说明', async () => {
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, application_type: 'alibaba' })
    const wrapper = await panel('alibaba')
    expect(wrapper.text()).toContain('企业自用应用 · 1688 授权')
    expect(wrapper.text()).not.toContain('请先在 1688 订购本服务')
  })

  it('管理员在企业自用卡验证 Token，提交成功后清空输入', async () => {
    account.isAdmin = true
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status, application_type: 'alibaba' })
    vi.mocked(api.bindSelfUseToken).mockResolvedValue({ ...status, application_type: 'alibaba', authorized: true })
    const wrapper = await panel('alibaba')
    const input = wrapper.get('#alibaba-buyer-token')
    await input.setValue('self-use-token')
    await input.trigger('keydown.enter')
    await flushPromises()
    expect(api.bindSelfUseToken).toHaveBeenCalledWith('self-use-token')
    expect(api.completeAlibabaAuthorization).not.toHaveBeenCalled()
    expect((input.element as HTMLInputElement).value).toBe('')
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

  it('万邦卡展示买家余额构成，管理员可查看企业 Key 用量并恢复已停用的 Key', async () => {
    account.isAdmin = true
    const onebound = { ...status, application_type: 'onebound' as const, refreshable: false }
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue(onebound)
    vi.mocked(api.fetchOneBoundQuota).mockResolvedValue({
      ok: true, configured: true, is_admin: true, active_provider: 'onebound', authorized: true,
      buyer: { member_id: 'buyer-a', account_name: '采购甲', balance: 185, granted: 200, earned: 10, spent_total: 25, spent_trade: 5, spent_auth: 2, spent_collect: 18 },
      buyers: [],
      key: {
        configured: true, today_snapshot: 120, today_estimated: 125, daily_max: 10000, reserve: 500,
        expires: '2030-02-19', days_until_expiry: 1000, expiring_soon: false, expired: false,
        observed_at: 1700000000, disabled: true, disabled_code: 'ONEBOUND_ACCOUNT_DISABLED',
        disabled_message: '万邦账户余额不足（4016）', disabled_at: 1700000001, exhausted_until: null, initial_grant: 200,
      },
      entries: [],
    })
    vi.mocked(api.restoreOneBoundKey).mockResolvedValue({
      ok: true, restored: true,
      quota: {
        ok: true, configured: true, is_admin: true, active_provider: 'onebound', authorized: true,
        buyer: { member_id: 'buyer-a', account_name: '采购甲', balance: 185, granted: 200, earned: 10, spent_total: 25, spent_trade: 5, spent_auth: 2, spent_collect: 18 },
        buyers: [],
        key: {
          configured: true, today_snapshot: 120, today_estimated: 125, daily_max: 10000, reserve: 500,
          expires: '2030-02-19', days_until_expiry: 1000, expiring_soon: false, expired: false,
          observed_at: 1700000000, disabled: false, disabled_code: '', disabled_message: '',
          disabled_at: null, exhausted_until: null, initial_grant: 200,
        },
        entries: [],
      },
    })
    const wrapper = await panel('onebound')
    expect(wrapper.text()).toContain('余额 185 次')
    expect(wrapper.text()).toContain('基础额度 200')
    expect(wrapper.text()).toContain('125 / 10000')
    expect(wrapper.text()).toContain('保留额度 500')
    expect(wrapper.text()).toContain('万邦账户余额不足（4016）')
    await wrapper.get('[data-testid="restore-onebound-key"]').trigger('click')
    await flushPromises()
    expect(api.restoreOneBoundKey).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('已恢复万邦企业 Key 使用')
    expect(wrapper.find('[data-testid="restore-onebound-key"]').exists()).toBe(false)
  })
})

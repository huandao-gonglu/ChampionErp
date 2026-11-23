import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ManagedSettingsPanel from '../ManagedSettingsPanel.vue'
import { fetchOneBoundQuota, restoreOneBoundKey } from '@/api/alibabaAuthorization'
import { fetchManagedSettings, saveManagedSettings, type ManagedSettings } from '@/api/managedSettings'

vi.mock('@/api/managedSettings', () => ({ fetchManagedSettings: vi.fn(), saveManagedSettings: vi.fn() }))
vi.mock('@/api/alibabaAuthorization', () => ({ fetchOneBoundQuota: vi.fn(), restoreOneBoundKey: vi.fn() }))
enableAutoUnmount(afterEach)
const saved: ManagedSettings = {
  ok: true, revision: 'revision-1',
  alibaba_api_application: 'alibaba',
  exchange_rates: { exchange_rate_api_url: 'https://rates.example/api', exchange_rate_timeout_seconds: '10', exchange_rate_cache_ttl_seconds: '3600' },
  alibaba: { app_key: '123', redirect_uri: 'https://erp.example/auth/alibaba/callback', secret_configured: true },
  alibaba_crossborder: { app_key: '', redirect_uri: '', secret_configured: false },
  onebound: { api_key: '', secret_configured: false, initial_grant: 200 },
  crossborderbus_secret_configured: true,
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchManagedSettings).mockResolvedValue(structuredClone(saved))
  vi.mocked(saveManagedSettings).mockResolvedValue(structuredClone(saved))
  vi.mocked(fetchOneBoundQuota).mockResolvedValue({
    ok: true, configured: false, is_admin: true, active_provider: 'alibaba', authorized: false,
    buyer: null, buyers: [], key: null, entries: [],
  })
})

it('管理员读取仅展示配置状态，空密钥保存时沿用已有值', async () => {
  const wrapper = mount(ManagedSettingsPanel); await flushPromises()
  expect(wrapper.findAll('input[type="password"]').every(input => (input.element as HTMLInputElement).value === '')).toBe(true)
  await wrapper.get('form').trigger('submit'); await flushPromises()
  const payload = vi.mocked(saveManagedSettings).mock.calls[0][0]
  expect(payload).toMatchObject({ revision: 'revision-1', exchange_rates: saved.exchange_rates })
  expect(payload.alibaba_crossborder).not.toHaveProperty('app_secret')
  expect(payload.alibaba_crossborder).not.toHaveProperty('secret_configured')
  expect(payload.alibaba).toEqual({ app_key: saved.alibaba.app_key, redirect_uri: saved.alibaba.redirect_uri })
  expect(payload.alibaba_api_application).toBe('alibaba')
  expect(payload.onebound).toEqual({ api_key: '', initial_grant: 200 })
  expect(payload).not.toHaveProperty('crossborderbus_client_secret')
  expect(wrapper.text()).toContain('管理员本人使用的 1688 方案')
  expect(wrapper.text()).toContain('普通用户固定使用万邦')
  expect(wrapper.text()).toContain('共享设置已保存')
})

it('管理员可以选择万邦并保存独立企业凭据', async () => {
  const wrapper = mount(ManagedSettingsPanel, { attachTo: document.body })
  await flushPromises()
  await wrapper.get('select').setValue('onebound')
  const section = wrapper.get('[aria-label="万邦企业配置"]')
  await section.get('input[autocomplete="off"]').setValue('vendor-key')
  await section.get('input[type="password"]').setValue('vendor-secret')
  await section.get('input[type="number"]').setValue('350')
  expect(saveManagedSettings).not.toHaveBeenCalled()
  await wrapper.get('header button[type="submit"]').trigger('click')
  await flushPromises()
  expect(saveManagedSettings).toHaveBeenCalledWith(expect.objectContaining({
    alibaba_api_application: 'onebound', onebound: { api_key: 'vendor-key', api_secret: 'vendor-secret', initial_grant: 350 },
  }))
  expect((section.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
})

it('管理员可切换应用并分别保存两套凭据', async () => {
  const wrapper = mount(ManagedSettingsPanel); await flushPromises()
  expect(wrapper.get('select').element.value).toBe('alibaba')
  await wrapper.get('select').setValue('alibaba_crossborder')
  const passwords = wrapper.findAll('input[type="password"]')
  await passwords[0].setValue('self-secret')
  await passwords[1].setValue('crossborder-secret')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(saveManagedSettings).toHaveBeenCalledWith(expect.objectContaining({
    alibaba_api_application: 'alibaba_crossborder',
    alibaba: expect.objectContaining({ app_secret: 'self-secret' }),
    alibaba_crossborder: expect.objectContaining({ app_secret: 'crossborder-secret' }),
  }))
  expect(passwords.every(input => (input.element as HTMLInputElement).value === '')).toBe(true)
})

it('顶部保存会提示无效字段，保存失败保留修改并可以重试', async () => {
  vi.mocked(saveManagedSettings).mockRejectedValueOnce(new Error('共享设置保存失败，请重试'))
  const wrapper = mount(ManagedSettingsPanel, { attachTo: document.body })
  const saveButton = wrapper.get('header button[type="submit"]')
  expect(saveButton.element).toHaveProperty('disabled', true)
  await flushPromises()
  const apiUrl = wrapper.get('input[type="url"][required]')
  await apiUrl.setValue('')
  await saveButton.trigger('click')
  expect(saveManagedSettings).not.toHaveBeenCalled()
  expect(wrapper.get('header [role="alert"]').text()).toContain('请检查表单')

  const updatedUrl = 'https://rates.example/latest'
  const updated = { ...saved, revision: 'revision-2', exchange_rates: { ...saved.exchange_rates, exchange_rate_api_url: updatedUrl } }
  vi.mocked(saveManagedSettings).mockResolvedValue(updated)
  await apiUrl.setValue(updatedUrl)
  await saveButton.trigger('click')
  await flushPromises()
  expect(wrapper.get('header [role="alert"]').text()).toBe('共享设置保存失败，请重试')
  expect(apiUrl.element).toHaveProperty('value', updatedUrl)
  await saveButton.trigger('click')
  await flushPromises()
  expect(wrapper.get('header [role="status"]').text()).toBe('共享设置已保存')
  expect(saveManagedSettings).toHaveBeenCalledTimes(2)

  await wrapper.get('select').setValue('alibaba_crossborder')
  expect(wrapper.find('header [role="status"]').exists()).toBe(false)
  await saveButton.trigger('click')
  expect(saveManagedSettings).toHaveBeenLastCalledWith(expect.objectContaining({
    revision: 'revision-2',
    alibaba_api_application: 'alibaba_crossborder',
  }))
  await flushPromises()
})

it('万邦企业 Key 停用时展示恢复按钮，点击后恢复使用', async () => {
  const disabledKey = {
    configured: true, today_snapshot: 10, today_estimated: 10, daily_max: 1000, reserve: 50,
    expires: '2030-01-01', days_until_expiry: 1000, expiring_soon: false, expired: false,
    observed_at: 1700000000, disabled: true, disabled_code: 'ONEBOUND_ACCOUNT_DISABLED',
    disabled_message: '万邦账户余额不足（4016）', disabled_at: 1700000001, exhausted_until: null, initial_grant: 200,
  }
  vi.mocked(fetchOneBoundQuota).mockResolvedValue({
    ok: true, configured: true, is_admin: true, active_provider: 'onebound', authorized: true,
    buyer: null, buyers: [], key: disabledKey, entries: [],
  })
  vi.mocked(restoreOneBoundKey).mockResolvedValue({
    ok: true, restored: true,
    quota: {
      ok: true, configured: true, is_admin: true, active_provider: 'onebound', authorized: true,
      buyer: null, buyers: [], key: { ...disabledKey, disabled: false, disabled_code: '', disabled_message: '' }, entries: [],
    },
  })
  const wrapper = mount(ManagedSettingsPanel)
  await flushPromises()
  expect(wrapper.text()).toContain('万邦账户余额不足（4016）')
  const restoreBtn = wrapper.findAll('button').find(b => b.text().includes('恢复使用'))!
  await restoreBtn.trigger('click')
  await flushPromises()
  expect(restoreOneBoundKey).toHaveBeenCalledTimes(1)
  expect(wrapper.text()).toContain('已恢复万邦企业 Key 使用')
})

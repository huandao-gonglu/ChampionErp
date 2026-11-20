import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ManagedSettingsPanel from '../ManagedSettingsPanel.vue'
import { fetchManagedSettings, saveManagedSettings, type ManagedSettings } from '@/api/managedSettings'

vi.mock('@/api/managedSettings', () => ({ fetchManagedSettings: vi.fn(), saveManagedSettings: vi.fn() }))
enableAutoUnmount(afterEach)
const saved: ManagedSettings = {
  ok: true, revision: 'revision-1',
  alibaba_api_application: 'alibaba',
  exchange_rates: { exchange_rate_api_url: 'https://rates.example/api', exchange_rate_timeout_seconds: '10', exchange_rate_cache_ttl_seconds: '3600' },
  alibaba: { app_key: '123', redirect_uri: 'https://erp.example/auth/alibaba/callback', secret_configured: true },
  alibaba_crossborder: { app_key: '', redirect_uri: '', secret_configured: false },
  onebound: { api_key: '', secret_configured: false },
  crossborderbus_secret_configured: true,
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchManagedSettings).mockResolvedValue(structuredClone(saved))
  vi.mocked(saveManagedSettings).mockResolvedValue(structuredClone(saved))
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
  expect(payload.onebound).toEqual({ api_key: '' })
  expect(payload).not.toHaveProperty('crossborderbus_client_secret')
  expect(wrapper.text()).toContain('共享设置已保存')
})

it('管理员可以选择万邦并保存独立企业凭据', async () => {
  const wrapper = mount(ManagedSettingsPanel, { attachTo: document.body })
  await flushPromises()
  await wrapper.get('select').setValue('onebound')
  const section = wrapper.get('[aria-label="万邦企业配置"]')
  await section.get('input[autocomplete="off"]').setValue('vendor-key')
  await section.get('input[type="password"]').setValue('vendor-secret')
  expect(saveManagedSettings).not.toHaveBeenCalled()
  await wrapper.get('header button[type="submit"]').trigger('click')
  await flushPromises()
  expect(saveManagedSettings).toHaveBeenCalledWith(expect.objectContaining({
    alibaba_api_application: 'onebound', onebound: { api_key: 'vendor-key', api_secret: 'vendor-secret' },
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

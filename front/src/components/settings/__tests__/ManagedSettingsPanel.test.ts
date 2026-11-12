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
  expect(payload).not.toHaveProperty('crossborderbus_client_secret')
  expect(wrapper.text()).toContain('共享设置已保存')
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

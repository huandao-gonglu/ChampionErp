import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ManagedSettingsPanel from '../ManagedSettingsPanel.vue'
import { fetchManagedSettings, saveManagedSettings, type ManagedSettings } from '@/api/managedSettings'

vi.mock('@/api/managedSettings', () => ({ fetchManagedSettings: vi.fn(), saveManagedSettings: vi.fn() }))
enableAutoUnmount(afterEach)
const saved: ManagedSettings = {
  ok: true, revision: 'revision-1',
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
  expect(payload.alibaba).not.toHaveProperty('app_secret')
  expect(payload.alibaba).not.toHaveProperty('secret_configured')
  expect(payload).not.toHaveProperty('crossborderbus_client_secret')
  expect(wrapper.text()).toContain('共享设置已保存')
})

import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import NewAPIAccount from '../NewAPIAccount.vue'
import { useAccountStore } from '@/stores/account'
import { authorizeNewAPI, fetchNewAPIInfo, previewNewAPI } from '@/api/accounts'
import zh from '@/i18n/locales/zh'

vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))
vi.mock('@/api/accounts', () => ({ authorizeNewAPI: vi.fn(), fetchNewAPIInfo: vi.fn(), previewNewAPI: vi.fn() }))
beforeEach(() => {
  setActivePinia(createPinia())
  useAccountStore().user = { id: 'a', email: 'a@example.com', display_name: '甲', role: 'user', status: 'enabled', created_at: 0, updated_at: 0 }
  vi.mocked(fetchNewAPIInfo).mockResolvedValue({ configured: true, url: 'http://localhost:13000/sign-in?force_erp=true', sync_pending: false })
  vi.mocked(previewNewAPI).mockResolvedValue({ code: 'ABCDEF123456', target: 'keys', expires_at: Date.now()/1000+180 })
})
afterEach(() => vi.clearAllMocks())
const render = () => mount(NewAPIAccount, { global: { plugins: [createI18n({ legacy: false, locale: 'zh', messages: { zh } })] } })

it('先核对浏览器授权码，再显式确认当前账号身份', async () => {
  const view = render(); await flushPromises()
  expect(view.find('a').attributes('target')).toBe('_blank')
  expect(authorizeNewAPI).not.toHaveBeenCalled()
  await view.find('input').setValue('abcdef123456')
  await view.find('form').trigger('submit'); await flushPromises()
  expect(previewNewAPI).toHaveBeenCalledWith('ABCDEF123456')
  expect(view.text()).toContain('a@example.com')
  await view.findAll('form')[1]!.trigger('submit'); await flushPromises()
  expect(authorizeNewAPI).toHaveBeenCalledWith({ code: 'ABCDEF123456' })
  expect(view.text()).toContain('已授权')
  view.unmount()
})

it('敏感操作确认时需要 ERP 密码，提交后清除输入', async () => {
  vi.mocked(previewNewAPI).mockResolvedValue({ code: 'ABCDEF123456', target: 'proof', expires_at: Date.now()/1000+180 })
  const view = render(); await flushPromises()
  await view.find('input').setValue('ABCDEF123456')
  await view.find('form').trigger('submit'); await flushPromises()
  await view.find('input[type=password]').setValue('test-password-not-shared-with-browser')
  await view.findAll('form')[1]!.trigger('submit'); await flushPromises()
  expect(authorizeNewAPI).toHaveBeenCalledWith({ code: 'ABCDEF123456', password: 'test-password-not-shared-with-browser' })
  expect(view.find('input[type=password]').exists()).toBe(false)
  view.unmount()
})

it('修改授权码后必须重新核对，旧确认不能提交', async () => {
  const view = render(); await flushPromises()
  await view.find('input').setValue('ABCDEF123456')
  await view.find('form').trigger('submit'); await flushPromises()
  await view.find('input').setValue('FEDCBA654321')
  expect(view.findAll('form')).toHaveLength(1)
  expect(authorizeNewAPI).not.toHaveBeenCalled()
  view.unmount()
})

it('同步尚未完成时展示待重试状态', async () => {
  vi.mocked(fetchNewAPIInfo).mockResolvedValue({ configured: true, url: 'http://localhost:13000/sign-in', sync_pending: true })
  const view = render(); await flushPromises()
  expect(view.text()).toContain('同步待重试')
  view.unmount()
})

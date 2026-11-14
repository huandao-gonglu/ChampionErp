import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import NewAPIAccount from '../NewAPIAccount.vue'
import PersonalAccountView from '@/views/account/PersonalAccountView.vue'
import { useAccountStore } from '@/stores/account'
import { authorizeNewAPI, fetchNewAPIInfo, previewNewAPI, launchNewAPI, logoutAccount } from '@/api/accounts'

const route = vi.hoisted(() => ({ query: {} as Record<string, string> }))
vi.mock('vue-router', () => ({ useRoute: () => route }))
vi.mock('@/api/accounts', () => ({ authorizeNewAPI: vi.fn(), fetchNewAPIInfo: vi.fn(), previewNewAPI: vi.fn(), launchNewAPI: vi.fn(), logoutAccount: vi.fn() }))
beforeEach(() => {
  route.query = {}
  setActivePinia(createPinia())
  useAccountStore().user = { id: 'a', email: 'a@example.com', display_name: '甲', role: 'user', status: 'enabled', created_at: 0, updated_at: 0 }
  vi.mocked(fetchNewAPIInfo).mockResolvedValue({ configured: true, url: 'http://localhost:13000/sign-in?force_erp=true', sync_pending: false })
  vi.mocked(previewNewAPI).mockResolvedValue({ code: 'ABCDEF123456', target: 'keys', expires_at: Date.now()/1000+180 })
})
afterEach(() => vi.clearAllMocks())
const render = () => mount(NewAPIAccount)

it('从 ERP 点击后直接 POST 当前会话的一次性票据，不要求输入或确认关联码', async () => {
  vi.mocked(launchNewAPI).mockResolvedValue({ ok: true, url: 'http://localhost:13000/api/erp/login', ticket: 'short-once-ticket', expires_at: 9999999999 })
  const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function(this: HTMLFormElement) {
    expect(this.method).toBe('post')
    expect(this.action).toBe('http://localhost:13000/api/erp/login')
    expect(this.querySelector('input[name="ticket"]')?.getAttribute('value')).toBe('short-once-ticket')
  })
  const view = render(); await flushPromises()
  expect(view.find('input').exists()).toBe(false)
  await view.find('[data-test="newapi-open"]').trigger('click'); await flushPromises()
  expect(launchNewAPI).toHaveBeenCalledWith('keys')
  expect(submit).toHaveBeenCalledOnce()
  expect(authorizeNewAPI).not.toHaveBeenCalled()
  submit.mockRestore();view.unmount()
})

it('敏感操作确认时需要 ERP 密码，提交后清除输入', async () => {
  route.query = { newapi_code: 'ABCDEF123456' }
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
  route.query = { newapi_code: 'ABCDEF123456' }
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

it('退出未确认时在个人账号页显示错误并允许再次点击退出', async () => {
  vi.mocked(logoutAccount).mockRejectedValue(new Error('未收到 New API 的退出确认，ERP 仍保持登录，请重新退出。'))
  const view = mount(PersonalAccountView, { global: { stubs: { RouterLink: true, NewAPIAccount: true } } })
  const button = view.findAll('button').find(item => item.text() === '退出登录')!
  await button.trigger('click')
  await flushPromises()
  expect(view.get('[role="alert"]').text()).toContain('ERP 仍保持登录')
  expect(useAccountStore().user?.id).toBe('a')
  expect(button.element.disabled).toBe(false)
  await button.trigger('click')
  await flushPromises()
  expect(logoutAccount).toHaveBeenCalledTimes(2)
  view.unmount()
})

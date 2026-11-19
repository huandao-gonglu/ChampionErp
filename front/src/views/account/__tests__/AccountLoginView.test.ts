import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AccountLoginView from '../AccountLoginView.vue'

const login = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({ useRoute: () => ({ name: 'AccountLogin', query: {} }) }))
vi.mock('@/stores/account', () => ({ useAccountStore: () => ({ initialized: true, login }) }))

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

it.each([true, false])('登录页恢复自动登录选择 %s，并在提交时保留该选择', async auto => {
  vi.stubGlobal('championDesktop', { savedLogin: async () => ({ available: true, email: 'dev@example.test', password: '', auto }) })
  const wrapper = mount(AccountLoginView, { global: { stubs: { RouterLink: true, RegistrationCode: true } } })
  await flushPromises()
  expect((wrapper.get('input[name="email"]').element as HTMLInputElement).value).toBe('dev@example.test')
  const checkboxes = wrapper.findAll('input[type="checkbox"]')
  expect((checkboxes[0].element as HTMLInputElement).checked).toBe(false)
  expect((checkboxes[1].element as HTMLInputElement).checked).toBe(auto)
  await wrapper.get('input[name="password"]').setValue('用于登录的测试密码')
  await wrapper.get('form').trigger('submit')
  expect(login).toHaveBeenCalledWith('dev@example.test', '用于登录的测试密码', undefined, { remember: false, auto })
  wrapper.unmount()
})

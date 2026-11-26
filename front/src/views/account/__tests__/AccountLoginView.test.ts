import { afterEach, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import AccountLoginView from '../AccountLoginView.vue'

const login = vi.hoisted(() => vi.fn())
const replace = vi.hoisted(() => vi.fn())
const routeState = vi.hoisted(() => ({ name: 'AccountLogin', query: {} as Record<string, string> }))
const userRef = ref<null | { id: string }>(null)
vi.mock('vue-router', () => ({ useRoute: () => routeState, useRouter: () => ({ replace }) }))
vi.mock('@/stores/account', () => ({
  useAccountStore: () => ({
    initialized: true,
    get user() { return userRef.value },
    login,
  }),
}))

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  routeState.name = 'AccountLogin'
  routeState.query = {}
  userRef.value = null
})

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

it('停留在登录页期间会话恢复后自动跳转回目标页面', async () => {
  routeState.query = { redirect: '/?tab=drafts' }
  const wrapper = mount(AccountLoginView, { global: { stubs: { RouterLink: true, RegistrationCode: true } } })
  await flushPromises()
  expect(replace).not.toHaveBeenCalled()
  userRef.value = { id: 'u1' }
  await flushPromises()
  expect(replace).toHaveBeenCalledWith('/?tab=drafts')
  wrapper.unmount()
})

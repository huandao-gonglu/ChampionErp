import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import DesktopHandoffNotice from '../DesktopHandoffNotice.vue'
import { useAccountStore } from '@/stores/account'
import { resolveDesktopHandoff, confirmDesktopHandoff } from '@/api/desktopHandoff'
import { desktopLaunch, receivedDesktopHandoff, resetDesktopHandoff, acknowledgeDesktopHandoff } from '@/utils/desktopHandoff'

vi.mock('@/api/desktopHandoff', () => ({ resolveDesktopHandoff: vi.fn(), confirmDesktopHandoff: vi.fn(), fetchDesktopInstallers: vi.fn(), desktopBackendOrigin: () => 'https://erp.example.test' }))

const user = { id: 'owner', email: 'owner@example.test', display_name: '测试账号', role: 'user' as const, status: 'enabled' as const, created_at: 1, updated_at: 1 }
const finish = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('championDesktop', { localOperations: true, pendingHandoff: async () => 'ticket', finishHandoff: finish,
    onHandoff: () => () => {} })
  vi.mocked(resolveDesktopHandoff).mockResolvedValue({ ok: true, target: '/?tab=collect', state: {}, opened: false })
})
afterEach(() => { resetDesktopHandoff(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })

async function setup(path: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/login', component: { template: '<div />' }, meta: { public: true } },
  ] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(DesktopHandoffNotice, { global: { plugins: [pinia, router], stubs: { WorkspaceDialog: true } } })
  await flushPromises()
  return { wrapper, router, account: useAccountStore() }
}

it.each([true, false])('开发模式 %s 使用对应的等待提示与安装入口', async development => {
  vi.stubEnv('DEV', development)
  vi.stubGlobal('championDesktop', undefined)
  desktopLaunch.value = { requestId: 'ticket', url: '', status: 'waiting' }
  const { wrapper } = await setup('/')
  expect(wrapper.text().includes('下载安装')).toBe(!development)
  expect(wrapper.text().includes('开发桌面登录同一账号')).toBe(development)
  wrapper.unmount()
})

it('登录页保存启动请求，登录并进入业务页后再恢复，防止登录重载丢失目标页面', async () => {
  const { wrapper, router, account } = await setup('/login')
  expect(resolveDesktopHandoff).not.toHaveBeenCalled()
  account.user = user
  await flushPromises()
  expect(resolveDesktopHandoff).not.toHaveBeenCalled()
  await router.push('/')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/?tab=collect')
  expect(confirmDesktopHandoff).toHaveBeenCalledWith('ticket')
  expect(finish).toHaveBeenCalledWith('ticket')
  wrapper.unmount()
})

it('工作台完成内容恢复前不回传打开确认，登录后的失败请求可以再次领取', async () => {
  vi.mocked(resolveDesktopHandoff).mockRejectedValueOnce(new Error('暂时连接失败'))
  vi.mocked(resolveDesktopHandoff).mockResolvedValueOnce({ ok: true, target: '/?tab=collect', state: { workflow: { collect: { mode: 'browser' } } }, opened: false })
  const { wrapper, account } = await setup('/')
  account.user = user
  await flushPromises()
  expect(wrapper.text()).toContain('暂时连接失败')
  await wrapper.get('[role="alert"] button.btn-primary').trigger('click')
  await flushPromises()
  expect(vi.mocked(resolveDesktopHandoff).mock.calls).toEqual([['ticket'], ['ticket']])
  expect(receivedDesktopHandoff.value?.state.workflow).toBeDefined()
  expect(confirmDesktopHandoff).not.toHaveBeenCalled()
  await acknowledgeDesktopHandoff('ticket', 'workflow')
  expect(confirmDesktopHandoff).toHaveBeenCalledWith('ticket')
  wrapper.unmount()
})

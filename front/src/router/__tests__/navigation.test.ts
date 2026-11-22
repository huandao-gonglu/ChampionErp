import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAccountStore } from '@/stores/account'
import DraftWorkspacePanel from '@/components/domain/DraftWorkspacePanel.vue'
import { workflowNavItems } from '@/constants/navigation'
import { router } from '../index'

describe('工作流顶级导航', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const account = useAccountStore()
    account.ready = true
    account.user = { id: 'test', email: 'test@example.com', display_name: '测试用户', role: 'user', status: 'enabled', created_at: 0, updated_at: 0 }
  })
  it('核价保留在草稿工作台中，旧入口会跳转到对应工作流标签', async () => {
    Object.defineProperty(window, 'scrollTo', { configurable: true, value: () => undefined })
    const navKeys = workflowNavItems.map((item) => item.key)
    const routePaths = router.getRoutes().map((route) => route.path)

    expect(navKeys).not.toContain('pricing')
    expect(navKeys).not.toContain('category')
    expect(navKeys).not.toContain('logs')
    expect(routePaths).not.toContain('/logs')
    expect(routePaths).toContain('/pricing')
    expect(routePaths).toContain('/publish')
    expect(navKeys).toContain('orders')
    expect(navKeys).toContain('drafts')
    expect(navKeys).toContain('publish')
    expect(router.resolve({ name: 'WorkflowHome' }).meta.keepAlive).toBe(true)

    await router.push('/settings')
    expect(router.currentRoute.value.query.tab).toBe('systemSettings')
    await router.push('/settings?auth_section=crossborderbus')
    expect(router.currentRoute.value.query.tab).toBe('auth')
    await router.push('/pricing')
    expect(router.currentRoute.value.path).toBe('/')
    expect(router.currentRoute.value.query.tab).toBe('drafts')
    await router.push('/publish')
    expect(router.currentRoute.value.path).toBe('/')
    expect(router.currentRoute.value.query.tab).toBe('publish')

    const workspace = mount(DraftWorkspacePanel, {
      props: {
        activeTab: 'text',
        draftTitle: '测试草稿',
        draftId: 'draft-1',
      },
    })
    expect(workspace.text()).toContain('核价')
    expect(workspace.text()).toContain('发布预检')
  })
  it.each([
    '/auth?auth_section=crossborderbus&bus_section=rules',
    '/settings?settings_section=logistics',
    '/settings?auth_section=logistics',
    '/?tab=systemSettings&settings_section=logistics',
    '/?tab=auth&auth_section=logistics',
  ])('物流入口 %s 打开跨境巴士默认履约', async entry => {
    await router.push(entry)
    expect(router.currentRoute.value.path).toBe('/')
    expect(router.currentRoute.value.query).toEqual({ tab: 'auth', auth_section: 'crossborderbus', bus_section: 'rules' })
  })
})

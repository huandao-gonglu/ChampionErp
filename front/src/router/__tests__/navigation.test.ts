import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DraftWorkspacePanel from '@/components/domain/DraftWorkspacePanel.vue'
import { workflowNavItems } from '@/constants/navigation'
import { router } from '../index'

describe('工作流顶级导航', () => {
  it('核价保留在草稿工作台中，旧入口会跳转到对应工作流标签', async () => {
    Object.defineProperty(window, 'scrollTo', { configurable: true, value: () => undefined })
    const navKeys = workflowNavItems.map((item) => item.key)
    const routePaths = router.getRoutes().map((route) => route.path)

    expect(navKeys).not.toContain('pricing')
    expect(navKeys).not.toContain('category')
    expect(routePaths).toContain('/pricing')
    expect(routePaths).toContain('/publish')
    expect(navKeys).toContain('drafts')
    expect(navKeys).toContain('publish')

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
})

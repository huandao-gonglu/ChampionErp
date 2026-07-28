import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DraftWorkspacePanel from '@/components/domain/DraftWorkspacePanel.vue'
import { workflowNavItems } from '@/constants/navigation'
import { router } from '../index'

describe('工作流顶级导航', () => {
  it('核价和发布预检只保留在草稿工作台中', () => {
    const navKeys = workflowNavItems.map((item) => item.key)
    const routePaths = router.getRoutes().map((route) => route.path)

    expect(navKeys).not.toContain('pricing')
    expect(navKeys).not.toContain('category')
    expect(routePaths).not.toContain('/pricing')
    expect(routePaths).not.toContain('/publish')
    expect(navKeys).toContain('drafts')
    expect(navKeys).toContain('publish')

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

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AiWorkFloatingButton from '../AiWorkFloatingButton.vue'

const mocks = vi.hoisted(() => ({
  route: { name: 'WorkflowHome' },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

describe('AiWorkFloatingButton', () => {
  beforeEach(() => {
    mocks.route.name = 'WorkflowHome'
  })

  it('仅提供新标签页导航入口', () => {
    const wrapper = mount(AiWorkFloatingButton)
    const link = wrapper.get('a')

    expect(link.attributes('href')).toBe('/aiWork')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
    expect(link.attributes('aria-label')).toBe('打开 AI 对话检查器')
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
  })

  it('在 AI Work 页面隐藏入口', () => {
    mocks.route.name = 'AiWork'

    const wrapper = mount(AiWorkFloatingButton)

    expect(wrapper.find('[data-testid="ai-work-floating"]').exists()).toBe(false)
  })
})

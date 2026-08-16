import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiChatStore } from '@/stores'
import AiWorkFloatingButton from '../AiWorkFloatingButton.vue'

const mocks = vi.hoisted(() => ({
  route: { name: 'WorkflowHome' },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

function mountFloatingButton() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(AiWorkFloatingButton, { global: { plugins: [pinia] } })
  return { wrapper, store: useAiChatStore() }
}

describe('AiWorkFloatingButton', () => {
  beforeEach(() => {
    mocks.route.name = 'WorkflowHome'
  })

  it('默认显示原版气泡图标，并使用新标签页链接', () => {
    const { wrapper } = mountFloatingButton()

    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')
    expect(link.element.tagName).toBe('A')
    expect(link.attributes('href')).toBe('/aiWork')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
    expect(link.find('path').attributes('d')).toBe(
      'M7.5 17.5 4 20v-4.3A7.5 7.5 0 0 1 2.5 11C2.5 6.9 6.5 3.5 11.5 3.5S20.5 6.9 20.5 11s-4 7.5-9 7.5c-1.45 0-2.8-.28-4-.78Z',
    )
  })

  it('鼠标悬停时自动显示活动对话，移出后自动收起', async () => {
    const { wrapper, store } = mountFloatingButton()
    const floating = wrapper.get('[data-testid="ai-work-floating"]')

    await floating.trigger('mouseenter')

    expect(store.floatingOpen).toBe(true)
    const panel = wrapper.get('[role="region"]')
    expect(panel.attributes('aria-label')).toBe('全局 AI 浮动对话')
    expect(panel.find('[data-testid="ai-chat-panel"]').exists()).toBe(true)
    expect(panel.find('[data-testid="ai-chat-input"]').exists()).toBe(true)

    await floating.trigger('mouseleave')
    expect(store.floatingOpen).toBe(false)
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
  })

  it('输入与发送都经过共享 store，不产生本地第二份状态', async () => {
    const { wrapper, store } = mountFloatingButton()
    const sendSpy = vi.spyOn(store, 'sendMessage').mockImplementation(() => {})

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await wrapper.get('[data-testid="ai-chat-input"]').setValue('帮我看看草稿')
    expect(store.input).toBe('帮我看看草稿')

    await wrapper.get('[data-testid="ai-chat-composer"]').trigger('submit')
    expect(sendSpy).toHaveBeenCalledTimes(1)
  })

  it('关闭按钮收起面板', async () => {
    const { wrapper, store } = mountFloatingButton()

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    expect(wrapper.find('[role="region"]').exists()).toBe(true)

    await wrapper.get('[data-testid="ai-work-floating-close"]').trigger('click')
    expect(store.floatingOpen).toBe(false)
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
  })

  it('活动气泡携带 conversation ID 在新标签页打开 AiWork', async () => {
    const { wrapper, store } = mountFloatingButton()
    const conversationId = store.startConversation()

    await wrapper.vm.$nextTick()
    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')

    expect(link.attributes('href')).toBe(`/aiWork?conversation_id=${conversationId}`)
    expect(link.attributes('target')).toBe('_blank')
  })

  it('悬浮聊天内容中不显示 AiWork 入口按钮', async () => {
    const { wrapper } = mountFloatingButton()

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')

    const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
    expect(panel.find('[data-testid="ai-work-floating-open-full"]').exists()).toBe(false)
    expect(panel.text()).not.toContain('打开完整对话')
  })

  it('在 AI Work 页面隐藏入口', () => {
    mocks.route.name = 'AiWork'

    const { wrapper } = mountFloatingButton()

    expect(wrapper.find('[data-testid="ai-work-floating"]').exists()).toBe(false)
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AiWorkFloatingButton from '../AiWorkFloatingButton.vue'

const mocks = vi.hoisted(() => ({
  route: { name: 'WorkflowHome' },
  fetchConversations: vi.fn(),
  fetchConversation: vi.fn(),
  waitForEvents: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

vi.mock('@/api/aiWork', () => ({
  fetchAiWorkConversations: mocks.fetchConversations,
  fetchAiWorkConversation: mocks.fetchConversation,
  waitForAiWorkEvents: mocks.waitForEvents,
}))

const conversation = {
  conversation_id: 'conversation-1',
  use_case_id: 'copy.generate',
  capability: 'chat_json',
  provider_id: 'openai_compatible',
  provider: 'OpenAI-Compatible',
  model_id: 'model-1',
  model: 'deepseek-chat',
  stream: true,
  status: 'running',
  created_at: '2026-07-26T10:00:00+08:00',
  updated_at: '2026-07-26T10:00:01+08:00',
  last_seq: 3,
  event_count: 3,
  error: '',
} as const

describe('AiWorkFloatingButton', () => {
  beforeEach(() => {
    mocks.route.name = 'WorkflowHome'
    mocks.fetchConversations.mockReset()
    mocks.fetchConversation.mockReset()
    mocks.waitForEvents.mockReset()
  })

  it('使用新标签页打开 AI Work', () => {
    const wrapper = mount(AiWorkFloatingButton)
    const link = wrapper.get('a')

    expect(link.attributes('href')).toBe('/aiWork')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
    expect(link.attributes('aria-label')).toBe('打开 AI 对话记录')
  })

  it('在 AI Work 页面隐藏入口', () => {
    mocks.route.name = 'AiWork'

    const wrapper = mount(AiWorkFloatingButton)

    expect(wrapper.find('[data-testid="ai-work-floating"]').exists()).toBe(false)
  })

  it('悬停时实时展示最新一条对话，移出后隐藏', async () => {
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [conversation] })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: conversation.conversation_id,
      events: [
        { seq: 1, type: 'RUN_STARTED' },
        { seq: 2, type: 'TEXT_MESSAGE_START' },
        { seq: 3, type: 'TEXT_MESSAGE_CONTENT', delta: '正在' },
      ],
    })
    mocks.waitForEvents.mockResolvedValue([
      { seq: 4, type: 'TEXT_MESSAGE_CONTENT', delta: '输出' },
      { seq: 5, type: 'TEXT_MESSAGE_END' },
      { seq: 6, type: 'RUN_FINISHED' },
    ])
    const wrapper = mount(AiWorkFloatingButton)
    const floating = wrapper.get('[data-testid="ai-work-floating"]')

    await floating.trigger('mouseenter')
    await flushPromises()

    expect(mocks.fetchConversations).toHaveBeenCalledWith(1)
    expect(wrapper.get('[data-testid="ai-work-latest"]').text()).toContain('copy.generate')
    expect(wrapper.get('[data-testid="ai-work-latest"]').text()).toContain('正在输出')
    expect(wrapper.get('[data-testid="ai-work-latest"]').text()).toContain('已完成')

    await floating.trigger('mouseleave')

    expect(wrapper.find('[data-testid="ai-work-latest"]').exists()).toBe(false)
  })
})

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
  provider: 'OpenAI',
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

  it('流式输出增长时自动滚动到末尾', async () => {
    let resolveEvents: (events: Array<Record<string, unknown>>) => void = () => {}
    const pendingEvents = new Promise<Array<Record<string, unknown>>>((resolve) => {
      resolveEvents = resolve
    })
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [conversation] })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: conversation.conversation_id,
      events: [
        { seq: 1, type: 'RUN_STARTED' },
        { seq: 2, type: 'TEXT_MESSAGE_CONTENT', delta: '第一段输出' },
      ],
    })
    mocks.waitForEvents.mockReturnValue(pendingEvents)
    const wrapper = mount(AiWorkFloatingButton)

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await flushPromises()

    const output = wrapper.get<HTMLElement>('[data-testid="ai-work-output"]').element
    Object.defineProperty(output, 'scrollHeight', { configurable: true, value: 420 })
    output.scrollTop = 0
    resolveEvents([
      { seq: 3, type: 'TEXT_MESSAGE_CONTENT', delta: '，第二段输出' },
      { seq: 4, type: 'RUN_FINISHED' },
    ])
    await flushPromises()

    expect(output.textContent).toBe('第一段输出，第二段输出')
    expect(output.scrollTop).toBe(420)
  })

  it('deferred run 显示等待审批并停止长轮询', async () => {
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [{ ...conversation, status: 'waiting_approval' }],
    })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: conversation.conversation_id,
      events: [
        { seq: 1, type: 'RUN_STARTED' },
        { seq: 2, type: 'RUN_DEFERRED', state_id: 'agent-state-1' },
      ],
    })

    const wrapper = mount(AiWorkFloatingButton)
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-latest"]').text()).toContain('等待审批')
    expect(wrapper.get('[data-testid="ai-work-latest"]').text()).toContain('等待人工审批')
    expect(mocks.waitForEvents).not.toHaveBeenCalled()
  })

  it('能力探测请求已发出时显示等待 Provider', async () => {
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [{ ...conversation, status: 'completed' }],
    })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: conversation.conversation_id,
      events: [
        { seq: 1, type: 'RUN_STARTED' },
        { seq: 2, type: 'CUSTOM', name: 'capability_probe.request', value: {} },
      ],
    })

    const wrapper = mount(AiWorkFloatingButton)
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-output"]').text()).toContain(
      '请求已发送，正在等待 Provider 返回',
    )
  })

  it('Agent 初始请求已记录时显示请求已发送', async () => {
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [{ ...conversation, capability: 'agent' }],
    })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: conversation.conversation_id,
      events: [
        { seq: 1, type: 'RUN_STARTED' },
        { seq: 2, type: 'CUSTOM', name: 'agent.request', value: { mode: 'initial' } },
      ],
    })
    mocks.waitForEvents.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(AiWorkFloatingButton)
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-output"]').text()).toContain(
      '请求已发送，正在等待 Provider 返回',
    )
    wrapper.unmount()
  })
})

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
  parent_conversation_id: null,
  use_case_id: 'copy.generate',
  capability: 'chat_json',
  provider_id: 'openai_compatible',
  provider: 'OpenAI',
  model_id: 'model-1',
  model: 'deepseek-chat',
  stream: true,
  required_capabilities: ['chat'],
  timeout_seconds: 60,
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

  it('实时展示 Provider 返回的推理字符', async () => {
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [conversation] })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: conversation.conversation_id,
      events: [
        { seq: 1, type: 'RUN_STARTED' },
        { seq: 2, type: 'REASONING_MESSAGE_START' },
        { seq: 3, type: 'REASONING_MESSAGE_CONTENT', delta: '正在分析商品' },
      ],
    })
    mocks.waitForEvents.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(AiWorkFloatingButton)
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-latest"]').text()).toContain('正在推理')
    expect(wrapper.get('[data-testid="ai-work-output"]').text()).toContain('正在分析商品')
    expect(wrapper.get('[data-testid="ai-work-output"]').text()).not.toContain('等待 Provider')
    wrapper.unmount()
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

  it('失败时同时展示真实错误码和消息', async () => {
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [{ ...conversation, status: 'failed' }],
    })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: conversation.conversation_id,
      events: [
        {
          seq: 1,
          type: 'RUN_ERROR',
          code: 'TOOL_INPUT_SCHEMA_INVALID',
          message: '$ 缺少必填字段：requests',
        },
      ],
    })

    const wrapper = mount(AiWorkFloatingButton)
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-output"]').text()).toContain(
      'TOOL_INPUT_SCHEMA_INVALID：$ 缺少必填字段：requests',
    )
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

  it('稳定全局对话展示最近回复和任务状态，并继续按 lifecycle 轮询', async () => {
    const globalConversation = {
      ...conversation,
      use_case_id: 'global.agent.chat',
      capability: 'agent',
      stream: false,
      latest_task_status: 'needs_input' as const,
    }
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [globalConversation],
    })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: globalConversation.conversation_id,
      conversation: globalConversation,
      events: [
        {
          seq: 1,
          type: 'CUSTOM',
          name: 'global.task_state',
          value: {
            task_id: 'task-1',
            status: 'needs_input',
            summary: '还缺少目标站点。',
          },
        },
        {
          seq: 2,
          type: 'CUSTOM',
          name: 'global.assistant_message',
          value: {
            task_id: 'task-1',
            message: '请告诉我需要准备哪个站点。',
          },
        },
      ],
    })
    mocks.waitForEvents.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(AiWorkFloatingButton)
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await flushPromises()

    const panel = wrapper.get('[data-testid="ai-work-latest"]')
    expect(panel.text()).toContain('全局 Agent 对话')
    expect(panel.text()).toContain('等待补充资料')
    expect(panel.text()).toContain('请告诉我需要准备哪个站点。')
    expect(panel.text()).not.toContain('正在准备 Provider 请求')
    expect(mocks.waitForEvents).toHaveBeenCalledWith(
      globalConversation.conversation_id,
      2,
      5_000,
      expect.anything(),
    )
    wrapper.unmount()
  })

  it('键盘焦点打开预览，焦点与鼠标共同决定关闭，并提供展开语义', async () => {
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [] })
    const wrapper = mount(AiWorkFloatingButton)
    const floating = wrapper.get('[data-testid="ai-work-floating"]')
    const link = wrapper.get('a')

    expect(link.attributes('aria-controls')).toBe('ai-work-latest-panel')
    expect(link.attributes('aria-expanded')).toBe('false')

    await floating.trigger('mouseenter')
    await link.trigger('focusin')
    await flushPromises()

    expect(wrapper.get('#ai-work-latest-panel').attributes('role')).toBe('region')
    expect(link.attributes('aria-expanded')).toBe('true')

    await floating.trigger('mouseleave')
    expect(wrapper.find('#ai-work-latest-panel').exists()).toBe(true)

    const outside = document.createElement('button')
    document.body.append(outside)
    await link.trigger('focusout', { relatedTarget: outside })
    expect(wrapper.find('#ai-work-latest-panel').exists()).toBe(false)
    expect(link.attributes('aria-expanded')).toBe('false')
    outside.remove()
  })

  it('Escape 关闭键盘打开的预览', async () => {
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [] })
    const wrapper = mount(AiWorkFloatingButton)
    const link = wrapper.get('a')

    await link.trigger('focusin')
    await flushPromises()
    expect(wrapper.find('#ai-work-latest-panel').exists()).toBe(true)

    await link.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('#ai-work-latest-panel').exists()).toBe(false)
    expect(link.attributes('aria-expanded')).toBe('false')
  })

  it('关闭与卸载会真正中止当前长轮询', async () => {
    const signals: AbortSignal[] = []
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [conversation] })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: conversation.conversation_id,
      conversation,
      events: [],
    })
    mocks.waitForEvents.mockImplementation((
      _conversationId: string,
      _afterSeq: number,
      _waitMs: number,
      signal: AbortSignal,
    ) => {
      signals.push(signal)
      return new Promise(() => {})
    })
    const wrapper = mount(AiWorkFloatingButton)
    const floating = wrapper.get('[data-testid="ai-work-floating"]')

    await floating.trigger('mouseenter')
    await flushPromises()
    expect(signals).toHaveLength(1)
    expect(signals[0].aborted).toBe(false)

    await floating.trigger('mouseleave')
    expect(signals[0].aborted).toBe(true)

    await floating.trigger('mouseenter')
    await flushPromises()
    expect(signals).toHaveLength(2)
    expect(signals[1].aborted).toBe(false)

    wrapper.unmount()
    expect(signals[1].aborted).toBe(true)
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AiWorkView from '../AiWorkView.vue'

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, string> },
  fetchConversations: vi.fn(),
  fetchConversation: vi.fn(),
  fetchChildren: vi.fn(),
  waitForEvents: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

vi.mock('@/api/aiWork', () => ({
  aiWorkRawUrl: (conversationId: string) => `/api/ai-work/${conversationId}/raw`,
  fetchAiWorkConversations: mocks.fetchConversations,
  fetchAiWorkConversation: mocks.fetchConversation,
  fetchAiWorkConversationChildren: mocks.fetchChildren,
  waitForAiWorkEvents: mocks.waitForEvents,
}))

function conversation(
  status: 'running' | 'completed' | 'failed',
  overrides: Record<string, unknown> = {},
) {
  return {
    conversation_id: 'agent-conversation-1',
    parent_conversation_id: null,
    use_case_id: 'category.product_match',
    capability: 'agent',
    provider_id: 'alibaba',
    provider: 'Alibaba',
    model_id: 'model-1',
    model: 'qwen3.7-plus',
    stream: false,
    required_capabilities: ['chat', 'json', 'tool_calling'],
    timeout_seconds: 60,
    status,
    created_at: '2026-08-02T22:05:30+08:00',
    updated_at: '2026-08-02T22:06:09+08:00',
    last_seq: 4,
    event_count: 4,
    error: status === 'failed' ? '模型输出未满足当前业务约束。' : '',
    ...overrides,
  }
}

async function openTab(wrapper: ReturnType<typeof mount>, label: string) {
  const button = wrapper.findAll('button').find((item) => item.text() === label)
  if (!button) throw new Error(`找不到页签：${label}`)
  await button.trigger('click')
}

describe('AiWorkView Agent 对话投影', () => {
  beforeEach(() => {
    window.localStorage.removeItem('ai-work.show-internal-conversations')
    mocks.route.query = {}
    mocks.fetchConversations.mockReset()
    mocks.fetchConversation.mockReset()
    mocks.fetchChildren.mockReset()
    mocks.waitForEvents.mockReset()
  })

  it('旧 Agent 记录使用已有运行摘要和终态，不再显示空白', async () => {
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [conversation('failed')],
    })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: 'agent-conversation-1',
      events: [
        {
          seq: 1,
          type: 'RUN_STARTED',
          input: { platform: 'ozon', site: 'global' },
          rawEvent: { capability: 'agent', use_case_id: 'category.product_match' },
        },
        {
          seq: 2,
          type: 'CUSTOM',
          name: 'TOOL_CALL_STARTED',
          value: { tool_name: 'search_categories', round: 1 },
        },
        {
          seq: 3,
          type: 'RUN_ERROR',
          code: 'MODEL_SELECTED_UNKNOWN_CATEGORY',
          message: '模型输出未满足当前业务约束。',
        },
      ],
    })

    const wrapper = mount(AiWorkView)
    await flushPromises()

    expect(wrapper.text()).toContain('TOOL_CALL_STARTED')
    expect(wrapper.text()).toContain('MODEL_SELECTED_UNKNOWN_CATEGORY')
    expect(wrapper.text()).toContain(
      'MODEL_SELECTED_UNKNOWN_CATEGORY：模型输出未满足当前业务约束。',
    )

    await openTab(wrapper, '原始请求')
    expect(wrapper.text()).toContain('历史 Agent 输入摘要')
    expect(wrapper.text()).toContain('该记录创建时尚未保存 Pydantic Agent 的逐轮请求')
    expect(wrapper.text()).toContain('ozon')

    await openTab(wrapper, '处理结果')
    expect(wrapper.text()).toContain('MODEL_SELECTED_UNKNOWN_CATEGORY')
    expect(wrapper.text()).toContain('模型输出未满足当前业务约束')
    wrapper.unmount()
  })

  it('新 Agent 记录展示逐轮模型请求、响应和业务结果', async () => {
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [conversation('completed')],
    })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: 'agent-conversation-1',
      events: [
        { seq: 1, type: 'RUN_STARTED', rawEvent: { capability: 'agent' } },
        {
          seq: 2,
          type: 'CUSTOM',
          name: 'agent.request',
          value: {
            mode: 'initial',
            messages: [
              { role: 'system', content: '先搜索类目' },
              { role: 'user', content: '匹配 Ventilador' },
            ],
          },
        },
        {
          seq: 3,
          type: 'CUSTOM',
          name: 'agent.transcript',
          value: {
            schema_version: 'agent.transcript.v1',
            messages: [
              {
                kind: 'request',
                state: 'complete',
                instructions: '先搜索类目',
                parts: [{ part_kind: 'user-prompt', content: '匹配 Ventilador' }],
              },
              {
                kind: 'response',
                state: 'complete',
                parts: [{ part_kind: 'tool-call', tool_name: 'search_categories', args: { keyword: 'ventilador' } }],
              },
            ],
          },
        },
        {
          seq: 4,
          type: 'CUSTOM',
          name: 'business.result',
          value: { status: 'completed', selected_category_id: '91443' },
        },
        { seq: 5, type: 'RUN_FINISHED', result: { status: 'completed' } },
      ],
    })

    const wrapper = mount(AiWorkView)
    await flushPromises()

    expect(wrapper.text()).toContain('Pydantic 模型请求 #1')
    expect(wrapper.text()).toContain('模型响应 #2')
    expect(wrapper.text()).toContain('search_categories')
    expect(wrapper.text()).toContain('91443')

    await openTab(wrapper, '原始请求')
    expect(wrapper.text()).toContain('匹配 Ventilador')

    await openTab(wrapper, '处理结果')
    expect(wrapper.text()).toContain('selected_category_id')
    expect(wrapper.text()).toContain('91443')
    wrapper.unmount()
  })

  it('独立展示实时推理字符，不再把推理阶段显示为等待 Provider', async () => {
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [conversation('running')],
    })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: 'agent-conversation-1',
      events: [
        { seq: 1, type: 'RUN_STARTED' },
        { seq: 2, type: 'REASONING_MESSAGE_START' },
        { seq: 3, type: 'REASONING_MESSAGE_CONTENT', delta: '先检查类目属性' },
        { seq: 4, type: 'REASONING_MESSAGE_CONTENT', delta: '，再生成 JSON' },
      ],
    })
    mocks.waitForEvents.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(AiWorkView)
    await flushPromises()

    const reasoning = wrapper.get('[data-testid="ai-work-reasoning"]')
    expect(reasoning.text()).toContain('思考过程')
    expect(reasoning.text()).toContain('正在推理')
    expect(reasoning.text()).toContain('先检查类目属性，再生成 JSON')
    expect(reasoning.text()).toContain('16 字符')
    expect(wrapper.text()).not.toContain('等待 Provider 返回')
    wrapper.unmount()
  })

  it('新建对话只打开空白全局 Agent 面板', async () => {
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [] })

    const wrapper = mount(AiWorkView)
    await flushPromises()
    await wrapper.get('[data-testid="new-global-conversation"]').trigger('click')

    expect(wrapper.text()).toContain('全局 Agent 新对话')
    expect(wrapper.get('[data-testid="global-agent-chat"]').text()).toContain(
      '告诉全局 Agent 你想完成什么',
    )
    expect(mocks.fetchConversation).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('默认只展示根会话，并把子 Agent 收纳到主对话的执行详情', async () => {
    const root = conversation('running', {
      conversation_id: 'global-conversation-1',
      use_case_id: 'global.agent.chat',
      latest_task_status: 'completed',
    })
    const child = conversation('completed', {
      conversation_id: 'planning-conversation-1',
      parent_conversation_id: 'global-conversation-1',
      use_case_id: 'global.task.plan',
      created_at: '2026-08-13T10:00:00+08:00',
      updated_at: '2026-08-13T10:00:07+08:00',
    })
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      // 即使旧服务意外混入 child，前端默认列表也要守住 root-only 边界。
      conversations: [child, root],
    })
    mocks.fetchChildren.mockResolvedValue({ ok: true, conversations: [child] })
    mocks.fetchConversation.mockImplementation(async (conversationId: string) => {
      if (conversationId === child.conversation_id) {
        return {
          ok: true,
          conversation_id: child.conversation_id,
          conversation: child,
          events: [
            { seq: 1, type: 'RUN_STARTED', rawEvent: { capability: 'agent' } },
            { seq: 2, type: 'RUN_FINISHED', result: { status: 'completed' } },
          ],
        }
      }
      return {
        ok: true,
        conversation_id: root.conversation_id,
        conversation: root,
        events: [{
          seq: 1,
          type: 'CUSTOM',
          name: 'global.agent_execution_link',
          value: {
            task_id: 'task-1',
            conversation_id: child.conversation_id,
          },
        }],
      }
    })
    mocks.waitForEvents.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(AiWorkView)
    await flushPromises()

    expect(wrapper.find('[data-testid="root-conversation-global-conversation-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="internal-conversation-planning-conversation-1"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('任务已完成')

    const details = wrapper.get('[data-testid="agent-execution-details"]')
    expect(details.text()).toContain('1 个子 Agent，全部完成')
    expect(details.text()).toContain('任务规划')
    expect(details.text()).toContain('耗时 7 秒')

    await wrapper.get('[data-testid="open-agent-execution-planning-conversation-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('内部执行 · 只读')
    expect(wrapper.text()).toContain('任务规划')
    expect(wrapper.find('[data-testid="return-to-parent-conversation"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="internal-conversation-planning-conversation-1"]').exists()).toBe(false)

    await wrapper.get('[data-testid="return-to-parent-conversation"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="global-agent-chat"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('内部会话开关显式请求完整列表，并按主从层级渲染', async () => {
    const root = conversation('running', {
      conversation_id: 'global-conversation-1',
      use_case_id: 'global.agent.chat',
    })
    const child = conversation('completed', {
      conversation_id: 'planning-conversation-1',
      parent_conversation_id: 'global-conversation-1',
      use_case_id: 'global.task.plan',
    })
    mocks.fetchConversations
      .mockResolvedValueOnce({ ok: true, conversations: [root] })
      .mockResolvedValue({ ok: true, conversations: [child, root] })
    mocks.fetchChildren.mockResolvedValue({ ok: true, conversations: [child] })
    mocks.fetchConversation.mockResolvedValue({
      ok: true,
      conversation_id: root.conversation_id,
      conversation: root,
      events: [],
    })
    mocks.waitForEvents.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(AiWorkView)
    await flushPromises()
    await wrapper.get('[data-testid="show-internal-conversations"]').setValue(true)
    await flushPromises()

    expect(mocks.fetchConversations).toHaveBeenLastCalledWith(50, true)
    const rows = wrapper.findAll('[data-testid^="root-conversation-"], [data-testid^="internal-conversation-"]')
    expect(rows.map((row) => row.attributes('data-testid'))).toEqual([
      'root-conversation-global-conversation-1',
      'internal-conversation-planning-conversation-1',
    ])
    expect(rows[1].text()).toContain('内部执行会话')
    wrapper.unmount()
  })

  it('route 直达子会话时读取详情但不把它注入默认侧栏', async () => {
    const root = conversation('running', {
      conversation_id: 'global-conversation-1',
      use_case_id: 'global.agent.chat',
    })
    const child = conversation('completed', {
      conversation_id: 'planning-conversation-1',
      parent_conversation_id: 'global-conversation-1',
      use_case_id: 'global.task.plan',
    })
    mocks.route.query = { conversation_id: child.conversation_id }
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [root] })
    mocks.fetchChildren.mockResolvedValue({ ok: true, conversations: [] })
    mocks.fetchConversation.mockImplementation(async (conversationId: string) => ({
      ok: true,
      conversation_id: conversationId,
      conversation: conversationId === child.conversation_id ? child : root,
      events: [],
    }))
    mocks.waitForEvents.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(AiWorkView)
    await flushPromises()

    expect(mocks.fetchConversation).toHaveBeenCalledWith(child.conversation_id)
    expect(wrapper.text()).toContain('任务规划')
    expect(wrapper.text()).toContain('内部执行 · 只读')
    expect(wrapper.find('[data-testid="root-conversation-global-conversation-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="internal-conversation-planning-conversation-1"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('切换会话、新建对话和卸载都会中止当前长轮询', async () => {
    const first = conversation('running')
    const second = conversation('running', {
      conversation_id: 'agent-conversation-2',
      use_case_id: 'copy.generate',
    })
    const signals: AbortSignal[] = []
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [first, second],
    })
    mocks.fetchConversation.mockImplementation(async (conversationId: string) => {
      const selected = conversationId === second.conversation_id ? second : first
      return {
        ok: true,
        conversation_id: conversationId,
        conversation: selected,
        events: [],
      }
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

    const wrapper = mount(AiWorkView)
    await flushPromises()
    expect(signals).toHaveLength(1)
    expect(signals[0].aborted).toBe(false)

    await wrapper.get('[data-testid="root-conversation-agent-conversation-2"]').trigger('click')
    await flushPromises()
    expect(signals[0].aborted).toBe(true)
    expect(signals).toHaveLength(2)
    expect(signals[1].aborted).toBe(false)

    await wrapper.get('[data-testid="new-global-conversation"]').trigger('click')
    expect(signals[1].aborted).toBe(true)

    await wrapper.get('[data-testid="root-conversation-agent-conversation-1"]').trigger('click')
    await flushPromises()
    expect(signals).toHaveLength(3)
    expect(signals[2].aborted).toBe(false)

    wrapper.unmount()
    expect(signals[2].aborted).toBe(true)
  })
})

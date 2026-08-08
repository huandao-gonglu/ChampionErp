import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AiWorkView from '../AiWorkView.vue'

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, string> },
  fetchConversations: vi.fn(),
  fetchConversation: vi.fn(),
  waitForEvents: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

vi.mock('@/api/aiWork', () => ({
  aiWorkRawUrl: (conversationId: string) => `/api/ai-work/${conversationId}/raw`,
  fetchAiWorkConversations: mocks.fetchConversations,
  fetchAiWorkConversation: mocks.fetchConversation,
  waitForAiWorkEvents: mocks.waitForEvents,
}))

function conversation(status: 'running' | 'completed' | 'failed') {
  return {
    conversation_id: 'agent-conversation-1',
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
  }
}

async function openTab(wrapper: ReturnType<typeof mount>, label: string) {
  const button = wrapper.findAll('button').find((item) => item.text() === label)
  if (!button) throw new Error(`找不到页签：${label}`)
  await button.trigger('click')
}

describe('AiWorkView Agent 对话投影', () => {
  beforeEach(() => {
    mocks.route.query = {}
    mocks.fetchConversations.mockReset()
    mocks.fetchConversation.mockReset()
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
})

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UIMessage } from 'ai'
import { useAiChatStore } from '@/stores'
import AiWorkView from '../AiWorkView.vue'

const GLOBAL_CHAT_ID = `conversation_global_chat_${'ab'.repeat(16)}`

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, unknown> },
  fetchConversations: vi.fn(),
  fetchConversation: vi.fn(),
  fetchUiMessages: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

vi.mock('@/api/aiWork', () => ({
  fetchPydanticConversations: mocks.fetchConversations,
  fetchPydanticConversation: mocks.fetchConversation,
  fetchUiMessages: mocks.fetchUiMessages,
  AI_CHAT_RUNS_PATH: '/api/v1/ai-chat/runs',
}))

const conversations = [
  {
    conversation_id: 'conversation-1',
    created_at: '2026-08-14T08:00:00+08:00',
    updated_at: '2026-08-14T08:02:00+08:00',
  },
  {
    conversation_id: 'conversation-2',
    created_at: '2026-08-14T09:00:00+08:00',
    updated_at: '2026-08-14T09:03:00+08:00',
  },
  {
    conversation_id: GLOBAL_CHAT_ID,
    created_at: '2026-08-14T08:20:00+08:00',
    updated_at: '2026-08-14T08:30:00+08:00',
  },
]

function detail(conversationId: string) {
  return {
    ok: true,
    conversation_id: conversationId,
    created_at: '2026-08-14T08:00:00+08:00',
    updated_at: '2026-08-14T08:03:00+08:00',
    messages: [
      {
        kind: 'request',
        parts: [{ part_kind: 'user-prompt', content: `原始内容-${conversationId}` }],
      },
      {
        kind: 'response',
        parts: [{ part_kind: 'text', content: '未经解释的响应' }],
      },
    ],
  }
}

function uiMessages(conversationId: string) {
  return {
    ok: true,
    conversation_id: conversationId,
    created_at: '2026-08-14T08:00:00+08:00',
    updated_at: '2026-08-14T08:03:00+08:00',
    messages: [
      {
        id: `${conversationId}-user-1`,
        role: 'user',
        parts: [{ type: 'text', text: `用户问题-${conversationId}` }],
      },
      {
        id: `${conversationId}-assistant-1`,
        role: 'assistant',
        parts: [{ type: 'text', text: `派生回答-${conversationId}` }],
      },
    ],
  }
}

function setupView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAiChatStore()
  const mountNow = () => mount(AiWorkView, { global: { plugins: [pinia] } })
  return { store, mountNow }
}

describe('AiWorkView 对话与历史', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations })
    mocks.fetchConversation.mockImplementation(async (conversationId: string) => detail(conversationId))
    mocks.fetchUiMessages.mockImplementation(async (conversationId: string) => uiMessages(conversationId))
  })

  it('加载列表、按 updated_at 倒序默认选中首项，并以派生气泡展示', async () => {
    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(mocks.fetchConversations).toHaveBeenCalledWith()
    expect(mocks.fetchUiMessages).toHaveBeenCalledWith('conversation-2')
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe('conversation-2')

    const chatView = wrapper.get('[data-testid="ai-work-chat-view"]')
    expect(chatView.text()).toContain('用户问题-conversation-2')
    expect(chatView.text()).toContain('派生回答-conversation-2')
    expect(chatView.text()).toContain('只读历史')
    expect(wrapper.find('[data-testid="ai-work-raw-view"]').exists()).toBe(false)
  })

  it('切换 conversation 与原始消息标签后仍可查看规范 JSON', async () => {
    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-conversation-conversation-1"]').trigger('click')
    await flushPromises()

    expect(mocks.fetchUiMessages).toHaveBeenLastCalledWith('conversation-1')
    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain('派生回答-conversation-1')

    await wrapper.get('[data-testid="ai-work-view-raw-tab"]').trigger('click')
    const tree = wrapper.get('[data-testid="ai-work-json-tree"]')
    expect(tree.text()).toContain('messages')
    expect(tree.text()).toContain('part_kind')
    expect(tree.text()).toContain('原始内容-conversation-1')

    await wrapper.get('[data-testid="ai-work-raw-tab"]').trigger('click')
    const raw = wrapper.get('[data-testid="ai-work-raw-json"]').text()
    expect(raw).toContain('"kind": "request"')
    expect(raw).toContain('原始内容-conversation-1')
  })

  it('手动刷新列表和当前详情，不启动自动轮询', async () => {
    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-refresh"]').trigger('click')
    await flushPromises()

    expect(mocks.fetchConversations).toHaveBeenCalledTimes(2)
    expect(mocks.fetchConversation).toHaveBeenCalledTimes(2)
    expect(mocks.fetchConversation).toHaveBeenLastCalledWith('conversation-2')
  })

  it('可通过 query 直接打开指定 conversation', async () => {
    mocks.route.query = { conversation_id: 'conversation-1' }

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(mocks.fetchUiMessages).toHaveBeenCalledWith('conversation-1')
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe('conversation-1')
  })

  it('选中活动会话时直接绑定共享 Chat 消息，不请求派生接口', async () => {
    const { store, mountNow } = setupView()
    const conversationId = store.startConversation()
    mocks.route.query = { conversation_id: conversationId }

    const wrapper = mountNow()
    await flushPromises()

    expect(mocks.fetchUiMessages).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(true)
    // 尚未持久化的活动会话以临时条目出现在左侧。
    expect(wrapper.find('[data-testid="ai-work-conversation-temporary"]').exists()).toBe(true)

    store.chat!.messages = [
      { id: 'm1', role: 'user', parts: [{ type: 'text', text: '草稿有几条？' }] },
      { id: 'm2', role: 'assistant', parts: [{ type: 'text', text: '共有 3 条草稿。' }] },
    ] as UIMessage[]
    await flushPromises()

    const live = wrapper.get('[data-testid="ai-work-live-chat"]')
    expect(live.text()).toContain('草稿有几条？')
    expect(live.text()).toContain('共有 3 条草稿。')
  })

  it('活动运行完成后刷新一次列表，服务端结果替换临时条目', async () => {
    const { store, mountNow } = setupView()
    const conversationId = store.startConversation()

    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe(conversationId)
    expect(wrapper.find('[data-testid="ai-work-conversation-temporary"]').exists()).toBe(true)

    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [
        ...conversations,
        {
          conversation_id: conversationId,
          created_at: '2026-08-14T10:00:00+08:00',
          updated_at: '2026-08-14T10:05:00+08:00',
        },
      ],
    })

    store.refreshHistory()
    await flushPromises()

    expect(mocks.fetchConversations).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="ai-work-conversation-temporary"]').exists()).toBe(false)
    expect(wrapper.find(`[data-testid="ai-work-conversation-${conversationId}"]`).exists()).toBe(true)
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe(conversationId)
    expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(true)
  })

  it('global.chat 历史可以重新激活后继续发送', async () => {
    const { store, mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    await wrapper.get(`[data-testid="ai-work-conversation-${GLOBAL_CHAT_ID}"]`).trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain(`派生回答-${GLOBAL_CHAT_ID}`)
    const reactivate = wrapper.get('[data-testid="ai-work-reactivate"]')
    expect(reactivate.attributes('disabled')).toBeUndefined()

    await reactivate.trigger('click')
    await flushPromises()

    expect(store.activeConversationId).toBe(GLOBAL_CHAT_ID)
    expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="ai-work-live-chat"]').text()).toContain(`派生回答-${GLOBAL_CHAT_ID}`)
    expect(wrapper.find('[data-testid="ai-work-reactivate"]').exists()).toBe(false)
  })

  it('非 global.chat 历史只读且不显示继续入口', async () => {
    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe('conversation-2')
    expect(wrapper.find('[data-testid="ai-work-reactivate"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain(
      '该会话属于其他业务 Agent，仅提供只读展示',
    )
  })

  it('list 请求失败时展示读取错误', async () => {
    mocks.fetchConversations.mockRejectedValueOnce(new Error('索引不可用'))

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('读取 conversation 列表失败：索引不可用')
  })

  it('派生消息读取失败单独展示在对话标签，不影响原始消息标签', async () => {
    mocks.fetchUiMessages.mockRejectedValueOnce(new Error('派生不可用'))

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-ui-messages-error"]').text()).toContain(
      '读取历史消息失败：派生不可用',
    )

    await wrapper.get('[data-testid="ai-work-view-raw-tab"]').trigger('click')
    expect(wrapper.get('[data-testid="ai-work-json-tree"]').text()).toContain('原始内容-conversation-2')
  })

  it('规范 detail 失败只影响原始消息标签，气泡标签正常', async () => {
    mocks.fetchConversation.mockRejectedValueOnce(new Error('详情不可用'))

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain('派生回答-conversation-2')

    await wrapper.get('[data-testid="ai-work-view-raw-tab"]').trigger('click')
    expect(wrapper.get('[data-testid="ai-work-detail-error"]').text()).toContain(
      '读取 conversation 失败：详情不可用',
    )
  })

  it('messages 不是数组时在原始消息标签显示 JSON 验证错误', async () => {
    mocks.fetchConversation.mockResolvedValue({
      ...detail('conversation-2'),
      messages: { invalid: true },
    })

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-view-raw-tab"]').trigger('click')
    expect(wrapper.get('[data-testid="ai-work-json-error"]').text()).toContain(
      '消息 JSON 格式无效：messages 必须是 JSON 数组。',
    )
    expect(wrapper.find('[data-testid="ai-work-json-tree"]').exists()).toBe(false)
  })

  it('从已加载的 messages 生成下载文件，不请求 raw 端点', async () => {
    const createObjectURL = vi.fn(() => 'blob:ai-work')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-view-raw-tab"]').trigger('click')
    await wrapper.get('[data-testid="ai-work-download"]').trigger('click')

    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:ai-work')
    expect(click).toHaveBeenCalledOnce()
    click.mockRestore()
    Reflect.deleteProperty(URL, 'createObjectURL')
    Reflect.deleteProperty(URL, 'revokeObjectURL')
  })
})

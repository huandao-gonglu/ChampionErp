import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Chat } from '@ai-sdk/vue'
import type { UIMessage } from 'ai'
import { useAiChatStore, useAiWorkDisplayStore } from '@/stores'
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
  const display = useAiWorkDisplayStore()
  const mountNow = () => mount(AiWorkView, { global: { plugins: [pinia] } })
  return { store, display, mountNow }
}

const PRESENTATION_CONVERSATION_ID = 'conversation_presentation_x'

function fakeObserveChat(messages: UIMessage[] = []): Chat<UIMessage> {
  return new Chat<UIMessage>({ id: 'presentation_x', messages })
}

function attachPresentation(
  display: ReturnType<typeof useAiWorkDisplayStore>,
  messages: UIMessage[] = [],
): void {
  display.attachForegroundPresentation(
    {
      presentationId: 'presentation_x',
      conversationId: PRESENTATION_CONVERSATION_ID,
      displayTitle: 'AI 匹配类目',
      status: 'running',
    },
    fakeObserveChat(messages),
  )
}

describe('AiWorkView 对话与历史', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations })
    mocks.fetchConversation.mockImplementation(async (conversationId: string) => detail(conversationId))
    mocks.fetchUiMessages.mockImplementation(async (conversationId: string) => uiMessages(conversationId))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
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
    // query 指向活动会话：立即绑定实时 Chat，不等待列表请求完成。
    expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(true)
    await flushPromises()

    expect(mocks.fetchUiMessages).not.toHaveBeenCalled()
    // 尚未持久化的活动会话不发起规范 detail 请求，避免必然 404。
    expect(mocks.fetchConversation).not.toHaveBeenCalled()
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

    // 持久化完成后读取一次规范 detail，原始消息标签可正常检查。
    expect(mocks.fetchConversation).toHaveBeenCalledTimes(1)
    expect(mocks.fetchConversation).toHaveBeenCalledWith(conversationId)
    await wrapper.get('[data-testid="ai-work-view-raw-tab"]').trigger('click')
    expect(wrapper.get('[data-testid="ai-work-json-tree"]').text()).toContain(
      `原始内容-${conversationId}`,
    )
    expect(wrapper.find('[data-testid="ai-work-raw-pending"]').exists()).toBe(false)
  })

  it('未持久化活动 conversation 无 404 错误，原始标签显示待完成状态', async () => {
    const { store, mountNow } = setupView()
    const conversationId = store.startConversation()
    mocks.route.query = { conversation_id: conversationId }

    const wrapper = mountNow()
    await flushPromises()

    expect(mocks.fetchConversation).not.toHaveBeenCalled()
    expect(mocks.fetchUiMessages).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="ai-work-detail-error"]').exists()).toBe(false)

    await wrapper.get('[data-testid="ai-work-view-raw-tab"]').trigger('click')
    expect(wrapper.find('[data-testid="ai-work-detail-error"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="ai-work-raw-pending"]').text()).toContain(
      '运行完成后可检查规范历史',
    )
  })

  it('活动会话已有持久化历史且新一轮流式中时，原始标签提示上一终态历史', async () => {
    const { store, mountNow } = setupView()
    const conversationId = store.startConversation()
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
    mocks.route.query = { conversation_id: conversationId }

    let controller: ReadableStreamDefaultController<Uint8Array> | undefined
    const fetchMock = vi.fn(async () => new Response(
      new ReadableStream<Uint8Array>({ start(value) { controller = value } }),
      {
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'x-vercel-ai-ui-message-stream': 'v1',
        },
      },
    ))
    vi.stubGlobal('fetch', fetchMock)

    store.input = '继续上一轮'
    store.sendMessage()
    await vi.waitFor(() => {
      expect(store.isBusy).toBe(true)
    })

    const wrapper = mountNow()
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-view-raw-tab"]').trigger('click')
    expect(wrapper.get('[data-testid="ai-work-raw-streaming-note"]').text()).toContain(
      '服务端已保存的上一终态历史',
    )
    expect(wrapper.get('[data-testid="ai-work-json-tree"]').text()).toContain(
      `原始内容-${conversationId}`,
    )

    // 收尾流，避免残留未完成的异步任务。
    const encoder = new TextEncoder()
    const chunk = (payload: Record<string, unknown> | '[DONE]') => {
      const value = payload === '[DONE]' ? payload : JSON.stringify(payload)
      return encoder.encode(`data: ${value}\n\n`)
    }
    controller!.enqueue(chunk({ type: 'start', messageId: 'assistant-1' }))
    controller!.enqueue(chunk({ type: 'start-step' }))
    controller!.enqueue(chunk({ type: 'finish-step' }))
    controller!.enqueue(chunk({ type: 'finish', finishReason: 'stop' }))
    controller!.enqueue(chunk('[DONE]'))
    controller!.close()
    await vi.waitFor(() => {
      expect(store.isBusy).toBe(false)
    })
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

  describe('前台 presentation 展示', () => {
    it('query 指向活动前台 presentation 时绑定 observe Chat 实时消息，不请求历史', async () => {
      const { display, mountNow } = setupView()
      attachPresentation(display, [
        {
          id: 'b1',
          role: 'assistant',
          parts: [{ type: 'text', text: '正在检索类目：ventilador' }],
        },
      ])
      mocks.route.query = {
        conversation_id: PRESENTATION_CONVERSATION_ID,
        presentation_id: 'presentation_x',
      }

      const wrapper = mountNow()
      // 首帧即绑定 presentation observe Chat，不等待列表请求。
      const live = wrapper.get('[data-testid="ai-work-presentation-live"]')
      expect(live.text()).toContain('AI 匹配类目')
      expect(live.text()).toContain('运行中')
      expect(live.text()).toContain('正在检索类目：ventilador')
      expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(false)

      await flushPromises()

      // 活动前台 presentation 不请求尚不存在的服务端历史。
      expect(mocks.fetchUiMessages).not.toHaveBeenCalledWith(PRESENTATION_CONVERSATION_ID)
      // observe Chat 消息增量继续渲染到同一分支。
      display.foregroundPresentation!.chat.messages = [
        {
          id: 'b1',
          role: 'assistant',
          parts: [{ type: 'text', text: '已找到候选：Ventiladores' }],
        },
      ] as UIMessage[]
      await flushPromises()
      expect(wrapper.get('[data-testid="ai-work-presentation-live"]').text())
        .toContain('已找到候选：Ventiladores')
      // 左侧列表出现 presentation 临时条目。
      expect(wrapper.find('[data-testid="ai-work-conversation-temporary"]').exists()).toBe(true)
    })

    it('presentation 期间点击其他历史 conversation：展示该历史自身消息，不串显实时消息', async () => {
      const { display, mountNow } = setupView()
      attachPresentation(display, [
        {
          id: 'b1',
          role: 'assistant',
          parts: [{ type: 'text', text: '正在检索类目：ventilador' }],
        },
      ])
      mocks.route.query = {
        conversation_id: PRESENTATION_CONVERSATION_ID,
        presentation_id: 'presentation_x',
      }

      const wrapper = mountNow()
      await flushPromises()
      expect(wrapper.find('[data-testid="ai-work-presentation-live"]').exists()).toBe(true)

      // presentation 期间点击左侧其他历史：选中项与展示必须同步切换。
      await wrapper.get('[data-testid="ai-work-conversation-conversation-1"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe('conversation-1')
      // query 残留 presentation_id 不是替代选择条件：不得在该历史标题下串显实时消息。
      expect(wrapper.find('[data-testid="ai-work-presentation-live"]').exists()).toBe(false)
      const chatView = wrapper.get('[data-testid="ai-work-chat-view"]')
      expect(chatView.text()).toContain('派生回答-conversation-1')
      expect(chatView.text()).not.toContain('正在检索类目')
      expect(mocks.fetchUiMessages).toHaveBeenCalledWith('conversation-1')

      // 点回 presentation 临时条目：重新绑定 observe Chat 的实时展示。
      await wrapper.get('[data-testid="ai-work-conversation-temporary"]').trigger('click')
      await flushPromises()
      expect(wrapper.get('[data-testid="ai-work-selected-id"]').text())
        .toBe(PRESENTATION_CONVERSATION_ID)
      expect(wrapper.get('[data-testid="ai-work-presentation-live"]').text())
        .toContain('正在检索类目：ventilador')
    })

    it('query 只带 presentation_id 时解析为 presentation conversation，不落到其他历史标题下', async () => {
      const { display, mountNow } = setupView()
      attachPresentation(display, [
        {
          id: 'b1',
          role: 'assistant',
          parts: [{ type: 'text', text: '正在检索类目：ventilador' }],
        },
      ])
      mocks.route.query = { presentation_id: 'presentation_x' }

      const wrapper = mountNow()
      // 首帧即把 presentation_id 解析为该 presentation 的 conversation 并绑定 observe Chat。
      expect(wrapper.get('[data-testid="ai-work-selected-id"]').text())
        .toBe(PRESENTATION_CONVERSATION_ID)
      expect(wrapper.find('[data-testid="ai-work-presentation-live"]').exists()).toBe(true)

      await flushPromises()

      // 列表刷新后仍保持 presentation conversation 选中，不默认落到其他历史。
      expect(wrapper.get('[data-testid="ai-work-selected-id"]').text())
        .toBe(PRESENTATION_CONVERSATION_ID)
      expect(wrapper.find('[data-testid="ai-work-presentation-live"]').exists()).toBe(true)
      expect(mocks.fetchUiMessages).not.toHaveBeenCalledWith(PRESENTATION_CONVERSATION_ID)
    })

    it('presentation terminal 后保持 conversation 只读并切换到服务端历史', async () => {
      const { display, mountNow } = setupView()
      attachPresentation(display)
      mocks.route.query = {
        conversation_id: PRESENTATION_CONVERSATION_ID,
        presentation_id: 'presentation_x',
      }

      const wrapper = mountNow()
      await flushPromises()
      expect(wrapper.find('[data-testid="ai-work-presentation-live"]').exists()).toBe(true)

      display.finishForegroundPresentation({ kind: 'success', text: '类目匹配完成' })
      await flushPromises()

      // 刷新列表一次，并用服务端派生历史替换临时 observe 展示。
      expect(mocks.fetchConversations).toHaveBeenCalledTimes(2)
      expect(mocks.fetchUiMessages).toHaveBeenCalledWith(PRESENTATION_CONVERSATION_ID)
      expect(wrapper.find('[data-testid="ai-work-presentation-live"]').exists()).toBe(false)
      expect(wrapper.get('[data-testid="ai-work-selected-id"]').text())
        .toBe(PRESENTATION_CONVERSATION_ID)
      const chatView = wrapper.get('[data-testid="ai-work-chat-view"]')
      expect(chatView.text()).toContain(`派生回答-${PRESENTATION_CONVERSATION_ID}`)
      expect(chatView.text()).toContain('只读历史')
      // 页面不自动跳回 global.chat，也不提供继续此对话入口。
      expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="ai-work-reactivate"]').exists()).toBe(false)
    })
  })
})

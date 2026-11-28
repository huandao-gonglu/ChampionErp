import { AI_CHAT_SELECTION_KEY } from '@/stores/aiChat'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UIMessage } from 'ai'
import { useAiChatStore } from '@/stores'
import AiWorkView from '../AiWorkView.vue'

beforeEach(() => localStorage.removeItem(AI_CHAT_SELECTION_KEY))

enableAutoUnmount(afterEach)

const GLOBAL_CHAT_ID = `conversation_global_chat_${'ab'.repeat(16)}`

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, unknown> },
  fetchConversations: vi.fn(),
  fetchConversation: vi.fn(),
  fetchUiMessages: vi.fn(),
  replaceRoute: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return {
    ...actual,
    useRoute: () => mocks.route,
    useRouter: () => ({ replace: mocks.replaceRoute }),
  }
})

vi.mock('@/api/aiWork', () => ({
  fetchPydanticConversations: mocks.fetchConversations,
  fetchPydanticConversation: mocks.fetchConversation,
  fetchUiMessages: mocks.fetchUiMessages,
  conversationEventsUrl: (conversationId: string, afterHistoryVersion: number) => (
    `/api/v1/ai-work/conversations/${conversationId}/events`
    + `?after_history_version=${Math.max(0, Math.floor(afterHistoryVersion))}`
  ),
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
    history_version: 3,
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
  const mountNow = () => mount(AiWorkView, {
    global: {
      plugins: [pinia],
      stubs: { RouterLink: true },
    },
  })
  return { store, mountNow }
}

describe('AiWorkView 对话与历史', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mocks.replaceRoute.mockResolvedValue(undefined)
    const saved = new Map<string, string>()
    vi.spyOn(localStorage, 'getItem').mockImplementation(key => saved.get(key) ?? null)
    vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => { saved.set(key, value) })
    vi.spyOn(localStorage, 'removeItem').mockImplementation(key => { saved.delete(key) })
    mocks.route.query = {}
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations })
    mocks.fetchConversation.mockImplementation(async (conversationId: string) => detail(conversationId))
    mocks.fetchUiMessages.mockImplementation(async (conversationId: string) => uiMessages(conversationId))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('没有上次选择时保持空白聊天，不自动打开最近任务或历史', async () => {
    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()
    expect(mocks.fetchConversations).toHaveBeenCalledWith(100, 'chats')
    expect(mocks.fetchUiMessages).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="ai-work-selected-id"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="ai-chat-input"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ai-chat-empty"]').exists()).toBe(true)
  })

  it('默认入口恢复上次普通对话，列表中更新的任务不能抢占', async () => {
    localStorage.setItem(AI_CHAT_SELECTION_KEY, GLOBAL_CHAT_ID)
    const { store, mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()
    expect(store.activeConversationId).toBe(GLOBAL_CHAT_ID)
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe(GLOBAL_CHAT_ID)
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="ai-chat-input"]').exists()).toBe(true)
  })

  it('切换对话后显示对应对话内容', async () => {
    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-conversation-conversation-1"]').trigger('click')
    await flushPromises()

    expect(mocks.fetchUiMessages).toHaveBeenLastCalledWith('conversation-1')
    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain('派生回答-conversation-1')
  })

  it('手动刷新列表和当前详情，不启动自动轮询', async () => {
    mocks.route.query = { conversation_id: 'conversation-2' }
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
    localStorage.setItem(AI_CHAT_SELECTION_KEY, GLOBAL_CHAT_ID)
    mocks.route.query = { conversation_id: 'conversation-1' }

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(mocks.fetchUiMessages).toHaveBeenCalledWith('conversation-1')
    expect(mocks.fetchUiMessages).not.toHaveBeenCalledWith(GLOBAL_CHAT_ID)
    expect(localStorage.getItem(AI_CHAT_SELECTION_KEY)).toBe(GLOBAL_CHAT_ID)
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe('conversation-1')
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

  it('/new 从持久化旧对话立即切换，列表刷新不能把旧会话重新载入', async () => {
    mocks.route.query = { conversation_id: GLOBAL_CHAT_ID, workspace_tab: 'orders' }
    const { store, mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()
    expect(store.activeConversationId).toBe(GLOBAL_CHAT_ID)

    store.input = '/new'
    store.sendMessage()
    const newId = store.activeConversationId
    const newChat = store.chat
    expect(newId).not.toBe(GLOBAL_CHAT_ID)
    await flushPromises()
    expect(store.activeConversationId).toBe(newId)
    expect(store.chat).toBe(newChat)
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe(newId)
    expect(wrapper.find('[data-testid="ai-chat-empty"]').exists()).toBe(true)
    expect(mocks.replaceRoute).toHaveBeenLastCalledWith(expect.objectContaining({ query: {
      workspace_tab: 'orders',
    } }))

    await wrapper.get('[data-testid="ai-work-refresh"]').trigger('click')
    await flushPromises()
    expect(store.chat).toBe(newChat)
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe(newId)
    wrapper.unmount()
  })

  it('/new 后迟到的旧历史消息和列表响应不能抢回新对话', async () => {
    mocks.route.query = { conversation_id: GLOBAL_CHAT_ID }
    let resolveHistory!: (value: ReturnType<typeof uiMessages>) => void
    mocks.fetchUiMessages.mockImplementationOnce(() => new Promise(resolve => { resolveHistory = resolve }))
    const { store, mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    let resolveList!: (value: { ok: boolean; conversations: typeof conversations }) => void
    mocks.fetchConversations.mockImplementationOnce(() => new Promise(resolve => { resolveList = resolve }))
    store.historyVersion++
    await flushPromises()
    store.input = '/new'
    store.sendMessage()
    const newId = store.activeConversationId
    await flushPromises()
    resolveList({ ok: true, conversations })
    resolveHistory(uiMessages(GLOBAL_CHAT_ID))
    await flushPromises()

    expect(store.activeConversationId).toBe(newId)
    expect(store.messages).toEqual([])
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe(newId)
    expect(wrapper.find('[data-testid="ai-work-ui-loading"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('活动运行完成后刷新一次列表，服务端结果替换临时条目', async () => {
    const { store, mountNow } = setupView()
    const conversationId = store.startConversation()

    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe(conversationId)
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
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe(conversationId)
    expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(true)

    // 持久化完成后读取一次规范 detail，更新头部元数据。
    expect(mocks.fetchConversation).toHaveBeenCalledTimes(1)
    expect(mocks.fetchConversation).toHaveBeenCalledWith(conversationId)
    expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(true)

  })

  it('活动会话已有持久化历史且新一轮流式中时，继续显示实时对话', async () => {
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

    expect(wrapper.get('[data-testid="ai-work-live-chat"]').text()).toContain('继续上一轮')

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

  it('选中普通对话就显示输入框，只读取一次历史，发送前不调用模型', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const { store, mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    await wrapper.get(`[data-testid="ai-work-conversation-${GLOBAL_CHAT_ID}"]`).trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain(`派生回答-${GLOBAL_CHAT_ID}`)
    expect(store.activeConversationId).toBe(GLOBAL_CHAT_ID)
    expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="ai-work-live-chat"]').text()).toContain(`派生回答-${GLOBAL_CHAT_ID}`)
    expect(wrapper.find('[data-testid="ai-chat-input"]').exists()).toBe(true)
    expect(mocks.fetchUiMessages.mock.calls.filter(([id]) => id === GLOBAL_CHAT_ID)).toHaveLength(1)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('直接打开普通对话链接也能输入，消息发到选中的原对话', async () => {
    mocks.route.query = { conversation_id: GLOBAL_CHAT_ID }
    const fetchMock = vi.fn(async () => new Response(
      'data: {"type":"start","messageId":"reply"}\n\ndata: {"type":"finish","finishReason":"stop"}\n\ndata: [DONE]\n\n',
      { headers: { 'x-vercel-ai-ui-message-stream': 'v1' } },
    ))
    vi.stubGlobal('fetch', fetchMock)
    const { store, mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()
    expect(store.activeConversationId).toBe(GLOBAL_CHAT_ID)
    expect(fetchMock).not.toHaveBeenCalled()
    await wrapper.get('[data-testid="ai-chat-input"]').setValue('继续讨论')
    await wrapper.get('[data-testid="ai-chat-composer"]').trigger('submit')
    await flushPromises()
    const init = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0]![1]
    const payload = JSON.parse(String(init.body))
    expect(payload.id).toBe(GLOBAL_CHAT_ID)
    expect(payload.messages[0].parts[0].text).toBe('继续讨论')
    wrapper.unmount()
    store.disconnectEvents()
  })

  it('历史迟到时不能把用户刚选中的业务记录切换成其他对话', async () => {
    let resolveHistory!: (value: ReturnType<typeof uiMessages>) => void
    mocks.fetchUiMessages.mockImplementation((id: string) => id === GLOBAL_CHAT_ID
      ? new Promise(resolve => { resolveHistory = resolve })
      : Promise.resolve(uiMessages(id)))
    const { store, mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()
    await wrapper.get(`[data-testid="ai-work-conversation-${GLOBAL_CHAT_ID}"]`).trigger('click')
    await wrapper.get('[data-testid="ai-work-conversation-conversation-1"]').trigger('click')
    await flushPromises()
    resolveHistory(uiMessages(GLOBAL_CHAT_ID))
    await flushPromises()
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe('conversation-1')
    expect(store.activeConversationId).toBeNull()
    expect(wrapper.find('[data-testid="ai-chat-input"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('普通对话读取失败时保留错误，重新选择后可以直接输入', async () => {
    mocks.fetchUiMessages.mockImplementation(async (id: string) => {
      if (id === GLOBAL_CHAT_ID) throw new Error('历史暂时不可用')
      return uiMessages(id)
    })
    const { store, mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()
    await wrapper.get(`[data-testid="ai-work-conversation-${GLOBAL_CHAT_ID}"]`).trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="ai-work-ui-messages-error"]').text()).toContain('历史暂时不可用')
    expect(store.activeConversationId).toBeNull()
    mocks.fetchUiMessages.mockImplementation(async (id: string) => uiMessages(id))
    await wrapper.get(`[data-testid="ai-work-conversation-${GLOBAL_CHAT_ID}"]`).trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="ai-chat-input"]').exists()).toBe(true)
    wrapper.unmount()
    store.disconnectEvents()
  })

  it('非 global.chat 历史只读并提示为任务记录', async () => {
    mocks.route.query = { conversation_id: 'conversation-2' }
    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-selected-id"]').attributes('data-conversation-id')).toBe('conversation-2')
    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain(
      '这是任务记录，无法在此继续对话',
    )
  })

  it('list 请求失败时展示读取错误', async () => {
    mocks.fetchConversations.mockRejectedValueOnce(new Error('索引不可用'))

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('读取对话列表失败：索引不可用')
  })

  it('派生消息读取失败时在对话区域展示错误', async () => {
    mocks.route.query = { conversation_id: 'conversation-2' }
    mocks.fetchUiMessages.mockRejectedValueOnce(new Error('派生不可用'))

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-ui-messages-error"]').text()).toContain(
      '读取历史消息失败：派生不可用',
    )


  })

  it('头部元数据读取失败不影响对话展示', async () => {
    mocks.route.query = { conversation_id: 'conversation-2' }
    mocks.fetchConversation.mockRejectedValueOnce(new Error('详情不可用'))

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain('派生回答-conversation-2')


  })

  it('头部详情消息格式异常时仍可展示对话', async () => {
    mocks.route.query = { conversation_id: 'conversation-2' }
    mocks.fetchConversation.mockResolvedValue({
      ...detail('conversation-2'),
      messages: { invalid: true },
    })

    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-chat-view"]').text()).toContain('派生回答-conversation-2')

  })

  it('手动业务记录沿用普通对话和同一个输入框', async () => {
    const { store, mountNow } = setupView()
    const id = store.startManualAction('AI 匹配类目')
    mocks.route.query = { conversation_id: id }
    const wrapper = mountNow()
    await flushPromises()
    expect(wrapper.get('[data-testid="ai-work-live-chat"]').text()).toContain('AI 匹配类目')
    expect(wrapper.find('[data-testid="ai-chat-input"]').exists()).toBe(true)
    expect(mocks.fetchConversations).toHaveBeenCalledWith(100, 'chats')
  })

  it('左侧栏支持搜索过滤与一键收起/展开，收起后仍保留返回工作台与新对话入口', async () => {
    mocks.fetchConversations.mockResolvedValue({
      ok: true,
      conversations: [
        { conversation_id: GLOBAL_CHAT_ID, title: '批量处理勾选草稿', created_at: '2026-11-28T08:00:00+08:00', updated_at: '2026-11-28T09:00:00+08:00' },
        { conversation_id: 'conversation-1', title: '订单采购对账', created_at: '2026-11-27T08:00:00+08:00', updated_at: '2026-11-27T09:00:00+08:00' },
      ],
    })
    const { mountNow } = setupView()
    const wrapper = mountNow()
    await flushPromises()

    expect(wrapper.find('[data-testid="ai-work-sidebar"]').exists()).toBe(true)
    await wrapper.get('[data-testid="ai-work-search"]').setValue('采购')
    expect(wrapper.find('[data-testid="ai-work-conversation-conversation-1"]').exists()).toBe(true)
    expect(wrapper.find(`[data-testid="ai-work-conversation-${GLOBAL_CHAT_ID}"]`).exists()).toBe(false)

    await wrapper.get('[data-testid="ai-work-sidebar-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="ai-work-sidebar"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="ai-work-back"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ai-work-new-chat"]').exists()).toBe(true)

    await wrapper.get('[data-testid="ai-work-sidebar-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="ai-work-sidebar"]').exists()).toBe(true)
  })
})

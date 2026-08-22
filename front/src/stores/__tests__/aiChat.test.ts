import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UIMessage } from 'ai'
import { useAiChatStore } from '../aiChat'

const encoder = new TextEncoder()

const mocks = vi.hoisted(() => ({
  fetchUiMessages: vi.fn(),
  fetchConversationTaskLink: vi.fn(),
  approveGlobalTask: vi.fn(),
  rejectGlobalTask: vi.fn(),
  cancelGlobalTask: vi.fn(),
}))

vi.mock('@/api/aiWork', () => ({
  AI_CHAT_RUNS_PATH: '/api/v1/ai-chat/runs',
  conversationEventsUrl: (conversationId: string, afterHistoryVersion: number) => (
    `/api/v1/ai-work/conversations/${conversationId}/events`
    + `?after_history_version=${Math.max(0, Math.floor(afterHistoryVersion))}`
  ),
  fetchConversationTaskLink: mocks.fetchConversationTaskLink,
  fetchUiMessages: mocks.fetchUiMessages,
}))

vi.mock('@/api/globalTasks', () => ({
  approveGlobalTask: mocks.approveGlobalTask,
  rejectGlobalTask: mocks.rejectGlobalTask,
  cancelGlobalTask: mocks.cancelGlobalTask,
}))

function encodedChunk(payload: Record<string, unknown> | '[DONE]'): Uint8Array {
  const value = payload === '[DONE]' ? payload : JSON.stringify(payload)
  return encoder.encode(`data: ${value}\n\n`)
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2

  readyState = FakeEventSource.OPEN
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  url: string

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED
  }

  emit(data: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

function emptyTaskLink(conversationId: string) {
  return { ok: true, conversation_id: conversationId, task_id: '', link_status: '', task: null }
}

describe('AiChatStore 实时流', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.fetchUiMessages.mockReset()
    mocks.fetchConversationTaskLink.mockReset()
    mocks.fetchConversationTaskLink.mockImplementation(async (conversationId: string) => (
      emptyTaskLink(conversationId)
    ))
    FakeEventSource.instances = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('只提交本轮用户消息并把多次 delta 合并到同一 assistant 气泡', async () => {
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined
    let requestBody: Record<string, unknown> | undefined
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requestBody = JSON.parse(String(init?.body || '{}')) as Record<string, unknown>
      const stream = new ReadableStream<Uint8Array>({
        start(value) {
          controller = value
        },
      })
      return new Response(stream, {
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'x-vercel-ai-ui-message-stream': 'v1',
        },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const store = useAiChatStore()
    store.input = '查询草稿'
    store.sendMessage()

    await vi.waitFor(() => {
      expect(controller).toBeDefined()
      expect(store.messages).toHaveLength(1)
      expect(store.status).toBe('submitted')
    })

    expect(requestBody?.trigger).toBe('submit-message')
    expect(requestBody?.id).toBe(store.activeConversationId)
    expect(requestBody?.messages).toHaveLength(1)
    expect((requestBody?.messages as Array<{ role: string }>)[0]?.role).toBe('user')

    controller!.enqueue(encodedChunk({ type: 'start', messageId: 'assistant-1' }))
    controller!.enqueue(encodedChunk({ type: 'start-step' }))
    controller!.enqueue(encodedChunk({ type: 'text-start', id: 'text-1' }))
    controller!.enqueue(encodedChunk({ type: 'text-delta', id: 'text-1', delta: '第一段' }))

    await vi.waitFor(() => {
      expect(store.status).toBe('streaming')
      expect(store.messages).toHaveLength(2)
      expect(store.messages[1]?.parts).toEqual([
        { type: 'step-start' },
        { type: 'text', text: '第一段', state: 'streaming' },
      ])
    })
    const assistantMessageId = store.messages[1]?.id

    controller!.enqueue(encodedChunk({ type: 'text-delta', id: 'text-1', delta: '第二段' }))
    controller!.enqueue(encodedChunk({ type: 'text-end', id: 'text-1' }))
    controller!.enqueue(encodedChunk({ type: 'finish-step' }))
    controller!.enqueue(encodedChunk({ type: 'finish', finishReason: 'stop' }))
    // 本回合结束后不做本地版本猜测：以服务端已提交历史对齐游标
    // （服务端版本为 3，若本地猜测 +1 则只会到 1）。
    mocks.fetchUiMessages.mockResolvedValue({
      ok: true,
      conversation_id: store.activeConversationId,
      history_version: 3,
      created_at: '',
      updated_at: '',
      messages: [
        { id: 'user-1', role: 'user', parts: [{ type: 'text', text: '查询草稿' }] },
        {
          id: 'assistant-1',
          role: 'assistant',
          parts: [
            { type: 'step-start' },
            { type: 'text', text: '第一段第二段', state: 'done' },
          ],
        },
      ],
    })
    controller!.enqueue(encodedChunk('[DONE]'))
    controller!.close()

    await vi.waitFor(() => {
      expect(store.status).toBe('ready')
      expect(store.historyVersion).toBe(3)
    })
    expect(mocks.fetchUiMessages).toHaveBeenCalledWith(store.activeConversationId)
    expect(store.messages).toHaveLength(2)
    expect(store.messages[1]?.id).toBe(assistantMessageId)
    expect(store.messages[1]?.parts).toEqual([
      { type: 'step-start' },
      { type: 'text', text: '第一段第二段', state: 'done' },
    ])
  })

  it('输入 /new 会切换到新的空白全局对话且不发送请求', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const store = useAiChatStore()
    const previousConversationId = store.startConversation()

    store.input = '  /new  '
    store.sendMessage()

    expect(store.activeConversationId).toMatch(/^conversation_global_chat_[0-9a-f]{32}$/)
    expect(store.activeConversationId).not.toBe(previousConversationId)
    expect(store.chat?.id).toBe(store.activeConversationId)
    expect(store.messages).toEqual([])
    expect(store.input).toBe('')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('AiChatStore 任务关联与发送锁定', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.fetchUiMessages.mockReset()
    mocks.fetchConversationTaskLink.mockReset()
    FakeEventSource.instances = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('存在未解决任务关联时锁定普通发送并给出明确原因', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-1',
      link_status: 'ready',
      task: null,
    })

    await store.refreshTaskLink()

    expect(store.hasUnresolvedTask).toBe(true)
    expect(store.sendBlockedReason).toContain('全局任务')
    store.input = '继续问一个问题'
    expect(store.canSend).toBe(false)

    store.sendMessage()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('任务关联清空后普通发送恢复可用', async () => {
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-1',
      link_status: 'ready',
      task: null,
    })
    await store.refreshTaskLink()
    expect(store.hasUnresolvedTask).toBe(true)

    mocks.fetchConversationTaskLink.mockResolvedValue(emptyTaskLink(conversationId))
    await store.refreshTaskLink()

    expect(store.hasUnresolvedTask).toBe(false)
    expect(store.sendBlockedReason).toBe('')
    store.input = '问题'
    expect(store.canSend).toBe(true)
  })

  it('发送锁定期间 /new 仍可切换到新会话', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-1',
      link_status: 'ready',
      task: null,
    })
    await store.refreshTaskLink()

    store.input = '/new'
    store.sendMessage()

    expect(store.activeConversationId).not.toBe(conversationId)
    expect(store.taskLink).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('AiChatStore 后台官方事件订阅', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.fetchUiMessages.mockReset()
    mocks.fetchConversationTaskLink.mockReset()
    mocks.fetchConversationTaskLink.mockImplementation(async (conversationId: string) => (
      emptyTaskLink(conversationId)
    ))
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('冷启动先读服务端历史版本，再以该版本为游标建立订阅', async () => {
    mocks.fetchUiMessages.mockResolvedValue({
      ok: true,
      conversation_id: 'conversation-1',
      history_version: 7,
      created_at: '',
      updated_at: '',
      messages: [
        { id: 'm1', role: 'user', parts: [{ type: 'text', text: '历史问题' }] },
      ],
    })

    const store = useAiChatStore()
    await expect(store.reactivateConversation('conversation-1')).resolves.toBe(true)

    expect(store.historyVersion).toBe(7)
    const source = FakeEventSource.instances.at(-1)
    expect(source?.url).toBe(
      '/api/v1/ai-work/conversations/conversation-1/events?after_history_version=7',
    )
    expect(store.messages).toHaveLength(1)
  })

  it('收到新版本批次后重读服务端历史并刷新任务关联', async () => {
    const continuationMessage: UIMessage = {
      id: 'assistant-final',
      role: 'assistant',
      parts: [{ type: 'text', text: '后台任务已完成。' }],
    }
    mocks.fetchUiMessages.mockResolvedValue({
      ok: true,
      conversation_id: '',
      history_version: 1,
      created_at: '',
      updated_at: '',
      messages: [continuationMessage],
    })

    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchUiMessages.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      history_version: 1,
      created_at: '',
      updated_at: '',
      messages: [continuationMessage],
    })
    mocks.fetchConversationTaskLink.mockResolvedValue(emptyTaskLink(conversationId))

    const source = FakeEventSource.instances.at(-1)!
    source.emit({
      type: 'batch',
      history_version: 1,
      run_id: 'run-1',
      kind: 'continuation',
      events: [],
    })

    await vi.waitFor(() => {
      expect(store.messages).toHaveLength(1)
      expect(store.historyVersion).toBe(1)
    })
    expect(store.messages[0]?.id).toBe('assistant-final')
    expect(mocks.fetchConversationTaskLink).toHaveBeenCalledWith(conversationId)
  })

  it('重复或旧版本批次只做去重，不重复重读历史', async () => {
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchUiMessages.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      history_version: 2,
      created_at: '',
      updated_at: '',
      messages: [],
    })

    const source = FakeEventSource.instances.at(-1)!
    source.emit({ type: 'batch', history_version: 2, events: [] })
    await vi.waitFor(() => {
      expect(store.historyVersion).toBe(2)
    })
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)

    // 重复投递同一版本与更旧版本：均被去重。
    source.emit({ type: 'batch', history_version: 2, events: [] })
    source.emit({ type: 'batch', history_version: 1, events: [] })
    await Promise.resolve()
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)
  })

  it('resync_required 时重读历史并以新版本游标重建订阅', async () => {
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchUiMessages.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      history_version: 5,
      created_at: '',
      updated_at: '',
      messages: [],
    })

    const source = FakeEventSource.instances.at(-1)!
    expect(FakeEventSource.instances).toHaveLength(1)
    source.emit({ type: 'resync_required', history_version: 5 })

    await vi.waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(2)
      expect(store.historyVersion).toBe(5)
    })
    expect(source.readyState).toBe(FakeEventSource.CLOSED)
    expect(FakeEventSource.instances.at(-1)?.url).toBe(
      `/api/v1/ai-work/conversations/${conversationId}/events?after_history_version=5`,
    )
  })

  it('切换新会话时关闭旧订阅', () => {
    const store = useAiChatStore()
    store.startConversation()
    const first = FakeEventSource.instances.at(-1)!

    store.newConversation()

    expect(first.readyState).toBe(FakeEventSource.CLOSED)
  })
})

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

describe('AiChatStore 反序响应防护（报告 R-04/R-06）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.fetchUiMessages.mockReset()
    mocks.fetchConversationTaskLink.mockReset()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('history 重读反序完成时旧响应不得覆盖新快照（R-04）', async () => {
    const store = useAiChatStore()
    const conversationId = store.startConversation()

    const stale = deferred<Record<string, unknown>>()
    const fresh = deferred<Record<string, unknown>>()
    const responses: Array<Promise<Record<string, unknown>>> = [
      stale.promise,
      fresh.promise,
    ]
    mocks.fetchUiMessages.mockImplementation(() => responses.shift()!)
    mocks.fetchConversationTaskLink.mockResolvedValue(
      emptyTaskLink(conversationId),
    )

    // 两个新版本批次触发两次并发 resync：A 拿到 v2 请求，B 拿到 v3 请求。
    const source = FakeEventSource.instances.at(-1)!
    source.emit({ type: 'batch', history_version: 2, events: [] })
    source.emit({ type: 'batch', history_version: 3, events: [] })
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(2)

    // 新响应先完成并应用。
    fresh.resolve({
      ok: true,
      conversation_id: conversationId,
      history_version: 3,
      created_at: '',
      updated_at: '',
      messages: [
        { id: 'v3', role: 'assistant', parts: [{ type: 'text', text: '新版本' }] },
      ],
    })
    await vi.waitFor(() => {
      expect(store.historyVersion).toBe(3)
    })
    expect(store.messages.map((message) => message.id)).toEqual(['v3'])

    // 旧响应后完成：不得把 messages 覆盖回 v2（旧 bug：v3 游标 + v2 消息，
    // 随后 v3 批次被当作重复事件忽略，最终回复一直不可见）。
    stale.resolve({
      ok: true,
      conversation_id: conversationId,
      history_version: 2,
      created_at: '',
      updated_at: '',
      messages: [
        { id: 'v2', role: 'assistant', parts: [{ type: 'text', text: '旧版本' }] },
      ],
    })
    await Promise.resolve()
    await Promise.resolve()
    expect(store.historyVersion).toBe(3)
    expect(store.messages.map((message) => message.id)).toEqual(['v3'])
  })

  it('task-link 旧 empty 响应不得覆盖新 ready 关联（R-06）', async () => {
    const store = useAiChatStore()
    const conversationId = store.startConversation()

    const staleEmpty = deferred<Record<string, unknown>>()
    const freshReady = deferred<Record<string, unknown>>()
    mocks.fetchConversationTaskLink
      .mockImplementationOnce(() => staleEmpty.promise)
      .mockImplementationOnce(() => freshReady.promise)

    const first = store.refreshTaskLink()
    const second = store.refreshTaskLink()

    freshReady.resolve({
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-1',
      link_status: 'ready',
      task: null,
    })
    await second
    expect(store.hasUnresolvedTask).toBe(true)

    // 旧 empty 响应晚到：不得覆盖较新的 ready 关联（否则任务卡消失、
    // 前端误放开被锁定的普通发送）。
    staleEmpty.resolve(emptyTaskLink(conversationId))
    await first
    expect(store.hasUnresolvedTask).toBe(true)
    expect(store.taskLink?.task_id).toBe('gtask-1')
  })

  it('task-link 旧 ready 响应不得覆盖新 empty 关联（R-06）', async () => {
    const store = useAiChatStore()
    const conversationId = store.startConversation()

    const staleReady = deferred<Record<string, unknown>>()
    const freshEmpty = deferred<Record<string, unknown>>()
    mocks.fetchConversationTaskLink
      .mockImplementationOnce(() => staleReady.promise)
      .mockImplementationOnce(() => freshEmpty.promise)

    const first = store.refreshTaskLink()
    const second = store.refreshTaskLink()

    freshEmpty.resolve(emptyTaskLink(conversationId))
    await second
    expect(store.hasUnresolvedTask).toBe(false)

    // 旧 ready 响应晚到：任务已结束的 empty 事实必须保留
    // （否则任务卡与发送锁被陈旧关联继续保留）。
    staleReady.resolve({
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-1',
      link_status: 'ready',
      task: null,
    })
    await first
    expect(store.hasUnresolvedTask).toBe(false)
    expect(store.taskLink?.task_id).toBe('')
  })

  it('duplicate-claim 恢复不得用旧 history 覆盖 continuation 已提交的新消息（A-07）', async () => {
    const store = useAiChatStore()
    const conversationId = store.startConversation()

    // recoverFromDuplicateClaim 的第一次 fetchUiMessages 返回慢 v1；
    // 其后由 continuation 批次触发的 resync 返回 v2。
    const staleV1 = deferred<Record<string, unknown>>()
    const freshV2: Record<string, unknown> = {
      ok: true,
      conversation_id: conversationId,
      history_version: 2,
      created_at: '',
      updated_at: '',
      messages: [
        { id: 'v2', role: 'assistant', parts: [{ type: 'text', text: '后台最终回复' }] },
      ],
    }
    mocks.fetchUiMessages.mockResolvedValue(freshV2)
    mocks.fetchUiMessages.mockImplementationOnce(() => staleV1.promise)
    mocks.fetchConversationTaskLink.mockResolvedValue(
      emptyTaskLink(conversationId),
    )

    // duplicate POST：服务端返回 AI_CHAT_TURN_ALREADY_ACCEPTED，触发
    // recoverFromDuplicateClaim。
    const fetchMock = vi.fn(
      async () => new Response(
        JSON.stringify({
          error: '本轮消息已被服务端接受。',
          error_code: 'AI_CHAT_TURN_ALREADY_ACCEPTED',
        }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    store.input = '你好'
    store.sendMessage()

    // 等待 recoverFromDuplicateClaim 发起 fetchUiMessages（慢 v1 在途）。
    await vi.waitFor(() => {
      expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)
    })

    // 慢 v1 在途期间，continuation 批次把 v2 提交并推进游标。
    const source = FakeEventSource.instances.at(-1)!
    source.emit({ type: 'batch', history_version: 2, events: [] })
    await vi.waitFor(() => {
      expect(store.historyVersion).toBe(2)
    })
    expect(store.messages.map((message) => message.id)).toEqual(['v2'])

    // 慢 v1 响应晚到：不得覆盖 v2 消息，也不得回退游标
    // （旧 bug：v2 游标 + v1 消息，随后真正的 v2 批次被当作重复忽略）。
    staleV1.resolve({
      ok: true,
      conversation_id: conversationId,
      history_version: 1,
      created_at: '',
      updated_at: '',
      messages: [
        { id: 'v1', role: 'assistant', parts: [{ type: 'text', text: '陈旧快照' }] },
      ],
    })
    await Promise.resolve()
    await Promise.resolve()
    expect(store.historyVersion).toBe(2)
    expect(store.messages.map((message) => message.id)).toEqual(['v2'])
  })

  it('history 重读失败后按退避确定性重试，最终读到已提交历史（A-11）', async () => {
    vi.useFakeTimers()
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue(
      emptyTaskLink(conversationId),
    )
    // 首次重读失败，重试后成功。
    mocks.fetchUiMessages
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValue({
        ok: true,
        conversation_id: conversationId,
        history_version: 2,
        created_at: '',
        updated_at: '',
        messages: [
          { id: 'v2', role: 'assistant', parts: [{ type: 'text', text: '重试成功' }] },
        ],
      })

    const source = FakeEventSource.instances.at(-1)!
    source.emit({ type: 'batch', history_version: 2, events: [] })
    await vi.advanceTimersByTimeAsync(0)
    // 首次重读失败：尚未应用，已安排退避重试。
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)
    expect(store.historyVersion).toBe(0)

    // 推进首个退避窗口（500ms）：重试成功并应用 v2。
    await vi.advanceTimersByTimeAsync(500)
    await vi.advanceTimersByTimeAsync(0)
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(2)
    expect(store.historyVersion).toBe(2)
    expect(store.messages.map((message) => message.id)).toEqual(['v2'])

    store.newConversation()
    vi.useRealTimers()
  })

  it('task-link 旧成功被取代且新请求失败时确定性重新对账（A-10）', async () => {
    vi.useFakeTimers()
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchUiMessages.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      history_version: 1,
      created_at: '',
      updated_at: '',
      messages: [],
    })

    const slowReady = deferred<Record<string, unknown>>()
    let callCount = 0
    mocks.fetchConversationTaskLink.mockImplementation(() => {
      callCount += 1
      if (callCount === 1) return slowReady.promise
      if (callCount === 2) return Promise.reject(new Error('network'))
      return Promise.resolve({
        ok: true,
        conversation_id: conversationId,
        task_id: 'gtask-1',
        link_status: 'ready',
        task: null,
      })
    })

    // R1（慢成功）在途。
    const first = store.refreshTaskLink()
    // R2（新请求，失败）取代 R1 的代次。
    const second = store.refreshTaskLink()
    await second
    // R2 失败：旧关联保留，已安排确定性重试。
    expect(store.hasUnresolvedTask).toBe(false)

    // R1 的慢成功此时到达：代次已过时，不得应用。
    slowReady.resolve({
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-1',
      link_status: 'ready',
      task: null,
    })
    await first
    expect(store.hasUnresolvedTask).toBe(false)

    // 推进首个退避窗口（250ms）：重试成功，关联完成对账。
    await vi.advanceTimersByTimeAsync(250)
    expect(store.hasUnresolvedTask).toBe(true)
    expect(store.taskLink?.task_id).toBe('gtask-1')

    store.newConversation()
    vi.useRealTimers()
  })

  it('reconnect 不得取消同会话的 task-link 对账重试（A-10）', async () => {
    vi.useFakeTimers()
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchUiMessages.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      history_version: 1,
      created_at: '',
      updated_at: '',
      messages: [],
    })
    let callCount = 0
    mocks.fetchConversationTaskLink.mockImplementation(() => {
      callCount += 1
      if (callCount <= 2) return Promise.reject(new Error('network'))
      return Promise.resolve({
        ok: true,
        conversation_id: conversationId,
        task_id: 'gtask-9',
        link_status: 'ready',
        task: null,
      })
    })

    // resync_required 触发的 resync 带 reconnect=true：成功读取 history 后
    // refreshTaskLink 失败并安排重试，随后的 disconnect/重连不得取消它
    // （旧 bug：disconnectEvents 的 resetRetryState 立即清除 timer）。
    const source = FakeEventSource.instances.at(-1)!
    source.emit({ type: 'resync_required' })
    await vi.advanceTimersByTimeAsync(0)
    expect(callCount).toBe(1)

    // 250ms：第一次重试（仍失败，安排第二次）。
    await vi.advanceTimersByTimeAsync(250)
    expect(callCount).toBe(2)
    // 500ms：第二次重试成功，关联完成对账。
    await vi.advanceTimersByTimeAsync(500)
    expect(callCount).toBe(3)
    expect(store.hasUnresolvedTask).toBe(true)
    expect(store.taskLink?.task_id).toBe('gtask-9')

    store.newConversation()
    vi.useRealTimers()
  })

  it('会话 A 耗尽 history 重试后切到会话 B，B 的失败仍会重试（A-11）', async () => {
    vi.useFakeTimers()
    const store = useAiChatStore()
    const conversationA = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue(
      emptyTaskLink(conversationA),
    )
    // 会话 A：history 重读持续失败，耗尽 3 次重试。
    mocks.fetchUiMessages.mockRejectedValue(new Error('network'))

    const sourceA = FakeEventSource.instances.at(-1)!
    sourceA.emit({ type: 'batch', history_version: 1, events: [] })
    await vi.advanceTimersByTimeAsync(0)
    // 500 + 1000 + 2000ms 三次退避全部失败。
    await vi.advanceTimersByTimeAsync(500)
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(2000)
    const attemptsForA = mocks.fetchUiMessages.mock.calls.length
    expect(attemptsForA).toBeGreaterThanOrEqual(4)

    // 切到会话 B：重试状态必须清零，B 的首次失败仍会得到重试。
    const conversationB = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue(
      emptyTaskLink(conversationB),
    )
    const callsBeforeB = mocks.fetchUiMessages.mock.calls.length

    const sourceB = FakeEventSource.instances.at(-1)!
    sourceB.emit({ type: 'batch', history_version: 1, events: [] })
    await vi.advanceTimersByTimeAsync(0)
    // B 首次失败后安排了重试（旧 bug：计数器未清零，直接放弃）。
    // 至少增加两次：B 的首次 fetch + 500ms 退避重试，直接证明重试发生。
    await vi.advanceTimersByTimeAsync(500)
    expect(mocks.fetchUiMessages.mock.calls.length).toBeGreaterThanOrEqual(
      callsBeforeB + 2,
    )

    store.newConversation()
    vi.useRealTimers()
  })
})

describe('AiChatStore 斜杠命令注册表分发', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.fetchUiMessages.mockReset()
    mocks.fetchConversationTaskLink.mockReset()
    mocks.approveGlobalTask.mockReset()
    mocks.rejectGlobalTask.mockReset()
    mocks.cancelGlobalTask.mockReset()
    mocks.approveGlobalTask.mockResolvedValue({ ok: true, task_id: 'gtask-9', task: {} })
    mocks.rejectGlobalTask.mockResolvedValue({ ok: true, task_id: 'gtask-9', task: {} })
    mocks.cancelGlobalTask.mockResolvedValue({ ok: true, task_id: 'gtask-9', task: {} })
    FakeEventSource.instances = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  function pendingApprovalTaskLink(conversationId: string) {
    return {
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-9',
      link_status: 'ready',
      task: {
        task_id: 'gtask-9',
        goal: '删除商品',
        status: 'pending_approval',
        steps: [],
        current_step_index: 0,
        pending_approval: {
          step_id: 'step-1',
          capability_name: 'product_delete',
          capability_version: '1',
          task_revision: 1,
          digest: 'digest',
          payload: { summary: '删除 3 个商品' },
          requested_at: '',
        },
        pending_inputs: [],
      },
    }
  }

  async function createStoreWithPendingApproval() {
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue(
      pendingApprovalTaskLink(conversationId),
    )
    await store.refreshTaskLink()
    expect(store.hasUnresolvedTask).toBe(true)
    return store
  }

  it('待审批任务存在时 /approve 经命令批准任务且不发送消息', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const store = await createStoreWithPendingApproval()

    store.input = '/approve'
    store.sendMessage()

    await vi.waitFor(() => {
      expect(mocks.approveGlobalTask).toHaveBeenCalledWith('gtask-9', 'step-1')
    })
    await vi.waitFor(() => {
      expect(store.input).toBe('')
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('/approve 确认弹窗取消时保留输入且不调用 API', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const store = await createStoreWithPendingApproval()

    store.input = '/approve'
    store.sendMessage()

    await Promise.resolve()
    expect(mocks.approveGlobalTask).not.toHaveBeenCalled()
    expect(store.input).toBe('/approve')
  })

  it('/reject 缺少原因时保留输入供用户补充', async () => {
    const store = await createStoreWithPendingApproval()

    store.input = '/reject'
    store.sendMessage()

    await Promise.resolve()
    expect(mocks.rejectGlobalTask).not.toHaveBeenCalled()
    expect(store.input).toBe('/reject')
  })

  it('/reject 附原因时拒绝任务并清空输入', async () => {
    const store = await createStoreWithPendingApproval()

    store.input = '/reject 太危险'
    store.sendMessage()

    await vi.waitFor(() => {
      expect(mocks.rejectGlobalTask).toHaveBeenCalledWith('gtask-9', 'step-1', '太危险')
    })
    await vi.waitFor(() => {
      expect(store.input).toBe('')
    })
  })

  it('任务执行中 /cancel 不可用，按发送锁定拦截', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const store = useAiChatStore()
    const conversationId = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-9',
      link_status: 'ready',
      task: {
        task_id: 'gtask-9',
        goal: '采集商品',
        status: 'in_progress',
        steps: [],
        current_step_index: 0,
        pending_approval: null,
        pending_inputs: [],
      },
    })
    await store.refreshTaskLink()

    store.input = '/cancel'
    store.sendMessage()

    expect(mocks.cancelGlobalTask).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(store.input).toBe('/cancel')
  })

  it('未注册命令文本按普通消息流程处理（锁定期被拦截）', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const store = await createStoreWithPendingApproval()

    store.input = '/unknown'
    store.sendMessage()

    expect(fetchMock).not.toHaveBeenCalled()
    expect(store.input).toBe('/unknown')
  })
})

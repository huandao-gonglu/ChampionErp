import { AI_CHAT_SELECTION_KEY } from '@/stores/aiChat'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiWorkUiMessagesResponse } from '@/types/aiWork'
import { useAiChatStore } from '../aiChat'
import { useAiPageContextStore } from '../aiPageContext'

beforeEach(() => localStorage.removeItem(AI_CHAT_SELECTION_KEY))

const encoder = new TextEncoder()

const mocks = vi.hoisted(() => ({
  fetchUiMessages: vi.fn(),
  cancelChatRun: vi.fn(),
  forkChatConversation: vi.fn(),
}))

vi.mock('@/api/aiWork', () => ({
  AI_CHAT_RUNS_PATH: '/api/v1/ai-chat/runs',
  conversationEventsUrl: (conversationId: string, afterHistoryVersion: number) => (
    `/api/v1/ai-work/conversations/${conversationId}/events`
    + `?after_history_version=${Math.max(0, Math.floor(afterHistoryVersion))}`
  ),
  fetchUiMessages: mocks.fetchUiMessages,
  cancelChatRun: mocks.cancelChatRun,
  forkChatConversation: mocks.forkChatConversation,
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

describe('对话分支', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('EventSource', FakeEventSource)
    mocks.forkChatConversation.mockReset()
  })
  afterEach(() => { useAiChatStore().disconnectEvents(); vi.unstubAllGlobals() })

  it('打开分支不发送模型请求，重复点击只提交一次并保留未发送的输入', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const store = useAiChatStore()
    const source = store.startConversation()
    store.input = '待发送的问题'
    let resolve!: (detail: AiWorkUiMessagesResponse) => void
    mocks.forkChatConversation.mockImplementation(() => new Promise<AiWorkUiMessagesResponse>(done => { resolve = done }))
    const pending = store.forkConversation('reply')
    await store.forkConversation('reply')
    store.sendMessage()
    expect(mocks.forkChatConversation).toHaveBeenCalledTimes(1)
    expect(mocks.forkChatConversation).toHaveBeenCalledWith(source, 'reply')
    expect(fetch).not.toHaveBeenCalled()
    resolve({ ok: true, conversation_id: 'branch', messages: [{ id: 'reply', role: 'assistant', parts: [{ type: 'text', text: '已有回答' }] }],
      history_version: 1, created_at: '', updated_at: '' })
    await pending
    expect(store.activeConversationId).toBe('branch')
    expect(store.messages[0]?.parts).toEqual([{ type: 'text', text: '已有回答' }])
    expect(store.input).toBe('待发送的问题')
    expect(fetch).not.toHaveBeenCalled()
    expect(store.forking).toBe(false)
  })

  it('分支失败保留原对话，迟到的分支响应不抢占新对话', async () => {
    const store = useAiChatStore()
    const source = store.startConversation()
    mocks.forkChatConversation.mockRejectedValueOnce(new Error('分支创建失败'))
    await store.forkConversation('reply')
    expect(store.activeConversationId).toBe(source)
    expect(store.error?.message).toBe('分支创建失败')
    let resolve!: (detail: AiWorkUiMessagesResponse) => void
    mocks.forkChatConversation.mockImplementation(() => new Promise<AiWorkUiMessagesResponse>(done => { resolve = done }))
    const pending = store.forkConversation('reply')
    const newId = store.startConversation()
    resolve({ ok: true, conversation_id: 'branch', messages: [], history_version: 1, created_at: '', updated_at: '' })
    await pending
    expect(store.activeConversationId).toBe(newId)
  })
})

describe('停止操作', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.cancelChatRun.mockReset()
    mocks.fetchUiMessages.mockReset()
  })
  afterEach(() => {
    useAiChatStore().disconnectEvents()
    vi.unstubAllGlobals()
  })

  it('立即中断浏览器流，独立取消后端；保留部分输出直到原生历史提交', async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>
    let requestSignal: AbortSignal | null | undefined
    let confirm!: () => void
    mocks.cancelChatRun.mockImplementation(() => new Promise<void>(resolve => { confirm = resolve }))
    const fetchMock = vi.fn(async (_url, init: RequestInit) => {
      requestSignal = init.signal
      const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value } })
      init.signal?.addEventListener('abort', () => controller.error(new DOMException('已停止', 'AbortError')))
      return new Response(stream, { headers: { 'x-vercel-ai-ui-message-stream': 'v1' } })
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useAiChatStore()
    store.input = '开始查询'
    store.sendMessage()
    await vi.waitFor(() => expect(controller).toBeDefined())
    controller.enqueue(encodedChunk({ type: 'start', messageId: 'assistant' }))
    controller.enqueue(encodedChunk({ type: 'text-start', id: 'text' }))
    controller.enqueue(encodedChunk({ type: 'text-delta', id: 'text', delta: '部分输出' }))
    await vi.waitFor(() => expect(store.status).toBe('streaming'))
    const userId = store.messages[0]!.id
    const partial = store.messages
    store.stopStreaming()
    store.stopStreaming()
    expect(store.stopping).toBe(true)
    expect(mocks.cancelChatRun).toHaveBeenCalledTimes(1)
    expect(mocks.cancelChatRun).toHaveBeenCalledWith(store.activeConversationId, userId)
    await vi.waitFor(() => expect(requestSignal?.aborted).toBe(true))
    expect(store.messages[1]?.parts).toEqual(partial[1]?.parts)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(mocks.fetchUiMessages).not.toHaveBeenCalled()
    mocks.fetchUiMessages
      .mockResolvedValueOnce({ history_version: 0, messages: [], run_active: true })
      .mockResolvedValue({ history_version: 1, messages: partial, run_active: false, run_status: 'cancelled' })
    confirm()
    await vi.waitFor(() => expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1))
    expect(store.stopping).toBe(true)
    expect(store.messages).toEqual(partial)
    await vi.waitFor(() => expect(store.stopping).toBe(false), { timeout: 3000 })
    expect(store.receivedNotice).toBe('')
    expect(store.error).toBeUndefined()
  })

  it('等待后台工具也能停止，失败后可以重试，旧会话响应不会影响新会话', async () => {
    const store = useAiChatStore()
    const id = store.startConversation()
    const detail: AiWorkUiMessagesResponse = { ok: true, conversation_id: id, created_at: '', updated_at: '', history_version: 1, messages: [], latest_message_id: 'user-1', pending_tool_calls: [
      { tool_call_id: 'job', tool_name: 'prepare', kind: 'external', summary: '' },
    ] }
    mocks.fetchUiMessages.mockResolvedValue(detail)
    store.openConversation(detail)
    expect(store.canStop).toBe(true)
    mocks.cancelChatRun.mockRejectedValueOnce(new Error('网络不可用'))
    store.stopStreaming()
    await vi.waitFor(() => expect(store.error?.message).toBe('网络不可用'))
    expect(store.stopping).toBe(false)
    expect(store.canStop).toBe(true)
    let confirm!: () => void
    mocks.cancelChatRun.mockImplementation(() => new Promise<void>(resolve => { confirm = resolve }))
    store.stopStreaming()
    expect(mocks.cancelChatRun).toHaveBeenCalledTimes(2)
    store.startConversation()
    confirm()
    await Promise.resolve()
    expect(store.stopping).toBe(false)
    expect(store.receivedNotice).toBe('')
    expect(store.messages).toEqual([])
  })
})


describe('AiChatStore 实时流', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.fetchUiMessages.mockReset()
    localStorage.removeItem('ai-chat-read-background')
    FakeEventSource.instances = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('SDK 连续追加空白时旧回执保持同一引用，活动文本仍完整更新', async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream<Uint8Array>({
      start(value) { controller = value },
    }), { headers: { 'x-vercel-ai-ui-message-stream': 'v1' } })))
    const store = useAiChatStore()
    store.openConversation({ ok: true, conversation_id: 'conversation_global_chat_test',
      created_at: '', updated_at: '', history_version: 1, messages: [{ id: 'old', role: 'assistant', parts: [
        { type: 'tool-read', toolCallId: 'old-call', state: 'output-available', input: {}, output: { payload: 'x'.repeat(70000) } },
      ] }] })
    const old = store.messages[0]!
    store.input = '执行'
    store.sendMessage()
    await vi.waitFor(() => expect(controller).toBeDefined())
    controller.enqueue(encodedChunk({ type: 'start', messageId: 'live' }))
    controller.enqueue(encodedChunk({ type: 'text-start', id: 't' }))
    controller.enqueue(encodedChunk({ type: 'text-delta', id: 't', delta: '我先处理。' }))
    await vi.waitFor(() => expect(store.messages.at(-1)?.parts[0]).toMatchObject({ text: '我先处理。' }))
    const initialText = store.messages.at(-1)!.parts[0]
    for (let i = 0; i < 4090; i++) controller.enqueue(encodedChunk({ type: 'text-delta', id: 't', delta: ' \n' }))
    await vi.waitFor(() => expect(store.messages.at(-1)?.parts[0]).toMatchObject({ text: '我先处理。' + ' \n'.repeat(4090) }))
    expect(store.messages[0]).toBe(old)
    expect(initialText).toMatchObject({ text: '我先处理。' })
    controller.enqueue(encodedChunk({ type: 'error', errorText: '生成中断' }))
    controller.close()
    await vi.waitFor(() => expect(store.error?.message).toBe('生成中断'))
    expect(store.messages[0]).toBe(old)
    store.disconnectEvents()
  })

  it.each([
    { page: 'draft_editor' as const, draft_id: 'draft-a', section: 'category' as const },
    { page: 'drafts' as const, selected_draft_ids: ['draft-a', 'draft-b'] },
  ])('发送时保留页面快照并把多次 delta 合并到同一 assistant 气泡：%j', async (context) => {
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
    const background = useAiPageContextStore()
    const owner = Symbol()
    background.setSource(owner, context)
    store.input = '查询草稿'
    store.sendMessage()
    background.setSource(owner, { page: 'product_editor', product_id: 'product-b' })

    await vi.waitFor(() => {
      expect(controller).toBeDefined()
      expect(store.messages).toHaveLength(1)
      expect(store.status).toBe('submitted')
    })

    expect(requestBody?.trigger).toBe('submit-message')
    expect(requestBody?.id).toBe(store.activeConversationId)
    expect(requestBody?.messages).toHaveLength(1)
    expect(requestBody?.page_context).toEqual(context)
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


describe('原生等待与批量入口', () => {
  beforeEach(() => { localStorage.removeItem('ai-chat-read-background'); setActivePinia(createPinia()); mocks.fetchUiMessages.mockReset() })
  afterEach(() => { useAiChatStore().disconnectEvents(); vi.unstubAllGlobals() })
  it('草稿批量入口只发送去重后的所选 ID，并显示后台接收状态', async () => {
    let request: { id: string; target_draft_ids: string[]; messages: { id: string; parts: { text: string }[] }[] } | undefined
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => {
      request = JSON.parse(init.body)
      mocks.fetchUiMessages.mockResolvedValue({ history_version: 0, messages: [], pending_tool_calls: [], received_messages: [{ message_id: request!.messages[0].id, status: 'received', text: request!.messages[0].parts[0].text }] })
      return new Response(JSON.stringify({ conversation_id: request!.id, message: '已收到，等待当前操作结束后应用' }), { status: 202 })
    }))
    const store = useAiChatStore()
    store.prepareDrafts(['draft-a', 'draft-b', 'draft-a'])
    await vi.waitFor(() => expect(request).toBeDefined())
    expect(request!.target_draft_ids).toEqual(['draft-a', 'draft-b'])
    expect(request!.messages[0].parts[0].text).toContain('本次仅准备草稿')
    expect(request!.messages[0].parts[0].text).toContain('包含共用主图和 SKU 套图生成')
    expect(request!.messages[0].parts[0].text).toContain('手动修改过的图片先确认保留或重做')
    await vi.waitFor(() => expect(store.receivedMessages[0]?.text).toContain('本次仅准备草稿'))
    expect(store.floatingOpen).toBe(true)
  })
  it('等待工具时追加输入直接进入同一会话收件箱', async () => {
    const store = useAiChatStore()
    const id = store.startConversation()
    store.pendingToolCalls = [{ tool_call_id: 'external', tool_name: 'prepare', kind: 'external', summary: '' }]
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      expect(url).toContain('/runs'); expect(init?.method).toBe('POST')
      const message = JSON.parse(String(init?.body)).messages[0]
      mocks.fetchUiMessages.mockResolvedValue({ history_version: 0, messages: [],
        pending_tool_calls: store.pendingToolCalls,
        received_messages: [{ message_id: message.id, status: 'received', text: message.parts[0].text }] })
      return new Response(JSON.stringify({ message: '已收到' }), { status: 202 })
    })
    vi.stubGlobal('fetch', fetchMock)
    const background = useAiPageContextStore()
    background.setSource(Symbol(), { page: 'draft_editor', draft_id: 'draft-a' })
    store.input = '只处理 draft-a'
    store.sendMessage()
    await vi.waitFor(() => expect(store.receivedMessages).toHaveLength(1))
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)).id).toBe(id)
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)).page_context.draft_id).toBe('draft-a')
    expect(store.receivedMessages[0].text).toBe('只处理 draft-a')
    expect(store.error).toBeUndefined()
    background.toggle()
    store.input = '继续'
    store.sendMessage()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).not.toHaveProperty('page_context')
  })
  it('重试直接开流时丢弃迟到的排队快照；追加输入被采用后立即移除且保留实时回复', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useAiChatStore()
    const id = store.startConversation()
    let stale!: (value: unknown) => void
    mocks.fetchUiMessages.mockImplementationOnce(() => new Promise(resolve => { stale = resolve }))
    store.refreshHistory()
    let controller!: ReadableStreamDefaultController<Uint8Array>
    const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c } })
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream, { headers: { 'x-vercel-ai-ui-message-stream': 'v1' } }))
      .mockImplementationOnce(async (_, init) => {
        const message = JSON.parse(init.body).messages[0]
        mocks.fetchUiMessages.mockResolvedValue({ history_version: 3, messages: [], run_active: true,
          received_messages: [{ message_id: message.id, status: 'received', text: message.parts[0].text }] })
        return new Response('{}', { status: 202 })
      })
    vi.stubGlobal('fetch', fetchMock)
    store.input = '没完成的工作重试'
    store.sendMessage()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    controller.enqueue(encodedChunk({ type: 'start', messageId: 'live' }))
    controller.enqueue(encodedChunk({ type: 'text-start', id: 't' }))
    controller.enqueue(encodedChunk({ type: 'text-delta', id: 't', delta: '正在执行' }))
    await vi.waitFor(() => expect(store.status).toBe('streaming'))
    stale({ history_version: 2, messages: [], received_messages: [{ message_id: store.messages[0].id, text: '没完成的工作重试' }] })
    await Promise.resolve()
    expect(store.receivedMessages).toEqual([])

    store.input = '只处理当前草稿'
    store.sendMessage()
    await vi.waitFor(() => expect(FakeEventSource.instances.at(-1)?.url).toContain('after_history_version=3'))
    expect(store.receivedMessages.map(row => row.text)).toEqual(['只处理当前草稿'])
    mocks.fetchUiMessages.mockResolvedValue({ history_version: 4, messages: [], received_messages: [] })
    FakeEventSource.instances.at(-1)!.emit({ type: 'resync_required', history_version: 4 })
    await vi.waitFor(() => expect(store.receivedMessages).toEqual([]))
    expect(store.messages.at(-1)?.parts[0]).toMatchObject({ text: '正在执行' })
    expect(store.status).toBe('streaming')
    expect(store.activeConversationId).toBe(id)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    controller.enqueue(encodedChunk('[DONE]'))
    controller.close()
    await vi.waitFor(() => expect(store.isBusy).toBe(false))
  })

  it('打开对话只绑定已读取历史，旧会话的订阅回调不能断开当前订阅', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const store = useAiChatStore()
    const first = store.startConversation()
    store.openConversation({ ok: true, conversation_id: 'second', created_at: '', updated_at: '', history_version: 3, messages: [] })
    const current = FakeEventSource.instances.at(-1)!
    store.connectEvents(first)
    expect(current.readyState).toBe(FakeEventSource.OPEN)
    expect(store.activeConversationId).toBe('second')
    expect(store.historyVersion).toBe(3)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(mocks.fetchUiMessages).not.toHaveBeenCalled()
  })
  it('同步和重新打开失败对话都保留具体原因，新回合成功后清除旧错误', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useAiChatStore()
    const id = store.startConversation()
    const runError = {
      code: 'AI_PROVIDER_PAYMENT_REQUIRED',
      message: 'AI Provider 余额不足或计费配置不可用。HTTP 402: Insufficient Balance (code=invalid_request_error)',
    }
    const detail: AiWorkUiMessagesResponse = {
      ok: true, conversation_id: id, created_at: '', updated_at: '',
      history_version: 1, messages: [], run_status: 'failed', run_active: false,
      run_error: runError,
    }
    const initial: AiWorkUiMessagesResponse = { ...detail, history_version: 0, run_status: 'claimed', run_error: null }
    store.openConversation(initial)
    mocks.fetchUiMessages.mockResolvedValue(detail)
    FakeEventSource.instances.at(-1)!.emit({ type: 'resync_required', history_version: 1 })
    await vi.waitFor(() => expect(store.error?.message).toBe(runError.message))
    expect(store.error?.code).toBe(runError.code)
    store.openConversation(detail)
    expect(store.error?.message).toBe(runError.message)
    mocks.fetchUiMessages.mockResolvedValue({ ...detail, history_version: 2, run_status: 'completed', run_error: null })
    FakeEventSource.instances.at(-1)!.emit({ type: 'resync_required', history_version: 2 })
    await vi.waitFor(() => expect(store.error).toBeUndefined())
  })
})

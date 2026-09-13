import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiWorkUiMessagesResponse } from '@/types/aiWork'
import { useAiChatStore } from '../aiChat'
import { useAiPageContextStore } from '../aiPageContext'

const encoder = new TextEncoder()

const mocks = vi.hoisted(() => ({
  fetchUiMessages: vi.fn(),
  cancelChatRun: vi.fn(),
}))

vi.mock('@/api/aiWork', () => ({
  AI_CHAT_RUNS_PATH: '/api/v1/ai-chat/runs',
  conversationEventsUrl: (conversationId: string, afterHistoryVersion: number) => (
    `/api/v1/ai-work/conversations/${conversationId}/events`
    + `?after_history_version=${Math.max(0, Math.floor(afterHistoryVersion))}`
  ),
  fetchUiMessages: mocks.fetchUiMessages,
  cancelChatRun: mocks.cancelChatRun,
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
    await vi.waitFor(() => expect(store.stopping).toBe(false))
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
    const background = useAiPageContextStore()
    const owner = Symbol()
    background.setSource(owner, { page: 'draft_editor', draft_id: 'draft-a', section: 'category' })
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
    expect(requestBody?.page_context).toEqual({ page: 'draft_editor', draft_id: 'draft-a', section: 'category' })
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
      mocks.fetchUiMessages.mockResolvedValue({ history_version: 0, messages: [], pending_tool_calls: [], received_messages: [{ message_id: request!.messages[0].id }] })
      return new Response(JSON.stringify({ conversation_id: request!.id, message: '已收到，等待当前操作结束后应用' }), { status: 202 })
    }))
    const store = useAiChatStore()
    store.prepareDrafts(['draft-a', 'draft-b', 'draft-a'])
    await vi.waitFor(() => expect(request).toBeDefined())
    expect(request!.target_draft_ids).toEqual(['draft-a', 'draft-b'])
    expect(request!.messages[0].parts[0].text).toContain('本次仅准备草稿')
    await vi.waitFor(() => expect(store.receivedNotice).toContain('已收到'))
    expect(store.floatingOpen).toBe(true)
  })
  it('等待工具时追加输入直接进入同一会话收件箱', async () => {
    const store = useAiChatStore()
    const id = store.startConversation()
    store.pendingToolCalls = [{ tool_call_id: 'external', tool_name: 'prepare', kind: 'external', summary: '' }]
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      expect(url).toContain('/runs'); expect(init?.method).toBe('POST')
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
})

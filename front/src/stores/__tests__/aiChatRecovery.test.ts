import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiWorkUiMessagesResponse } from '@/types/aiWork'
import { AI_CHAT_SELECTION_KEY, useAiChatStore } from '../aiChat'

const mocks = vi.hoisted(() => ({ fetchUiMessages: vi.fn() }))
vi.mock('@/api/aiWork', () => ({
  AI_CHAT_RUNS_PATH: '/api/v1/ai-chat/runs',
  fetchUiMessages: mocks.fetchUiMessages,
  conversationEventsUrl: (id: string) => `/events/${id}`,
}))

class FakeEventSource {
  static urls: string[] = []
  static instances: FakeEventSource[] = []
  onmessage?: () => void
  onerror?: () => void
  constructor(url: string) { FakeEventSource.urls.push(url); FakeEventSource.instances.push(this) }
  close() {}
}
const savedId = `conversation_global_chat_${'ab'.repeat(16)}`
function detail(id = savedId): AiWorkUiMessagesResponse {
  return {
    ok: true, conversation_id: id, created_at: '', updated_at: '', history_version: 2,
    messages: [{ id: 'user', role: 'user', parts: [{ type: 'text', text: '上次的问题' }] }],
  }
}
function reload() { setActivePinia(createPinia()); return useAiChatStore() }

describe('普通聊天选择恢复', () => {
  beforeEach(() => {
    localStorage.removeItem(AI_CHAT_SELECTION_KEY)
    const saved = new Map<string, string>()
    vi.spyOn(localStorage, 'getItem').mockImplementation(key => saved.get(key) ?? null)
    vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => { saved.set(key, value) })
    vi.spyOn(localStorage, 'removeItem').mockImplementation(key => { saved.delete(key) })
    mocks.fetchUiMessages.mockReset().mockResolvedValue(detail())
    FakeEventSource.urls = []
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal('fetch', vi.fn())
    reload()
  })
  afterEach(() => { useAiChatStore().disconnectEvents(); vi.unstubAllGlobals() })

  it('刷新或新标签页恢复选中的普通聊天，只读历史并订阅原会话', async () => {
    const previous = useAiChatStore()
    previous.openConversation(detail())
    previous.disconnectEvents()
    const store = reload()
    store.openFloating()
    expect(store.restoring).toBe(true)
    await store.restoreConversation()
    expect(store.activeConversationId).toBe(savedId)
    expect(store.messages).toEqual(detail().messages)
    expect(store.restoring).toBe(false)
    expect(FakeEventSource.urls.at(-1)).toBe(`/events/${savedId}`)
    expect(fetch).not.toHaveBeenCalled()
    store.closeFloating()
    store.openFloating()
    await store.restoreConversation()
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)
  })

  it('恢复中的重复展开共用请求，不能提前发送到新会话', async () => {
    localStorage.setItem(AI_CHAT_SELECTION_KEY, savedId)
    let resolve!: (value: AiWorkUiMessagesResponse) => void
    mocks.fetchUiMessages.mockImplementation(() => new Promise(done => { resolve = done }))
    const store = useAiChatStore()
    store.openFloating()
    store.openFloating()
    store.input = '继续'
    store.sendMessage()
    expect(fetch).not.toHaveBeenCalled()
    expect(store.input).toBe('继续')
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)
    resolve(detail())
    await store.restoreConversation()
    expect(store.activeConversationId).toBe(savedId)
  })

  it.each([new Error('网络中断'), Object.assign(new Error('对话不存在'), { status: 404 })])(
    '恢复失败保留原选择并允许重试，不静默新建：%s', async error => {
      localStorage.setItem(AI_CHAT_SELECTION_KEY, savedId)
      mocks.fetchUiMessages.mockRejectedValueOnce(error)
      const store = useAiChatStore()
      await store.restoreConversation()
      expect(store.restoreError).toContain(error.message)
      expect(store.chat).toBeNull()
      expect(store.restoring).toBe(false)
      expect(localStorage.getItem(AI_CHAT_SELECTION_KEY)).toBe(savedId)
      await store.restoreConversation()
      expect(store.activeConversationId).toBe(savedId)
      expect(store.restoreError).toBe('')
      expect(fetch).not.toHaveBeenCalled()
    },
  )

  it('返回其他会话时拒绝绑定，保留原选择', async () => {
    localStorage.setItem(AI_CHAT_SELECTION_KEY, savedId)
    mocks.fetchUiMessages.mockResolvedValue(detail('conversation_other'))
    const store = useAiChatStore()
    await store.restoreConversation()
    expect(store.restoreError).toContain('与上次选择不一致')
    expect(store.chat).toBeNull()
  })

  it.each(['新对话', '其他历史'] as const)('迟到的恢复响应不能覆盖用户选择的%s', async choice => {
    localStorage.setItem(AI_CHAT_SELECTION_KEY, savedId)
    let resolve!: (value: AiWorkUiMessagesResponse) => void
    mocks.fetchUiMessages.mockImplementation(() => new Promise(done => { resolve = done }))
    const store = useAiChatStore()
    const pending = store.restoreConversation()
    const nextId = choice === '新对话' ? store.startConversation() : `conversation_global_chat_${'cd'.repeat(16)}`
    if (choice === '其他历史') store.openConversation(detail(nextId))
    resolve(detail())
    await pending
    expect(store.activeConversationId).toBe(nextId)
    expect(store.restoring).toBe(false)
    expect(store.restoreError).toBe('')
  })

  it('明确新建后刷新保持空白，不回到旧历史或请求不存在的空白对话', async () => {
    const store = useAiChatStore()
    store.openConversation(detail())
    store.startConversation()
    expect(store.conversationLinkId).toBeNull()
    expect(localStorage.getItem(AI_CHAT_SELECTION_KEY)).toBeNull()
    store.disconnectEvents()
    const next = reload()
    await next.restoreConversation()
    expect(next.messages).toEqual([])
    expect(next.activeConversationId).toBeNull()
    expect(mocks.fetchUiMessages).not.toHaveBeenCalled()
  })

  it('首次发送即记住会话，后台仍运行时刷新也能恢复', async () => {
    let resolve!: (response: Response) => void
    vi.mocked(fetch).mockImplementation(() => new Promise(done => { resolve = done }))
    const store = useAiChatStore()
    store.input = '开始处理'
    store.sendMessage()
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    const id = store.activeConversationId!
    expect(localStorage.getItem(AI_CHAT_SELECTION_KEY)).toBe(id)
    mocks.fetchUiMessages.mockResolvedValue({ ...detail(id), run_active: true, latest_message_id: 'user' })
    const next = reload()
    await next.restoreConversation()
    expect(next.activeConversationId).toBe(id)
    expect(next.canStop).toBe(true)
    resolve(new Response('data: {"type":"finish","finishReason":"stop"}\n\ndata: [DONE]\n\n', {
      headers: { 'x-vercel-ai-ui-message-stream': 'v1' },
    }))
    await vi.waitFor(() => expect(store.historyVersion).toBe(2))
    store.disconnectEvents()
    next.disconnectEvents()
  })

  it.each([
    new TypeError('network error'),
    new TypeError('Load failed'),
  ])('输出流中途断开（%s）时不向对话框展示断流错误，且只触发单次重同步', async (streamError) => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"type":"start"}\n\n'))
        controller.error(streamError)
      },
    })
    vi.mocked(fetch).mockResolvedValue(new Response(body, { headers: { 'x-vercel-ai-ui-message-stream': 'v1' } }))
    const store = useAiChatStore()
    const id = store.startConversation()
    let resolveDetail!: (value: AiWorkUiMessagesResponse) => void
    mocks.fetchUiMessages.mockImplementation(() => new Promise(done => { resolveDetail = done }))
    store.input = '开始处理'
    store.sendMessage()
    await vi.waitFor(() => expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1))
    // 断流后到服务端快照返回之前，绝不能在对话框上方闪现 network error，且保持运行态。
    expect(store.error).toBeUndefined()
    expect(store.status).toBe('ready')
    expect(store.isRunning).toBe(true)
    expect(FakeEventSource.urls.filter(url => url === `/events/${id}`)).toHaveLength(1)
    resolveDetail({
      ...detail(id), history_version: 3, run_active: false,
      messages: [...detail(id).messages, { id: 'reply', role: 'assistant', parts: [{ type: 'text', text: '后台已继续完成' }] }],
    })
    await vi.waitFor(() => expect(store.historyVersion).toBe(3))
    expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)
    expect(store.messages.at(-1)?.id).toBe('reply')
    expect(store.error).toBeUndefined()
    expect(store.isRunning).toBe(false)
    expect(store.status).toBe('ready')
    store.disconnectEvents()
  })

  it('流中途断开且重同步暂时失败时不残留 network error，服务端返回真实 run_error 时再展示', async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"type":"start"}\n\n'))
        controller.error(new TypeError('network error'))
      },
    })
    vi.mocked(fetch).mockResolvedValue(new Response(body, { headers: { 'x-vercel-ai-ui-message-stream': 'v1' } }))
    const store = useAiChatStore()
    const id = store.startConversation()
    mocks.fetchUiMessages
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({
        ...detail(id),
        history_version: 2,
        run_active: false,
        run_status: 'failed',
        run_error: { code: 'AI_PROVIDER_PAYMENT_REQUIRED', message: '模型余额不足' },
      })
    store.input = '开始处理'
    store.sendMessage()
    await vi.waitFor(() => expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1))
    expect(store.error).toBeUndefined()
    await vi.waitFor(() => expect(store.error?.message).toBe('模型余额不足'), { timeout: 2500 })
    expect(store.error?.code).toBe('AI_PROVIDER_PAYMENT_REQUIRED')
    store.disconnectEvents()
  })

  it('SSE 心跳注释帧（: keepalive）会被透明忽略，不影响正常流式回复', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(': keepalive\n\n'))
        controller.enqueue(encoder.encode('data: {"type":"start","messageId":"assistant-ka"}\n\n'))
        controller.enqueue(encoder.encode(': keepalive\n\n'))
        controller.enqueue(encoder.encode('data: {"type":"text-start","id":"t1"}\n\n'))
        controller.enqueue(encoder.encode('data: {"type":"text-delta","id":"t1","delta":"保持连接成功"}\n\n'))
        controller.enqueue(encoder.encode(': keepalive\n\n'))
        controller.enqueue(encoder.encode('data: {"type":"text-end","id":"t1"}\n\n'))
        controller.enqueue(encoder.encode('data: {"type":"finish","finishReason":"stop"}\n\n'))
        controller.enqueue(encoder.encode('data: [DONE]\n\n'))
        controller.close()
      },
    })
    vi.mocked(fetch).mockResolvedValue(new Response(body, { headers: { 'x-vercel-ai-ui-message-stream': 'v1' } }))
    const store = useAiChatStore()
    const id = store.startConversation()
    mocks.fetchUiMessages.mockResolvedValue({
      ...detail(id), history_version: 2, run_active: false,
      messages: [...detail(id).messages, { id: 'assistant-ka', role: 'assistant', parts: [{ type: 'text', text: '保持连接成功' }] }],
    })
    store.input = '长任务'
    store.sendMessage()
    await vi.waitFor(() => expect(store.historyVersion).toBe(2))
    expect(store.error).toBeUndefined()
    expect(store.messages.at(-1)?.parts[0]).toMatchObject({ type: 'text', text: '保持连接成功' })
    store.disconnectEvents()
  })

  it('请求尚未建立响应时的网络失败仍向用户展示错误', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    mocks.fetchUiMessages.mockRejectedValue(new TypeError('Failed to fetch'))
    const store = useAiChatStore()
    store.input = '离线发送'
    store.sendMessage()
    await vi.waitFor(() => expect(store.error?.message).toBe('Failed to fetch'))
    store.disconnectEvents()
  })

  it('请求被拒绝的 HTTP 错误在重读历史后继续保留', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ error: '目标草稿无效', error_code: 'AI_CHAT_TARGET_INVALID' }), { status: 400 }))
    const store = useAiChatStore()
    store.input = '开始处理'
    store.sendMessage()
    await vi.waitFor(() => expect(store.error?.message).toBe('目标草稿无效'))
    const id = store.activeConversationId!
    mocks.fetchUiMessages.mockResolvedValue({ ...detail(id), history_version: 0, messages: [] })
    store.refreshHistory()
    await vi.waitFor(() => expect(mocks.fetchUiMessages).toHaveBeenCalled())
    await Promise.resolve()
    expect(store.error?.message).toBe('目标草稿无效')
    store.disconnectEvents()
  })

  it('仅等待用户问答或审批时依赖事件流通知，不每 2 秒轮询全量 ui-messages', async () => {
    vi.useFakeTimers()
    try {
      const store = useAiChatStore()
      const id = `conversation_global_chat_${'ef'.repeat(16)}`
      const waitingDetail = {
        ...detail(id),
        history_version: 3,
        run_active: false,
        pending_tool_calls: [{
          tool_call_id: 'ask-1',
          tool_name: 'request_user_input',
          kind: 'input' as const,
          summary: '',
          title: '确认规格',
          question: '请确认规格',
        }],
      }
      mocks.fetchUiMessages.mockResolvedValue(waitingDetail)
      store.openConversation(waitingDetail)
      expect(store.pendingUserActionsCount).toBe(1)

      // 等待 10 秒也不应触发无意义的周期性 /ui-messages 轮询。
      await vi.advanceTimersByTimeAsync(10_000)
      expect(mocks.fetchUiMessages).not.toHaveBeenCalled()

      // 切换为后台 external 长任务时仍按预期轮询进度。
      const externalDetail = {
        ...waitingDetail,
        history_version: 4,
        pending_tool_calls: [{
          tool_call_id: 'job-1',
          tool_name: 'draft_images_prepare',
          kind: 'external' as const,
          summary: '',
        }],
      }
      mocks.fetchUiMessages.mockResolvedValue(externalDetail)
      store.openConversation(externalDetail)
      await vi.advanceTimersByTimeAsync(250)
      expect(mocks.fetchUiMessages).toHaveBeenCalledTimes(1)
      store.disconnectEvents()
    } finally {
      vi.useRealTimers()
    }
  })
})

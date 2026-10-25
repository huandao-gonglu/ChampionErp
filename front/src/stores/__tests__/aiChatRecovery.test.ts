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
  constructor(url: string) { FakeEventSource.urls.push(url) }
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
    await vi.waitFor(() => expect(store.isBusy).toBe(false))
    store.disconnectEvents()
  })
})

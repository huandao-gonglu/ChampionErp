import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiChatStore } from '../aiChat'

const encoder = new TextEncoder()

function encodedChunk(payload: Record<string, unknown> | '[DONE]'): Uint8Array {
  const value = payload === '[DONE]' ? payload : JSON.stringify(payload)
  return encoder.encode(`data: ${value}\n\n`)
}

describe('AiChatStore 实时流', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
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
    controller!.enqueue(encodedChunk('[DONE]'))
    controller!.close()

    await vi.waitFor(() => {
      expect(store.status).toBe('ready')
      expect(store.historyVersion).toBe(1)
    })
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
